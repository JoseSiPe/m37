"""M37 (Move 37) — SPEC-04: 5-Pass Deterministic Editorial Compiler & Traceability Mapper."""

from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import markdown as md_lib

from m37.config import WorkspacePaths, get_core_repo_root
from m37.guards import resolve_artifact_by_id, write_utf8_lf
from m37.schemas import (
    EPISTEMIC_RANK,
    RANK_TO_EPISTEMIC,
    BriefContract,
    ContractValidationError,
    DerivationContract,
    EvidenceContract,
    FindingContract,
    QAVerdictContract,
    extract_wikilink_id,
    parse_raw_artifact,
    validate_artifact_content,
)
from m37.verify import (
    has_valid_content_bound_qa_verdict,
    refresh_engagement_hashes_and_quarantine,
    verify_derivation_phase_a,
    verify_finding_phase_a,
)

SUSTENTO_COMMENT_RE = re.compile(r"<!--\s*sustento\s*:\s*([^>]+?)-->", re.IGNORECASE)
TRANSCLUSION_RE = re.compile(r"!\[\[([^\]#|]+)(?:#([^\]|]+))?(?:\|[^\]]+)?\]\]")
INTERPOLATION_RE = re.compile(
    r"\{\{\s*([A-Za-z0-9_-]+)\.([A-Za-z0-9_.-]+)(?:\s*\|\s*fmt\(\s*['\"]([^'\"]*)['\"]\s*\))?\s*\}\}"
)
WIKILINK_INLINE_RE = re.compile(r"(?<!!)\[\[([^\]#|]+)(?:#[^\]|]+)?(?:\|([^\]]+))?\]\]")
EXECUTABLE_FENCE_RE = re.compile(r"^`{3,}\s*\{\s*[A-Za-z0-9_+-]+", re.MULTILINE)
DANGEROUS_RAW_HTML_RE = re.compile(
    r"<\s*/?\s*(script|img|iframe|object|embed|svg|link|style|form|input|meta|base|audio|video)\b",
    re.IGNORECASE,
)
RAW_HTML_TAG_RE = re.compile(r"<(/?[A-Za-z!][^>]*>)")
UNCONDITIONED_GUARANTEE_RE = re.compile(
    r"\b(garantiza(?:do|da|n|mos)?\s+sin\s+riesgo|éxito\s+garantizado|exito\s+garantizado|"
    r"cero\s+riesgo|100%\s+seguro|certeza\s+absoluta|infalible)\b",
    re.IGNORECASE,
)
PROSE_QUANT_METRIC_RE = re.compile(
    r"(?:(?:USD|MXN|EUR|\$)\s*\d+(?:[.,]\d+)?\s*(?:M|MM|K|B|millones|mil)?\b"
    r"|\b\d+(?:[.,]\d+)?\s*%"
    r"|\b\d+(?:[.,]\d+)?\s+(?:meses|años)\b)",
    re.IGNORECASE,
)


def _extract_numeric_tokens(text: str) -> set[str]:
    """Extract normalized numeric tokens (e.g. '2.45', '18.4', '12', '20') from text/values."""
    tokens: set[str] = set()
    for m in re.finditer(r"\d+(?:[.,]\d+)?", text):
        norm = m.group(0).replace(",", ".")
        tokens.add(norm)
        try:
            fval = float(norm)
            tokens.add(f"{fval:g}")
            tokens.add(f"{fval:.1f}")
            tokens.add(f"{fval:.2f}")
        except ValueError:
            pass
    return tokens


class CompilerAbortError(RuntimeError):
    """Deterministic compilation failure carrying SPEC-04 exit code and pass number."""

    def __init__(self, message: str, *, pass_number: int, exit_code: int) -> None:
        super().__init__(message)
        self.pass_number = pass_number
        self.exit_code = exit_code


@dataclass
class CompiledOutputs:
    engagement_id: str
    markdown_path: Path
    html_path: Path
    traceability_map_path: Path
    qmd_path: Path | None = None
    quarto_output_path: Path | None = None
    quarto_degraded: bool = False
    warnings: list[str] = field(default_factory=list)


def _split_markdown_blocks(body: str) -> list[str]:
    """Segment Markdown body into logical blocks while preserving fenced code blocks and tables."""
    blocks: list[str] = []
    current: list[str] = []
    in_code_fence = False

    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code_fence = not in_code_fence
            current.append(line)
            continue

        if not in_code_fence and stripped == "":
            if current:
                blocks.append("\n".join(current).strip())
                current = []
        else:
            current.append(line)

    if current:
        blocks.append("\n".join(current).strip())

    return [b for b in blocks if b]


def _is_exempt_structural_block(block: str) -> bool:
    """Headings, horizontal rules, and pure non-prose HTML comments do not require sustento anchors."""
    lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
    if not lines:
        return True
    if len(lines) == 1:
        single = lines[0]
        if single.startswith("#"):
            return True
        if single in ("---", "***", "___"):
            return True
        if single.startswith("<!--") and single.endswith("-->") and not SUSTENTO_COMMENT_RE.search(single):
            return True
    if all(ln.startswith("#") for ln in lines):
        return True
    return False


def _extract_block_finding_ids(block: str) -> list[str]:
    """Extract all H-* IDs referenced via <!-- sustento: ... --> or ![[H-XX...]] in a block."""
    found: list[str] = []
    for match in SUSTENTO_COMMENT_RE.finditer(block):
        raw_list = match.group(1)
        for token in raw_list.split(","):
            fid = extract_wikilink_id(token)
            if fid and fid not in found:
                found.append(fid)

    for match in TRANSCLUSION_RE.finditer(block):
        fid = extract_wikilink_id(match.group(1))
        if fid.startswith("H-") and fid not in found:
            found.append(fid)

    return found


def _extract_markdown_section(markdown_body: str, heading_title: str) -> str:
    """Extract text beneath '## <heading_title>' up to the next heading of level <= 2."""
    target = heading_title.strip().lstrip("#").strip().lower()
    lines = markdown_body.splitlines()
    capturing = False
    captured: list[str] = []
    captured_level = 2

    for line in lines:
        stripped = line.strip()
        m = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if m:
            lvl = len(m.group(1))
            title = m.group(2).strip().lower()
            if not capturing and title == target:
                capturing = True
                captured_level = lvl
                continue
            elif capturing and lvl <= captured_level:
                break
        if capturing:
            captured.append(line)

    if not capturing:
        raise KeyError(f"La sección {heading_title!r} no existe en la nota transcluida.")
    return "\n".join(captured).strip()


def _lookup_derivation_value(salidas: dict[str, Any], dotted_key: str) -> Any:
    if dotted_key in salidas:
        return salidas[dotted_key]
    cur: Any = salidas
    for part in dotted_key.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            raise KeyError(f"Clave {dotted_key!r} no encontrada en salidas de la Derivación.")
    return cur


def _has_approved_qa_verdict(
    paths: WorkspacePaths,
    engagement_id: str,
    finding_id: str,
    finding_path: Path | None = None,
) -> bool:
    target_path = finding_path or resolve_artifact_by_id(paths, engagement_id, finding_id)
    _, matches = has_valid_content_bound_qa_verdict(paths, engagement_id, finding_id, target_path)
    return matches


EXECUTIVE_CSS = """
:root {
  --ink: #16191f;
  --muted: #5a6270;
  --accent: #0f3d5e;
  --border: #d8dee6;
  --surface: #f7f9fb;
}
* { box-sizing: border-box; }
body {
  font-family: "Inter", "Segoe UI", -apple-system, BlinkMacSystemFont, sans-serif;
  color: var(--ink);
  line-height: 1.65;
  max-width: 880px;
  margin: 2.5rem auto;
  padding: 0 2rem;
  background: #ffffff;
}
header.m37-header {
  border-bottom: 3px solid var(--accent);
  padding-bottom: 1.25rem;
  margin-bottom: 2rem;
}
header.m37-header .kicker {
  font-size: 0.78rem;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--accent);
  font-weight: 700;
}
header.m37-header h1 {
  margin: 0.35rem 0 0.5rem 0;
  font-size: 1.95rem;
  line-height: 1.25;
}
header.m37-header .meta {
  font-size: 0.88rem;
  color: var(--muted);
}
h1, h2, h3 { color: #0e2436; }
h2 {
  margin-top: 2.2rem;
  padding-bottom: 0.35rem;
  border-bottom: 1px solid var(--border);
  font-size: 1.35rem;
}
table {
  width: 100%;
  border-collapse: collapse;
  margin: 1.4rem 0;
  font-size: 0.93rem;
}
th, td {
  border: 1px solid var(--border);
  padding: 0.6rem 0.8rem;
  text-align: left;
}
th { background: var(--surface); font-weight: 600; }
blockquote {
  border-left: 4px solid var(--accent);
  background: var(--surface);
  margin: 1.25rem 0;
  padding: 0.75rem 1.1rem;
}
sup.m37-ref a {
  color: var(--accent);
  text-decoration: none;
  font-weight: 600;
  padding: 0 0.12rem;
}
section.m37-footnotes {
  margin-top: 3rem;
  padding-top: 1.25rem;
  border-top: 2px solid var(--border);
  font-size: 0.87rem;
  color: var(--muted);
}
section.m37-footnotes ol { padding-left: 1.25rem; }
section.m37-footnotes li { margin-bottom: 0.45rem; }
@media print {
  body { margin: 0; padding: 0.5in; max-width: none; font-size: 10.5pt; }
  h2, table, blockquote { page-break-inside: avoid; }
}
""".strip()


def compile_deliverable(
    paths: WorkspacePaths,
    engagement_id: str,
    *,
    deliverable_name: str | None = None,
    output_format: str = "html",
) -> CompiledOutputs:
    """SPEC-04 §3: Run the 5-pass deterministic editorial compiler on an engagement deliverable."""
    enc_dir = paths.engagement_dir(engagement_id)
    if not enc_dir.is_dir():
        raise FileNotFoundError(f"El encargo {engagement_id!r} no existe en {enc_dir}.")

    brief_path = enc_dir / "00_Brief.md"
    if not brief_path.is_file():
        raise FileNotFoundError(f"Falta 00_Brief.md en {enc_dir}.")

    brief_raw = brief_path.read_text(encoding="utf-8")
    brief_obj, _, _ = validate_artifact_content(
        brief_raw,
        suffix=".md",
        expected_tipo="brief",
    )
    assert isinstance(brief_obj, BriefContract)
    rigor = brief_obj.nivel_rigor

    entregables_dir = enc_dir / "Entregables"
    if deliverable_name:
        fname = deliverable_name if deliverable_name.endswith(".md") else f"{deliverable_name}.md"
        source_md_path = entregables_dir / fname
    else:
        candidates = sorted(entregables_dir.glob("*.md")) if entregables_dir.is_dir() else []
        if not candidates:
            raise FileNotFoundError(f"No hay borradores en {entregables_dir}.")
        source_md_path = candidates[0]

    if not source_md_path.is_file():
        raise FileNotFoundError(f"No existe el borrador editorial: {source_md_path}")

    raw_deliverable = source_md_path.read_text(encoding="utf-8")
    deliv_meta, deliv_body = parse_raw_artifact(raw_deliverable, suffix=".md")
    title = str(deliv_meta.get("titulo") or f"Informe — {brief_obj.id_encargo}")

    # ==================================================================
    # PASS 1: Structural Segmentation, RCE/HTML Guards & Orphan Detection (exit 2)
    # ==================================================================
    if EXECUTABLE_FENCE_RE.search(deliv_body):
        raise CompilerAbortError(
            "PASADA 1 FALLIDA — Celdas de código ejecutable Quarto (```{python}, ```{r}, etc.) están estrictamente prohibidas en entregables.",
            pass_number=1,
            exit_code=2,
        )
    if DANGEROUS_RAW_HTML_RE.search(deliv_body):
        raise CompilerAbortError(
            "PASADA 1 FALLIDA — Etiquetas HTML crudas activas (<script>, <img>, <iframe>, <svg>, etc.) están prohibidas en entregables.",
            pass_number=1,
            exit_code=2,
        )

    blocks = _split_markdown_blocks(deliv_body)
    block_findings_map: dict[int, list[str]] = {}
    orphan_blocks: list[str] = []

    for idx, block in enumerate(blocks):
        if _is_exempt_structural_block(block):
            block_findings_map[idx] = []
            continue
        fids = _extract_block_finding_ids(block)
        if not fids:
            snippet = block.splitlines()[0][:90]
            orphan_blocks.append(f"Bloque #{idx + 1}: {snippet!r}")
        else:
            block_findings_map[idx] = fids

    if orphan_blocks:
        raise CompilerAbortError(
            "PASADA 1 FALLIDA — Bloques sustantivos huérfanos sin <!-- sustento: H-XX --> ni ![[H-XX]]:\n  - "
            + "\n  - ".join(orphan_blocks),
            pass_number=1,
            exit_code=2,
        )

    # ==================================================================
    # PASS 2: Quarantine, Hash Integrity, QA Status & Blocking Domains (exit 3)
    # ==================================================================
    refresh_engagement_hashes_and_quarantine(paths, engagement_id)

    all_referenced_fids = sorted({fid for fids in block_findings_map.values() for fid in fids})
    loaded_findings: dict[str, tuple[FindingContract, str]] = {}
    finding_allowed_numbers: dict[str, set[str]] = {}
    brief_numbers = _extract_numeric_tokens(brief_raw)
    pass2_errors: list[str] = []

    for fid in all_referenced_fids:
        try:
            h_path = resolve_artifact_by_id(paths, engagement_id, fid)
        except FileNotFoundError:
            pass2_errors.append(f"Hallazgo citado inexistente: {fid}")
            continue

        try:
            h_raw = h_path.read_text(encoding="utf-8")
            f_obj, _, f_body = validate_artifact_content(
                h_raw,
                suffix=".md",
                expected_tipo="hallazgo",
            )
            assert isinstance(f_obj, FindingContract)
        except ContractValidationError as exc:
            pass2_errors.append(f"Contrato inválido en {fid}: {exc}")
            continue

        if f_obj.estado_qa in ("invalidado", "desactualizado", "rechazado"):
            pass2_errors.append(f"Hallazgo {fid} bloqueado por estado_qa={f_obj.estado_qa!r}.")
            continue

        if rigor in ("L3", "L4"):
            if f_obj.estado_qa not in ("aprobado", "aprobado_con_correcciones"):
                pass2_errors.append(
                    f"Hallazgo {fid} sin aprobación QA para rigor {rigor} (estado_qa={f_obj.estado_qa!r})."
                )
                continue
            if not _has_approved_qa_verdict(paths, engagement_id, f_obj.id):
                pass2_errors.append(
                    f"Hallazgo {fid} declara estado_qa={f_obj.estado_qa!r} pero no posee acta verificable en QA/V-*.md."
                )
                continue

        phase_a = verify_finding_phase_a(paths, engagement_id, h_path, rigor=rigor)
        if not phase_a.passed:
            for err in phase_a.errors:
                pass2_errors.append(f"[{fid}] {err}")
            continue

        loaded_findings[fid] = (f_obj, f_body)

        allowed_nums = set(brief_numbers)
        allowed_nums.update(_extract_numeric_tokens(f_obj.afirmacion_corta))
        allowed_nums.update(_extract_numeric_tokens(f_body))
        allowed_nums.update(_extract_numeric_tokens(json.dumps(f_obj.valores_anclados)))
        for ev_ref in f_obj.sustento_evidencia:
            try:
                ev_p = resolve_artifact_by_id(paths, engagement_id, ev_ref)
                allowed_nums.update(_extract_numeric_tokens(ev_p.read_text(encoding="utf-8")))
            except FileNotFoundError:
                pass
        for der_ref in f_obj.sustento_derivaciones:
            try:
                der_p = resolve_artifact_by_id(paths, engagement_id, der_ref)
                allowed_nums.update(_extract_numeric_tokens(der_p.read_text(encoding="utf-8")))
            except FileNotFoundError:
                pass
        finding_allowed_numbers[fid] = allowed_nums

    # Verify dominios_bloqueantes coverage
    if brief_obj.dominios_bloqueantes:
        covered_domains = {f_obj.dominio for f_obj, _ in loaded_findings.values()}
        missing_domains = [d for d in brief_obj.dominios_bloqueantes if d not in covered_domains]
        if missing_domains:
            pass2_errors.append(
                f"Faltan hallazgos aprobados para dominios_bloqueantes declarados en Brief: {missing_domains}"
            )

    if pass2_errors:
        raise CompilerAbortError(
            "PASADA 2 FALLIDA — Violación de integridad, cuarentena o QA:\n  - " + "\n  - ".join(pass2_errors),
            pass_number=2,
            exit_code=3,
        )

    # ==================================================================
    # PASS 3: Transclusion, Numeric Interpolation & Traceability Map (exit 4)
    # ==================================================================
    footnote_index_by_ev_id: dict[str, int] = {}
    footnote_records: list[dict[str, Any]] = []

    def _register_evidence_footnote(ev_id_raw: str) -> int | None:
        ev_id = extract_wikilink_id(ev_id_raw)
        if ev_id in footnote_index_by_ev_id:
            return footnote_index_by_ev_id[ev_id]
        try:
            ev_path = resolve_artifact_by_id(paths, engagement_id, ev_id)
            ev_obj, _, _ = validate_artifact_content(
                ev_path.read_text(encoding="utf-8"),
                suffix=ev_path.suffix,
                expected_tipo="evidencia",
            )
            assert isinstance(ev_obj, EvidenceContract)
        except (FileNotFoundError, ContractValidationError):
            return None

        idx_num = len(footnote_records) + 1
        footnote_index_by_ev_id[ev_id] = idx_num
        footnote_records.append(
            {
                "numero": idx_num,
                "id": ev_obj.id,
                "titulo": ev_obj.titulo,
                "emisor_o_autor": ev_obj.emisor_o_autor,
                "fecha_publicacion": ev_obj.fecha_publicacion,
                "fecha_verificacion": ev_obj.fecha_verificacion.isoformat(),
                "localizador_verificable": ev_obj.localizador_verificable,
                "url": ev_obj.url,
            }
        )
        return idx_num

    rendered_md_blocks: list[str] = []
    rendered_html_blocks: list[str] = []
    traceability_entries: list[dict[str, Any]] = []

    for idx, block in enumerate(blocks):
        fids = block_findings_map.get(idx, [])
        interpolated_derivs: list[str] = []

        # High #8: Verify that any quantitative metrics written directly in prose (outside {{...}} and ![[...]])
        # are anchored in the cited Findings, Derivations, Evidence, or Brief.
        if rigor in ("L3", "L4") and fids:
            prose_without_placeholders = INTERPOLATION_RE.sub("", block)
            prose_without_placeholders = TRANSCLUSION_RE.sub("", prose_without_placeholders)
            prose_without_placeholders = SUSTENTO_COMMENT_RE.sub("", prose_without_placeholders)
            prose_without_placeholders = WIKILINK_INLINE_RE.sub("", prose_without_placeholders)
            allowed_block_nums: set[str] = set()
            for fid in fids:
                allowed_block_nums.update(finding_allowed_numbers.get(fid, set()))
            for m_quant in PROSE_QUANT_METRIC_RE.finditer(prose_without_placeholders):
                phrase = m_quant.group(0)
                num_tokens = _extract_numeric_tokens(phrase)
                if num_tokens and not (num_tokens & allowed_block_nums):
                    raise CompilerAbortError(
                        f"PASADA 3 FALLIDA — Cifra cuantitativa en prosa sin anclaje verificable ({phrase!r} en Bloque #{idx + 1}): "
                        f"no coincide con {{D-XX.clave}} ni con el sustento de {fids}.",
                        pass_number=3,
                        exit_code=4,
                    )

        # 3a. Expand ![[H-XX#Sección]] transclusions
        def _replace_transclusion(match: re.Match[str]) -> str:
            target_id = extract_wikilink_id(match.group(1))
            section_name = match.group(2)
            if target_id not in loaded_findings:
                raise CompilerAbortError(
                    f"PASADA 3 FALLIDA — Transclusión hacia artefacto no aprobado o inexistente: {target_id!r}.",
                    pass_number=3,
                    exit_code=4,
                )
            f_obj, f_body = loaded_findings[target_id]
            if not section_name:
                return f_obj.afirmacion_corta
            try:
                return _extract_markdown_section(f_body, section_name)
            except KeyError as exc:
                raise CompilerAbortError(
                    f"PASADA 3 FALLIDA — {exc} (artefacto {target_id}).",
                    pass_number=3,
                    exit_code=4,
                ) from exc

        expanded = TRANSCLUSION_RE.sub(_replace_transclusion, block)

        # 3b. Safe Python interpolation of {{D-XX.clave | fmt(...)}}
        def _replace_interpolation(match: re.Match[str]) -> str:
            deriv_id = match.group(1).strip()
            key_path = match.group(2).strip()
            fmt_spec = match.group(3)

            try:
                d_path = resolve_artifact_by_id(paths, engagement_id, deriv_id)
            except FileNotFoundError as exc:
                raise CompilerAbortError(
                    f"PASADA 3 FALLIDA — Derivación interpolada no existe: {deriv_id!r}.",
                    pass_number=3,
                    exit_code=4,
                ) from exc

            d_verify = verify_derivation_phase_a(paths, engagement_id, d_path, rigor=rigor)
            if not d_verify.passed:
                raise CompilerAbortError(
                    f"PASADA 3 FALLIDA — Derivación interpolada {deriv_id!r} inválida: {'; '.join(d_verify.errors)}",
                    pass_number=3,
                    exit_code=4,
                )

            d_obj, _, _ = validate_artifact_content(
                d_path.read_text(encoding="utf-8"),
                suffix=d_path.suffix,
                expected_tipo="derivacion",
            )
            assert isinstance(d_obj, DerivationContract)

            try:
                raw_val = _lookup_derivation_value(d_obj.salidas, key_path)
            except KeyError as exc:
                raise CompilerAbortError(
                    f"PASADA 3 FALLIDA — {exc} ({deriv_id}.{key_path}).",
                    pass_number=3,
                    exit_code=4,
                ) from exc

            interpolated_derivs.append(f"{deriv_id}.{key_path}")
            if fmt_spec is not None:
                try:
                    return format(raw_val, fmt_spec)
                except (ValueError, TypeError) as exc:
                    raise CompilerAbortError(
                        f"PASADA 3 FALLIDA — Formato {fmt_spec!r} incompatible con {deriv_id}.{key_path}={raw_val!r}: {exc}",
                        pass_number=3,
                        exit_code=4,
                    ) from exc
            return str(raw_val)

        expanded = INTERPOLATION_RE.sub(_replace_interpolation, expanded)

        # ==============================================================
        # PASS 4: Editorial Sanitization & Verifiable Footnote Anchoring
        # ==============================================================
        cleaned = SUSTENTO_COMMENT_RE.sub("", expanded)
        cleaned = WIKILINK_INLINE_RE.sub(lambda m: (m.group(2) or m.group(1)).strip(), cleaned).strip()
        if not cleaned:
            continue

        if EXECUTABLE_FENCE_RE.search(cleaned) or DANGEROUS_RAW_HTML_RE.search(cleaned):
            raise CompilerAbortError(
                f"PASADA 4 FALLIDA — Contenido transcluido contiene código ejecutable Quarto o HTML inseguro en Bloque #{idx + 1}.",
                pass_number=4,
                exit_code=4,
            )

        block_fn_nums: list[int] = []
        if fids:
            ranks: list[int] = []
            for fid in fids:
                f_obj, _ = loaded_findings[fid]
                ranks.append(EPISTEMIC_RANK[f_obj.fuerza_afirmacion])
                for ev_ref in f_obj.sustento_evidencia:
                    fn_num = _register_evidence_footnote(ev_ref)
                    if fn_num and fn_num not in block_fn_nums:
                        block_fn_nums.append(fn_num)
                for der_ref in f_obj.sustento_derivaciones:
                    try:
                        dp = resolve_artifact_by_id(paths, engagement_id, der_ref)
                        dm, _ = parse_raw_artifact(dp.read_text(encoding="utf-8"), suffix=dp.suffix)
                        for inp in dm.get("insumos") or []:
                            iref = inp.get("id_o_ruta", "") if isinstance(inp, dict) else str(inp)
                            fn_num = _register_evidence_footnote(iref)
                            if fn_num and fn_num not in block_fn_nums:
                                block_fn_nums.append(fn_num)
                    except (FileNotFoundError, ContractValidationError):
                        pass

            min_rank = min(ranks)
            ceiling = RANK_TO_EPISTEMIC[min_rank]
            if rigor in ("L3", "L4"):
                if UNCONDITIONED_GUARANTEE_RE.search(cleaned):
                    raise CompilerAbortError(
                        f"PASADA 4 FALLIDA — Violación de techo epistémico ({ceiling!r}) en Bloque #{idx + 1}: "
                        "lenguaje de garantía absoluta o libre de riesgo prohibido en entregables L3/L4.",
                        pass_number=4,
                        exit_code=4,
                    )
                if min_rank <= EPISTEMIC_RANK["inferencia_condicionada"] and re.search(
                    r"\b(demuestra\s+inequívocamente|prueba\s+definitivamente|garantiza\b)",
                    cleaned,
                    re.IGNORECASE,
                ):
                    raise CompilerAbortError(
                        f"PASADA 4 FALLIDA — Inflación epistémica en Bloque #{idx + 1}: la prosa afirma certeza "
                        f"categórica por encima de techo_fuerza_afirmacion={ceiling!r}.",
                        pass_number=4,
                        exit_code=4,
                    )

            traceability_entries.append(
                {
                    "bloque_indice": len(traceability_entries) + 1,
                    "extracto": cleaned[:160],
                    "hallazgos_sustento": fids,
                    "techo_fuerza_afirmacion": ceiling,
                    "derivaciones_interpoladas": interpolated_derivs,
                    "notas_al_pie": block_fn_nums,
                }
            )

        html_safe_cleaned = RAW_HTML_TAG_RE.sub(lambda m: html.escape(m.group(0)), cleaned)
        if block_fn_nums and not cleaned.startswith(("#", "|", "```", "-", "*", "1.")):
            md_refs = "".join(f"[^{n}]" for n in block_fn_nums)
            html_refs = "".join(
                f'<sup class="m37-ref"><a href="#fn-{n}" id="fnref-{n}">[{n}]</a></sup>'
                for n in block_fn_nums
            )
            rendered_md_blocks.append(f"{cleaned} {md_refs}")
            rendered_html_blocks.append(f"{html_safe_cleaned} {html_refs}")
        else:
            rendered_md_blocks.append(cleaned)
            rendered_html_blocks.append(html_safe_cleaned)

    # Append verifiable footnotes section to Markdown
    if footnote_records:
        fn_md_lines = ["## Fuentes y Referencias Verificables", ""]
        for rec in footnote_records:
            loc = f" — {rec['localizador_verificable']}" if rec["localizador_verificable"] else ""
            pub = f" ({rec['fecha_publicacion']})" if rec.get("fecha_publicacion") else ""
            url_str = f" <{rec['url']}>" if rec.get("url") else ""
            fn_md_lines.append(
                f"[^{rec['numero']}]: **{rec['emisor_o_autor']}**{pub}. *{rec['titulo']}*{loc}. "
                f"Verificado: {rec['fecha_verificacion']}.{url_str}"
            )
        rendered_md_blocks.append("\n".join(fn_md_lines))

    final_clean_md = "\n\n".join(rendered_md_blocks).strip() + "\n"

    # Build self-contained executive HTML
    body_html = md_lib.markdown(
        "\n\n".join(rendered_html_blocks),
        extensions=["tables", "fenced_code", "sane_lists"],
    )
    footnotes_html = ""
    if footnote_records:
        items_html = []
        for rec in footnote_records:
            t_esc = html.escape(str(rec["titulo"]))
            a_esc = html.escape(str(rec["emisor_o_autor"]))
            l_esc = html.escape(str(rec["localizador_verificable"]))
            v_esc = html.escape(str(rec["fecha_verificacion"]))
            url_part = (
                f' · <a href="{html.escape(str(rec["url"]), quote=True)}">Enlace fuente</a>'
                if rec.get("url")
                else ""
            )
            items_html.append(
                f'<li id="fn-{rec["numero"]}"><strong>{a_esc}</strong>. <em>{t_esc}</em> — {l_esc} '
                f'(Verificado: {v_esc}){url_part} <a href="#fnref-{rec["numero"]}">↩</a></li>'
            )
        footnotes_html = (
            '<section class="m37-footnotes">\n'
            "  <h2>Fuentes y Referencias Verificables</h2>\n"
            f"  <ol>\n    {''.join(items_html)}\n  </ol>\n"
            "</section>"
        )

    final_html = (
        "<!DOCTYPE html>\n"
        '<html lang="es">\n'
        "<head>\n"
        '  <meta charset="utf-8">\n'
        '  <meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"  <title>{html.escape(title)}</title>\n"
        f"  <style>\n{EXECUTIVE_CSS}\n  </style>\n"
        "</head>\n"
        "<body>\n"
        '  <header class="m37-header">\n'
        f'    <div class="kicker">M37 · Rigor {html.escape(rigor)} · Trazabilidad Verificada</div>\n'
        f"    <h1>{html.escape(title)}</h1>\n"
        f'    <div class="meta">Audiencia: {html.escape(brief_obj.audiencia)} · '
        f"Fecha: {brief_obj.fecha.isoformat()}</div>\n"
        "  </header>\n"
        f"  <main>\n{body_html}\n  </main>\n"
        f"  {footnotes_html}\n"
        "</body>\n"
        "</html>\n"
    )

    out_dir = paths.render_root / engagement_id
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = source_md_path.stem

    md_out_path = out_dir / f"{stem}.md"
    html_out_path = out_dir / f"{stem}.html"
    map_out_path = out_dir / "mapa_trazabilidad.json"

    traceability_doc = {
        "encargo": engagement_id,
        "entregable_origen": source_md_path.name,
        "nivel_rigor": rigor,
        "compilado_utc": datetime.now(timezone.utc).isoformat(),
        "hallazgos_totales_auditados": all_referenced_fids,
        "bloques": traceability_entries,
        "fuentes_primarias": footnote_records,
    }

    write_utf8_lf(md_out_path, final_clean_md)
    write_utf8_lf(html_out_path, final_html)
    write_utf8_lf(map_out_path, json.dumps(traceability_doc, ensure_ascii=False, indent=2) + "\n")

    # ==================================================================
    # PASS 5: Optional Quarto Bridge (pdf / docx / pptx)
    # ==================================================================
    fmt_lower = output_format.lower().strip()
    qmd_path: Path | None = None
    quarto_out: Path | None = None
    quarto_degraded = False
    warnings: list[str] = []

    if fmt_lower in ("pdf", "docx", "pptx"):
        qmd_path = out_dir / f"{stem}.qmd"
        css_src = get_core_repo_root() / "extensions" / "quarto_templates" / "estilo_m37.css"
        if css_src.is_file():
            shutil.copy2(css_src, out_dir / "estilo_m37.css")

        qmd_header = (
            "---\n"
            f'title: "{title}"\n'
            f'date: "{brief_obj.fecha.isoformat()}"\n'
            "lang: es\n"
            "execute:\n"
            "  enabled: false\n"
            f"format:\n  {fmt_lower}:\n    toc: true\n"
            "---\n\n"
        )
        write_utf8_lf(qmd_path, qmd_header + final_clean_md)

        quarto_bin = shutil.which("quarto")
        if quarto_bin:
            proc = subprocess.run(
                [quarto_bin, "render", str(qmd_path), "--to", fmt_lower, "--no-execute"],
                cwd=out_dir,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
            candidate_out = out_dir / f"{stem}.{fmt_lower}"
            if proc.returncode == 0 and candidate_out.is_file():
                quarto_out = candidate_out
            else:
                quarto_degraded = True
                warnings.append(
                    f"Quarto falló al exportar .{fmt_lower} ({proc.stderr.strip()[:200]}); "
                    f"se conservan {html_out_path.name}, {md_out_path.name} y {qmd_path.name}."
                )
        else:
            quarto_degraded = True
            warnings.append(
                f"Quarto CLI no está instalado en este sistema; degradación limpia a {html_out_path.name} "
                f"(imprimible a PDF desde navegador) y {qmd_path.name} listo para renderizar."
            )

    return CompiledOutputs(
        engagement_id=engagement_id,
        markdown_path=md_out_path,
        html_path=html_out_path,
        traceability_map_path=map_out_path,
        qmd_path=qmd_path,
        quarto_output_path=quarto_out,
        quarto_degraded=quarto_degraded,
        warnings=warnings,
    )

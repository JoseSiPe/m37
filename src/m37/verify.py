"""M37 (Move 37) — SPEC-03 & SPEC-05: Phase A Mechanical Verification, SDD Audit & Blind QA Allowlist."""

from __future__ import annotations

import importlib.util
import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from m37.config import WorkspacePaths, get_core_repo_root
from m37.guards import (
    EXCLUDED_CODE_DIRS,
    RECEIPT_FILENAME,
    AgentExecutionContext,
    ExecutionGuard,
    QuarantinedArtifactError,
    compute_code_tree_sha256,
    compute_inputs_hash,
    is_artifact_quarantined,
    resolve_artifact_by_id,
    sha256_file,
    write_utf8_lf,
)
from m37.schemas import (
    EPISTEMIC_RANK,
    SAFE_SLUG_RE,
    ContractValidationError,
    DerivationContract,
    EvidenceContract,
    FindingContract,
    QAVerdictContract,
    RigorLevel,
    compute_finding_substantive_sha256,
    dump_markdown_note,
    extract_wikilink_id,
    parse_raw_artifact,
    validate_artifact_content,
)

RF_ID_RE = re.compile(r"\bRF-\d{2,3}\b")
PY_TEST_DEF_RE = re.compile(r"^\s*(?:async\s+)?def\s+(test_[A-Za-z0-9_]+)\s*\(", re.MULTILINE)
ALLOWED_CORE_VALIDATOR_PLUGINS: frozenset[str] = frozenset({"sdd_codigo"})


@dataclass
class PhaseAResult:
    passed: bool
    artifact_id: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    computed_hash_insumos: str | None = None


def _lookup_nested_key(mapping: dict[str, Any], dotted_key: str) -> tuple[bool, Any]:
    if dotted_key in mapping:
        return True, mapping[dotted_key]
    cur: Any = mapping
    for part in dotted_key.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return False, None
    return True, cur


def _values_equal(expected: Any, actual: Any) -> bool:
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)) and not isinstance(
        expected, bool
    ) and not isinstance(actual, bool):
        return abs(float(expected) - float(actual)) <= 1e-6
    return expected == actual


def _collect_existing_test_functions(code_dir: Path) -> tuple[set[str], set[str]]:
    """Return (bare_test_names, qualified_relpath_test_names) discovered inside codigo/<encargo>/."""
    bare_names: set[str] = set()
    qualified_names: set[str] = set()
    if not code_dir.is_dir():
        return bare_names, qualified_names

    for py_file in sorted(code_dir.rglob("*.py")):
        rel = py_file.relative_to(code_dir)
        if any(part in EXCLUDED_CODE_DIRS for part in rel.parts):
            continue
        content = py_file.read_text(encoding="utf-8", errors="replace")
        for match in PY_TEST_DEF_RE.finditer(content):
            fn_name = match.group(1)
            bare_names.add(fn_name)
            qualified_names.add(f"{rel.as_posix()}::{fn_name}")
            qualified_names.add(f"{py_file.name}::{fn_name}")
    return bare_names, qualified_names


def verify_sdd_code_derivation(
    paths: WorkspacePaths,
    engagement_id: str,
    deriv: DerivationContract,
) -> list[str]:
    """SPEC-03 §2.2 & SPEC-05 §4.2 (#10, #11): Verify kernel receipt, live tree hash, and 100% EARS RF-* test coverage."""
    errors: list[str] = []
    code_dir = (paths.codigo_root / engagement_id).resolve()
    receipt_path = code_dir / RECEIPT_FILENAME

    if not code_dir.is_dir():
        return [f"No existe el directorio de código aislado codigo/{engagement_id}/."]
    if not receipt_path.is_file():
        return [
            f"Falta el recibo de verificación del microkernel ({RECEIPT_FILENAME}) en codigo/{engagement_id}/. "
            "Ejecuta run_verification_cmd antes de registrar la Derivación."
        ]

    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"Recibo {RECEIPT_FILENAME} corrupto: {exc}"]

    if receipt.get("exit_code") != 0:
        errors.append(
            f"La suite de tests falló en el recibo del microkernel (exit_code={receipt.get('exit_code')!r} != 0)."
        )

    cmd_str = str(receipt.get("command") or "").strip()
    cmd_tokens = cmd_str.split()
    is_real_test_runner = any(tok in ("pytest", "unittest") for tok in cmd_tokens[:3])
    forbidden_flags = {"--collect-only", "--co", "--setup-only", "--setup-plan", "--version", "-V", "-h", "--help"}
    has_noop_flag = any(tok.split("=", 1)[0] in forbidden_flags for tok in cmd_tokens)
    if not is_real_test_runner or has_noop_flag:
        errors.append(
            f"El recibo {RECEIPT_FILENAME} no proviene de una corrida real de pytest o unittest (command={cmd_str!r})."
        )
    else:
        combined_out = f"{receipt.get('stdout_tail', '')}\n{receipt.get('stderr_tail', '')}"
        if "pytest" in cmd_tokens[:3] and combined_out.strip():
            if "no tests ran" in combined_out.lower() or "collected 0 items" in combined_out.lower():
                errors.append("El recibo de pytest indica que no se ejecutó ninguna prueba ('no tests ran').")

    live_tree_sha256 = compute_code_tree_sha256(code_dir)
    if receipt.get("tree_sha256") != live_tree_sha256:
        errors.append(
            "Desajuste de integridad en código (tree_sha256): los archivos en codigo/<encargo>/ fueron modificados "
            f"después de ejecutar los tests (recibo={receipt.get('tree_sha256')!r} vs actual={live_tree_sha256!r})."
        )

    if deriv.productor_sha256 and deriv.productor_sha256 != live_tree_sha256:
        errors.append(
            f"productor_sha256 declarado en Derivación ({deriv.productor_sha256!r}) no coincide con "
            f"tree_sha256 real ({live_tree_sha256!r})."
        )

    # Gather all required EARS RF-* IDs from metadatos_metodo and upstream input specs
    required_rfs: set[str] = set()
    meta_rfs = deriv.metadatos_metodo.get("requisitos_ears")
    if isinstance(meta_rfs, list):
        for item in meta_rfs:
            if isinstance(item, dict) and "id" in item:
                required_rfs.add(str(item["id"]).strip())
            else:
                for m in RF_ID_RE.findall(str(item)):
                    required_rfs.add(m)
    elif isinstance(meta_rfs, dict):
        required_rfs.update(str(k).strip() for k in meta_rfs.keys())

    for inp in deriv.insumos:
        try:
            inp_path = resolve_artifact_by_id(paths, engagement_id, inp.id_o_ruta)
            if inp_path.suffix.lower() in (".md", ".json", ".yaml", ".yml", ".txt"):
                txt = inp_path.read_text(encoding="utf-8", errors="replace")
                required_rfs.update(RF_ID_RE.findall(txt))
        except FileNotFoundError:
            pass

    cobertura = deriv.salidas.get("cobertura_rf")
    if not isinstance(cobertura, dict) or not cobertura:
        errors.append(
            "La Derivación de verificación de código debe incluir salidas['cobertura_rf'] mapeando cada RF-* a sus tests."
        )
        return errors

    if not required_rfs:
        required_rfs.update(str(k).strip() for k in cobertura.keys() if RF_ID_RE.fullmatch(str(k).strip()))
    if not required_rfs:
        errors.append("No se encontraron requisitos EARS (RF-*) declarados ni en insumos ni en metadatos_metodo.")
        return errors

    bare_tests, qualified_tests = _collect_existing_test_functions(code_dir)

    for rf_id in sorted(required_rfs):
        mapped = cobertura.get(rf_id)
        test_refs = [mapped] if isinstance(mapped, str) else (list(mapped) if isinstance(mapped, list) else [])
        test_refs = [str(t).strip() for t in test_refs if str(t).strip()]
        if not test_refs:
            errors.append(f"Requisito EARS {rf_id} huérfano: sin tests asignados en salidas['cobertura_rf'].")
            continue

        for tref in test_refs:
            fn_part = tref.split("::")[-1]
            if tref not in qualified_tests and fn_part not in bare_tests:
                errors.append(
                    f"El test {tref!r} declarado para {rf_id} no existe como función 'def {fn_part}(...)' en codigo/{engagement_id}/."
                )

    return errors


def _load_and_run_method_validator_plugin(
    paths: WorkspacePaths,
    engagement_id: str,
    metodo_id: str,
    deriv: DerivationContract,
) -> list[str]:
    """Load optional validador_plugin declared in 40_Metodos/<metodo>.md frontmatter from core repo allowlist ONLY."""
    bare_metodo = extract_wikilink_id(metodo_id)
    if not SAFE_SLUG_RE.fullmatch(bare_metodo):
        return [f"Identificador de método inválido o con path traversal bloqueado: {metodo_id!r}"]

    metodos_root = (paths.vault_root / "40_Metodos").resolve()
    metodo_note = (metodos_root / f"{bare_metodo}.md").resolve()
    plugin_name = ""
    if metodo_note.parent == metodos_root and metodo_note.is_file():
        try:
            meta, _ = parse_raw_artifact(metodo_note.read_text(encoding="utf-8"), suffix=".md")
        except ContractValidationError:
            return []
        if meta.get("tipo") == "derivacion" or meta.get("tipo") == "hallazgo":
            return [f"El archivo 40_Metodos/{bare_metodo}.md no es una nota de método válida."]
        plugin_name = str(meta.get("validador_plugin") or "").strip()
    elif bare_metodo in ("verificacion-sdd-codigo", "sdd-ears-tdd", "sdd_codigo") or deriv.productor_modo == "verificacion_codigo":
        plugin_name = "sdd_codigo"

    if not plugin_name:
        return []

    if not SAFE_SLUG_RE.fullmatch(plugin_name) or plugin_name not in ALLOWED_CORE_VALIDATOR_PLUGINS:
        return [
            f"validador_plugin {plugin_name!r} no está registrado en la lista blanca del motor M37 "
            f"({sorted(ALLOWED_CORE_VALIDATOR_PLUGINS)})."
        ]

    # Security Rule (Critical #1): Load validator plugins ONLY from the immutable core engine repo, NEVER from workspace
    plugin_file = (get_core_repo_root() / "extensions" / "method_validators" / f"{plugin_name}.py").resolve()
    if plugin_file.is_file():
        spec = importlib.util.spec_from_file_location(f"m37_validator_{plugin_name}", plugin_file)
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            validate_fn = getattr(module, "validate_derivation", None)
            if callable(validate_fn):
                return list(validate_fn(paths, engagement_id, deriv))

    if plugin_name == "sdd_codigo":
        return verify_sdd_code_derivation(paths, engagement_id, deriv)

    return [f"No se encontró el validador_plugin {plugin_name!r} requerido por 40_Metodos/{bare_metodo}.md."]


def _extract_json_dict_from_stdout(stdout: str) -> dict[str, Any] | None:
    stripped = stdout.strip()
    if not stripped:
        return None
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    for line in reversed([ln.strip() for ln in stripped.splitlines() if ln.strip()]):
        if line.startswith("{") and line.endswith("}"):
            try:
                parsed = json.loads(line)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                continue
    return None


def verify_derivation_phase_a(
    paths: WorkspacePaths,
    engagement_id: str,
    derivation_path: Path,
    rigor: RigorLevel = "L3",
    *,
    rerun: bool = False,
) -> PhaseAResult:
    """Mechanical Phase A check for a Derivation artifact (D-*.json or D-*.md), with deterministic script execution in L3/L4."""
    if not derivation_path.is_file():
        return PhaseAResult(passed=False, artifact_id=derivation_path.stem, errors=["Archivo de Derivación no existe."])

    try:
        validated, _, _ = validate_artifact_content(
            derivation_path.read_text(encoding="utf-8"),
            suffix=derivation_path.suffix,
            expected_tipo="derivacion",
        )
    except ContractValidationError as exc:
        return PhaseAResult(passed=False, artifact_id=derivation_path.stem, errors=[str(exc)])

    assert isinstance(validated, DerivationContract)
    errors: list[str] = []
    warnings: list[str] = []

    if validated.invalidada:
        errors.append(f"La Derivación {validated.id} está en cuarentena: {validated.motivo_invalidacion}")

    for inp in validated.insumos:
        try:
            inp_path = resolve_artifact_by_id(paths, engagement_id, inp.id_o_ruta)
        except FileNotFoundError as exc:
            errors.append(str(exc))
            continue

        quarantined, q_reason = is_artifact_quarantined(inp_path)
        if quarantined:
            errors.append(f"Insumo {inp.id_o_ruta!r} de {validated.id} está en cuarentena: {q_reason}")

        actual_sha = sha256_file(inp_path)
        if inp.sha256 != actual_sha:
            errors.append(
                f"Insumo desactualizado en {validated.id}: {inp.id_o_ruta!r} cambió de hash "
                f"(declarado={inp.sha256[:12]}... vs actual={actual_sha[:12]}...)."
            )

    if validated.productor_modo == "script":
        script_candidates = [
            (paths.workspace_root / validated.productor_ref).resolve(),
            (paths.analisis_root / engagement_id / validated.productor_ref).resolve(),
        ]
        script_file = next((p for p in script_candidates if p.is_file()), None)
        if script_file is None:
            errors.append(f"Script productor no encontrado para {validated.id}: {validated.productor_ref!r}")
        elif not validated.productor_sha256:
            errors.append(f"La Derivación {validated.id} con productor_modo='script' debe declarar productor_sha256.")
        elif sha256_file(script_file) != validated.productor_sha256:
            errors.append(f"El script {validated.productor_ref!r} cambió respecto a productor_sha256.")
        elif rerun or rigor in ("L3", "L4"):
            guard = ExecutionGuard(
                paths,
                AgentExecutionContext(
                    engagement_id=engagement_id,
                    client_id="VERIFY",
                    node_id="VERIFY-RERUN",
                    profile="analitico",
                ),
            )
            try:
                run_res = guard.run_sandbox_script(script_file)
                if run_res["exit_code"] != 0:
                    errors.append(
                        f"Re-ejecución de {validated.productor_ref!r} falló con exit_code={run_res['exit_code']}: "
                        f"{run_res['stderr']}"
                    )
                else:
                    recomputed = _extract_json_dict_from_stdout(str(run_res["stdout"]))
                    if recomputed is None:
                        errors.append(
                            f"Re-ejecución de {validated.productor_ref!r} no emitió un objeto JSON verificable en stdout."
                        )
                    else:
                        for k, expected_v in validated.salidas.items():
                            found, actual_v = _lookup_nested_key(recomputed, k)
                            if not found or not _values_equal(expected_v, actual_v):
                                errors.append(
                                    f"Irreproducibilidad numérica en {validated.id}: salida {k!r} declarada={expected_v!r} "
                                    f"vs recalculada={actual_v!r}."
                                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"Error al re-ejecutar script {validated.productor_ref!r}: {exc}")

    if validated.productor_modo in ("verificacion_codigo", "plugin_metodo") or (
        extract_wikilink_id(validated.metodo) == "verificacion-sdd-codigo"
    ):
        for err in _load_and_run_method_validator_plugin(paths, engagement_id, "verificacion-sdd-codigo", validated):
            if err not in errors:
                errors.append(err)

    plugin_errors = _load_and_run_method_validator_plugin(paths, engagement_id, validated.metodo, validated)
    for err in plugin_errors:
        if err not in errors:
            errors.append(err)

    return PhaseAResult(passed=(len(errors) == 0), artifact_id=validated.id, errors=errors, warnings=warnings)


def verify_finding_phase_a(
    paths: WorkspacePaths,
    engagement_id: str,
    finding_path: Path,
    rigor: RigorLevel = "L3",
    *,
    r1_finding_snapshot: FindingContract | dict[str, Any] | str | None = None,
    r1_verdict: QAVerdictContract | Path | None = None,
    rerun: bool = False,
) -> PhaseAResult:
    """SPEC-03 §2.2 & §2.3: Mechanical Phase A check + Ronda 2 Anti-Cosmetic Concession Gate."""
    if not finding_path.is_file():
        return PhaseAResult(passed=False, artifact_id=finding_path.stem, errors=["Archivo de Hallazgo no existe."])

    try:
        validated, _, _ = validate_artifact_content(
            finding_path.read_text(encoding="utf-8"),
            suffix=finding_path.suffix,
            expected_tipo="hallazgo",
        )
    except ContractValidationError as exc:
        return PhaseAResult(passed=False, artifact_id=finding_path.stem, errors=[str(exc)])

    assert isinstance(validated, FindingContract)
    errors: list[str] = []
    warnings: list[str] = []

    if rigor in ("L3", "L4") and validated.contrahipotesis_probadas < 1:
        errors.append(f"Rigor {rigor} exige contrahipotesis_probadas >= 1 (actual: {validated.contrahipotesis_probadas}).")

    # 1. Verify upstream Evidence references
    today = date.today()
    for ev_ref in validated.sustento_evidencia:
        try:
            ev_path = resolve_artifact_by_id(paths, engagement_id, ev_ref)
        except FileNotFoundError as exc:
            errors.append(str(exc))
            continue

        quarantined, q_reason = is_artifact_quarantined(ev_path)
        if quarantined:
            errors.append(f"Evidencia {ev_ref!r} en cuarentena: {q_reason}")
            continue

        try:
            ev_raw = ev_path.read_text(encoding="utf-8")
            ev_obj, ev_meta, ev_body = validate_artifact_content(
                ev_raw,
                suffix=ev_path.suffix,
                expected_tipo="evidencia",
            )
            assert isinstance(ev_obj, EvidenceContract)
            if rigor in ("L3", "L4") and not ev_obj.cita_verificada:
                errors.append(f"La evidencia {ev_obj.id} tiene cita_verificada=False (bloqueante en {rigor}).")
            if ev_obj.cita_verificada and not ev_obj.snapshot_local:
                # A hand-written `cita_verificada: true` with nothing to check it against is not a verified citation.
                msg = (
                    f"La evidencia {ev_obj.id} declara cita_verificada=True sin snapshot_local: "
                    "no hay fuente registrada contra la cual comprobar el extracto."
                )
                if rigor in ("L3", "L4"):
                    errors.append(msg)
                else:
                    warnings.append(msg)
            if ev_obj.snapshot_local:
                snap_guard = ExecutionGuard(
                    paths,
                    AgentExecutionContext(
                        engagement_id=engagement_id,
                        client_id="VERIFY",
                        node_id="VERIFY-EVIDENCE",
                        profile="documental",
                    ),
                )
                snap_ok, computed_snap_hash = snap_guard._verify_evidence_snapshot_extract(ev_meta, ev_body)
                if not snap_ok:
                    errors.append(
                        f"Evidencia {ev_obj.id} declara snapshot_local={ev_obj.snapshot_local!r}, pero el archivo no existe "
                        "en referencias/, no está registrado por el kernel (web_fetch_clean o `m37 ingest`), cambió desde "
                        "su registro o no contiene el extracto textual declarado."
                    )
                elif ev_obj.hash_snapshot and computed_snap_hash and (
                    ev_obj.hash_snapshot.removeprefix("sha256:") != computed_snap_hash.removeprefix("sha256:")
                ):
                    errors.append(
                        f"hash_snapshot desactualizado en evidencia {ev_obj.id}: "
                        f"declarado={ev_obj.hash_snapshot!r} vs actual={computed_snap_hash!r}."
                    )
            age_days = (today - ev_obj.fecha_verificacion).days
            if age_days > ev_obj.vigencia_dias_max:
                msg = f"Evidencia {ev_obj.id} vencida ({age_days} días > vigencia {ev_obj.vigencia_dias_max} días)."
                if rigor == "L4":
                    errors.append(msg)
                else:
                    warnings.append(msg)
        except ContractValidationError as exc:
            errors.append(f"Contrato inválido en evidencia {ev_ref!r}: {exc}")

    # 2. Verify upstream Derivation references & collect outputs for valores_anclados
    derivation_outputs: list[dict[str, Any]] = []
    for der_ref in validated.sustento_derivaciones:
        try:
            der_path = resolve_artifact_by_id(paths, engagement_id, der_ref)
        except FileNotFoundError as exc:
            errors.append(str(exc))
            continue

        der_res = verify_derivation_phase_a(paths, engagement_id, der_path, rigor=rigor, rerun=rerun)
        if not der_res.passed:
            errors.extend(f"[{der_res.artifact_id}] {e}" for e in der_res.errors)
        else:
            der_meta, _ = parse_raw_artifact(der_path.read_text(encoding="utf-8"), suffix=der_path.suffix)
            if isinstance(der_meta.get("salidas"), dict):
                derivation_outputs.append(der_meta["salidas"])

    # 3. Verify hash_insumos freshness
    computed_hash: str | None = None
    try:
        computed_hash = compute_inputs_hash(
            paths,
            engagement_id,
            validated.sustento_evidencia,
            validated.sustento_derivaciones,
            check_quarantine=True,
        )
        if validated.hash_insumos != computed_hash:
            errors.append(
                f"hash_insumos desactualizado en {validated.id} "
                f"(declarado={validated.hash_insumos[:12]}... vs actual={computed_hash[:12]}...)."
            )
    except (FileNotFoundError, QuarantinedArtifactError, ContractValidationError) as exc:
        if str(exc) not in errors:
            errors.append(str(exc))

    # 4. Verify valores_anclados against cited Derivations' salidas
    if validated.valores_anclados:
        if not derivation_outputs:
            errors.append(
                f"{validated.id} declara valores_anclados pero no cita ninguna Derivación válida en sustento_derivaciones."
            )
        else:
            for anchor_key, anchor_val in validated.valores_anclados.items():
                matched = False
                for out_dict in derivation_outputs:
                    found, real_val = _lookup_nested_key(out_dict, anchor_key)
                    if found and _values_equal(anchor_val, real_val):
                        matched = True
                        break
                if not matched:
                    errors.append(
                        f"Valor anclado {anchor_key}={anchor_val!r} no coincide con ninguna salida de sustento_derivaciones."
                    )

    # 5. Ronda 2 Anti-Cosmetic Concession & Epistemic Drift Gate (SPEC-03 §2.3)
    if r1_finding_snapshot is not None:
        if isinstance(r1_finding_snapshot, FindingContract):
            r1_finding = r1_finding_snapshot
        elif isinstance(r1_finding_snapshot, str):
            r1_obj, _, _ = validate_artifact_content(r1_finding_snapshot, suffix=".md", expected_tipo="hallazgo")
            assert isinstance(r1_obj, FindingContract)
            r1_finding = r1_obj
        else:
            r1_finding = FindingContract.model_validate(r1_finding_snapshot)

        rank_r1 = EPISTEMIC_RANK[r1_finding.fuerza_afirmacion]
        rank_r2 = EPISTEMIC_RANK[validated.fuerza_afirmacion]
        hash_changed = validated.hash_insumos != r1_finding.hash_insumos

        if rank_r2 > rank_r1 and not hash_changed:
            errors.append(
                "Deriva epistémica ascendente prohibida en Ronda 2: fuerza_afirmacion subió de "
                f"{r1_finding.fuerza_afirmacion!r} a {validated.fuerza_afirmacion!r} sin nuevo sustento (hash_insumos idéntico)."
            )

        verdict_obj: QAVerdictContract | None = None
        if isinstance(r1_verdict, QAVerdictContract):
            verdict_obj = r1_verdict
        elif isinstance(r1_verdict, Path) and r1_verdict.is_file():
            v_parsed, _, _ = validate_artifact_content(
                r1_verdict.read_text(encoding="utf-8"),
                suffix=r1_verdict.suffix,
                expected_tipo="veredicto_qa",
            )
            assert isinstance(v_parsed, QAVerdictContract)
            verdict_obj = v_parsed

        if verdict_obj is not None:
            requires_material_change = any(issue.exige_cambio_material for issue in verdict_obj.hallazgos_critica)
            if requires_material_change and not (hash_changed or rank_r2 < rank_r1):
                errors.append(
                    "Concesión cosmética bloqueada en Ronda 2: el veredicto R1 marcó exige_cambio_material=True, "
                    "pero hash_insumos permanece idéntico y fuerza_afirmacion no se degradó en EPISTEMIC_RANK."
                )

    return PhaseAResult(
        passed=(len(errors) == 0),
        artifact_id=validated.id,
        errors=errors,
        warnings=warnings,
        computed_hash_insumos=computed_hash,
    )


def verdict_is_attested(verdict: QAVerdictContract) -> bool:
    """NC3: a verdict counts for L3/L4 only if the kernel attested blindness or a human signed it with `m37 bless`."""
    is_human_bless = verdict.id.startswith("V-BLESS-") and verdict.familia_auditor == "humano"
    return bool(verdict.revision_ciega_verificada) or is_human_bless


def has_valid_content_bound_qa_verdict(
    paths: WorkspacePaths,
    engagement_id: str,
    finding_id: str,
    finding_path: Path,
    *,
    require_attested: bool = False,
) -> tuple[bool, bool]:
    """Return (has_any_verdict_for_finding, latest_verdict_approves_live_hash).

    With require_attested=True (L3/L4) the latest verdict must also be kernel-attested blind QA or a human bless;
    a verdict relayed by a host-harness subagent (`revision_ciega_verificada: false`) is advisory only.
    """
    qa_dir = paths.engagement_dir(engagement_id) / "QA"
    if not qa_dir.is_dir() or not finding_path.is_file():
        return False, False
    live_sha = compute_finding_substantive_sha256(finding_path)
    matched_verdicts: list[tuple[tuple[int, int, int, str], QAVerdictContract]] = []
    for vfile in qa_dir.glob("V-*.md"):
        try:
            v_obj, _, _ = validate_artifact_content(
                vfile.read_text(encoding="utf-8"),
                suffix=".md",
                expected_tipo="veredicto_qa",
            )
            assert isinstance(v_obj, QAVerdictContract)
            if extract_wikilink_id(v_obj.artefacto_auditado) != finding_id:
                continue
            is_bless = 1 if (v_obj.id.startswith("V-BLESS-") or vfile.name.startswith("V-BLESS-")) else 0
            sort_key = (is_bless, v_obj.ronda, vfile.stat().st_mtime_ns, vfile.name)
            matched_verdicts.append((sort_key, v_obj))
        except ContractValidationError:
            continue
    if not matched_verdicts:
        return False, False
    matched_verdicts.sort(key=lambda item: item[0])
    latest_verdict = matched_verdicts[-1][1]
    is_approved = (
        latest_verdict.decision in ("aceptar", "aceptar_con_correcciones")
        and latest_verdict.sha256_artefacto_auditado == live_sha
        and (not require_attested or verdict_is_attested(latest_verdict))
    )
    return True, is_approved


def describe_latest_verdict(
    paths: WorkspacePaths,
    engagement_id: str,
    finding_id: str,
) -> dict[str, Any] | None:
    """M11: who audited a finding and under which guarantees, for the traceability map."""
    qa_dir = paths.engagement_dir(engagement_id) / "QA"
    if not qa_dir.is_dir():
        return None
    best: tuple[tuple[int, int, int, str], QAVerdictContract] | None = None
    for vfile in qa_dir.glob("V-*.md"):
        try:
            v_obj, _, _ = validate_artifact_content(vfile.read_text(encoding="utf-8"), suffix=".md", expected_tipo="veredicto_qa")
        except ContractValidationError:
            continue
        assert isinstance(v_obj, QAVerdictContract)
        if extract_wikilink_id(v_obj.artefacto_auditado) != finding_id:
            continue
        is_bless = 1 if (v_obj.id.startswith("V-BLESS-") or vfile.name.startswith("V-BLESS-")) else 0
        key = (is_bless, v_obj.ronda, vfile.stat().st_mtime_ns, vfile.name)
        if best is None or key > best[0]:
            best = (key, v_obj)
    if best is None:
        return None
    verdict = best[1]
    return {
        "veredicto": verdict.id,
        "decision": verdict.decision,
        "ronda": verdict.ronda,
        "modelo_auditor": verdict.modelo_auditor,
        "familia_auditor": verdict.familia_auditor,
        "revision_ciega_verificada": verdict.revision_ciega_verificada,
        "atestiguado": verdict_is_attested(verdict),
    }


def refresh_engagement_hashes_and_quarantine(
    paths: WorkspacePaths,
    engagement_id: str,
) -> dict[str, list[str]]:
    """Propagate upstream quarantine -> 'invalidado' and upstream/post-QA edits -> 'desactualizado' across an engagement."""
    enc_dir = paths.engagement_dir(engagement_id)
    deriv_dir = enc_dir / "Derivaciones"
    hallazgos_dir = enc_dir / "Hallazgos"

    summary: dict[str, list[str]] = {
        "derivaciones_invalidadas": [],
        "invalidados": [],
        "desactualizados": [],
        "vigentes": [],
    }

    # Step 1: Cascade quarantine into Derivations if any upstream input is quarantined
    if deriv_dir.is_dir():
        for dpath in sorted(deriv_dir.glob("D-*")):
            if not dpath.is_file() or dpath.suffix.lower() not in (".json", ".md"):
                continue
            try:
                meta, body = parse_raw_artifact(dpath.read_text(encoding="utf-8"), suffix=dpath.suffix)
            except ContractValidationError:
                continue
            if meta.get("invalidada"):
                summary["derivaciones_invalidadas"].append(dpath.stem)
                continue
            for item in meta.get("insumos") or []:
                ref = item.get("id_o_ruta", "") if isinstance(item, dict) else str(item)
                try:
                    up_path = resolve_artifact_by_id(paths, engagement_id, ref)
                except FileNotFoundError:
                    continue
                q, q_reason = is_artifact_quarantined(up_path)
                if q:
                    meta["invalidada"] = True
                    meta["motivo_invalidacion"] = f"Cascada automática por cuarentena de {ref}: {q_reason}"
                    if dpath.suffix.lower() == ".json":
                        write_utf8_lf(dpath, json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
                    else:
                        write_utf8_lf(dpath, dump_markdown_note(meta, body))
                    summary["derivaciones_invalidadas"].append(dpath.stem)
                    break

    # Step 2: Cascade quarantine ('invalidado'), input hash changes, and post-QA content tampering (P3) into Findings
    if hallazgos_dir.is_dir():
        for hpath in sorted(hallazgos_dir.glob("H-*.md")):
            try:
                validated, meta, body = validate_artifact_content(
                    hpath.read_text(encoding="utf-8"),
                    suffix=".md",
                    expected_tipo="hallazgo",
                )
            except ContractValidationError:
                continue
            assert isinstance(validated, FindingContract)

            try:
                live_hash = compute_inputs_hash(
                    paths,
                    engagement_id,
                    validated.sustento_evidencia,
                    validated.sustento_derivaciones,
                    check_quarantine=True,
                )
            except QuarantinedArtifactError:
                if meta.get("estado_qa") != "invalidado":
                    meta["estado_qa"] = "invalidado"
                    write_utf8_lf(hpath, dump_markdown_note(meta, body))
                summary["invalidados"].append(validated.id)
                continue
            except FileNotFoundError:
                if meta.get("estado_qa") != "desactualizado":
                    meta["estado_qa"] = "desactualizado"
                    write_utf8_lf(hpath, dump_markdown_note(meta, body))
                summary["desactualizados"].append(validated.id)
                continue

            # Check if finding content was modified after its QA verdict was issued (P3)
            saw_verdict, verdict_matches_content = has_valid_content_bound_qa_verdict(
                paths,
                engagement_id,
                validated.id,
                hpath,
            )
            tampered_after_qa = (
                meta.get("estado_qa") in ("aprobado", "aprobado_con_correcciones")
                and saw_verdict
                and not verdict_matches_content
            )

            if live_hash != validated.hash_insumos or tampered_after_qa:
                if meta.get("estado_qa") != "desactualizado":
                    meta["estado_qa"] = "desactualizado"
                    write_utf8_lf(hpath, dump_markdown_note(meta, body))
                summary["desactualizados"].append(validated.id)
            else:
                if meta.get("estado_qa") == "invalidado":
                    summary["invalidados"].append(validated.id)
                elif meta.get("estado_qa") == "desactualizado":
                    summary["desactualizados"].append(validated.id)
                else:
                    summary["vigentes"].append(validated.id)

    return summary


def build_blind_qa_allowlist(
    paths: WorkspacePaths,
    engagement_id: str,
    artifact_path: Path,
    *,
    ronda: int = 1,
    prior_verdict_path: Path | None = None,
) -> frozenset[Path]:
    """SPEC-03 §2.3: Construct strict file allowlist for Blind QA (zero access to Nodos/, 90_Bitacora/, 10_Clientes/)."""
    resolved_artifact = artifact_path.resolve()
    enc_dir = paths.engagement_dir(engagement_id).resolve()

    allowed: set[Path] = {resolved_artifact}

    brief_path = (enc_dir / "00_Brief.md").resolve()
    if brief_path.is_file():
        allowed.add(brief_path)

    for agent_file in (
        paths.vault_root / "00_Sistema" / "Agentes" / "qa-critico.md",
        paths.vault_root / "00_Sistema" / "Agentes" / "qa-adversarial.md",
        paths.vault_root / "00_Sistema" / "Agentes" / "validador-dominio.md",
    ):
        if agent_file.is_file():
            allowed.add(agent_file.resolve())

    meta, _ = parse_raw_artifact(resolved_artifact.read_text(encoding="utf-8"), suffix=resolved_artifact.suffix)

    dominio = str(meta.get("dominio") or "").strip()
    if dominio:
        for skill_candidate in (
            paths.vault_root / "00_Sistema" / "Skills_Dominio" / f"{dominio}.md",
            paths.vault_root / "00_Sistema" / "Skills_Dominio" / f"{dominio}-base.md",
        ):
            if skill_candidate.is_file():
                allowed.add(skill_candidate.resolve())

    metodo = str(meta.get("metodo") or "").strip()
    if metodo:
        m_id = extract_wikilink_id(metodo)
        m_path = (paths.vault_root / "40_Metodos" / f"{m_id}.md").resolve()
        if m_path.is_file():
            allowed.add(m_path)

    def _add_derivation_and_inputs(d_path: Path) -> None:
        allowed.add(d_path.resolve())
        try:
            d_meta, _ = parse_raw_artifact(d_path.read_text(encoding="utf-8"), suffix=d_path.suffix)
        except ContractValidationError:
            return
        d_metodo = str(d_meta.get("metodo") or "").strip()
        if d_metodo:
            mp = (paths.vault_root / "40_Metodos" / f"{extract_wikilink_id(d_metodo)}.md").resolve()
            if mp.is_file():
                allowed.add(mp)

        modo = d_meta.get("productor_modo")
        if modo == "script" and d_meta.get("productor_ref"):
            for sc in (
                paths.workspace_root / str(d_meta["productor_ref"]),
                paths.analisis_root / engagement_id / str(d_meta["productor_ref"]),
            ):
                if sc.is_file():
                    allowed.add(sc.resolve())
        elif modo == "verificacion_codigo":
            code_dir = (paths.codigo_root / engagement_id).resolve()
            if code_dir.is_dir():
                for cf in code_dir.rglob("*"):
                    if cf.is_file() and not any(p in EXCLUDED_CODE_DIRS for p in cf.parts):
                        allowed.add(cf.resolve())

        for inp in d_meta.get("insumos") or []:
            ref = inp.get("id_o_ruta", "") if isinstance(inp, dict) else str(inp)
            try:
                inp_p = resolve_artifact_by_id(paths, engagement_id, ref)
                allowed.add(inp_p.resolve())
            except FileNotFoundError:
                pass

    if meta.get("tipo") == "derivacion":
        _add_derivation_and_inputs(resolved_artifact)
    else:
        for ev_ref in meta.get("sustento_evidencia") or []:
            try:
                allowed.add(resolve_artifact_by_id(paths, engagement_id, str(ev_ref)).resolve())
            except FileNotFoundError:
                pass
        for der_ref in meta.get("sustento_derivaciones") or []:
            try:
                dp = resolve_artifact_by_id(paths, engagement_id, str(der_ref)).resolve()
                _add_derivation_and_inputs(dp)
            except FileNotFoundError:
                pass

    if ronda == 2 and prior_verdict_path and prior_verdict_path.is_file():
        allowed.add(prior_verdict_path.resolve())

    # Safety assertion: strictly scrub any forbidden Blind QA paths
    forbidden_roots = (
        (enc_dir / "Nodos").resolve(),
        (enc_dir / "01_Plan.md").resolve(),
        (paths.vault_root / "90_Bitacora").resolve(),
        (paths.vault_root / "10_Clientes").resolve(),
    )
    sanitized = {
        p
        for p in allowed
        if not any(p == fr or fr in p.parents for fr in forbidden_roots)
    }
    return frozenset(sanitized)

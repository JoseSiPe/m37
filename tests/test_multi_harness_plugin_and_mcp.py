"""M37 (Move 37) — End-to-End Tests for Universal Multi-Harness Plugin (Antigravity, Claude Code, Codex CLI) & MCP Kernel Server."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from m37.cli import app, ingest_reference, run_kernel_blind_qa
from m37.config import WorkspacePaths
from m37.gates import approve_gate
from m37.harness import inspect_harness_status, install_harness_plugin
from m37.mcp_server import handle_jsonrpc_request
from m37.router import RoutingGuardrailError
from m37.schemas import parse_raw_artifact


def _call_mcp(name: str, arguments: dict | None = None, *, vault: str | None = None) -> dict:
    resp = handle_jsonrpc_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        },
        vault=vault,
    )
    assert resp is not None
    assert "result" in resp, f"MCP error: {resp}"
    assert resp["result"]["isError"] is False, resp["result"]["content"][0]["text"]
    return json.loads(resp["result"]["content"][0]["text"])


def _call_mcp_error(name: str, arguments: dict | None = None, *, vault: str | None = None) -> str:
    """Call a tool that must be refused and return the error text."""
    resp = handle_jsonrpc_request(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        },
        vault=vault,
    )
    assert resp is not None and "result" in resp, resp
    assert resp["result"]["isError"] is True, resp["result"]["content"][0]["text"]
    return str(resp["result"]["content"][0]["text"])


def _fake_qa_llm(decision: str = "aceptar"):
    """Simulated auditor model: writes one verdict to the path the kernel assigned, then stops."""

    def _llm(*, model: str, messages: list, tools=None, **kwargs):
        if messages[-1]["role"] == "tool":
            return {
                "choices": [{"message": {"content": "Veredicto emitido.", "tool_calls": None}}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 5},
            }
        prompt = messages[1]["content"]
        v_rel = re.search(r"escribe el veredicto en `([^`]+)` con id `([^`]+)`", prompt)
        assert v_rel, prompt[:300]
        audited = re.search(r"el artefacto `([^`]+)\.md`", prompt)
        assert audited
        ronda = int(re.search(r"Audita en Ronda (\d)", prompt).group(1))
        verdict = (
            "---\n"
            f"id: {v_rel.group(2)}\n"
            "tipo: veredicto_qa\n"
            "encargo: ENC-HARNESS-01\n"
            f"artefacto_auditado: '[[{audited.group(1)}]]'\n"
            f"ronda: {ronda}\n"
            "modelo_auditor: lo_que_diga_el_modelo\n"
            "familia_auditor: lo_que_diga_el_modelo\n"
            "fase_a_mecanica: aprobada\n"
            "mejor_ataque: La muestra se concentra en un solo corredor logístico regional.\n"
            "hallazgos_critica: []\n"
            f"decision: {decision}\n"
            "---\n\n## Dictamen\n\nAuditoría simulada.\n"
        )
        return {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "tc-v",
                                "function": {
                                    "name": "write_note",
                                    "arguments": json.dumps({"path": v_rel.group(1), "content": verdict}),
                                },
                            }
                        ],
                    }
                }
            ],
            "usage": {"prompt_tokens": 200, "completion_tokens": 120},
        }

    return _llm


def test_universal_mcp_server_harness_lifecycle_with_human_gates_and_kernel_qa(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ciclo completo en modo harness: el agente trabaja por MCP, la persona aprueba puertas e ingiere la fuente,
    y el QA atestiguado lo ejecuta el kernel con un modelo de otra familia."""
    ws_dir = tmp_path / "harness_ws"
    monkeypatch.setenv("M37_DATA_PRIVACY_ACK", "paid_billing")
    monkeypatch.delenv("M37_VAULT", raising=False)

    # 1. Initialize & list tools
    init_rpc = handle_jsonrpc_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert init_rpc is not None
    assert init_rpc["result"]["serverInfo"]["name"] == "m37-kernel"

    list_rpc = handle_jsonrpc_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert list_rpc is not None
    tools_by_name = {t["name"]: t for t in list_rpc["result"]["tools"]}
    assert {
        "m37_workspace_status",
        "m37_init_or_new_engagement",
        "m37_plan_engagement",
        "m37_write_artifact",
        "m37_prepare_blind_qa",
        "m37_record_qa_verdict",
        "m37_run_kernel_qa",
        "m37_verify_and_compile",
        "fuentes_por_vencer",
        "desempeno_empirico_modelos",
        "patrones_rechazo_qa",
        "cuellos_presupuesto_arquetipo",
        "linaje_artefacto",
    } <= set(tools_by_name)
    # NC1/NC3: no tool schema offers gate approval or a caller-declared auditor identity.
    all_props = {prop for t in tools_by_name.values() for prop in t["inputSchema"].get("properties", {})}
    assert not {p for p in all_props if p.startswith("approve_gate")}
    assert "familia_auditor" not in all_props and "modelo_auditor" not in all_props

    # 2. Agent creates the engagement; it cannot approve Puerta 1
    created = _call_mcp(
        "m37_init_or_new_engagement",
        {
            "vault": str(ws_dir),
            "id_encargo": "ENC-HARNESS-01",
            "cliente": "CLIENTE_H",
            "rigor": "L3",
            "pregunta": "¿Es viable el despliegue regional bajo tope de USD 1.20M?",
        },
    )
    assert created["workspace_inicializado_ahora"] is True
    assert created["puerta_1_aprobada"] is False
    assert "m37 approve ENC-HARNESS-01 --gate 1" in created["siguiente_paso_humano"]
    assert "approve_gate_1" in _call_mcp_error(
        "m37_init_or_new_engagement",
        {"vault": str(ws_dir), "id_encargo": "ENC-X", "approve_gate_1": True},
    )
    assert "Puerta 1 pendiente" in _call_mcp_error(
        "m37_plan_engagement", {"id_encargo": "ENC-HARNESS-01"}, vault=str(ws_dir)
    )

    paths = WorkspacePaths.from_root(ws_dir)
    approve_gate(paths, "ENC-HARNESS-01", 1, approver="socia_prueba")  # la persona, en su terminal

    # 3. Plan in harness mode: honest routing, Puerta 2 still pending
    planned = _call_mcp(
        "m37_plan_engagement",
        {"id_encargo": "ENC-HARNESS-01", "arquetipo": "ARQ_Analisis-Cuantitativo"},
        vault=str(ws_dir),
    )
    assert planned["puerta_2_aprobada"] is False and planned["modo_ejecucion"] == "harness"
    assert len(planned["nodos"]) == 3
    n1_text = (paths.engagement_dir("ENC-HARNESS-01") / "Nodos" / "N-01.md").read_text(encoding="utf-8")
    assert "modelo_ejecutor: harness_anfitrion" in n1_text and "openai_flagship" not in n1_text

    ev_md = (
        "---\n"
        "id: EI-HARNESS-01\n"
        "tipo: evidencia\n"
        "visibilidad: interna_encargo\n"
        "encargo_origen: ENC-HARNESS-01\n"
        "clase: documento_interno\n"
        "titulo: Auditoría Operativa Corredor Sur\n"
        "emisor_o_autor: Dirección de Operaciones\n"
        "fecha_publicacion: '2026-10-01'\n"
        "fecha_verificacion: '2026-10-15'\n"
        "vigencia_dias_max: 3650\n"
        "localizador_verificable: Tabla 1.1, pág. 4\n"
        "snapshot_local: referencias/ENC-HARNESS-01/corredor_sur.txt\n"
        "fiabilidad: alta\n"
        "cita_verificada: true\n"
        "origen_snapshot: kernel_fetch\n"
        "invalidada: false\n"
        "---\n\n"
        "## Síntesis sustantiva\n\n"
        "La retención anual medida en el corredor es de 92.4%.\n\n"
        "## Extracto o referencia verificable\n\n"
        "Tabla 1.1 (pág. 4): Retención neta anual del 92.4% en cohorte auditada.\n\n"
        "## Límites y sesgos de la fuente\n\n"
        "Muestra concentrada en clientes de flota mediana.\n"
    )
    ev_args = {
        "id_encargo": "ENC-HARNESS-01",
        "node_id": "N-01",
        "action": "write_note",
        "path": "vault/20_Encargos/ENC-HARNESS-01/Evidencia_Interna/EI-HARNESS-01.md",
        "content": ev_md,
    }
    # Nothing can be written before a human seals Puerta 2
    assert "Puerta 2 pendiente" in _call_mcp_error("m37_write_artifact", ev_args, vault=str(ws_dir))
    approve_gate(paths, "ENC-HARNESS-01", 2, approver="socia_prueba")

    # 4. NC2: the agent has no way to mint a citable snapshot
    assert "Acción desconocida" in _call_mcp_error(
        "m37_write_artifact",
        {
            "id_encargo": "ENC-HARNESS-01",
            "node_id": "N-01",
            "action": "write_reference_snapshot",
            "path": "referencias/ENC-HARNESS-01/corredor_sur.txt",
            "content": "texto inventado por el agente",
        },
        vault=str(ws_dir),
    )
    # NA1: an unknown node_id carries no profile
    assert "no corresponde a ningún nodo" in _call_mcp_error(
        "m37_write_artifact", {**ev_args, "node_id": "ORQ-99"}, vault=str(ws_dir)
    )

    # The person ingests the real source document
    source_doc = tmp_path / "corredor_sur.txt"
    source_doc.write_text(
        "Auditoría Operativa Corredor Sur — Tabla 1.1 (pág. 4): Retención neta anual del 92.4% en cohorte auditada.\n",
        encoding="utf-8",
    )
    rec = ingest_reference(paths, "ENC-HARNESS-01", source_doc, author="socia_prueba")
    assert rec["origen"] == "ingesta_humana" and rec["path"] == "referencias/ENC-HARNESS-01/corredor_sur.txt"

    assert _call_mcp("m37_write_artifact", ev_args, vault=str(ws_dir))["ok"] is True
    ev_meta, _ = parse_raw_artifact(
        (paths.engagement_dir("ENC-HARNESS-01") / "Evidencia_Interna" / "EI-HARNESS-01.md").read_text(encoding="utf-8"),
        suffix=".md",
    )
    assert ev_meta["cita_verificada"] is True
    assert ev_meta["origen_snapshot"] == "ingesta_humana"  # stamped by the kernel, not what the agent declared

    # 5. Script + Derivation + Finding under N-02 (analitico, requiere_computo: true)
    script_py = 'import json\nprint(json.dumps({"retencion_pct": 92.4}))\n'
    _call_mcp(
        "m37_write_artifact",
        {
            "id_encargo": "ENC-HARNESS-01",
            "node_id": "N-02",
            "action": "write_script",
            "path": "analisis/ENC-HARNESS-01/calc_retencion.py",
            "content": script_py,
        },
        vault=str(ws_dir),
    )
    # A node without requiere_computo cannot run or write scripts, whatever the action is called
    assert "write_sandbox_script" in _call_mcp_error(
        "m37_write_artifact",
        {
            "id_encargo": "ENC-HARNESS-01",
            "node_id": "N-01",
            "action": "write_script",
            "path": "analisis/ENC-HARNESS-01/otro.py",
            "content": script_py,
        },
        vault=str(ws_dir),
    )
    der_json = json.dumps(
        {
            "id": "D-HARNESS-01",
            "tipo": "derivacion",
            "encargo": "ENC-HARNESS-01",
            "metodo": "matriz-comparativa-alternativas",
            "insumos": [{"id_o_ruta": "EI-HARNESS-01", "sha256": "auto"}],
            "productor_modo": "script",
            "productor_ref": "analisis/ENC-HARNESS-01/calc_retencion.py",
            "productor_sha256": "auto",
            "salidas": {"retencion_pct": 0.0},
            "invalidada": False,
        }
    )
    _call_mcp(
        "m37_write_artifact",
        {
            "id_encargo": "ENC-HARNESS-01",
            "node_id": "N-02",
            "action": "write_note",
            "path": "vault/20_Encargos/ENC-HARNESS-01/Derivaciones/D-HARNESS-01.json",
            "content": der_json,
        },
        vault=str(ws_dir),
    )
    h_md = (
        "---\n"
        "id: H-HARNESS-01\n"
        "tipo: hallazgo\n"
        "encargo: ENC-HARNESS-01\n"
        "dominio: estrategia-base\n"
        "naturaleza: estrategico\n"
        "fuerza_afirmacion: inferencia_condicionada\n"
        "afirmacion_corta: El corredor regional mantiene una retención anual de 92.4% condicionada a soporte local.\n"
        "sustento_evidencia:\n"
        "  - '[[EI-HARNESS-01]]'\n"
        "sustento_derivaciones:\n"
        "  - '[[D-HARNESS-01]]'\n"
        "metodo: matriz-comparativa-alternativas\n"
        "valores_anclados:\n"
        "  retencion_pct: 92.4\n"
        "hash_insumos: auto\n"
        "confianza: alta\n"
        "estado_qa: aprobado\n"
        "contrahipotesis_probadas: 1\n"
        "que_lo_refutaria: Caída de retención por debajo de 85.0% en el primer semestre.\n"
        "promovible_a_capital: false\n"
        "---\n\n"
        "## Afirmación\n\n"
        "El corredor regional sostiene una retención anual de 92.4% bajo esquema modular.\n\n"
        "## Sustento y trazabilidad\n\n"
        "Sustentado en [[EI-HARNESS-01]] (Tabla 1.1, pág. 4).\n\n"
        "## Explicaciones rivales descartadas\n\n"
        "Se descartó sesgo estacional al evaluar 12 meses continuos.\n\n"
        "## Condiciones de validez y límites\n\n"
        "Válido para flotas medianas dentro del tope de USD 1.20M.\n"
    )
    _call_mcp(
        "m37_write_artifact",
        {
            "id_encargo": "ENC-HARNESS-01",
            "node_id": "N-02",
            "action": "write_note",
            "path": "vault/20_Encargos/ENC-HARNESS-01/Hallazgos/H-HARNESS-01.md",
            "content": h_md,
        },
        vault=str(ws_dir),
    )

    # 6. Harness subagent verdict: recorded, but NOT attested and NOT enough for L3
    prep_qa = _call_mcp(
        "m37_prepare_blind_qa",
        {"id_encargo": "ENC-HARNESS-01", "id_hallazgo": "H-HARNESS-01", "ronda": 1},
        vault=str(ws_dir),
    )
    assert prep_qa["phase_a_passed"] is True
    assert prep_qa["veredicto_subagente_es_consultivo"] is True
    assert any("H-HARNESS-01.md" in f for f in prep_qa["allowlist_files"])
    assert all("01_Plan.md" not in f and "/Nodos/" not in f for f in prep_qa["allowlist_files"])

    assert "modelo_auditor" in _call_mcp_error(
        "m37_record_qa_verdict",
        {
            "id_encargo": "ENC-HARNESS-01",
            "id_hallazgo": "H-HARNESS-01",
            "decision": "aceptar",
            "mejor_ataque": "La muestra se concentra en un solo corredor logístico regional.",
            "modelo_auditor": "claude_opus",
            "familia_auditor": "anthropic",
        },
        vault=str(ws_dir),
    )
    qa_res = _call_mcp(
        "m37_record_qa_verdict",
        {
            "id_encargo": "ENC-HARNESS-01",
            "id_hallazgo": "H-HARNESS-01",
            "decision": "aceptar",
            "mejor_ataque": "La muestra se concentra en un solo corredor logístico regional.",
            "ronda": 1,
            "node_id": "N-02",
        },
        vault=str(ws_dir),
    )
    assert qa_res["atestiguado"] is False
    assert qa_res["estado_qa_hallazgo"] == "pendiente"
    v_meta, _ = parse_raw_artifact(
        (paths.workspace_root / qa_res["verdict_path"]).read_text(encoding="utf-8"), suffix=".md"
    )
    assert v_meta["revision_ciega_verificada"] is False
    assert v_meta["familia_auditor"] == "harness_no_verificable"

    informe_md = (
        "---\ntitulo: Informe Ejecutivo Harness\n---\n\n"
        "## Conclusión\n\n"
        "![[H-HARNESS-01#Afirmación]] <!-- sustento: H-HARNESS-01 -->\n"
    )
    _call_mcp(
        "m37_write_artifact",
        {
            "id_encargo": "ENC-HARNESS-01",
            "node_id": "N-03",
            "action": "write_note",
            "path": "vault/20_Encargos/ENC-HARNESS-01/Entregables/Informe.md",
            "content": informe_md,
        },
        vault=str(ws_dir),
    )
    blocked = _call_mcp(
        "m37_verify_and_compile",
        {"id_encargo": "ENC-HARNESS-01", "output_format": "html"},
        vault=str(ws_dir),
    )
    assert blocked["verify_passed"] is False and "compiled" not in blocked

    # 7. Kernel-run QA on a model of ANOTHER family than the harness author => attested
    qa_env = {
        "M37_DATA_PRIVACY_ACK": "paid_billing",
        "M37_HARNESS_FAMILY": "anthropic",
        "ANTHROPIC_API_KEY": "ant-paid-123456789",
        "GEMINI_API_KEY": "gem-paid-123456789",
    }
    with pytest.raises(RoutingGuardrailError, match="familia distinta"):
        run_kernel_blind_qa(
            paths,
            "ENC-HARNESS-01",
            "H-HARNESS-01",
            env={k: v for k, v in qa_env.items() if k != "GEMINI_API_KEY"},
            completion_fn=_fake_qa_llm(),
        )
    kq = run_kernel_blind_qa(paths, "ENC-HARNESS-01", "H-HARNESS-01", env=qa_env, completion_fn=_fake_qa_llm())
    assert kq["ok"] is True and kq["decision"] == "aceptar" and kq["familia_qa"] == "google"
    kv_meta, _ = parse_raw_artifact(
        (paths.engagement_dir("ENC-HARNESS-01") / "QA" / f"{kq['veredicto']}.md").read_text(encoding="utf-8"),
        suffix=".md",
    )
    assert kv_meta["revision_ciega_verificada"] is True
    assert kv_meta["familia_auditor"] == "google"  # stamped by the kernel, not by the model's own text
    with pytest.raises(ValueError, match="ya tiene un veredicto atestiguado vigente"):
        run_kernel_blind_qa(paths, "ENC-HARNESS-01", "H-HARNESS-01", env=qa_env, completion_fn=_fake_qa_llm())

    comp = _call_mcp(
        "m37_verify_and_compile",
        {"id_encargo": "ENC-HARNESS-01", "output_format": "html", "rerun_scripts": True},
        vault=str(ws_dir),
    )
    assert comp["verify_passed"] is True
    html_text = Path(comp["compiled"]["html_path"]).read_text(encoding="utf-8")
    assert "Trazabilidad Verificada" not in html_text and "prosa no auditada por QA" in html_text
    trace = json.loads(Path(comp["compiled"]["traceability_map_path"]).read_text(encoding="utf-8"))
    assert trace["qa_por_hallazgo"]["H-HARNESS-01"]["atestiguado"] is True

    # NA4: harness work leaves a trail in the episodic log
    events = [
        json.loads(line)["evento"]
        for line in paths.episodios_path().read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert {"harness_accion", "snapshot_ingerido", "harness_veredicto_no_atestiguado", "qa_kernel_ejecutado"} <= set(events)

    # 8. Graph tools
    linaje = _call_mcp("linaje_artefacto", {"artefacto_id": "H-HARNESS-01"}, vault=str(ws_dir))
    upstream_ids = {u["id"] for u in linaje["insumos_upstream"]}
    assert {"EI-HARNESS-01", "D-HARNESS-01"} <= upstream_ids

    fuentes = _call_mcp("fuentes_por_vencer", {"dias_horizonte": 36500}, vault=str(ws_dir))
    assert any(f["id"] == "EI-HARNESS-01" for f in fuentes)
    assert isinstance(_call_mcp("desempeno_empirico_modelos", {}, vault=str(ws_dir)), list)
    assert isinstance(_call_mcp("patrones_rechazo_qa", {}, vault=str(ws_dir)), list)
    assert isinstance(_call_mcp("cuellos_presupuesto_arquetipo", {}, vault=str(ws_dir)), list)


def test_multi_harness_plugin_installer_antigravity_claude_and_codex(tmp_path: Path) -> None:
    """Verifica que m37 plugin install configure correctamente Antigravity, Claude Code y OpenAI Codex CLI."""
    fake_home = tmp_path / "home"
    fake_ws = tmp_path / "repo"
    fake_home.mkdir()
    fake_ws.mkdir()

    # Pre-seed an existing ~/.codex/config.toml setting to ensure idempotent non-destructive upsert
    codex_dir = fake_home / ".codex"
    codex_dir.mkdir(parents=True)
    (codex_dir / "config.toml").write_text('model = "gpt-5-codex"\n', encoding="utf-8")

    res = install_harness_plugin(
        target="all",
        scope="both",
        workspace_dir=fake_ws,
        home_dir=fake_home,
        portable_python=True,
    )
    assert res["installed"]["antigravity"]
    assert res["installed"]["claude"]
    assert res["installed"]["codex"]

    status = inspect_harness_status(workspace_dir=fake_ws, home_dir=fake_home)
    assert status == {
        "antigravity": {"global": True, "workspace": True},
        "claude": {"global": True, "workspace": True},
        "codex": {"global": True, "workspace": True},
    }

    # Verify Antigravity plugin.json schema compliance
    agy_manifest = json.loads(
        (fake_home / ".gemini" / "config" / "plugins" / "m37" / "plugin.json").read_text(encoding="utf-8")
    )
    allowed_agy_keys = {
        "name",
        "description",
        "logo",
        "suggestedPrompts",
        "displayName",
        "version",
        "author",
        "homepage",
        "repository",
        "disabled",
    }
    assert set(agy_manifest.keys()) <= allowed_agy_keys
    assert len(agy_manifest["suggestedPrompts"]) == 3

    # Verify Codex config.toml preserved existing model setting and added m37_kernel MCP server
    codex_toml = (fake_home / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert 'model = "gpt-5-codex"' in codex_toml
    assert "[mcp_servers.m37_kernel]" in codex_toml

    # Verify CLI subcommands `m37 plugin status`
    runner = CliRunner()
    cli_res = runner.invoke(app, ["plugin", "status", "--workspace", str(fake_ws)])
    assert cli_res.exit_code == 0, cli_res.stdout

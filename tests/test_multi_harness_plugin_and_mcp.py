"""M37 (Move 37) — End-to-End Tests for Universal Multi-Harness Plugin (Antigravity, Claude Code, Codex CLI) & MCP Kernel Server."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from m37.cli import app
from m37.harness import inspect_harness_status, install_harness_plugin
from m37.mcp_server import handle_jsonrpc_request


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


def test_universal_mcp_server_full_zero_api_key_lifecycle_and_graph_fallback(tmp_path: Path) -> None:
    """Verifica que el servidor MCP m37-kernel ejecute todo el ciclo de vida M37 con $0 en llaves API externas."""
    ws_dir = tmp_path / "harness_ws"

    # 1. Initialize & list 12 tools
    init_rpc = handle_jsonrpc_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert init_rpc is not None
    assert init_rpc["result"]["serverInfo"]["name"] == "m37-kernel"

    list_rpc = handle_jsonrpc_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert list_rpc is not None
    tool_names = {t["name"] for t in list_rpc["result"]["tools"]}
    assert {
        "m37_workspace_status",
        "m37_init_or_new_engagement",
        "m37_plan_engagement",
        "m37_write_artifact",
        "m37_prepare_blind_qa",
        "m37_record_qa_verdict",
        "m37_verify_and_compile",
        "fuentes_por_vencer",
        "desempeno_empirico_modelos",
        "patrones_rechazo_qa",
        "cuellos_presupuesto_arquetipo",
        "linaje_artefacto",
    } <= tool_names

    # 2. Initialize workspace & create new L3 engagement via MCP
    created = _call_mcp(
        "m37_init_or_new_engagement",
        {
            "vault": str(ws_dir),
            "id_encargo": "ENC-HARNESS-01",
            "cliente": "CLIENTE_H",
            "rigor": "L3",
            "pregunta": "¿Es viable el despliegue regional bajo tope de USD 1.20M?",
            "approve_gate_1": True,
        },
    )
    assert created["workspace_inicializado_ahora"] is True
    assert created["puerta_1_aprobada"] is True
    assert created["puerta_1_sha256"]

    # 3. Plan engagement in harness mode (0 API keys in .env)
    planned = _call_mcp(
        "m37_plan_engagement",
        {
            "id_encargo": "ENC-HARNESS-01",
            "arquetipo": "ARQ_Diagnostico-Cualitativo",
            "approve_gate_1": True,
            "approve_gate_2": True,
        },
        vault=str(ws_dir),
    )
    assert planned["puerta_2_aprobada"] is True
    assert planned["puerta_2_sha256"]
    assert len(planned["nodos"]) == 3

    # 4. Write local reference snapshot + internal evidence (N-01) via ExecutionGuard MCP tool
    snap_res = _call_mcp(
        "m37_write_artifact",
        {
            "id_encargo": "ENC-HARNESS-01",
            "node_id": "N-01",
            "action": "write_reference_snapshot",
            "path": "referencias/ENC-HARNESS-01/corredor_sur.txt",
            "content": "Auditoría Operativa Corredor Sur — Tabla 1.1 (pág. 4): Retención neta anual del 92.4% en cohorte auditada.\n",
        },
        vault=str(ws_dir),
    )
    assert snap_res["ok"] is True

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
        "vigencia_dias_max: 90\n"
        "localizador_verificable: Tabla 1.1, pág. 4\n"
        "snapshot_local: referencias/ENC-HARNESS-01/corredor_sur.txt\n"
        "fiabilidad: alta\n"
        "cita_verificada: true\n"
        "invalidada: false\n"
        "---\n\n"
        "## Síntesis sustantiva\n\n"
        "La retención anual medida en el corredor es de 92.4%.\n\n"
        "## Extracto o referencia verificable\n\n"
        "Tabla 1.1 (pág. 4): Retención neta anual del 92.4% en cohorte auditada.\n\n"
        "## Límites y sesgos de la fuente\n\n"
        "Muestra concentrada en clientes de flota mediana.\n"
    )
    w_ev = _call_mcp(
        "m37_write_artifact",
        {
            "id_encargo": "ENC-HARNESS-01",
            "node_id": "N-01",
            "action": "write_note",
            "path": "vault/20_Encargos/ENC-HARNESS-01/Evidencia_Interna/EI-HARNESS-01.md",
            "content": ev_md,
        },
        vault=str(ws_dir),
    )
    assert w_ev["ok"] is True

    # 5. Write Finding (N-02) via ExecutionGuard MCP tool
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
        "sustento_derivaciones: []\n"
        "metodo: matriz-comparativa-alternativas\n"
        "valores_anclados:\n"
        "  retencion_pct: 92.4\n"
        "hash_insumos: auto\n"
        "confianza: alta\n"
        "estado_qa: pendiente\n"
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
    # Note: valores_anclados requires a derivation if non-empty, so let's write a script + derivation first!
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
    h_md_with_der = h_md.replace("sustento_derivaciones: []", "sustento_derivaciones:\n  - '[[D-HARNESS-01]]'")
    _call_mcp(
        "m37_write_artifact",
        {
            "id_encargo": "ENC-HARNESS-01",
            "node_id": "N-02",
            "action": "write_note",
            "path": "vault/20_Encargos/ENC-HARNESS-01/Hallazgos/H-HARNESS-01.md",
            "content": h_md_with_der,
        },
        vault=str(ws_dir),
    )

    # 6. Prepare Blind QA & Record QA Verdict via MCP
    prep_qa = _call_mcp(
        "m37_prepare_blind_qa",
        {"id_encargo": "ENC-HARNESS-01", "id_hallazgo": "H-HARNESS-01", "ronda": 1},
        vault=str(ws_dir),
    )
    assert prep_qa["phase_a_passed"] is True
    assert any("H-HARNESS-01.md" in f for f in prep_qa["allowlist_files"])
    assert all("01_Plan.md" not in f and "/Nodos/" not in f for f in prep_qa["allowlist_files"])

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
    assert qa_res["ok"] is True
    assert qa_res["estado_qa_hallazgo"] == "aprobado"

    # 7. Write Deliverable (N-03) and run m37_verify_and_compile
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

    comp = _call_mcp(
        "m37_verify_and_compile",
        {"id_encargo": "ENC-HARNESS-01", "output_format": "html", "rerun_scripts": True},
        vault=str(ws_dir),
    )
    assert comp["verify_passed"] is True
    assert Path(comp["compiled"]["html_path"]).is_file()

    # 8. Test all 5 Bitemporal Graph tools (working even without DuckDB via pure-Python JSON fallback)
    linaje = _call_mcp("linaje_artefacto", {"artefacto_id": "H-HARNESS-01"}, vault=str(ws_dir))
    upstream_ids = {u["id"] for u in linaje["insumos_upstream"]}
    assert {"EI-HARNESS-01", "D-HARNESS-01"} <= upstream_ids

    fuentes = _call_mcp("fuentes_por_vencer", {"dias_horizonte": 3650}, vault=str(ws_dir))
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

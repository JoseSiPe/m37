"""M37 — Regresiones del handoff técnico de seis hallazgos (cierre, interpolación, compuerta QA, Ronda 2,
instalador por target, perímetro de productor_ref, anti-DNS-rebinding y servidor MCP secundario)."""

from __future__ import annotations

import importlib.util
import json
import re
import socket
from pathlib import Path
from typing import Any

import pytest

from m37.cli import (
    bless_finding,
    close_engagement,
    create_new_engagement,
    generate_plan_from_archetype,
    init_workspace,
    run_engagement,
    run_kernel_blind_qa,
)
from m37.compiler import CompilerAbortError, compile_deliverable
from m37.config import WorkspacePaths, get_core_repo_root
from m37.guards import (
    AgentExecutionContext,
    ExecutionGuard,
    PermissionDeniedError,
    quarantine_artifact,
    sha256_file,
    write_utf8_lf,
)
from m37.harness import install_harness_plugin
from m37.schemas import (
    ContractValidationError,
    compute_finding_substantive_sha256,
    dump_markdown_note,
    parse_raw_artifact,
    validate_artifact_content,
)
from m37.verify import build_blind_qa_allowlist, verify_derivation_phase_a

DEMO = "DEMO-01_entrada-mercado"
PAID_ENV = {
    "GEMINI_API_KEY": "gem-paid-123456789",
    "ANTHROPIC_API_KEY": "ant-paid-123456789",
    "M37_DATA_PRIVACY_ACK": "paid_billing",
}


def _finding_md(h_id: str, engagement: str, *, fuerza: str = "inferencia_condicionada", extra: str = "") -> str:
    return dump_markdown_note(
        {
            "id": h_id,
            "tipo": "hallazgo",
            "encargo": engagement,
            "dominio": "estrategia-base",
            "naturaleza": "estrategico",
            "fuerza_afirmacion": fuerza,
            "afirmacion_corta": "El segmento mediano del mercado regional muestra demanda sostenida por el servicio.",
            "sustento_evidencia": ["[[E-202610-001]]"],
            "sustento_derivaciones": [],
            "metodo": "matriz-comparativa-alternativas",
            "hash_insumos": "auto",
            "confianza": "media",
            "estado_qa": "pendiente",
            "contrahipotesis_probadas": 1,
            "que_lo_refutaria": "Una caída sostenida de la demanda en la cohorte siguiente.",
            "promovible_a_capital": True,
        },
        "## Afirmación\n\nEl segmento mediano muestra demanda sostenida bajo un esquema modular." + extra + "\n\n"
        "## Sustento y trazabilidad\n\nVer la evidencia sectorial citada.\n\n"
        "## Explicaciones rivales descartadas\n\nSe descartó un efecto estacional.\n\n"
        "## Condiciones de validez y límites\n\nSolo corredores urbanos de la región.\n",
    )


# ----------------------------------------------------------------------------------------------------------------------
# Tarea 1 — m37 close
# ----------------------------------------------------------------------------------------------------------------------
def _blessed_l3_engagement(tmp_path: Path) -> tuple[WorkspacePaths, str, Path]:
    paths = init_workspace(tmp_path / "ws")
    enc = "ENC-ACME-CONF-01"
    create_new_engagement(paths, enc, "ACME", rigor="L3")
    guard = ExecutionGuard(
        paths, AgentExecutionContext(engagement_id=enc, client_id="ACME", node_id="N-02", profile="analitico")
    )
    rel = guard.write_note(f"vault/20_Encargos/{enc}/Hallazgos/H-{enc}-01.md", _finding_md(f"H-{enc}-01", enc))
    bless_finding(paths, enc, f"H-{enc}-01", "Revisión humana completa del hallazgo anonimizado.")
    return paths, enc, paths.workspace_root / rel


def test_t1_capital_ids_and_content_are_anonymous(tmp_path: Path) -> None:
    paths, enc, _ = _blessed_l3_engagement(tmp_path)
    closed = close_engagement(paths, enc)
    assert len(closed["capital_promovido"]) == 1 and closed["capital_omitido"] == []
    cap_id = closed["capital_promovido"][0]
    assert re.fullmatch(r"C-\d{6}-\d{3}", cap_id)

    cap_files = list((paths.vault_root / "50_Capital").glob("*.md"))
    assert [f.stem for f in cap_files] == [cap_id]
    for needle in ("ACME", enc, "H-ENC"):
        assert needle not in cap_files[0].name
        assert needle.lower() not in cap_files[0].read_text(encoding="utf-8").lower()
    # El vínculo capital -> hallazgo solo queda en la bitácora, que ningún agente de ejecución puede leer
    ficha, _ = parse_raw_artifact(Path(closed["ficha_episodica"]).read_text(encoding="utf-8"), suffix=".md")
    assert ficha["capital_origen"] == {cap_id: f"H-{enc}-01"}


def test_t1_quarantined_input_or_post_qa_edit_blocks_promotion(tmp_path: Path) -> None:
    # (a) evidencia citada entra en cuarentena antes del cierre
    paths, enc, _ = _blessed_l3_engagement(tmp_path / "a")
    quarantine_artifact(paths, enc, "E-202610-001", "Fuente retirada por el emisor original.")
    closed = close_engagement(paths, enc)
    assert closed["capital_promovido"] == [] and [o["hallazgo"] for o in closed["capital_omitido"]] == [f"H-{enc}-01"]
    assert not list((paths.vault_root / "50_Capital").glob("C-*.md"))

    # (b) hallazgo editado en disco después del QA, dejando el frontmatter en "aprobado"
    paths, enc, hpath = _blessed_l3_engagement(tmp_path / "b")
    meta, body = parse_raw_artifact(hpath.read_text(encoding="utf-8"), suffix=".md")
    assert meta["estado_qa"] in ("aprobado", "aprobado_con_correcciones")
    hpath.write_text(
        dump_markdown_note(meta, body.replace("demanda sostenida", "demanda garantizada y creciente")), encoding="utf-8"
    )
    closed = close_engagement(paths, enc)
    assert closed["capital_promovido"] == [] and len(closed["capital_omitido"]) == 1
    assert not list((paths.vault_root / "50_Capital").glob("C-*.md"))


# ----------------------------------------------------------------------------------------------------------------------
# Tarea 2 — interpolación de derivaciones ajenas al sustento
# ----------------------------------------------------------------------------------------------------------------------
def test_t2_interpolating_a_derivation_not_cited_by_the_block_finding_aborts(tmp_path: Path) -> None:
    paths = init_workspace(tmp_path / "ws")
    enc_dir = paths.engagement_dir(DEMO)
    # D-99: una derivación real y válida en Fase A, pero que ningún hallazgo auditado cita
    d_src = enc_dir / "Derivaciones" / "D-DEMO-01-01.json"
    d99 = json.loads(d_src.read_text(encoding="utf-8"))
    d99["id"] = "D-DEMO-01-99"
    d99_path = enc_dir / "Derivaciones" / "D-DEMO-01-99.json"
    write_utf8_lf(d99_path, json.dumps(d99, ensure_ascii=False, indent=2) + "\n")
    assert verify_derivation_phase_a(paths, DEMO, d99_path, rigor="L3").passed

    informe = enc_dir / "Entregables" / "Informe.md"
    original = informe.read_text(encoding="utf-8")
    compile_deliverable(paths, DEMO)  # control: el DEMO interpola D-DEMO-01-01, citada por su hallazgo

    informe.write_text(
        original.rstrip() + "\n\nEl valor alterno es {{D-DEMO-01-99.van_musd}}. <!-- sustento: H-DEMO-01-01 -->\n",
        encoding="utf-8",
    )
    with pytest.raises(CompilerAbortError, match="sin respaldo auditado") as exc:
        compile_deliverable(paths, DEMO)
    assert exc.value.pass_number == 3 and exc.value.exit_code == 4


# ----------------------------------------------------------------------------------------------------------------------
# Tarea 3 — compuerta QA
# ----------------------------------------------------------------------------------------------------------------------
def _verdict(decision: str, *, ronda: int = 1, issues: list[dict[str, Any]] | None = None, prev: dict[str, str] | None = None) -> str:
    meta: dict[str, Any] = {
        "id": "V-X-01",
        "tipo": "veredicto_qa",
        "encargo": "X",
        "artefacto_auditado": "[[H-X-01]]",
        "ronda": ronda,
        "modelo_auditor": "m",
        "familia_auditor": "f",
        "fase_a_mecanica": "aprobada",
        "mejor_ataque": "La muestra única no sostiene la fuerza de la afirmación.",
        "hallazgos_critica": issues or [],
        "decision": decision,
    }
    if prev is not None:
        meta["resolucion_ronda_previa"] = prev
    return dump_markdown_note(meta, "## Dictamen\n\nTexto.\n")


BLOCKER = {
    "id_defecto": "DEF-01",
    "severidad": "bloqueante",
    "categoria": "inflacion_fuerza_epistemica",
    "exige_cambio_material": True,
    "ubicacion": "fuerza_afirmacion",
    "evidencia": "Exceso de fuerza epistémica para una muestra única.",
    "remediacion": "Degradar a inferencia_condicionada.",
}


@pytest.mark.parametrize("decision", ["aceptar", "aceptar_con_correcciones"])
def test_t3_no_acceptance_with_a_blocking_or_persisting_defect(decision: str) -> None:
    with pytest.raises(ContractValidationError, match="bloqueante"):
        validate_artifact_content(_verdict(decision, issues=[BLOCKER]), suffix=".md", expected_tipo="veredicto_qa")
    with pytest.raises(ContractValidationError, match="persiste"):
        validate_artifact_content(
            _verdict(decision, ronda=2, prev={"DEF-01": "persiste"}), suffix=".md", expected_tipo="veredicto_qa"
        )
    # Lo que sí es válido: rechazar con bloqueante, y aceptar con correcciones menores
    validate_artifact_content(_verdict("rechazar", issues=[BLOCKER]), suffix=".md", expected_tipo="veredicto_qa")
    minor = {**BLOCKER, "severidad": "menor", "exige_cambio_material": False}
    validate_artifact_content(
        _verdict("aceptar_con_correcciones", issues=[minor]), suffix=".md", expected_tipo="veredicto_qa"
    )


# ----------------------------------------------------------------------------------------------------------------------
# Tarea 4 — paridad de Ronda 2 entre `m37 run` y `m37 qa`
# ----------------------------------------------------------------------------------------------------------------------
def _run_until_r1_rejection(tmp_path: Path) -> tuple[WorkspacePaths, str, Path, dict[str, int], dict[str, Any]]:
    """`m37 run` con un auditor que rechaza en Ronda 1 (exige cambio material) y un ejecutor que en Ronda 2 no cambia nada."""
    paths = init_workspace(tmp_path / "ws")
    enc = "ENC-R2-01"
    create_new_engagement(paths, enc, "ACME", rigor="L3")
    generate_plan_from_archetype(
        paths, enc, archetype_name="ARQ_Diagnostico-Cualitativo", approve_gate_1=True, approve_gate_2=True, env=PAID_ENV
    )
    counters = {"n02_exec": 0, "qa_r1": 0, "qa_r2": 0}

    def _tool(path: str, content: str) -> dict[str, Any]:
        call = {"id": "tc", "function": {"name": "write_note", "arguments": json.dumps({"path": path, "content": content})}}
        return {"choices": [{"message": {"content": "", "tool_calls": [call]}}], "usage": {"prompt_tokens": 50, "completion_tokens": 20}}

    def _llm(*, model: str, messages: list[dict[str, Any]], tools: Any = None, **kwargs: Any) -> dict[str, Any]:
        done = {"choices": [{"message": {"content": "Listo.", "tool_calls": None}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
        if messages[-1]["role"] == "tool":
            return done
        text = messages[1]["content"]
        if "Audita en Ronda 1" in text:
            counters["qa_r1"] += 1
            m = re.search(r"escribe el veredicto en `([^`]+)` con id `([^`]+)`", text)
            assert m
            meta = json.loads(json.dumps(parse_raw_artifact(_verdict("rechazar", issues=[BLOCKER]), suffix=".md")[0]))
            meta.update({"id": m.group(2), "encargo": enc, "artefacto_auditado": f"[[H-{enc}-01]]"})
            return _tool(m.group(1), dump_markdown_note(meta, "## Dictamen Ronda 1\n\nRechazado.\n"))
        if "Audita en Ronda 2" in text:
            counters["qa_r2"] += 1
            return done
        if "(N-01)" in text:
            ev = dump_markdown_note(
                {
                    "id": f"EI-{enc}-01",
                    "tipo": "evidencia",
                    "visibilidad": "interna_encargo",
                    "encargo_origen": enc,
                    "clase": "documento_interno",
                    "titulo": "Reporte operativo",
                    "emisor_o_autor": "Operaciones",
                    "fecha_verificacion": "2026-10-15",
                    "localizador_verificable": "Anexo 2",
                    "fiabilidad": "media",
                },
                "## Síntesis sustantiva\n\nResumen.\n\n## Extracto o referencia verificable\n\nAnexo 2.\n\n"
                "## Límites y sesgos de la fuente\n\nSerie corta.\n",
            )
            return _tool(f"vault/20_Encargos/{enc}/Evidencia_Interna/EI-{enc}-01.md", ev)
        if "(N-02)" in text:
            counters["n02_exec"] += 1
            if counters["n02_exec"] == 1:
                return _tool(
                    f"vault/20_Encargos/{enc}/Hallazgos/H-{enc}-01.md",
                    _finding_md(f"H-{enc}-01", enc, fuerza="prescripcion_accionable"),
                )
            return done  # Ronda 2: el ejecutor dice que terminó sin tocar el hallazgo
        return done

    run_engagement(paths, enc, target_node_id="N-01", env=PAID_ENV, completion_fn=_llm)
    result = run_engagement(paths, enc, target_node_id="N-02", env=PAID_ENV, completion_fn=_llm)
    return paths, enc, paths.engagement_dir(enc) / "Hallazgos" / f"H-{enc}-01.md", counters, result


def test_t4_unchanged_finding_in_round_2_is_not_sent_to_a_second_auditor(tmp_path: Path) -> None:
    _, _, hpath, counters, result = _run_until_r1_rejection(tmp_path)
    assert counters == {"n02_exec": 2, "qa_r1": 1, "qa_r2": 0}
    assert result["N-02"]["estado"] == "escalado_humano"
    assert parse_raw_artifact(hpath.read_text(encoding="utf-8"), suffix=".md")[0]["estado_qa"] == "rechazado"


def test_t4_run_persists_the_r1_snapshot_so_m37_qa_detects_a_cosmetic_fix(tmp_path: Path) -> None:
    paths, enc, hpath, _, _ = _run_until_r1_rejection(tmp_path)
    snapshots = list((paths.engagement_dir(enc) / "QA" / ".r1").glob("*.snapshot"))
    assert len(snapshots) == 1
    assert "prescripcion_accionable" in snapshots[0].read_text(encoding="utf-8")

    # Remediación cosmética: cambia la redacción, no la fuerza de la afirmación ni los insumos
    before = compute_finding_substantive_sha256(hpath)
    hpath.write_text(
        hpath.read_text(encoding="utf-8").replace("Se descartó un efecto estacional.", "Se descartó, con cuidado, un efecto estacional."),
        encoding="utf-8",
    )
    assert compute_finding_substantive_sha256(hpath) != before
    qa_env = {**PAID_ENV, "M37_HARNESS_FAMILY": "openai"}
    calls = {"n": 0}

    def _must_not_be_called(**kwargs: Any) -> dict[str, Any]:
        calls["n"] += 1
        return {"choices": [{"message": {"content": "x", "tool_calls": None}}], "usage": {}}

    res = run_kernel_blind_qa(paths, enc, hpath.stem, env=qa_env, completion_fn=_must_not_be_called)
    assert res["ok"] is False and res["ronda"] == 2 and res["fase_a_aprobada"] is False
    assert calls["n"] == 0  # la concesión cosmética se detecta mecánicamente, sin gastar en un auditor

    # Sin el snapshot, el kernel no puede distinguir material de cosmético: falla cerrado
    hpath.write_text(hpath.read_text(encoding="utf-8").replace("con cuidado", "con mucho cuidado"), encoding="utf-8")
    snapshots[0].unlink()
    with pytest.raises(PermissionError, match="exige cambio material"):
        run_kernel_blind_qa(paths, enc, hpath.stem, env=qa_env, completion_fn=_must_not_be_called)
    assert calls["n"] == 0


# ----------------------------------------------------------------------------------------------------------------------
# Tarea 5 — instalador por target
# ----------------------------------------------------------------------------------------------------------------------
def test_t5_workspace_install_writes_only_the_requested_harness(tmp_path: Path) -> None:
    home = tmp_path / "home"
    claude_ws = tmp_path / "solo_claude"
    install_harness_plugin(target="claude", scope="workspace", workspace_dir=claude_ws, home_dir=home)
    assert (claude_ws / ".claude" / "skills" / "m37" / "SKILL.md").is_file() and (claude_ws / ".mcp.json").is_file()
    for absent in ("plugins", ".agents", ".codex", "AGENTS.md"):
        assert not (claude_ws / absent).exists(), absent

    codex_ws = tmp_path / "solo_codex"
    install_harness_plugin(target="codex", scope="workspace", workspace_dir=codex_ws, home_dir=home)
    assert (codex_ws / "AGENTS.md").is_file() and (codex_ws / ".codex" / "config.toml.example").is_file()
    for absent in ("plugins", ".agents", ".claude", ".claude-plugin", ".mcp.json", "CLAUDE.md"):
        assert not (codex_ws / absent).exists(), absent

    agy_ws = tmp_path / "solo_antigravity"
    install_harness_plugin(target="antigravity", scope="workspace", workspace_dir=agy_ws, home_dir=home)
    assert (agy_ws / "plugins" / "m37" / "plugin.json").is_file()
    for absent in (".claude", ".codex", ".mcp.json", "CLAUDE.md", "AGENTS.md"):
        assert not (agy_ws / absent).exists(), absent


# ----------------------------------------------------------------------------------------------------------------------
# Tarea 6 — perímetro de productor_ref, anti-DNS-rebinding, servidor MCP secundario
# ----------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("rigor", ["L1", "L2", "L3"])
def test_t6_producer_script_must_live_inside_the_engagement_analysis_dir(tmp_path: Path, rigor: str) -> None:
    paths = init_workspace(tmp_path / "ws")
    enc_dir = paths.engagement_dir(DEMO)
    informe = enc_dir / "Entregables" / "Informe.md"
    d_path = enc_dir / "Derivaciones" / "D-DEMO-01-01.json"
    deriv = json.loads(d_path.read_text(encoding="utf-8"))
    # Edición en disco: la derivación "produce" desde el entregable, con el hash correcto para que solo falle el perímetro
    deriv["productor_ref"] = f"vault/20_Encargos/{DEMO}/Entregables/Informe.md"
    deriv["productor_sha256"] = sha256_file(informe)
    write_utf8_lf(d_path, json.dumps(deriv, ensure_ascii=False, indent=2) + "\n")

    res = verify_derivation_phase_a(paths, DEMO, d_path, rigor=rigor)  # type: ignore[arg-type]
    assert not res.passed and any("fuera de analisis/" in e for e in res.errors)

    allow = build_blind_qa_allowlist(paths, DEMO, enc_dir / "Hallazgos" / "H-DEMO-01-01.md")
    assert informe.resolve() not in allow
    assert not any("Entregables" in p.parts for p in allow)


def test_t6_web_fetch_connects_to_the_validated_ip_without_resolving_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = init_workspace(tmp_path / "ws", clean_l4=True)
    create_new_engagement(paths, "ENC-W", "CLI_W", rigor="L2")
    guard = ExecutionGuard(
        paths, AgentExecutionContext(engagement_id="ENC-W", client_id="CLI_W", node_id="N-01", profile="documental")
    )
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)

    resolutions: list[str] = []
    answers = iter(["93.184.216.34", "127.0.0.1"])  # 1.ª respuesta pública; un rebinding devolvería loopback después

    def _fake_getaddrinfo(host: str, port: Any, *args: Any, **kwargs: Any) -> list[Any]:
        resolutions.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (next(answers), port))]

    connected: list[tuple[str, int]] = []

    def _fake_create_connection(address: tuple[str, int], *args: Any, **kwargs: Any) -> Any:
        connected.append(address)
        raise OSError("conexión simulada: sin red en el test")

    monkeypatch.setattr(socket, "getaddrinfo", _fake_getaddrinfo)
    monkeypatch.setattr(socket, "create_connection", _fake_create_connection)

    with pytest.raises(OSError):
        guard.web_fetch_clean("http://fuente-publica.example/informe")
    assert resolutions == ["fuente-publica.example"]  # una sola resolución
    assert connected == [("93.184.216.34", 80)]  # el socket va a la IP que se validó

    # Un nombre que no resuelve no se puede validar: se rechaza en vez de dejarlo pasar
    def _unresolvable(*args: Any, **kwargs: Any) -> list[Any]:
        raise socket.gaierror("no existe")

    monkeypatch.setattr(socket, "getaddrinfo", _unresolvable)
    with pytest.raises(PermissionDeniedError, match="no pudo resolver"):
        guard.web_fetch_clean("http://no-existe.example/")


def test_t6_secondary_graph_server_is_pinned_and_robust(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ws = init_workspace(tmp_path / "ws")
    other = init_workspace(tmp_path / "otro", clean_l4=True)
    monkeypatch.delenv("M37_VAULT", raising=False)
    spec = importlib.util.spec_from_file_location(
        "m37_graph_server_under_test", get_core_repo_root() / "extensions" / "mcp_episodic_graph" / "server.py"
    )
    assert spec and spec.loader
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)

    def _call(args: dict[str, Any], vault: str | None) -> dict[str, Any]:
        resp = server.handle_jsonrpc_request(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "fuentes_por_vencer", "arguments": args}},
            vault=vault,
        )
        assert resp is not None
        return resp["result"]

    pinned = str(ws.workspace_root)
    swapped = _call({"vault": str(other.workspace_root)}, pinned)
    assert swapped["isError"] is True and "fijado a un workspace" in swapped["content"][0]["text"]
    assert _call({"vault": pinned}, pinned)["isError"] is False
    assert _call({}, pinned)["isError"] is False

    # El mismo candado aplica cuando el transporte es FastMCP y el workspace viene de M37_VAULT
    monkeypatch.setenv("M37_VAULT", pinned)
    with pytest.raises(PermissionError, match="fijado a un workspace"):
        server.fuentes_por_vencer(vault=str(other.workspace_root))

    assert server.handle_jsonrpc_request({"jsonrpc": "2.0", "id": 9, "method": "ping"}) == {"jsonrpc": "2.0", "id": 9, "result": {}}
    assert server._parse_request(b"no es json")[1]["error"]["code"] == -32700
    assert server._parse_request(b"[1, 2]")[1]["error"]["code"] == -32600

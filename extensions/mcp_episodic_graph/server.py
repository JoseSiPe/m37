"""M37 (Move 37) — SPEC-05 §3.1: Read-Only Episodic & Lineage Graph MCP Server (FastMCP + Native JSON-RPC 2.0 Fallback)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from m37.config import WorkspacePaths, resolve_workspace_paths

_REINDEX_PATH = Path(__file__).parent / "reindex.py"
_spec = importlib.util.spec_from_file_location("m37_reindex_queries", _REINDEX_PATH)
assert _spec and _spec.loader
_queries = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_queries)


def _get_db_path(vault_override: Path | str | None = None) -> Path:
    paths: WorkspacePaths = resolve_workspace_paths(vault_override)
    db_path = paths.duckdb_index_path()
    if not db_path.is_file():
        _queries.reindex_vault_to_duckdb(paths)
    return db_path


def fuentes_por_vencer(dias_horizonte: int = 30, vault: str | None = None) -> list[dict[str, Any]]:
    """Lista fichas de evidencia vencidas, en cuarentena o próximas a vencer en N días."""
    return list(_queries.query_fuentes_por_vencer(_get_db_path(vault), int(dias_horizonte)))


def desempeno_empirico_modelos(vault: str | None = None) -> list[dict[str, Any]]:
    """Devuelve tasas históricas de aprobación QA en Ronda 1, turnos y costo medio por modelo y perfil."""
    return list(_queries.query_desempeno_empirico_modelos(_get_db_path(vault)))


def patrones_rechazo_qa(vault: str | None = None) -> list[dict[str, Any]]:
    """Agrupa defectos históricos emitidos por QA Ciego según categoría y severidad."""
    return list(_queries.query_patrones_rechazo_qa(_get_db_path(vault)))


def cuellos_presupuesto_arquetipo(vault: str | None = None) -> list[dict[str, Any]]:
    """Muestra consumo acumulado de presupuesto USD y bloqueos por arquetipo y perfil de nodo."""
    return list(_queries.query_cuellos_presupuesto_arquetipo(_get_db_path(vault)))


def linaje_artefacto(artefacto_id: str, vault: str | None = None) -> dict[str, Any]:
    """Consulta insumos upstream, dependientes downstream e historial bitemporal de hashes de un artefacto."""
    return dict(_queries.query_linaje_artefacto(_get_db_path(vault), str(artefacto_id)))


MCP_TOOL_CATALOG: list[dict[str, Any]] = [
    {
        "name": "fuentes_por_vencer",
        "description": fuentes_por_vencer.__doc__,
        "inputSchema": {
            "type": "object",
            "properties": {"dias_horizonte": {"type": "integer", "default": 30}},
        },
    },
    {
        "name": "desempeno_empirico_modelos",
        "description": desempeno_empirico_modelos.__doc__,
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "patrones_rechazo_qa",
        "description": patrones_rechazo_qa.__doc__,
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "cuellos_presupuesto_arquetipo",
        "description": cuellos_presupuesto_arquetipo.__doc__,
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "linaje_artefacto",
        "description": linaje_artefacto.__doc__,
        "inputSchema": {
            "type": "object",
            "properties": {"artefacto_id": {"type": "string"}},
            "required": ["artefacto_id"],
        },
    },
]

TOOL_DISPATCH = {
    "fuentes_por_vencer": fuentes_por_vencer,
    "desempeno_empirico_modelos": desempeno_empirico_modelos,
    "patrones_rechazo_qa": patrones_rechazo_qa,
    "cuellos_presupuesto_arquetipo": cuellos_presupuesto_arquetipo,
    "linaje_artefacto": linaje_artefacto,
}


def handle_jsonrpc_request(req: dict[str, Any], *, vault: str | None = None) -> dict[str, Any] | None:
    """Native stdlib MCP JSON-RPC 2.0 handler so the server works even without fastmcp installed."""
    method = req.get("method")
    req_id = req.get("id")
    if req_id is None:
        return None  # JSON-RPC notification

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "m37-episodic-graph", "version": "0.2.0"},
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": MCP_TOOL_CATALOG}}
    if method == "tools/call":
        params = req.get("params") or {}
        name = params.get("name")
        args = dict(params.get("arguments") or {})
        if vault and "vault" not in args:
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
        "error": {"code": -32601, "message": f"Método no soportado: {method!r}"},
    }


def run_stdio_fallback() -> None:
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        resp = handle_jsonrpc_request(json.loads(line))
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()


try:
    from fastmcp import FastMCP  # type: ignore[import-not-found]

    mcp: Any = FastMCP("m37-episodic-graph")
    for _fn in TOOL_DISPATCH.values():
        mcp.tool()(_fn)
except ImportError:
    mcp = None


if __name__ == "__main__":
    if mcp is not None:
        mcp.run()
    else:
        run_stdio_fallback()

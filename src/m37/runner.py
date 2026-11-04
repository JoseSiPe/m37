"""M37 (Move 37) — SPEC-02 & SPEC-05: Stateless Per-Node Micro-Runner (LiteLLM + ExecutionGuard)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

# SPEC-05 §3.2: Zero remote egress/telemetry on import (force local cost map before importing litellm)
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
os.environ.setdefault("LITELLM_LOG", "ERROR")

import litellm

from m37.config import ModelCatalogEntry, WorkspacePaths
from m37.guards import (
    PROFILE_ALLOWED_TOOLS,
    AgentExecutionContext,
    ExecutionGuard,
    GuardViolationError,
)
from m37.schemas import ContractValidationError

litellm.telemetry = False
litellm.suppress_debug_info = True


ALL_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "allocate_next_id": {
        "type": "function",
        "function": {
            "name": "allocate_next_id",
            "description": "Reserva atómicamente el siguiente ID secuencial canónico para un artefacto (prefijos válidos: E, EI, D, H, V, N).",
            "parameters": {
                "type": "object",
                "properties": {
                    "prefix": {
                        "type": "string",
                        "enum": ["E", "EI", "D", "H", "V", "N"],
                        "description": "Prefijo de artefacto a numerar.",
                    }
                },
                "required": ["prefix"],
            },
        },
    },
    "read_file": {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Lee un archivo autorizado del workspace o del Vault (paginado a máx 25,000 caracteres por llamada).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Ruta relativa al workspace o al Vault."},
                    "offset": {"type": "integer", "default": 0, "description": "Desplazamiento inicial en caracteres."},
                    "limit": {"type": "integer", "default": 25000, "description": "Máximo de caracteres (tope 25000)."},
                },
                "required": ["path"],
            },
        },
    },
    "list_files": {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "Lista archivos visibles respetando la Muralla China de cliente y encargo.",
            "parameters": {
                "type": "object",
                "properties": {"directory": {"type": "string", "default": "vault"}},
            },
        },
    },
    "grep_vault": {
        "type": "function",
        "function": {
            "name": "grep_vault",
            "description": "Busca expresiones regulares dentro del perímetro autorizado del Vault.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "directory": {"type": "string", "default": "vault"},
                },
                "required": ["pattern"],
            },
        },
    },
    "write_note": {
        "type": "function",
        "function": {
            "name": "write_note",
            "description": "Crea o sobrescribe una nota Markdown/JSON validando su contrato Pydantic v2 e inyectando hash_insumos.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    "edit_note": {
        "type": "function",
        "function": {
            "name": "edit_note",
            "description": "Reemplaza exactamente un bloque de texto en una nota existente y revalida su contrato.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
    },
    "write_code_file": {
        "type": "function",
        "function": {
            "name": "write_code_file",
            "description": "Escribe código fuente o tests dentro de codigo/<ID-Encargo>/ escaneando secretos hardcodeados.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    },
    "edit_code_file": {
        "type": "function",
        "function": {
            "name": "edit_code_file",
            "description": "Edita quirúrgicamente un archivo dentro de codigo/<ID-Encargo>/.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string"},
                    "new_text": {"type": "string"},
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
    },
    "run_verification_cmd": {
        "type": "function",
        "function": {
            "name": "run_verification_cmd",
            "description": "Ejecuta pytest/ruff/mypy/unittest en codigo/<ID-Encargo>/ sin API keys y estampa .m37_verify_receipt.json.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cmd": {"type": "string", "description": "Ej: 'pytest -q' o 'unittest discover'"},
                },
                "required": ["cmd"],
            },
        },
    },
    "write_sandbox_script": {
        "type": "function",
        "function": {
            "name": "write_sandbox_script",
            "description": "Escribe un script Python analítico reproducible en analisis/<ID-Encargo>/.",
            "parameters": {
                "type": "object",
                "properties": {
                    "script_rel_path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["script_rel_path", "content"],
            },
        },
    },
    "run_sandbox_script": {
        "type": "function",
        "function": {
            "name": "run_sandbox_script",
            "description": "Ejecuta un script Python con guardarraíl en analisis/<ID-Encargo>/ y bloqueo de escritura sobre vault/.",
            "parameters": {
                "type": "object",
                "properties": {
                    "script_path": {"type": "string"},
                    "args": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["script_path"],
            },
        },
    },
    "web_fetch_clean": {
        "type": "function",
        "function": {
            "name": "web_fetch_clean",
            "description": "Descarga texto público HTTP(S) sin credenciales y lo envuelve en cerca <datos_fuente_no_ejecutables>.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
}


@dataclass
class RunnerOutcome:
    status: LiteralStatus
    final_message: str
    turns_used: int
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    written_files: list[str] = field(default_factory=list)
    guard_violations: list[str] = field(default_factory=list)
    contract_errors: int = 0
    cost_source: str = "catalogo"


LiteralStatus = str


def compute_turn_cost_usd(
    response: Any,
    model_entry: ModelCatalogEntry,
    in_tok: int,
    out_tok: int,
) -> tuple[float, str]:
    """SPEC-02 §3.3 #4: prefer litellm.completion_cost(); fall back to Modelos.yaml prices for unknown/custom models."""
    if not isinstance(response, dict):
        try:
            real_cost = float(litellm.completion_cost(completion_response=response, model=model_entry.litellm_id) or 0.0)
            if real_cost > 0.0:
                return real_cost, "litellm"
        except Exception:  # noqa: BLE001 - modelo ausente del mapa local de costos de LiteLLM
            pass
    catalog_cost = (in_tok / 1_000_000.0) * model_entry.costo_usd_1m_in + (
        out_tok / 1_000_000.0
    ) * model_entry.costo_usd_1m_out
    return catalog_cost, "catalogo"


def get_tool_schemas_for_context(ctx: AgentExecutionContext) -> list[dict[str, Any]]:
    allowed = set(PROFILE_ALLOWED_TOOLS.get(ctx.profile, frozenset()))
    if not ctx.allow_surgical_edit:
        allowed.discard("edit_note")
        allowed.discard("edit_code_file")
    return [ALL_TOOL_SCHEMAS[name] for name in sorted(allowed) if name in ALL_TOOL_SCHEMAS]


def append_episodic_event(episodios_path: Path, event: dict[str, Any]) -> None:
    """SPEC-02 §5 & SPEC-05 §3.1: Append-only Level 0 telemetry line in vault/90_Bitacora/episodios.jsonl."""
    episodios_path.parent.mkdir(parents=True, exist_ok=True)
    now_iso = datetime.now(timezone.utc).isoformat()
    record = {"ts": now_iso, "timestamp_utc": now_iso, **event}
    line = json.dumps(record, ensure_ascii=False) + "\n"
    with episodios_path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(line)


def _require_args(tool_name: str, args: dict[str, Any], required: tuple[str, ...]) -> None:
    missing = [k for k in required if k not in args]
    if missing:
        raise ValueError(
            f"Argumentos faltantes o mal nombrados en {tool_name!r}: faltan {missing} (recibidos: {sorted(args.keys())})."
        )


def _dispatch_guard_tool(guard: ExecutionGuard, tool_name: str, args: dict[str, Any]) -> Any:
    if not isinstance(args, dict):
        raise ValueError(f"Los argumentos de {tool_name!r} deben ser un objeto JSON clave-valor.")
    if tool_name == "allocate_next_id":
        _require_args(tool_name, args, ("prefix",))
        return {"id": guard.allocate_id(str(args["prefix"]))}
    if tool_name == "read_file":
        _require_args(tool_name, args, ("path",))
        return guard.read_file(
            args["path"],
            offset=int(args.get("offset", 0) or 0),
            limit=int(args.get("limit", 25_000) or 25_000),
        )
    if tool_name == "list_files":
        return guard.list_files(args.get("directory", "vault"))
    if tool_name == "grep_vault":
        _require_args(tool_name, args, ("pattern",))
        return guard.grep_vault(args["pattern"], args.get("directory", "vault"))
    if tool_name == "write_note":
        _require_args(tool_name, args, ("path", "content"))
        return {"written": guard.write_note(args["path"], args["content"])}
    if tool_name == "edit_note":
        _require_args(tool_name, args, ("path", "old_text", "new_text"))
        return {"edited": guard.edit_note(args["path"], args["old_text"], args["new_text"])}
    if tool_name == "write_code_file":
        _require_args(tool_name, args, ("path", "content"))
        return {"written_code": guard.write_code_file(args["path"], args["content"])}
    if tool_name == "edit_code_file":
        _require_args(tool_name, args, ("path", "old_text", "new_text"))
        return {"edited_code": guard.edit_code_file(args["path"], args["old_text"], args["new_text"])}
    if tool_name == "run_verification_cmd":
        _require_args(tool_name, args, ("cmd",))
        return guard.run_verification_cmd(args["cmd"])
    if tool_name == "write_sandbox_script":
        _require_args(tool_name, args, ("script_rel_path", "content"))
        return {"written_script": guard.write_sandbox_script(args["script_rel_path"], args["content"])}
    if tool_name == "run_sandbox_script":
        _require_args(tool_name, args, ("script_path",))
        return guard.run_sandbox_script(args["script_path"], args.get("args"))
    if tool_name == "web_fetch_clean":
        _require_args(tool_name, args, ("url",))
        return guard.web_fetch_clean(args["url"])
    raise GuardViolationError(f"Herramienta desconocida o no registrada: {tool_name!r}")


def execute_stateless_node_loop(
    paths: WorkspacePaths,
    ctx: AgentExecutionContext,
    model_entry: ModelCatalogEntry,
    system_prompt: str,
    user_prompt: str,
    *,
    max_turns: int = 14,
    budget_max_usd: float = 1.50,
    completion_fn: Callable[..., Any] | None = None,
) -> RunnerOutcome:
    """SPEC-02 §1 & §4: Execute an ephemeral, zero-memory-carryover multi-turn tool loop for a single node."""
    guard = ExecutionGuard(paths, ctx)
    tools = get_tool_schemas_for_context(ctx)
    call_llm = completion_fn or litellm.completion

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    total_in_tokens = 0
    total_out_tokens = 0
    total_cost_usd = 0.0
    written_files: list[str] = []
    guard_violations: list[str] = []
    contract_errors = 0
    cost_source = "catalogo"

    for turn in range(1, max_turns + 1):
        response = call_llm(
            model=model_entry.litellm_id,
            messages=messages,
            tools=tools if tools else None,
            temperature=model_entry.limites_operativos.temperatura_recomendada,
        )

        # Extract token usage & compute cost via litellm.completion_cost() with catalog fallback
        usage = getattr(response, "usage", None) or (response.get("usage") if isinstance(response, dict) else None)
        in_tok = 0
        out_tok = 0
        if usage:
            in_tok = int(getattr(usage, "prompt_tokens", 0) or (usage.get("prompt_tokens", 0) if isinstance(usage, dict) else 0))
            out_tok = int(
                getattr(usage, "completion_tokens", 0)
                or (usage.get("completion_tokens", 0) if isinstance(usage, dict) else 0)
            )
            total_in_tokens += in_tok
            total_out_tokens += out_tok
        turn_cost, turn_source = compute_turn_cost_usd(response, model_entry, in_tok, out_tok)
        total_cost_usd += turn_cost
        if turn_source == "litellm":
            cost_source = "litellm"

        if total_cost_usd > budget_max_usd:
            return RunnerOutcome(
                status="bloqueado_presupuesto",
                final_message=(
                    f"Ejecución abortada en turno {turn}: costo acumulado (${total_cost_usd:.4f}) "
                    f"excedió presupuesto_max_usd (${budget_max_usd:.4f})."
                ),
                turns_used=turn,
                prompt_tokens=total_in_tokens,
                completion_tokens=total_out_tokens,
                cost_usd=round(total_cost_usd, 6),
                written_files=written_files,
                guard_violations=guard_violations,
                contract_errors=contract_errors,
                cost_source=cost_source,
            )

        choice = response.choices[0] if hasattr(response, "choices") else response["choices"][0]
        msg = choice.message if hasattr(choice, "message") else choice["message"]
        content = getattr(msg, "content", None) if not isinstance(msg, dict) else msg.get("content")
        tool_calls = getattr(msg, "tool_calls", None) if not isinstance(msg, dict) else msg.get("tool_calls")

        if not tool_calls:
            return RunnerOutcome(
                status="completado",
                final_message=str(content or ""),
                turns_used=turn,
                prompt_tokens=total_in_tokens,
                completion_tokens=total_out_tokens,
                cost_usd=round(total_cost_usd, 6),
                written_files=written_files,
                guard_violations=guard_violations,
                contract_errors=contract_errors,
                cost_source=cost_source,
            )

        serialized_tcs: list[dict[str, Any]] = []
        for tc in tool_calls:
            tc_id = getattr(tc, "id", None) or tc["id"]
            fn = getattr(tc, "function", None) or tc["function"]
            fn_name = getattr(fn, "name", None) or fn["name"]
            fn_args_raw = getattr(fn, "arguments", "{}") if not isinstance(fn, dict) else fn.get("arguments", "{}")
            serialized_tcs.append(
                {
                    "id": tc_id,
                    "type": "function",
                    "function": {"name": fn_name, "arguments": fn_args_raw},
                }
            )

        messages.append({"role": "assistant", "content": content or "", "tool_calls": serialized_tcs})

        for tc_dict in serialized_tcs:
            fn_name = tc_dict["function"]["name"]
            raw_args = tc_dict["function"]["arguments"]
            try:
                parsed_args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                result_obj = _dispatch_guard_tool(guard, fn_name, parsed_args)
                if isinstance(result_obj, dict):
                    for k in ("written", "edited", "written_code", "edited_code", "written_script"):
                        if k in result_obj and result_obj[k] not in written_files:
                            written_files.append(str(result_obj[k]))
                tool_output = (
                    result_obj if isinstance(result_obj, str) else json.dumps(result_obj, ensure_ascii=False)
                )
            except (
                GuardViolationError,
                ContractValidationError,
                FileNotFoundError,
                ValueError,
                KeyError,
                TypeError,
            ) as exc:
                err_msg = f"ERROR_GUARDIA ({type(exc).__name__}): {exc}"
                if isinstance(exc, (GuardViolationError, ValueError, KeyError, TypeError)):
                    guard_violations.append(err_msg)
                if isinstance(exc, ContractValidationError):
                    contract_errors += 1
                tool_output = err_msg
            except Exception as exc:  # noqa: BLE001 - P8: never crash node loop on unexpected tool error
                tool_output = f"ERROR_HERRAMIENTA ({type(exc).__name__}): {exc}"

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc_dict["id"],
                    "name": fn_name,
                    "content": tool_output,
                }
            )

    return RunnerOutcome(
        status="max_turns_alcanzado",
        final_message=f"Se alcanzó el límite de turnos (max_turns={max_turns}) sin cierre explícito.",
        turns_used=max_turns,
        prompt_tokens=total_in_tokens,
        completion_tokens=total_out_tokens,
        cost_usd=round(total_cost_usd, 6),
        written_files=written_files,
        guard_violations=guard_violations,
        contract_errors=contract_errors,
        cost_source=cost_source,
    )

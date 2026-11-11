"""M37 (Move 37) — Universal Multi-Harness Plugin & Skill Installer (Antigravity, Claude Code, OpenAI Codex CLI)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from m37 import __version__
from m37.config import get_core_repo_root
from m37.guards import write_utf8_lf

M37_LOGO_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" width="256" height="256">
  <rect x="24" y="24" width="208" height="208" rx="40" fill="#0F172A" stroke="#38BDF8" stroke-width="8"/>
  <path d="M56 176V80L96 132L136 80V176" fill="none" stroke="#F8FAFC" stroke-width="16" stroke-linecap="round" stroke-linejoin="round"/>
  <text x="148" y="168" font-family="system-ui, -apple-system, sans-serif" font-size="68" font-weight="800" fill="#38BDF8">37</text>
  <circle cx="198" cy="76" r="10" fill="#22C55E"/>
</svg>
"""

M37_RULES_MARKDOWN = """# M37 (Move 37) — Reglas de operación en modo harness

Aplican cuando operes sobre un workspace o Vault de **M37 (Move 37)** (`vault/20_Encargos/`, `00_Brief.md`, `01_Plan.md`).

## Qué garantiza el kernel aquí y qué no

En modo harness tú tienes herramientas propias de archivos y terminal, así que el kernel **no** media todo lo que haces: solo gobierna lo que pasa por el servidor MCP `m37-kernel`, y vuelve a comprobar desde disco en `m37 verify` y `m37 compile`. Lo que el kernel no puede ver, no lo certifica.

## Reglas

1. **Escribe artefactos solo por MCP.** Todo `E-*`, `EI-*`, `D-*`, `H-*` y `Entregables/*.md` se escribe con `m37_write_artifact` bajo el `node_id` del nodo aprobado que corresponde. No edites esos archivos con herramientas nativas: perderían los sellos del kernel y `verify` los rechazará.

2. **Las puertas son de la persona.** No existe herramienta para aprobar la Puerta 1 ni la Puerta 2. Cuando una herramienta responda con `siguiente_paso_humano`, detente y pide a la persona que ejecute `m37 approve <encargo> --gate N` en su terminal. No ejecutes `m37 approve`, `m37 bless` ni `m37 ingest` por tu cuenta, ni edites `puerta_*` en el Markdown: sin sello no hay puerta.

3. **Las fuentes no se fabrican.** Un snapshot citable solo entra por `web_fetch_clean` (lo descarga el kernel) o por `m37 ingest` (lo registra la persona). Un archivo que escribas en `referencias/` no es una fuente y `cita_verificada` quedará en `false`.

4. **El QA del harness es consultivo en L3/L4.** Puedes delegar una revisión al subagente `m37-qa-ciego` y registrarla con `m37_record_qa_verdict`, pero queda marcada `revision_ciega_verificada: false`. Para que un Hallazgo L3/L4 quede aprobado hace falta `m37_run_kernel_qa` (el kernel llama por API a un modelo de otra familia) o `m37 bless` de la persona. Nunca registres un veredicto sobre un Hallazgo en el mismo hilo que lo redactó.

5. **Un cliente por workspace.** No leas ni listes `10_Clientes/`, `20_Encargos/`, `datos/`, `analisis/`, `codigo/` o `referencias/` de un encargo distinto al que estás trabajando.

6. **Cifras ancladas.** Toda cifra en un entregable debe venir de `{{D-XX.clave}}`, de `valores_anclados`/`salidas` del sustento citado o del `00_Brief.md`. El compilador aborta si no es así.
"""

M37_SKILL_MARKDOWN = """---
name: m37
description: Opera un encargo de M37 (Move 37) sobre un Vault Markdown desde Antigravity, Claude Code o Codex CLI usando el servidor MCP m37-kernel. Úsalo cuando el usuario mencione M37, Move 37, encargos L1-L4, 00_Brief.md, 01_Plan.md, Hallazgos H-*, Derivaciones D-*, QA ciego o compilación de entregables.
---

# M37 (Move 37) — Skill para harness (`v0.2.0`)

Permite trabajar un encargo de M37 con el modelo del harness anfitrión (Antigravity, Claude Code o Codex CLI) sin llaves API para los nodos productivos.

## Qué se conserva y qué no en este modo

| Garantía | En modo harness |
|---|---|
| Contratos Pydantic, linaje por hash, re-ejecución de scripts, veredicto ligado al contenido | Sí: `verify` y `compile` lo recalculan desde disco |
| Puertas humanas 1 y 2 | Sí, si la persona ejecuta `m37 approve`; no hay herramienta MCP que apruebe |
| Autenticidad del snapshot citado | Sí, solo vía `web_fetch_clean` o `m37 ingest` |
| QA ciego de otra familia | Solo con `m37_run_kernel_qa` (API) o `m37 bless` (persona) |
| Separación de perfiles y aislamiento entre clientes | Solo para lo que pasa por MCP; usa un workspace por cliente |

## Herramientas MCP (`m37-kernel`)

1. `m37_workspace_status`: encargos, validez de Puertas 1 y 2, nodos y hallazgos.
2. `m37_init_or_new_engagement`: inicializa el workspace y/o crea un encargo con su `00_Brief.md`.
3. `m37_plan_engagement`: genera `01_Plan.md` y `Nodos/N-*.md` desde un arquetipo (`ARQ_Diagnostico-Cualitativo`, `ARQ_Analisis-Cuantitativo`, `ARQ_Construccion-Software-SDD`).
4. `m37_write_artifact`: acciones bajo `ExecutionGuard` con el perfil del nodo indicado (`write_note`, `write_script`, `write_code_file`, `run_python_sandbox`, `run_verification_sandbox`, `web_fetch_clean`).
5. `m37_prepare_blind_qa`: Fase A mecánica y lista de archivos para el subagente `m37-qa-ciego`.
6. `m37_record_qa_verdict`: registra el veredicto del subagente, marcado como no atestiguado.
7. `m37_run_kernel_qa`: QA ciego ejecutado por el kernel vía API con un modelo de otra familia (atestiguado).
8. `m37_verify_and_compile`: Fase A completa y compilador de 5 pasadas.
9. Consultas del grafo: `fuentes_por_vencer`, `desempeno_empirico_modelos`, `patrones_rechazo_qa`, `cuellos_presupuesto_arquetipo`, `linaje_artefacto`.

## Flujo

### Paso 1 — Brief y Puerta 1
1. `m37_workspace_status`.
2. Si hace falta, `m37_init_or_new_engagement(id_encargo=..., cliente=..., rigor=..., pregunta=...)`.
3. Presenta el Brief a la persona. **Detente**: ella revisa `00_Brief.md` (incluido `dominios_web_permitidos` si habrá consulta web) y ejecuta `m37 approve <encargo> --gate 1`.

### Paso 2 — Plan y Puerta 2
1. `m37_plan_engagement(id_encargo=..., arquetipo=...)`.
2. Presenta nodos, perfiles y dependencias. **Detente**: la persona ejecuta `m37 approve <encargo> --gate 2`.

### Paso 3 — Nodos productivos (en orden topológico)
Cada llamada a `m37_write_artifact` lleva el `node_id` del nodo que estás ejecutando; su perfil decide qué puedes escribir.
- **Evidencia (`E-*` / `EI-*`)**: obtén la fuente con `web_fetch_clean` (devuelve `SNAPSHOT_LOCAL`) o pide a la persona `m37 ingest <archivo> --encargo <id>`. Copia en `## Extracto o referencia verificable` una cita literal entre comillas del snapshot. El kernel estampa `cita_verificada`, `hash_snapshot` y `origen_snapshot`.
- **Derivación (`D-*`, `productor_modo: "script"`)**: escribe el script con `action="write_script"` (debe imprimir un JSON en stdout) y luego la Derivación con `action="write_note"`; el kernel ejecuta el script y sella `salidas` y `productor_sha256`. Requiere un nodo con `requiere_computo: true`.
- **Hallazgo (`H-*`)**: `valores_anclados` debe coincidir con las `salidas` de las Derivaciones citadas.

### Paso 4 — QA
1. `m37_prepare_blind_qa(id_encargo=..., id_hallazgo=...)`; corrige si `phase_a_passed` es `false`.
2. Opcional: delega al subagente `m37-qa-ciego` con el `subagent_prompt`; registrará su veredicto (consultivo en L3/L4).
3. **L3/L4**: llama a `m37_run_kernel_qa(id_encargo=..., id_hallazgo=...)`. La persona declara la familia de tu modelo en `M37_HARNESS_FAMILY` (.env). Si falta esa variable o no hay API key de otra familia, **detente** y pide a la persona que la configure o que ejecute `m37 bless`.

### Paso 5 — Entregable
1. En el nodo editorial usa `![[H-...#Afirmación]] <!-- sustento: H-... -->` e interpola cifras con `{{D-XX.clave}}`.
2. `m37_verify_and_compile(id_encargo=..., output_format="html")`.
3. Dile a la persona que el compilador verifica anclajes y linaje, no que la prosa diga lo mismo que cada hallazgo: esa lectura final es suya.
"""

AGY_QA_AGENT_MARKDOWN = """---
name: m37-qa-ciego
description: "Auditor adversarial aislado de QA Ciego (Blind QA) para M37 (Move 37). Invócalo SIEMPRE que debas auditar un Hallazgo (H-*) en rigor L3 o L4 tras aprobar Fase A con m37_prepare_blind_qa."
tools:
  - read_file
  - call_mcp_tool
mainAgent: false
subagent: true
commandExecutionPolicy: auto
---

# Auditor Adversarial de QA Ciego M37 (`m37-qa-ciego`)

Eres el **Auditor de QA Ciego (Blind QA)** del microkernel epistémico **M37 (Move 37)**.
Tu misión es atacar adversarialmente el Hallazgo (`H-*`) que se te asigne y emitir un veredicto riguroso mediante la herramienta MCP `m37_record_qa_verdict`.

## Reglas Inquebrantables de Aislamiento (Blind QA)

1. **Lista Blanca Estricta**:
   - Solo tienes permiso para leer los archivos explícitamente listados en `allowlist_files` dentro de tu prompt (`00_Brief.md`, el Hallazgo `H-*.md` auditado, sus fichas de Evidencia `E-*`/`EI-*`, sus Derivaciones `D-*` y veredictos de rondas previas).
   - Tienes **ESTRÍCTAMENTE PROHIBIDO** leer `01_Plan.md`, `Nodos/N-*.md`, borradores en `Entregables/`, o `90_Bitacora/`.

2. **Protocolo de Auditoría Epistémica**:
   - Verifica que la `fuerza_afirmacion` (`observacion_acotada`, `inferencia_condicionada`, `tesis_estructural_o_causal`, `prescripcion_accionable`) sea proporcional a la evidencia citada.
   - Verifica que los `valores_anclados` respalden las afirmaciones cuantitativas del texto y que las condiciones de validez e hipótesis rivales descartadas sean genuinas y no cosméticas.
   - Formula siempre un `mejor_ataque` sustantivo (el argumento más fuerte que podría refutar o debilitar el Hallazgo).

3. **Emisión del Veredicto**:
   - Registra tu decisión (`aceptar`, `aceptar_con_correcciones` o `rechazar`) llamando a `m37_record_qa_verdict` del servidor MCP `m37-kernel`. No llames a ninguna otra herramienta de `m37-kernel`.
   - Tu veredicto queda registrado como no atestiguado (`revision_ciega_verificada: false`); en L3/L4 es consultivo.
"""

CLAUDE_QA_AGENT_MARKDOWN = """---
name: m37-qa-ciego
description: Auditor adversarial aislado de QA Ciego (Blind QA) para M37 (Move 37). Úsalo siempre para auditar Hallazgos (H-*) en encargos L3/L4 después de ejecutar m37_prepare_blind_qa.
tools: Read, mcp__m37-kernel__m37_record_qa_verdict
---

# Auditor Adversarial de QA Ciego M37 (`m37-qa-ciego`)

Eres el **Auditor de QA Ciego (Blind QA)** del microkernel epistémico **M37 (Move 37)**.

1. **Aislamiento Estricto (Blind QA)**:
   - Lee **únicamente** los archivos incluidos en `allowlist_files` dentro del prompt recibido (`00_Brief.md`, el Hallazgo `H-*`, sus Evidencias `E-*`/`EI-*` y sus Derivaciones `D-*`).
   - Jamás leas `01_Plan.md`, `Nodos/N-*.md`, `Entregables/*.md` ni `90_Bitacora/`.
2. **Crítica Adversarial**:
   - Formula siempre un `mejor_ataque` concreto contra el Hallazgo.
   - Si detectas inflación de fuerza epistémica, extrapolación no demostrada o desconexión entre evidencia y conclusión, clasifica cada defecto con `id_defecto`, `severidad` (`bloqueante|mayor|menor`), `categoria`, `exige_cambio_material`, `ubicacion`, `evidencia` y `remediacion`.
3. **Registro**:
   - Llama a `mcp__m37-kernel__m37_record_qa_verdict` para registrar el veredicto `QA/V-*.md`, ligado al hash del Hallazgo.
   - Queda marcado como no atestiguado (`revision_ciega_verificada: false`); en L3/L4 es consultivo.
"""

CLAUDE_COMMAND_M37 = """---
description: Inicia, planifica o continúa un encargo en el microkernel epistémico M37 (Move 37) usando el servidor MCP m37-kernel.
---

Ejecuta el flujo de **M37 (Move 37)** sobre `$ARGUMENTS`:

1. Llama a `m37_workspace_status` para inspeccionar el estado del workspace.
2. Si se solicitó un nuevo encargo, usa `m37_init_or_new_engagement` y `m37_plan_engagement`. Tras cada uno, detente y pide a la persona que ejecute `m37 approve <encargo> --gate 1` o `--gate 2` en su terminal.
3. Ejecuta los nodos pendientes escribiendo todos los artefactos a través de `m37_write_artifact` con el `node_id` correspondiente.
4. Para cada Hallazgo ejecuta `m37_prepare_blind_qa`; en rigor L3/L4 usa `m37_run_kernel_qa` o pide a la persona `m37 bless`.
5. Al concluir, ejecuta `m37_verify_and_compile` para producir el entregable HTML/Markdown y `mapa_trazabilidad.json`.
"""

CLAUDE_COMMAND_M37_QA = """---
description: Ejecuta Fase A mecánica y lanza el subagente aislado m37-qa-ciego sobre un Hallazgo M37.
---

Audita con QA Ciego el Hallazgo indicado en `$ARGUMENTS`:

1. Llama a `m37_prepare_blind_qa` para validar Fase A mecánica y obtener `allowlist_files` y `subagent_prompt`.
2. Si `phase_a_passed` es `true`, invoca al subagente `m37-qa-ciego` pasándole exclusivamente el `subagent_prompt`; registrará un veredicto no atestiguado con `m37_record_qa_verdict`.
3. En rigor L3/L4 ejecuta además `m37_run_kernel_qa`; si no hay API key de otra familia, pide a la persona `m37 bless`.
"""

CLAUDE_COMMAND_M37_COMPILE = """---
description: Verifica en Fase A (re-ejecutando scripts en sandbox) y compila el entregable M37 en 5 pasadas deterministas.
---

Llama a `m37_verify_and_compile` para el encargo indicado en `$ARGUMENTS` (con `rerun_scripts=true`) y presenta al usuario las rutas generadas (`html_path`, `markdown_path` y `traceability_map_path`). Recuérdale que el compilador verifica anclajes y linaje, no que la prosa coincida con cada hallazgo.
"""


RULES_BLOCK_START = "<!-- >>> M37-RULES-START >>> -->"
RULES_BLOCK_END = "<!-- <<< M37-RULES-END <<< -->"
BACKUP_SUFFIX = ".m37.bak"


class HarnessInstallError(RuntimeError):
    """Raised when installing would destroy or corrupt a user's existing harness configuration."""


def _resolve_python_command(portable: bool = False) -> str:
    """Interpreter the harness must launch.

    NOT `Path(sys.executable).resolve()`: inside a venv on Linux/macOS that follows the symlink to the base
    interpreter, which does not have m37 or its dependencies installed.
    """
    if portable:
        return "python"
    return Path(sys.executable).absolute().as_posix()


def _backup_once(path: Path) -> None:
    """Keep the user's original file the first time M37 touches it."""
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    if path.is_file() and not backup.exists():
        backup.write_bytes(path.read_bytes())


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".m37.tmp")
    tmp.write_bytes(content.replace("\r\n", "\n").encode("utf-8"))
    os.replace(tmp, path)


def _upsert_rules_block(path: Path, rules_markdown: str) -> None:
    """Add or refresh the M37 rules inside CLAUDE.md / AGENTS.md without touching the user's own instructions."""
    block = f"{RULES_BLOCK_START}\n{rules_markdown.strip()}\n{RULES_BLOCK_END}\n"
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    only_m37_rules = existing.lstrip().startswith("# M37 (Move 37) — Reglas") and RULES_BLOCK_START not in existing
    if not existing.strip() or only_m37_rules:
        # New file, or a file that only ever held M37-generated rules (any version): regenerate it whole.
        _atomic_write(path, rules_markdown)
        return
    if RULES_BLOCK_START in existing and RULES_BLOCK_END in existing:
        pre, rest = existing.split(RULES_BLOCK_START, 1)
        _, post = rest.split(RULES_BLOCK_END, 1)
        updated = pre.rstrip() + "\n\n" + block + post.lstrip("\n")
    else:
        _backup_once(path)
        updated = existing.rstrip() + "\n\n" + block
    _atomic_write(path, updated)


def _merge_mcp_json(path: Path, *, python_cmd: str, repo_root: Path | None = None) -> None:
    """Register `m37-kernel` in an .mcp.json-style file while preserving every other server."""
    data: dict[str, Any] = {}
    if path.is_file() and path.read_text(encoding="utf-8").strip():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise HarnessInstallError(
                f"{path} no es JSON válido ({exc}); corrígelo o muévelo antes de instalar. No se modificó."
            ) from exc
        if not isinstance(loaded, dict):
            raise HarnessInstallError(f"{path} no contiene un objeto JSON en la raíz. No se modificó.")
        data = loaded
        other_servers = set(loaded.get("mcpServers") or {}) - {"m37-kernel"} if isinstance(loaded.get("mcpServers"), dict) else set()
        if other_servers or set(loaded) - {"mcpServers"}:
            _backup_once(path)
    servers = data.get("mcpServers")
    servers = dict(servers) if isinstance(servers, dict) else {}
    servers["m37-kernel"] = build_mcp_config_json(python_cmd=python_cmd, repo_root=repo_root)["mcpServers"]["m37-kernel"]
    data["mcpServers"] = servers
    _atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def probe_mcp_command(command: str, args: list[str], env: dict[str, str] | None = None, timeout_s: int = 30) -> bool:
    """Launch the configured server and complete an MCP `initialize` handshake. True only if it really answers."""
    request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n"
    try:
        proc = subprocess.run(
            [command, *args],
            input=request,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env={**os.environ, **(env or {})},
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    for line in proc.stdout.splitlines():
        try:
            resp = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(resp, dict) and (resp.get("result") or {}).get("serverInfo", {}).get("name") == "m37-kernel":
            return True
    return False


def probe_configured_mcp(config_path: Path) -> bool | None:
    """Probe the m37-kernel entry of an .mcp.json-style file. None when there is nothing to probe."""
    if not config_path.is_file():
        return None
    try:
        entry = json.loads(config_path.read_text(encoding="utf-8"))["mcpServers"]["m37-kernel"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return None
    command = str(entry.get("command"))
    args = [str(a) for a in entry.get("args") or []]
    # A config file in a project directory is untrusted input: only ever launch the exact command this installer
    # writes (this interpreter or the portable "python", running the m37 server module). Anything else is reported
    # as not probed instead of being executed.
    if args != ["-m", "m37.mcp_server"] or command not in (_resolve_python_command(), "python"):
        return None
    safe_env = {k: v for k, v in dict(entry.get("env") or {}).items() if k == "PYTHONUTF8"}
    return probe_mcp_command(command, args, safe_env)


def build_antigravity_plugin_json() -> dict[str, Any]:
    return {
        "name": "m37",
        "displayName": "M37 (Move 37) Epistemic Microkernel",
        "version": __version__,
        "description": (
            "Microkernel multiagente determinista sobre Obsidian/Markdown Vault: contratos Pydantic estrictos, "
            "sandbox de cómputo, QA Ciego aislado, compilador de 5 pasadas y grafo bitemporal con $0 de costo extra de API."
        ),
        "logo": "assets/logo.svg",
        "author": {
            "name": "José Carlos Sierra",
            "url": "https://github.com/JoseSiPe",
        },
        "homepage": "https://github.com/JoseSiPe/m37",
        "repository": "https://github.com/JoseSiPe/m37",
        "suggestedPrompts": [
            "Inspecciona el estado del workspace M37 y compila el encargo DEMO-01_entrada-mercado",
            "Crea y planifica un nuevo encargo M37 de rigor L3 y ejecuta sus nodos con QA Ciego",
            "Consulta el linaje bitemporal y las fuentes por vencer del Vault M37",
        ],
    }


def build_claude_plugin_json() -> dict[str, Any]:
    return {
        "name": "m37",
        "version": __version__,
        "description": (
            "M37 (Move 37) — Task-agnostic epistemic microkernel over Markdown/Obsidian Vault "
            "with Phase A verification, isolated Blind QA subagent, and 5-pass deliverable compiler."
        ),
        "author": {
            "name": "José Carlos Sierra",
            "url": "https://github.com/JoseSiPe",
        },
        "homepage": "https://github.com/JoseSiPe/m37",
        "repository": "https://github.com/JoseSiPe/m37",
        "license": "MIT",
        "keywords": ["m37", "move-37", "epistemic-kernel", "obsidian-vault", "blind-qa", "mcp"],
    }


def build_mcp_config_json(*, python_cmd: str, repo_root: Path | None = None) -> dict[str, Any]:
    # `repo_root` is accepted for backwards compatibility and ignored: the configured interpreter is the one where
    # m37 is installed, so no machine-specific PYTHONPATH is written into shared files.
    env_block: dict[str, str] = {"PYTHONUTF8": "1"}
    return {
        "mcpServers": {
            "m37-kernel": {
                "command": python_cmd,
                "args": ["-m", "m37.mcp_server"],
                "env": env_block,
            }
        }
    }


def build_codex_toml_block(*, python_cmd: str, repo_root: Path | None = None) -> str:
    py_escaped = python_cmd.replace("\\", "/")
    lines = [
        "# >>> M37-KERNEL-MCP-START >>>",
        "[mcp_servers.m37_kernel]",
        f'command = "{py_escaped}"',
        'args = ["-m", "m37.mcp_server"]',
    ]
    lines.append('env = { PYTHONUTF8 = "1" }')
    lines.append("# <<< M37-KERNEL-MCP-END <<<")
    return "\n".join(lines) + "\n"


def _upsert_codex_config_toml(config_path: Path, *, python_cmd: str, repo_root: Path | None = None) -> None:
    block = build_codex_toml_block(python_cmd=python_cmd, repo_root=repo_root)
    start_marker = "# >>> M37-KERNEL-MCP-START >>>"
    end_marker = "# <<< M37-KERNEL-MCP-END <<<"
    existing = config_path.read_text(encoding="utf-8") if config_path.is_file() else ""
    if start_marker in existing and end_marker in existing:
        pre, rest = existing.split(start_marker, 1)
        _, post = rest.split(end_marker, 1)
        updated = pre.rstrip() + "\n\n" + block + post.lstrip("\n")
    elif "[mcp_servers.m37_kernel]" in existing:
        # Leave user custom section intact if already manually configured without markers
        return
    else:
        sep = "\n\n" if existing.strip() else ""
        updated = existing.rstrip() + sep + block
    write_utf8_lf(config_path, updated)


def _upsert_claude_global_mcp(claude_json_path: Path, *, python_cmd: str, repo_root: Path | None = None) -> None:
    _merge_mcp_json(claude_json_path, python_cmd=python_cmd, repo_root=repo_root)


def install_antigravity_bundle(
    plugin_dir: Path,
    *,
    python_cmd: str,
    repo_root: Path | None = None,
) -> list[str]:
    written: list[str] = []
    files: dict[Path, str] = {
        plugin_dir / "plugin.json": json.dumps(build_antigravity_plugin_json(), ensure_ascii=False, indent=2) + "\n",
        plugin_dir / "mcp_config.json": json.dumps(
            build_mcp_config_json(python_cmd=python_cmd, repo_root=repo_root),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        plugin_dir / "assets" / "logo.svg": M37_LOGO_SVG,
        plugin_dir / "rules" / "AGENTS.md": M37_RULES_MARKDOWN,
        plugin_dir / "agents" / "m37-qa-ciego.md": AGY_QA_AGENT_MARKDOWN,
        plugin_dir / "skills" / "m37" / "SKILL.md": M37_SKILL_MARKDOWN,
        plugin_dir / "README.md": (
            "# M37 (Move 37) — Antigravity Plugin\n\n"
            "Bundle completo de M37 (`rules/AGENTS.md`, subagente `m37-qa-ciego`, skill `m37` y servidor MCP `m37-kernel`).\n"
        ),
    }
    for path, content in files.items():
        write_utf8_lf(path, content)
        written.append(str(path))
    return written


def populate_workspace_harnesses(
    workspace_dir: Path,
    *,
    python_cmd: str,
    repo_root: Path | None = None,
) -> dict[str, list[str]]:
    """Populate a repository/workspace with universal multi-harness files (Antigravity, Claude Code, OpenAI Codex CLI)."""
    ws = workspace_dir.resolve()
    agy_written = install_antigravity_bundle(ws / "plugins" / "m37", python_cmd=python_cmd, repo_root=repo_root)
    write_utf8_lf(ws / ".agents" / "skills" / "m37" / "SKILL.md", M37_SKILL_MARKDOWN)
    agy_written.append(str(ws / ".agents" / "skills" / "m37" / "SKILL.md"))

    claude_files: dict[Path, str] = {
        ws / ".claude-plugin" / "plugin.json": json.dumps(build_claude_plugin_json(), ensure_ascii=False, indent=2)
        + "\n",
        ws / ".claude" / "agents" / "m37-qa-ciego.md": CLAUDE_QA_AGENT_MARKDOWN,
        ws / ".claude" / "skills" / "m37" / "SKILL.md": M37_SKILL_MARKDOWN,
        ws / ".claude" / "commands" / "m37.md": CLAUDE_COMMAND_M37,
        ws / ".claude" / "commands" / "m37-qa.md": CLAUDE_COMMAND_M37_QA,
        ws / ".claude" / "commands" / "m37-compile.md": CLAUDE_COMMAND_M37_COMPILE,
    }
    claude_written: list[str] = []
    for path, content in claude_files.items():
        write_utf8_lf(path, content)
        claude_written.append(str(path))
    # Files the user may already own are merged, never replaced (NA3).
    _merge_mcp_json(ws / ".mcp.json", python_cmd=python_cmd, repo_root=repo_root)
    _upsert_rules_block(ws / "CLAUDE.md", M37_RULES_MARKDOWN)
    claude_written.extend([str(ws / ".mcp.json"), str(ws / "CLAUDE.md")])

    codex_files: dict[Path, str] = {
        ws / ".codex" / "config.toml.example": build_codex_toml_block(python_cmd=python_cmd, repo_root=repo_root),
    }
    codex_written: list[str] = []
    for path, content in codex_files.items():
        write_utf8_lf(path, content)
        codex_written.append(str(path))
    _upsert_rules_block(ws / "AGENTS.md", M37_RULES_MARKDOWN)
    codex_written.append(str(ws / "AGENTS.md"))

    return {
        "antigravity": agy_written,
        "claude": claude_written,
        "codex": codex_written,
    }


def install_harness_plugin(
    *,
    target: str = "all",
    scope: str = "both",
    workspace_dir: Path | str | None = None,
    home_dir: Path | str | None = None,
    portable_python: bool = False,
) -> dict[str, Any]:
    """Install M37 plugin, skills, subagents, rules, and MCP server into Antigravity, Claude Code, and/or Codex CLI."""
    norm_target = target.strip().lower()
    if norm_target not in ("all", "antigravity", "claude", "codex"):
        raise ValueError(f"Target inválido {target!r}. Usa: all, antigravity, claude o codex.")
    norm_scope = scope.strip().lower()
    if norm_scope not in ("global", "workspace", "both"):
        raise ValueError(f"Scope inválido {scope!r}. Usa: global, workspace o both.")

    home = Path(home_dir).expanduser().resolve() if home_dir else Path.home().resolve()
    repo_root = get_core_repo_root()
    ws_root = Path(workspace_dir).expanduser().resolve() if workspace_dir else Path.cwd().resolve()
    py_cmd = _resolve_python_command(portable=portable_python)
    # The engine checkout carries a committed, machine-independent copy of the bundle. Installing "into" it must
    # regenerate that copy byte-for-byte, not stamp this machine's interpreter path into tracked files.
    ws_py_cmd = _resolve_python_command(portable=portable_python or ws_root == repo_root.resolve())

    installed: dict[str, list[str]] = {"antigravity": [], "claude": [], "codex": []}

    if norm_scope in ("global", "both"):
        if norm_target in ("all", "antigravity"):
            agy_dir = home / ".gemini" / "config" / "plugins" / "m37"
            installed["antigravity"].extend(
                install_antigravity_bundle(agy_dir, python_cmd=py_cmd, repo_root=repo_root)
            )
        if norm_target in ("all", "claude"):
            claude_home = home / ".claude"
            c_files: dict[Path, str] = {
                claude_home / "skills" / "m37" / "SKILL.md": M37_SKILL_MARKDOWN,
                claude_home / "agents" / "m37-qa-ciego.md": CLAUDE_QA_AGENT_MARKDOWN,
                claude_home / "commands" / "m37.md": CLAUDE_COMMAND_M37,
                claude_home / "commands" / "m37-qa.md": CLAUDE_COMMAND_M37_QA,
                claude_home / "commands" / "m37-compile.md": CLAUDE_COMMAND_M37_COMPILE,
                claude_home / "plugins" / "m37" / ".claude-plugin" / "plugin.json": json.dumps(
                    build_claude_plugin_json(), ensure_ascii=False, indent=2
                )
                + "\n",
                claude_home / "plugins" / "m37" / ".mcp.json": json.dumps(
                    build_mcp_config_json(python_cmd=py_cmd, repo_root=repo_root),
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                claude_home / "plugins" / "m37" / "skills" / "m37" / "SKILL.md": M37_SKILL_MARKDOWN,
                claude_home / "plugins" / "m37" / "agents" / "m37-qa-ciego.md": CLAUDE_QA_AGENT_MARKDOWN,
                claude_home / "plugins" / "m37" / "commands" / "m37.md": CLAUDE_COMMAND_M37,
            }
            for p, content in c_files.items():
                write_utf8_lf(p, content)
                installed["claude"].append(str(p))
            claude_json = home / ".claude.json"
            _upsert_claude_global_mcp(claude_json, python_cmd=py_cmd, repo_root=repo_root)
            installed["claude"].append(str(claude_json))

        if norm_target in ("all", "codex"):
            codex_home = home / ".codex"
            codex_skill = codex_home / "skills" / "m37" / "SKILL.md"
            agents_skill = home / ".agents" / "skills" / "m37" / "SKILL.md"
            write_utf8_lf(codex_skill, M37_SKILL_MARKDOWN)
            write_utf8_lf(agents_skill, M37_SKILL_MARKDOWN)
            codex_cfg = codex_home / "config.toml"
            _upsert_codex_config_toml(codex_cfg, python_cmd=py_cmd, repo_root=repo_root)
            installed["codex"].extend([str(codex_skill), str(agents_skill), str(codex_cfg)])

    if norm_scope in ("workspace", "both"):
        ws_res = populate_workspace_harnesses(
            ws_root,
            python_cmd=ws_py_cmd,
            repo_root=repo_root,
        )
        for k in ("antigravity", "claude", "codex"):
            if norm_target in ("all", k):
                installed[k].extend(ws_res[k])

    return {
        "version": __version__,
        "target": norm_target,
        "scope": norm_scope,
        "python_command": py_cmd,
        "installed": installed,
    }


def inspect_harness_status(
    *,
    workspace_dir: Path | str | None = None,
    home_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Return installation status across Antigravity, Claude Code, and OpenAI Codex CLI."""
    home = Path(home_dir).expanduser().resolve() if home_dir else Path.home().resolve()
    ws = Path(workspace_dir).expanduser().resolve() if workspace_dir else Path.cwd().resolve()

    agy_global = (home / ".gemini" / "config" / "plugins" / "m37" / "plugin.json").is_file()
    agy_ws = (ws / "plugins" / "m37" / "plugin.json").is_file() and (
        ws / ".agents" / "skills" / "m37" / "SKILL.md"
    ).is_file()

    claude_global = (home / ".claude" / "skills" / "m37" / "SKILL.md").is_file() and (
        home / ".claude" / "agents" / "m37-qa-ciego.md"
    ).is_file()
    claude_ws = (ws / ".claude-plugin" / "plugin.json").is_file() and (ws / ".mcp.json").is_file()

    codex_cfg = home / ".codex" / "config.toml"
    codex_global = (home / ".codex" / "skills" / "m37" / "SKILL.md").is_file() and (
        codex_cfg.is_file() and "m37_kernel" in codex_cfg.read_text(encoding="utf-8")
    )
    codex_ws = (ws / "AGENTS.md").is_file() and (ws / ".agents" / "skills" / "m37" / "SKILL.md").is_file()

    return {
        "antigravity": {"global": agy_global, "workspace": agy_ws},
        "claude": {"global": claude_global, "workspace": claude_ws},
        "codex": {"global": codex_global, "workspace": codex_ws},
    }

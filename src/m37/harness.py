"""M37 (Move 37) — Universal Multi-Harness Plugin & Skill Installer (Antigravity, Claude Code, OpenAI Codex CLI)."""

from __future__ import annotations

import json
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

M37_RULES_MARKDOWN = """# M37 (Move 37) — Reglas Epistémicas y de Gobernanza Multi-Harness

Cuando operes sobre un workspace o Vault de **M37 (Move 37)** (`vault/20_Encargos/`, `00_Brief.md`, `01_Plan.md`):

1. **Cero Escritura Cruda sin Guardián (`ExecutionGuard`)**:
   - Todo artefacto de M37 (`00_Brief.md`, `01_Plan.md`, `Nodos/N-*.md`, `Evidencia_Interna/EI-*.md`, `30_Evidencia_Publica/E-*.md`, `Derivaciones/D-*.json`, `Hallazgos/H-*.md`, `QA/V-*.md`, `Entregables/*.md`) DEBE escribirse a través de la herramienta MCP `m37_write_artifact` o `m37_record_qa_verdict` (o validarse con `m37 verify`).
   - Todos los contratos Pydantic v2 aplican `extra="forbid"`: no inventes llaves YAML fuera del esquema oficial.

2. **Ejecución con Costo API Extra \\$0 (Modo Nativo del Harness)**:
   - Usa tu propio razonamiento dentro del harness anfitrión (**Antigravity**, **Claude Code** o **Codex CLI**) en lugar de invocar APIs pagadas externas cuando el usuario opere bajo su suscripción actual.
   - Planifica con `m37_plan_engagement` (0 tokens externos).

3. **Aislamiento Estricto en QA Ciego (`m37-qa-ciego`)**:
   - Nunca auto-apruebes un Hallazgo `L3` o `L4` en el mismo hilo de razonamiento que lo redactó.
   - Ejecuta primero `m37_prepare_blind_qa` (que corre Fase A mecánica y re-ejecuta scripts en el sandbox).
   - Delega la auditoría al subagente aislado `m37-qa-ciego` pasándole **únicamente** los archivos de `allowlist_files`. El subagente tiene prohibido leer `01_Plan.md`, `Nodos/N-*.md`, `Entregables/*.md` o `90_Bitacora/`.

4. **Anclaje Cuantitativo y Techo Epistémico**:
   - Toda cifra cuantitativa (`%`, `USD`, `VAN`, `TIR`, meses) en un entregable debe provenir de `valores_anclados` de un Hallazgo aceptado o del `00_Brief.md`.
   - Toda cita textual (`cita_verificada: true`) con `snapshot_local` debe existir literalmente en `referencias/`.
"""

M37_SKILL_MARKDOWN = """---
name: m37
description: Ejecuta el microkernel epistémico M37 (Move 37) sobre un Obsidian/Markdown Vault con verificación mecánica Fase A, sandbox determinista, QA Ciego aislado y compilador de 5 pasadas en Antigravity, Claude Code o Codex CLI (sin costo extra de API). Úsalo cuando el usuario mencione M37, Move 37, encargos L1-L4, 00_Brief.md, 01_Plan.md, Hallazgos H-*, Derivaciones D-*, QA Ciego o compilación de entregables auditables.
---

# M37 (Move 37) — Universal Multi-Harness Skill (`v0.2.0`)

Este skill permite operar el ciclo completo de **M37 (Move 37)** usando directamente la suscripción del harness anfitrión (**Antigravity**, **Claude Code** o **OpenAI Codex CLI**) con **\\$0 de gasto en APIs externas**, manteniendo el 100% de las garantías criptográficas y epistémicas del microkernel Python.

## Herramientas MCP Disponibles (`m37-kernel`)

Si el servidor MCP `m37-kernel` está activo, dispones de 12 herramientas deterministas:

1. `m37_workspace_status`: Inspecciona encargos, firmas SHA-256 de Puerta 1 y Puerta 2, nodos DAG y hallazgos.
2. `m37_init_or_new_engagement`: Inicializa un workspace M37 (`init_workspace`) y/o crea un encargo (`00_Brief.md`).
3. `m37_plan_engagement`: Compila `01_Plan.md` y `Nodos/N-*.md` desde un Arquetipo declarativo (`ARQ_Diagnostico-Cualitativo`, `ARQ_Modelo-Cuantitativo`, `ARQ_Desarrollo-Software-SDD`) en modo nativo del harness.
4. `m37_write_artifact`: Escribe notas o scripts y ejecuta código en el sandbox restringido a través de `ExecutionGuard` (`write_note`, `write_script`, `run_python_sandbox`, `run_verification_sandbox`, `web_fetch_clean`).
5. `m37_prepare_blind_qa`: Ejecuta Fase A mecánica sobre un Hallazgo (`H-*`) y devuelve la lista blanca (`allowlist_files`) y el prompt aislado para el subagente `m37-qa-ciego`.
6. `m37_record_qa_verdict`: Registra el veredicto (`QA/V-*.md`) ligado al `sha256_hallazgo_auditado`.
7. `m37_verify_and_compile`: Re-ejecuta scripts en sandbox (`Fase A`) y ejecuta el compilador determinista de 5 pasadas (`HTML`, `Markdown`, `mapa_trazabilidad.json`).
8. **Consultas Bitemporales del Grafo (0 tokens LLM)**:
   - `fuentes_por_vencer`
   - `desempeno_empirico_modelos`
   - `patrones_rechazo_qa`
   - `cuellos_presupuesto_arquetipo`
   - `linaje_artefacto`

*(Si en algún entorno prefieres usar la CLI directamente, todos los pasos equivalen a `m37 init`, `m37 new`, `m37 plan --harness`, `m37 verify --rerun`, `m37 compile` y `m37 close`).*

---

## Flujo Operativo Paso a Paso (Costo API Extra: \\$0)

### Paso 1: Inicialización y Puerta 1 (`00_Brief.md`)
1. Llama a `m37_workspace_status` para inspeccionar el workspace actual.
2. Si el usuario pide crear un encargo nuevo, llama a `m37_init_or_new_engagement(id_encargo=..., cliente=..., rigor=..., pregunta=...)`.
3. Presenta el resumen del `00_Brief.md` al usuario para confirmar **Puerta 1** (o pasa `approve_gate_1=True` cuando el usuario lo autorice).

### Paso 2: Planificación DAG y Puerta 2 (`01_Plan.md`)
1. Llama a `m37_plan_engagement(id_encargo=..., arquetipo=..., approve_gate_1=True, approve_gate_2=True)`.
2. El kernel generará `01_Plan.md` y los nodos `Nodos/N-*.md` sellando criptográficamente `puerta_1_sha256` y `puerta_2_sha256`.

### Paso 3: Ejecución Custodiada de Nodos Productivos (`N-01`, `N-02`, ...)
Para cada nodo en orden topológico:
- **Evidencia (`E-*` / `EI-*`)**:
  - Si usa `snapshot_local: "referencias/..."`, guarda primero el archivo en `referencias/` y verifica que el bloque `## Extracto o referencia verificable` contenga una cita literal exacta del snapshot antes de llamar a `m37_write_artifact(..., action="write_note")`.
- **Derivación (`D-*` con `productor_modo: "script"`)**:
  - Primero escribe el script Python reproducible en `analisis/<id_encargo>/<script>.py` usando `m37_write_artifact(..., action="write_script")` (el script debe imprimir un JSON a `stdout` con las métricas).
  - Luego escribe `vault/20_Encargos/<id_encargo>/Derivaciones/D-*.json` con `m37_write_artifact(..., action="write_note")`. El kernel ejecutará automáticamente el script en el sandbox y sellará `salidas` y `productor_sha256`.
- **Hallazgo (`H-*`)**:
  - Escribe `vault/20_Encargos/<id_encargo>/Hallazgos/H-*.md` mediante `m37_write_artifact(..., action="write_note")` asegurándote de que cada valor de `valores_anclados` coincida exactamente con las `salidas` de las Derivaciones citadas.

### Paso 4: Verificación Fase A + QA Ciego Aislado (`m37-qa-ciego`)
Para todo Hallazgo en rigor `L3` o `L4`:
1. Llama a `m37_prepare_blind_qa(id_encargo=..., id_hallazgo=..., ronda=1)`.
2. Si `phase_a_passed` es `False`, corrige los errores mecánicos reportados antes de continuar.
3. Si `phase_a_passed` es `True`, invoca un **subagente aislado** (`invoke_subagent` en Antigravity, subagente `m37-qa-ciego` en Claude Code, o tarea aislada en Codex) pasándole el `subagent_prompt` devuelto por `m37_prepare_blind_qa`.
4. El subagente auditará exclusivamente los archivos en `allowlist_files` y registrará su dictamen con `m37_record_qa_verdict`.

### Paso 5: Nodo Editorial y Compilador Determinista de 5 Pasadas
1. En el nodo editorial (`Informe.md`), usa transclusiones `![[H-...#Afirmación]] <!-- sustento: H-... -->` y asegúrate de que toda cifra en prosa esté anclada en `valores_anclados` o en `00_Brief.md`, sin lenguaje de garantía incondicional (`100% garantizado`, `riesgo cero`, etc.).
2. Llama a `m37_verify_and_compile(id_encargo=..., output_format="html", rerun_scripts=True)` para obtener el HTML ejecutivo autocontenido, el Markdown limpio y `mapa_trazabilidad.json`.
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
   - Verifica que la `fuerza_afirmacion` (`hecho_verificado`, `inferencia_condicionada`, `prescripcion_accionable`, `hipotesis_de_trabajo`) sea proporcional a la evidencia citada.
   - Verifica que los `valores_anclados` respalden las afirmaciones cuantitativas del texto y que las condiciones de validez e hipótesis rivales descartadas sean genuinas y no cosméticas.
   - Formula siempre un `mejor_ataque` sustantivo (el argumento más fuerte que podría refutar o debilitar el Hallazgo).

3. **Emisión del Veredicto**:
   - Registra tu decisión (`aceptar`, `aceptar_con_correcciones` o `rechazar`) llamando a `m37_record_qa_verdict` del servidor MCP `m37-kernel`.
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
3. **Registro Criptográfico**:
   - Llama a `mcp__m37-kernel__m37_record_qa_verdict` para sellar el veredicto `QA/V-*.md` con el `sha256_hallazgo_auditado`.
"""

CLAUDE_COMMAND_M37 = """---
description: Inicia, planifica o continúa un encargo en el microkernel epistémico M37 (Move 37) usando el servidor MCP m37-kernel.
---

Ejecuta el flujo de **M37 (Move 37)** sobre `$ARGUMENTS`:

1. Llama a `m37_workspace_status` para inspeccionar el estado del workspace.
2. Si se solicitó un nuevo encargo, usa `m37_init_or_new_engagement` y `m37_plan_engagement`.
3. Ejecuta los nodos pendientes escribiendo todos los artefactos a través de `m37_write_artifact`.
4. Para cada Hallazgo en rigor L3/L4, ejecuta `m37_prepare_blind_qa` y delega la auditoría al subagente aislado `m37-qa-ciego`.
5. Al concluir, ejecuta `m37_verify_and_compile` para producir el entregable HTML/Markdown y `mapa_trazabilidad.json`.
"""

CLAUDE_COMMAND_M37_QA = """---
description: Ejecuta Fase A mecánica y lanza el subagente aislado m37-qa-ciego sobre un Hallazgo M37.
---

Audita con QA Ciego el Hallazgo indicado en `$ARGUMENTS`:

1. Llama a `m37_prepare_blind_qa` para validar Fase A mecánica y obtener `allowlist_files` y `subagent_prompt`.
2. Si `phase_a_passed` es `true`, invoca al subagente `m37-qa-ciego` pasándole exclusivamente el `subagent_prompt` para que audite a ciegas y registre el veredicto con `m37_record_qa_verdict`.
"""

CLAUDE_COMMAND_M37_COMPILE = """---
description: Verifica en Fase A (re-ejecutando scripts en sandbox) y compila el entregable M37 en 5 pasadas deterministas.
---

Llama a `m37_verify_and_compile` para el encargo indicado en `$ARGUMENTS` (con `rerun_scripts=true`) y presenta al usuario las rutas generadas (`html_path`, `markdown_path` y `traceability_map_path`).
"""


def _resolve_python_command(portable: bool = False) -> str:
    if portable:
        return "python"
    return Path(sys.executable).resolve().as_posix()


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
    env_block: dict[str, str] = {"PYTHONUTF8": "1"}
    if repo_root is not None:
        src_path = (repo_root / "src").resolve().as_posix()
        env_block["PYTHONPATH"] = src_path
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
    if repo_root is not None:
        src_escaped = (repo_root / "src").resolve().as_posix()
        lines.append(f'env = {{ PYTHONUTF8 = "1", PYTHONPATH = "{src_escaped}" }}')
    else:
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
    data: dict[str, Any] = {}
    if claude_json_path.is_file():
        try:
            data = dict(json.loads(claude_json_path.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            return
    mcp_servers = dict(data.get("mcpServers") or {})
    mcp_cfg = build_mcp_config_json(python_cmd=python_cmd, repo_root=repo_root)["mcpServers"]["m37-kernel"]
    mcp_servers["m37-kernel"] = mcp_cfg
    data["mcpServers"] = mcp_servers
    write_utf8_lf(claude_json_path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


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
        ws / ".mcp.json": json.dumps(
            build_mcp_config_json(python_cmd=python_cmd, repo_root=repo_root),
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        ws / "CLAUDE.md": M37_RULES_MARKDOWN,
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

    codex_files: dict[Path, str] = {
        ws / "AGENTS.md": M37_RULES_MARKDOWN,
        ws / ".codex" / "config.toml.example": build_codex_toml_block(python_cmd=python_cmd, repo_root=repo_root),
    }
    codex_written: list[str] = []
    for path, content in codex_files.items():
        write_utf8_lf(path, content)
        codex_written.append(str(path))

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
    ws_root = Path(workspace_dir).expanduser().resolve() if workspace_dir else repo_root
    py_cmd = _resolve_python_command(portable=portable_python)

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
            python_cmd=_resolve_python_command(portable=portable_python),
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
    ws = Path(workspace_dir).expanduser().resolve() if workspace_dir else get_core_repo_root()

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

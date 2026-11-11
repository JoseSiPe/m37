---
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

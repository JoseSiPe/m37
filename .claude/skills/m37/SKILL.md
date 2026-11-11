---
name: m37
description: Ejecuta el microkernel epistémico M37 (Move 37) sobre un Obsidian/Markdown Vault con verificación mecánica Fase A, sandbox determinista, QA Ciego aislado y compilador de 5 pasadas en Antigravity, Claude Code o Codex CLI (sin costo extra de API). Úsalo cuando el usuario mencione M37, Move 37, encargos L1-L4, 00_Brief.md, 01_Plan.md, Hallazgos H-*, Derivaciones D-*, QA Ciego o compilación de entregables auditables.
---

# M37 (Move 37) — Universal Multi-Harness Skill (`v0.2.0`)

Este skill permite operar el ciclo completo de **M37 (Move 37)** usando directamente la suscripción del harness anfitrión (**Antigravity**, **Claude Code** o **OpenAI Codex CLI**) con **\$0 de gasto en APIs externas**, manteniendo el 100% de las garantías criptográficas y epistémicas del microkernel Python.

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

## Flujo Operativo Paso a Paso (Costo API Extra: \$0)

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

# M37 (Move 37) — Reglas Epistémicas y de Gobernanza Multi-Harness

Cuando operes sobre un workspace o Vault de **M37 (Move 37)** (`vault/20_Encargos/`, `00_Brief.md`, `01_Plan.md`):

1. **Cero Escritura Cruda sin Guardián (`ExecutionGuard`)**:
   - Todo artefacto de M37 (`00_Brief.md`, `01_Plan.md`, `Nodos/N-*.md`, `Evidencia_Interna/EI-*.md`, `30_Evidencia_Publica/E-*.md`, `Derivaciones/D-*.json`, `Hallazgos/H-*.md`, `QA/V-*.md`, `Entregables/*.md`) DEBE escribirse a través de la herramienta MCP `m37_write_artifact` o `m37_record_qa_verdict` (o validarse con `m37 verify`).
   - Todos los contratos Pydantic v2 aplican `extra="forbid"`: no inventes llaves YAML fuera del esquema oficial.

2. **Ejecución con Costo API Extra \$0 (Modo Nativo del Harness)**:
   - Usa tu propio razonamiento dentro del harness anfitrión (**Antigravity**, **Claude Code** o **Codex CLI**) en lugar de invocar APIs pagadas externas cuando el usuario opere bajo su suscripción actual.
   - Planifica con `m37_plan_engagement` (0 tokens externos).

3. **Aislamiento Estricto en QA Ciego (`m37-qa-ciego`)**:
   - Nunca auto-apruebes un Hallazgo `L3` o `L4` en el mismo hilo de razonamiento que lo redactó.
   - Ejecuta primero `m37_prepare_blind_qa` (que corre Fase A mecánica y re-ejecuta scripts en el sandbox).
   - Delega la auditoría al subagente aislado `m37-qa-ciego` pasándole **únicamente** los archivos de `allowlist_files`. El subagente tiene prohibido leer `01_Plan.md`, `Nodos/N-*.md`, `Entregables/*.md` o `90_Bitacora/`.

4. **Anclaje Cuantitativo y Techo Epistémico**:
   - Toda cifra cuantitativa (`%`, `USD`, `VAN`, `TIR`, meses) en un entregable debe provenir de `valores_anclados` de un Hallazgo aceptado o del `00_Brief.md`.
   - Toda cita textual (`cita_verificada: true`) con `snapshot_local` debe existir literalmente en `referencias/`.

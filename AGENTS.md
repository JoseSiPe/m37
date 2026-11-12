# M37 (Move 37) — Reglas de operación en modo harness

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

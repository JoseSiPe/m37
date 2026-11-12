# Changelog

## Sin publicar — seis hallazgos de auditoría (2026-11-12)

### Cambios que rompen compatibilidad
- **`m37 close`.** El capital promovido usa IDs anónimos `C-YYYYMM-NNN` (antes `C-<encargo>-NN`). Solo se promueve un
  hallazgo con Fase A vigente y, en L3/L4, veredicto atestiguado; lo demás se lista en `capital_omitido` con su motivo.
- **Compilador.** `{{D-XX.clave}}` solo puede interpolar una Derivación citada en `sustento_derivaciones` de un Hallazgo
  citado en el mismo bloque.
- **Veredictos QA.** `aceptar_con_correcciones` ya no es válido con un defecto bloqueante ni con uno que `persiste`.
- **Derivaciones.** `productor_ref` debe resolver dentro de `analisis/<encargo>/`, en todos los niveles de rigor.
- **`m37 qa` en Ronda 2.** Falla cerrado si la Ronda 1 exigía cambio material y falta `QA/.r1/<V>.snapshot`
  (encargos auditados con una versión anterior de `m37 run`): repetir la Ronda 1 o usar `m37 bless`.
- **`m37 run` en Ronda 2.** Si el hallazgo no cambió, no se llama al segundo auditor: `rechazado` y `escalado_humano`.
- **Instalador.** `--scope workspace` escribe solo los archivos del `--target` pedido.

### Corregido
- `web_fetch_clean` conecta a la IP que validó (anti-DNS-rebinding) y rechaza hosts que no resuelven.
- Servidor MCP `mcp_episodic_graph`: workspace fijado, `ping`, errores JSON-RPC y encuadre `Content-Length`.

## Sin publicar — remediación Fase 1 y 2 de la auditoría 2026-11-11

### Cambios que rompen compatibilidad
- **Puertas humanas.** `puerta_N_aprobada: true` escrito a mano ya no aprueba nada: una puerta solo vale si está
  sellada con `m37 approve <encargo> --gate N` (hash, autor y fecha). `m37 approve`, `m37 bless`, `m37 ingest` y los
  flags `--approve-gate-N` exigen una terminal interactiva.
- **Hash de Puerta 2.** Ahora cubre `enrutamiento_modelo` (ejecutor, QA, presupuesto, turnos) y `depende_de`. El kernel
  ya no re-sella la puerta tras cada nodo. Los planes aprobados con una versión anterior deben volver a aprobarse.
- **Servidor MCP.** Se eliminan `approve_gate_1/2`, la acción `write_reference_snapshot`, el perfil por prefijo `ORQ` y
  los parámetros `modelo_auditor`/`familia_auditor`. Cada acción exige el `node_id` de un nodo existente y puertas
  vigentes.
- **Snapshots.** `cita_verificada` solo puede ser verdadero contra un snapshot registrado por el kernel
  (`web_fetch_clean`) o por una persona (`m37 ingest`). Los snapshots web van a `referencias/_publico/`;
  `referencias/<encargo>/` queda bajo la Muralla China.
- **QA en L3/L4.** Solo cuenta un veredicto atestiguado: QA ciego ejecutado por el kernel (`m37 run`, `m37 qa`,
  `m37_run_kernel_qa`) o acta humana (`m37 bless`). El veredicto de un subagente del harness se registra con
  `revision_ciega_verificada: false` y es consultivo.
- **Cómputo.** Con el backend `process`, el cómputo se bloquea si el workspace contiene encargos de más de un cliente
  (salvo `M37_ALLOW_SHARED_WORKSPACE_COMPUTE=1`).
- **Compilador.** La Pasada 3 exige que toda cifra en prosa coincida con el sustento citado o el Brief. Se rechazan
  enlaces con esquemas distintos de http/https/mailto, imágenes remotas, bloques raw de Pandoc/Quarto y shortcodes.
  El rótulo "Trazabilidad Verificada" pasa a "Anclajes y linaje verificados mecánicamente · prosa no auditada por QA".
- **Instalador de plugins.** El directorio de proyecto por defecto es el actual (antes, el repo del motor).

### Añadido
- `m37 approve`, `m37 qa`, `m37 ingest`; herramienta MCP `m37_run_kernel_qa`.
- Backend de sandbox `container` (docker/podman, sin red, solo el encargo montado) y `extensions/sandbox/Dockerfile`.
- `dominios_web_permitidos` en el Brief (obligatorio para `web_fetch_clean` en L3/L4).
- `modo_ejecucion` en el Plan; `origen_snapshot` en la Evidencia; `max_output_tokens` en `limites_operativos`.
- `mapa_trazabilidad.json` incluye `alcance_verificacion` y `qa_por_hallazgo`.
- CI (Linux y Windows), `uv.lock`, `requirements.txt` con hashes, wheel con `starter_vault/` y `extensions/`.

### Corregido
- `m37 run` fallaba antes de la primera llamada al proveedor (`max_output_tokens` inexistente; `credencial_env: null`).
- El instalador sobrescribía `CLAUDE.md`, `AGENTS.md` y `.mcp.json` ajenos y configuraba un intérprete sin dependencias.
- `estructuracion_analitica` aceptaba cifras como texto; `fmt()` sin tope; lock de numeración huérfano; IDs sin validar.
- El servidor MCP moría con una línea no-JSON y tardaba ~2.7 s en arrancar por importar LiteLLM.

# Changelog

## Sin publicar — remediación Handoff H1–H10 (2026-11-16)

### Seguridad y aislamiento
- **Citas y traversal entre encargos (`H1`, `H8`):** `InputHashRef`, `DerivationContract`, `FindingContract` y `resolve_artifact_by_id` rechazan rutas con `..`, barras invertidas, IDs no canónicos y rutas fuera de `datos/<encargo>/`, `analisis/<encargo>/` y `codigo/<encargo>/` (antes de recortar `.md`/`.json`), y el muro de cliente/encargo/bitácora aplica comparación *case-insensitive*.
- **Snapshot del sandbox no destructivo y timeout por nodo (`H2`):** `_capture_protected_snapshot` y `_verify_and_restore_snapshot` excluyen `.obsidian/`, `.trash/` y `.indice/`, no eliminan archivos creados por terceros durante la ejecución y conservan la restauración de archivos protegidos preexistentes; `NodeContract.timeout_segundos` y `AgentExecutionContext.default_timeout_s` permiten timeouts configurables de `5` a `600` segundos.
- **Verificación SDD con recibo JUnit (`H3`):** `run_verification_cmd` prohíbe filtros parciales de `pytest` (`-k`, `-m`, `--deselect`, `--ignore`, `--junitxml`, rutas puntuales), inyecta `--junitxml` en temporal fuera de `codigo/` y estampa `full_suite` y `passed_tests`; `verify_sdd_code_derivation` exige que cada test mapeado en `cobertura_rf` figure como `passed`.
- **Anclaje numérico y sanitización HTML (`H4`, `H7`):** `_numeric_variants` distingue separadores de miles US/MX (`1,200` → `1200`) y europeos (`1.200,50` → `1200.50`), Pasada 3 excluye `metadatos_metodo` de los números permitidos, `_contains_numeric_value` rechaza cantidades embebidas como `"aprox. 37%"` en `estructuracion_analitica`, `EvidenceContract.url` exige `http://` o `https://` y el compilador filtra enlaces en notas al pie y HTML final.
- **Cierre a `50_Capital/` (`H5`):** `close_engagement` extrae alias declarados en `10_Clientes/*/Perfil.md`; `m37 close` cambia `--promote-capital` a `false` por defecto y exige terminal interactiva y Puerta 1 al solicitar promoción.
- **`web_fetch_clean` (`H6`):** valida esquema `http`/`https` y política/allowlist antes de resolver DNS y en cada redirección, bloquea IPs no globales (`not ip.is_global`), rechaza `Content-Type` binarios (`application/pdf`, etc.), decodifica según `charset` declarado y registra la URL final.
- **Router y estado QA L1 (`H9`):** `compute_episodic_rejection_rates` exige coincidencia exacta o por sufijo `-base` en dominios; `run_engagement` deja `veredicto_qa_r1` y `aprobado_r1` en `None` y no marca `estado_qa: aprobado` cuando `qa_entry` es `None`.
- **Gobernanza MCP y caché del índice (`H10`):** `generate_plan_from_archetype` y `m37_plan_engagement` impiden sobrescribir un `01_Plan.md` con Puerta 2 aprobada sin `motivo_replanificacion` (e incrementan `version` reiniciando Puerta 2); `m37_write_artifact` valida las dependencias en `depende_de` antes de escribir; `_ensure_index` omite `reindex_vault_to_duckdb` cuando el índice es más reciente que los archivos del Vault.

### Corregido (baja severidad)
- `runner.py`: `ContractValidationError` ya no se contabiliza doblemente dentro de `guard_violations`.
- `config.py`: `check_privacy_gate` permite operación `L3`/`L4` puramente local cuando `M37_ENABLE_LOCAL_OLLAMA=1` sin API keys cloud configuradas.
- `harness.py`: `M37_SKILL_MARKDOWN` interpola dinámicamente `__version__`; `_upsert_codex_config_toml` crea respaldo `.m37.bak` previo y escribe atómicamente.

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

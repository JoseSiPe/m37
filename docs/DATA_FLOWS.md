# M37 (Move 37) — Mapa de Flujos de Datos y Almacenes (`DATA_FLOWS.md`)

Este documento detalla de forma exhaustiva todos los puntos de salida por red, aislamiento de subprocesos y almacenes en disco de **M37 (`m37-core`)**, facilitando auditorías técnicas y revisiones de confidencialidad de clientes (alineado con controles de transparencia tipo ISO/IEC 42001).

## 1. Puntos de Contacto de Red (Egress)

| Componente | Destino de red | Cuándo ocurre | Qué datos salen | Cómo audititarlo o apagarlo |
|---|---|---|---|---|
| `src/m37/runner.py` (`LiteLLM`) | Endpoints de API declarados por el usuario en `00_Sistema/Modelos.yaml` (ej. Google Gemini, Anthropic, OpenAI, OpenRouter u `ollama` local) | Únicamente durante `m37 plan` o `m37 run` | System prompt del agente activo + extractos leídos del encargo activo permitidos por `ExecutionGuard` | **Bloqueo L3/L4:** `m37 doctor`, `plan` y `run` abortan en rigor `L3`/`L4` si `M37_DATA_PRIVACY_ACK != "paid_billing"`. Para ejecución 100% local, apuntar `Modelos.yaml` a `ollama/...`. |
| Telemetría de `LiteLLM` | **DESHABILITADA (`0 bytes`)** | Nunca | Ninguno | `litellm.telemetry = False` se fuerza al importar `src/m37/runner.py`. |
| `web_fetch_clean` (`src/m37/guards.py`) | URL pública específica solicitada por un agente con `perfil_herramientas: documental` | Solo en nodos de perfil `documental` | Petición HTTP `GET` estándar a la URL objetivo (sin contenido del Vault) | Prohibida estructuralmente en los perfiles `analitico`, `construccion`, `editorial` y `qa_ciego`. Toda respuesta entrante se sanitiza y envuelve en `<datos_fuente_no_ejecutables>`. |

## 2. Subprocesos Locales y Filtrado de Secretos

| Herramienta | Directorio de trabajo (`cwd`) | Entorno (`env`) recibido | Control de integridad |
|---|---|---|---|
| `run_sandbox_script` (`analitico` con `requiere_computo: true`) | `analisis/<encargo_activo>/` | Entorno limpio: **se eliminan todas las llaves `*_API_KEY` y secretos de `.env`** | Snapshot SHA-256 previo/posterior fuera de `analisis/<encargo>/` y `Derivaciones/`; reversión y aborto ante cualquier mutación externa. |
| `run_verification_cmd` (`construccion`) | `codigo/<encargo_activo>/` | Entorno limpio sin API keys de `.env` | Solo comandos en *allowlist* (`pytest`, `unittest`, `ruff`, `mypy`, `npm_test`, `cargo_test`, `python_m`); sella recibo `.m37_verify_receipt.json` con `tree_sha256`. |

## 3. Almacenes Locales en Disco

| Ruta | Formato | Contenido | Separación Git / IP |
|---|---|---|---|
| `<M37_VAULT>/vault/` | Markdown + YAML frontmatter / JSON | Clientes (`10_`), Encargos aislados (`20_`), Evidencia pública (`30_`), Métodos (`40_`), Capital anonimizado (`50_`) | Vive físicamente fuera de `m37-core/`; `m37 doctor` bloquea si apunta dentro del remoto de `m37-core`. |
| `<M37_VAULT>/vault/90_Bitacora/episodios.jsonl` | JSON Lines (*append-only*) | Telemetría determinista de nodos (tokens, USD, modelo, veredicto QA) y tarjetas de cierre | Local al workspace privado; excluido en workspaces `m37 init --clean-l4`. |
| `<M37_VAULT>/vault/.indice/grafo.duckdb` | Base embebida `DuckDB` (opcional, Modo `Full`) | Proyección bitemporal determinista (0 llamadas LLM) de frontmatters y episodios | Ignorado en `.gitignore`; reconstruible al 100% con `m37 reindex`. |
| `<M37_VAULT>/codigo/<encargo>/.m37_verify_receipt.json` | JSON de solo-kernel | `{cmd, exit_code, tree_sha256, ts, summary}` de la última corrida de pruebas | Bloqueado contra escritura por agentes (`write_code_file` / `edit_code_file`). |

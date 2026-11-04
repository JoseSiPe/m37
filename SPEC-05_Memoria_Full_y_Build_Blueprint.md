# M37 (Move 37) — SPEC-05: Memoria Episódica, Grafo Full MCP, Gobierno L4 y Blueprint de Construcción

> **Proyecto:** **M37** (*Move 37*)
> **Especificación:** `SPEC-05` (Etapa 5 de 5)
> **Objetivo:** Cerrar (1) la especificación de memoria episódica desde `Bare Bones` (`episodios.jsonl`) hasta el modo `Full` (`grafo.duckdb` bitemporal + servidor MCP `readonly`), (2) la política de compresión en 3 capas y aislamiento `L4`, y (3) el **Plan Maestro Ejecutable de Construcción** módulo por módulo con su matriz de pruebas adversariales.

---

## 1. Memoria Episódica: De `Bare Bones` al Grafo Bitemporal `Full`

Cumpliendo el **Principio #1** y la matriz de acceso de `00_M37_Arquitectura_v2.md`, **ningún agente escribe su propia memoria episódica** y **solo el Orquestador puede consultarla**.

### 1.1 Sustrato Universal en `Bare Bones` (`90_Bitacora/episodios.jsonl`)

Archivo *append-only* escrito determinísticamente por `runner.py` (eventos Nivel 0) y `cli.py:close` (tarjetas Nivel 1):

* **Evento Nivel 0 (`evento: "cierre_nodo"`):** Registra `ts`, `encargo`, `nodo`, `arquetipo`, `rol_tarea`, `dominio`, `metodo`, `eje_cognitivo`, `modelo`, `familia`, `turnos`, `tokens_in`, `tokens_out`, `costo_usd`, `estado_salida`, `errores_contrato_autocorregidos`, `modelo_qa`, `familia_qa`, `veredicto_qa_r1`, `motivo_rechazo_corto` y `activo: true`.
* **Evento Nivel 1 (`evento: "cierre_encargo"`):** Generado al correr `m37 close <encargo>`. Marca los eventos Nivel 0 de ese encargo como fríos (`activo: false` en índice) y consolida una tarjeta con: `encargo`, `arquetipo`, `dominios`, `nivel_rigor`, `costo_total_usd`, `desviacion_presupuesto_pct`, `tasa_aprobacion_qa_r1`, `ataques_qa_mas_fuertes: [...]`, `metodos_validados: [...]` y `sorpresas_operativas`.

En modo **`Bare Bones`** (sin DuckDB instalado), `router.py` lee `episodios.jsonl` en Python puro (<5 ms para miles de líneas) y construye una tabla Markdown compacta de **Nivel 2** (*tasa de aprobación QA en Ronda 1 y costo mediano USD por `modelo × eje_cognitivo × dominio`*) que se inyecta al Orquestador durante `m37 plan`.

---

## 2. Esquema SQL del Grafo Bitemporal y Servidor MCP (`Modo Full`)

Cuando se instala el extra `full` (`uv sync --extra full`), `m37 reindex` proyecta todos los frontmatters del Vault y `episodios.jsonl` hacia `vault/.indice/grafo.duckdb`. **Cero llamadas a LLM durante la indexación** (100% determinista y reconstruible desde cero borrando `.indice/`).

### 2.1 DDL Exacto en DuckDB (`extensions/mcp_episodic_graph/schema.sql`)

```sql
CREATE TABLE IF NOT EXISTS nodos_grafo (
    id              VARCHAR PRIMARY KEY,
    etiqueta        VARCHAR NOT NULL,   -- Cliente | Encargo | Arquetipo | Nodo | Modelo | Metodo | Fuente | Derivacion | Hallazgo | VeredictoQA
    titulo          VARCHAR,
    dominio         VARCHAR,
    estado          VARCHAR,
    ruta_archivo    VARCHAR,
    propiedades     JSON,
    valido_desde    TIMESTAMP NOT NULL,
    valido_hasta    TIMESTAMP,          -- NULL = vigente actualmente
    indexado_en     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS aristas_grafo (
    id_arista       VARCHAR PRIMARY KEY,
    origen_id       VARCHAR NOT NULL REFERENCES nodos_grafo(id),
    relacion        VARCHAR NOT NULL,   -- CONTRATA | INSTANCIA | TIENE_NODO | EJECUTO_CON | USO_METODO | SE_APOYA_EN | SUPERSEDE | AUDITO
    destino_id      VARCHAR NOT NULL REFERENCES nodos_grafo(id),
    peso_costo_usd  DOUBLE DEFAULT 0.0,
    turnos          INTEGER,
    resultado_qa    VARCHAR,
    severidad_max   VARCHAR,
    propiedades     JSON,
    valido_desde    TIMESTAMP NOT NULL,
    valido_hasta    TIMESTAMP           -- Permite consultas en cualquier corte histórico
);

CREATE INDEX IF NOT EXISTS idx_aristas_origen ON aristas_grafo(origen_id, relacion);
CREATE INDEX IF NOT EXISTS idx_aristas_destino ON aristas_grafo(destino_id, relacion);
```

### 2.2 Las 5 Herramientas del Servidor MCP (`extensions/mcp_episodic_graph/server.py`)

Expuestas mediante `fastmcp` abriendo `grafo.duckdb` con `read_only=True` (exclusivas para sesiones de planificación/arbitraje del Orquestador):

1. `fuentes_por_vencer(dias_horizonte: int = 30)`
   * **Qué responde:** Consulta recursiva (`WITH RECURSIVE`) que detecta notas `Fuente` cuya ventana `fecha_verificacion + vigencia_dias_max` vence dentro de `dias_horizonte` y lista todos los `Hallazgos` vigentes que dependen directa o indirectamente de ellas.
2. `desempeno_empirico_modelos(eje_cognitivo: str | None = None, dominio: str | None = None)`
   * **Qué responde:** Devuelve por cada `Modelo`: número de corridas, tasa de éxito en Ronda 1 de QA, promedio de errores Pydantic autocorregidos y percentiles P50/P90 de `costo_usd` y `turnos`. Permite al Orquestador jubilar modelos que fallan en la práctica.
3. `patrones_rechazo_qa(dominio: str, metodo: str | None = None)`
   * **Qué responde:** Recupera los motivos de rechazo y campos `mejor_ataque` históricos en `VeredictoQA` sobre ese dominio/método para que el Orquestador inyecte advertencias preventivas en los nuevos `Nodos/N-XX.md`.
4. `cuellos_presupuesto_arquetipo(arquetipo_id: str)`
   * **Qué responde:** Identifica qué pasos del `Arquetipo` han caído en `bloqueado_presupuesto` o requerido 2 rondas de QA para calibrar `max_turns` y `presupuesto_max_usd` con datos reales.
5. `linaje_artefacto(artefacto_id: str)`
   * **Qué responde:** Subgrafo completo aguas arriba (insumos hasta la fuente primaria) y aguas abajo (entregables afectados) de cualquier ID del Vault.

---

## 3. Política de Compresión, Promoción de Capital y Aislamiento `L4`

### 3.1 Protocolo de cierre (`m37 close <encargo>`)

1. Verifica que no existan nodos `en_curso`.
2. Consolida la **Tarjeta Episódica Nivel 1** en `90_Bitacora/episodios.jsonl` y recalcula agregados Nivel 2 (evitando que para el encargo #30 el contexto del Orquestador se llene de trazas viejas).
3. Lista en consola todos los `Hallazgos` aprobados con `promovible_a_capital: true`.
4. Si el humano elige promover uno a `50_Capital/`, `m37 close` crea un borrador en `50_Capital/C-YYYYMM-NN.md` **eliminando automáticamente el campo `encargo` y ejecutando un escaneo regex que aborta si el texto aún contiene el nombre o alias del `cliente` registrado en `10_Clientes/`**.

### 3.2 Aislamiento Físico Efímero para Nivel `L4` (`m37 init --clean-l4 <ruta>`)

Para peritajes, litigios o NDAs con cláusula de destrucción certificada al cierre:
* `m37 init --clean-l4 ../caso_aislado` crea un workspace virgen copiando **únicamente** `00_Sistema/` y `40_Metodos/` (sin `10_Clientes/`, sin `30_Evidencia_Publica/` previa, sin `50_Capital/` y sin `episodios.jsonl` históricos). Al concluir el caso, la carpeta completa puede archivarse cifrada o destruirse sin afectar el Vault Maestro.

---

## 4. Plan Maestro Ejecutable de Construcción (`Blueprint v0.1 → v0.2`)

Este orden de implementación minimiza acoplamiento y permite tener el repositorio **`Bare Bones` compartible en GitHub desde el Hito 1**, con menos de 1,000 líneas de Python bien probadas.

### 4.1 Orden exacto de módulos a escribir (`m37-core`)

| Orden | Archivo | LOC est. (`v0.1`) / LOC real (`v0.2.0`) | Responsabilidad exacta | Depende de |
|---|---|---|---|---|
| **1** | `pyproject.toml` + `.env.example` + `.gitignore` | 65 / 95 | Empaquetado `uv`/PEP 621 (`v0.2.0`), entry-point `m37`, bloqueo anti-fugas Git | — |
| **2** | `src/m37/schemas.py` | 185 / 465 | Contratos Pydantic v2 (`SPEC-03` + `ArchetypeContract` + `ArtifactKind` + validación topológica DAG) + encabezados `##` | — |
| **3** | `src/m37/config.py` | 90 / 280 | Carga de `.env`, resolución de `M37_VAULT`, filtro de `Modelos.yaml` y chequeo `M37_DATA_PRIVACY_ACK` | `schemas.py` |
| **4** | `src/m37/guards.py` | 225 / 1,135 | `ExecutionGuard`: muralla entre clientes (`vault/`, `analisis/`, `codigo/`), aislamiento episódico (`90_Bitacora/` y `.indice/`), *allowlist* QA ciego, cerca anti-inyección, cuarentena, anti-secretos y recibo `.m37_verify_receipt.json` | `schemas.py`, `config.py` |
| **5** | `src/m37/verify.py` | 155 / 515 | QA Fase A determinista: integridad referencial, cuarentena, anti-concesión cosmética, compuerta `tree_sha256` / `sdd_codigo` (`RF-*`) + `validador_plugin` | `schemas.py`, `guards.py` |
| **6** | `src/m37/router.py` | 110 / 418 | Resolución de `--orchestrator`, ruteo multicriterio (`L1`–`L4`, margen 60% de ventana, penalización $>35\%$ QA R1), resumen episódico Nivel 2 y los 4 guardarraíles | `config.py`, `schemas.py` |
| **7** | `src/m37/runner.py` | 195 / 420 | Loop sin estado sobre `litellm.completion()` (`telemetry=False`), costo vía `litellm.completion_cost()` con respaldo en catálogo, herramientas envueltas por `guards.py` y `episodios.jsonl` | `guards.py`, `router.py` |
| **8** | `src/m37/compiler.py` | 150 / 435 | Pipeline de 5 pasadas (`<!-- sustento -->`, cuarentena, techo `fuerza_afirmacion` en `mapa_trazabilidad.json`, salida `.md`/`.html` y puente `quarto`) | `verify.py`, `schemas.py` |
| **9** | `src/m37/cli.py` | 145 / 1,405 | Comandos `doctor`, `init`, `new`, `plan`, `run`, `compile`, `quarantine`, `bless`, `close` y `reindex` (máquina de estados *fail-closed* con presupuesto compartido en 4 fases) | Todos los anteriores |
| **10** | `starter_vault/` + `docs/` + `extensions/` | — | `Modelos.yaml` (tarifas reales actualizadas), agentes canónicos `SPEC-04` (`evidencia`, `analisis-sintesis`, `desarrollo-codigo`, `redaccion`, `qa-critico`, `orquestador`, `integrador`), 3 *Domain Skills*, 3 Arquetipos declarativos (`ArchetypeContract`), `DEMO-01`, `docs/DATA_FLOWS.md`, `docs/RISK_REGISTER.md` (`R1`–`R8`) y extensión `DuckDB/MCP` | — |

*(Presupuesto original de diseño `v0.1.0-barebones`: ~1,320 LOC; implementación completa `v0.2.0` con guardias exhaustivas, SDD/TDD y extensiones `Full`: ~5,000 LOC en `src/m37/` y `extensions/`).*

### 4.2 Matriz Obligatoria de Pruebas Automatizadas (`tests/`, 21 pruebas en `pytest`)

Ningún release de `m37-core` es válido si no pasan las **21 pruebas automatizadas en `pytest`** (12 en `test_adversarial_matrix.py` y 9 en `test_cli_runner_and_full_graph.py`, sin gastar tokens reales):

1. `test_1_chinese_wall_and_path_traversal_blocked`: Un agente activo en `Encargo_Cliente_B` intenta `read_file` y `grep_vault` sobre `10_Clientes/Cliente_A/`, `20_Encargos/Encargo_Cliente_A/` y `codigo/Encargo_Cliente_A/` (incluyendo rutas con `../`) → recibe `PermissionDeniedError` en el 100% de los intentos.
2. `test_2_blind_qa_allowlist_blocks_plan_nodes_and_conversations`: El contexto `qa_ciego` intenta leer `Nodos/N-01.md` y `90_Bitacora/episodios.jsonl` → bloqueado por *allowlist*.
3. `test_3_internal_evidence_cannot_be_written_to_public_folder`: Un agente intenta guardar una nota con `visibilidad: interna_encargo` dentro de `30_Evidencia_Publica/` → rechazado en `PreToolCheck`.
4. `test_4_append_only_evidence_immutability`: Un agente intenta sobrescribir o editar (`edit_note`) `E-202610-001.md` ya existente → rechazado.
5. `test_5_upstream_hash_cascade_invalidation_to_derivations_and_findings`: Se aprueba `H-01.md`, luego se modifica 1 byte en su fuente `E-001.md` y se invoca `m37 compile` → el compilador marca `H-01` como `desactualizado` y aborta con código de error $\neq 0$.
6. `test_6_indirect_prompt_injection_tag_escape` y `test_7_quarantined_artifact_blocked_in_read_and_compile`: Escape insensible a mayúsculas/espacios de `</datos_fuente_no_ejecutables>` y bloqueo de artefactos con `invalidada: true` en lectura y compilación.
7. `test_8_ronda2_anti_cosmetic_concession_gate` y `test_9_epistemic_strength_ladder_and_compiler_cap`: Rechazo mecánico de reescrituras cosméticas en Ronda 2 y bloqueo de inflación de `fuerza_afirmacion`.
8. `test_10_agnostic_qualitative_vs_quantitative_deliverable_compilation`: Compilación cualitativa (0 scripts, 0 `D-XX`) y cuantitativa (`{{D-01.clave}}`), abortando ante párrafos huérfanos sin `<!-- sustento: H-XX -->`.
9. `test_11_code_sandbox_isolation_and_secret_leak_prevention` y `test_12_verification_receipt_blocks_untested_post_edit_code`: Confinamiento en `codigo/<encargo>/`, bloqueo de secretos hardcodeados y exigencia de `.m37_verify_receipt.json` con `tree_sha256` coincidente y cobertura `RF-*`.
10. `test_noop_and_max_turns_nodes_escalate_and_block_dag`: Un nodo que termina sin producir sus `artefactos_requeridos` (*no-op*) o que agota `max_turns` pasa obligatoriamente a `escalado_humano` y bloquea la ejecución de nodos dependientes.
11. `test_task_and_editorial_agents_blocked_from_episodic_memory_and_duckdb_index`: Ningún perfil distinto de `orquestador` (`documental`, `analitico`, `construccion`, `editorial`) puede leer, listar ni buscar con `grep_vault` en `90_Bitacora/` (`episodios.jsonl`, `EP-*.md`) ni en `vault/.indice/` (`grafo.duckdb`).
12. `test_declarative_archetype_router_rigor_context_and_episodic_penalty_and_orchestrator_flag`: Creación de un arquetipo declarativo nuevo sin tocar Python, selección sensible a `L1`–`L4` y ventana del 60%, cambio automático de modelo cuando la tasa histórica de rechazo en Ronda 1 de QA supera el 35% y soporte de `--orchestrator`.
13. `test_shared_node_budget_across_r1_qa1_r2_qa2_and_full_episodic_schema`: Techo presupuestal único compartido entre las 4 fases del nodo (`R1`, `QA-R1`, `R2`, `QA-R2`) y verificación del esquema completo `SPEC-02` §5 en `episodios.jsonl`.

### 4.3 Hito 2 — Extensiones `Full` (`v0.2.0` — Implementado)

1. `extensions/mcp_episodic_graph/reindex.py` y `server.py` (`DuckDB` bitemporal + JSON-RPC 2.0 MCP con las 5 consultas analíticas).
2. Plantillas corporativas en `extensions/quarto_templates/` y Pasada 5 de `compiler.py`.
3. Agentes canónicos `SPEC-04` (`evidencia`, `analisis-sintesis`, `desarrollo-codigo`, `redaccion`, `qa-critico`, `orquestador`, `integrador`) y paneles dinámicos `.base` de Obsidian.

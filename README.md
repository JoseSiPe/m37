# M37 (Move 37) — Microkernel Agéntico Agnóstico para Consultoría de Alto Rigor

**M37** (*Move 37*) es un microkernel agéntico en Python (`LiteLLM` + `Pydantic v2`) respaldado por un Vault Markdown compatible con Obsidian. Su núcleo es **100% agnóstico a disciplinas y proveedores de modelos**: ejecuta con las mismas garantías deterministas encargos de estrategia cualitativa, cumplimiento normativo, síntesis documental, modelado cuantitativo o ingeniería de software dirigida por especificaciones (`SDD + EARS + TDD`).

## Pilares Arquitectónicos

1. **Separación Física `Motor` vs. `Workspace` (`SPEC-01`):** `m37-core` nunca almacena clientes reales; tu operación vive fuera del repositorio en `M37_VAULT`, eliminando fugas accidentales en `git push`.
2. **Dos Anillos (`Bare Bones` y `Full`):** `Bare Bones` arranca en menos de 5 minutos sin binarios externos pesados (ni Docker, ni Node, ni Pandoc obligatorio); `Full` añade grafo episódico bitemporal en `DuckDB` + servidor `MCP` de solo-lectura y compilación corporativa vía `Quarto`.
3. **Enrutamiento Multi-Modelo y QA Ciego Heterogéneo (`SPEC-02`):** El usuario solo elige `--orchestrator`; `router.py` asigna modelos desde `00_Sistema/Modelos.yaml` y exige que el auditor `qa-critico` pertenezca a una **familia de proveedor distinta** a la del ejecutor cuando hay $\ge 2$ familias activas.
4. **Guardias *Fail-Closed* en Proceso (`SPEC-03`):** Muralla China entre clientes, evidencia *append-only*, cercas anti-*prompt injection* indirecto (`<datos_fuente_no_ejecutables>`), cuarentena física (`m37 quarantine`), escalera de fuerza epistémica y compuerta anti-concesión cosmética en Ronda 2.
5. **Ingeniería de Software Verificable (`SDD + EARS + TDD + AppSec`):** Perfil `construccion` aislado en `codigo/<encargo>/`, escáner determinista anti-secretos y recibo criptográfico `.m37_verify_receipt.json` (`tree_sha256` + `exit_code == 0`).

## Inicio Rápido (5 minutos)

```powershell
# Con venv estándar (o `uv sync --extra dev --extra full` si usas uv)
python -m venv .venv
.\.venv\Scripts\pip install -e ".[dev,full]"
Copy-Item .env.example .env

# 1. Diagnóstico del entorno y privacidad de API
.\.venv\Scripts\m37 doctor

# 2. Inicializar un workspace operativo fuera del repo del motor
.\.venv\Scripts\m37 init ..\m37_workspace

# 3. Compilar el encargo demostrativo incluido de fábrica (HTML + MD + Mapa de Trazabilidad)
.\.venv\Scripts\m37 compile DEMO-01_entrada-mercado --vault ..\m37_workspace --format html
```

## Ciclo Operativo en 5 Comandos (4 Puertas Humanas)

1. `m37 new <ID_ENCARGO> --cliente <CLIENTE> --rigor L3` → Crea encargo aislado y `00_Brief.md` (**Puerta 1**).
2. `m37 plan <ID_ENCARGO> --arquetipo ARQ_Diagnostico-Cualitativo --orchestrator claude_sonnet` → Instancia el DAG declarativo desde `00_Sistema/Arquetipos/*.md`, aplica ruteo multi-modelo sensible a rigor/contexto/penalización histórica y genera `01_Plan.md` y `Nodos/N-XX.md` (**Puerta 2**).
3. `m37 run <ID_ENCARGO>` → Ejecuta el DAG bajo `ExecutionGuard` + verificación mecánica `artefactos_requeridos` + QA Fase A (`verify.py`) + QA Fase B ciego heterogéneo con techo presupuestal compartido (**Puerta 3**).
4. `m37 compile <ID_ENCARGO> --format html` → Audita linaje, cuarentenas y `hash_insumos`, interpola `{{D-XX.clave}}` y compila (**Puerta 4**).
5. `m37 close <ID_ENCARGO>` → Consolida tarjeta episódica y promueve capital anonimizado a `50_Capital/`.

Comandos adicionales de gobernanza:
* `m37 quarantine <ID_ARTEFACTO> --encargo <ID> --reason "..."` (o `--motivo`) — Cuarentena física sin borrar pista de auditoría.
* `m37 bless <ID_HALLAZGO> --encargo <ID> --reason "..."` (o `--justificacion`) — Aprobación humana auditable tras inspección o re-sellado.
* `m37 reindex` — Reconstruye `vault/.indice/grafo.duckdb` (o índice bitemporal JSON en modo *Bare Bones*, 0 llamadas LLM).

## Plugin Multi-Harness Universal (`Antigravity`, `Claude Code`, `OpenAI Codex CLI`)

Puedes ejecutar **M37** directamente sobre tu plan topado actual (**\$0 en llaves API adicionales**) instalando el plugin universal y el servidor MCP `m37-kernel`:

```powershell
# Instala globalmente (~/.gemini, ~/.claude, ~/.codex) y en el repositorio actual
.\.venv\Scripts\m37 plugin install --target all --scope both

# Verifica el estado en los 3 harnesses
.\.venv\Scripts\m37 plugin status
```

* **Google Antigravity:** Instala `~/.gemini/config/plugins/m37/` (`plugin.json`, `mcp_config.json`, `rules/AGENTS.md`, subagente `agents/m37-qa-ciego.md` y `skills/m37/SKILL.md`).
* **Claude Code:** Instala `.claude-plugin/plugin.json`, `.mcp.json`, `CLAUDE.md`, subagente aislado `.claude/agents/m37-qa-ciego.md`, skill `.claude/skills/m37/SKILL.md` y comandos `/m37`, `/m37-qa`, `/m37-compile`.
* **OpenAI Codex CLI:** Instala `AGENTS.md`, `.agents/skills/m37/SKILL.md` y registra `[mcp_servers.m37_kernel]` en `~/.codex/config.toml`.

## Transparencia, Referencias y Licencia

* Flujos de red y almacenes: [`docs/DATA_FLOWS.md`](docs/DATA_FLOWS.md)
* Registro de riesgos y controles: [`docs/RISK_REGISTER.md`](docs/RISK_REGISTER.md)
* Arquitectura maestra, citas académicas/técnicas (Dycke & Gurevych; Lu et al.; Mavin et al. EARS; Cheng-I Wu `academic-research-skills` bajo diseño limpio; Brais Moure `hello-sdd` `Apache-2.0`; Jesse Vincent `obra/superpowers` `MIT`; `BehiSecc/VibeSec-Skill` `Apache-2.0`) y especificaciones completas: ver `00_M37_Arquitectura_v2.md` y `SPEC-01` a `SPEC-05`.
* Distribuido bajo licencia **MIT** ([`LICENSE`](LICENSE)).

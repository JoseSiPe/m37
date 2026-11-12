# M37 (Move 37) — SPEC-03: Contratos Agnósticos, Máquina de Estados y Guardias de Seguridad

> **Proyecto:** **M37** (*Move 37*)
> **Especificación:** `SPEC-03` (Etapa 3 de 5)
> **Objetivo:** Definir con precisión formal los **6 esquemas Pydantic v2** del microkernel (`src/m37/schemas.py`), la **máquina de estados** de nodos y hallazgos, el algoritmo de **invalidación en cascada por hash** y las reglas exactas de aislamiento y permisos en `src/m37/guards.py`.

---

## 1. Los 6 Contratos Base Agnósticos (`src/m37/schemas.py`)

Ningún esquema base contiene campos acoplados a estadística, derecho, finanzas o estrategia. Sirven de forma idéntica para un encargo 100% cualitativo, normativo, conceptual o cuantitativo.

```python
from __future__ import annotations

from datetime import date
from typing import Any, Literal
from pydantic import BaseModel, Field, model_validator

RigorLevel = Literal["L1", "L2", "L3", "L4"]
ToolProfile = Literal["documental", "analitico", "construccion", "editorial", "qa_ciego"]
NodeState = Literal[
    "pendiente",
    "en_curso",
    "entregado",
    "en_qa",
    "aceptado",
    "rechazado_r1",
    "bloqueado_presupuesto",
    "escalado_humano",
]
FindingQAState = Literal[
    "pendiente",
    "aprobado",
    "aprobado_con_correcciones",
    "rechazado",
    "desactualizado",
    "invalidado",
]
# Escalera agnóstica de fuerza epistémica (ordenada de índice 0 a 3 para control determinista de deriva)
EpistemicStrength = Literal[
    "observacion_acotada",          # Nivel 0: hecho directo, cláusula literal o dato puntual observado
    "inferencia_condicionada",      # Nivel 1: asociación, tendencia o lectura sujeta a supuestos explícitos
    "tesis_estructural_o_causal",   # Nivel 2: mecanismo causal identificado, doctrina firme o prueba robusta
    "prescripcion_accionable",      # Nivel 3: recomendación de decisión respaldada por niveles previos + análisis de riesgo
]
EPISTEMIC_RANK: dict[str, int] = {
    "observacion_acotada": 0,
    "inferencia_condicionada": 1,
    "tesis_estructural_o_causal": 2,
    "prescripcion_accionable": 3,
}


# 1. CONTRATO DE BRIEF (20_Encargos/<ID>/00_Brief.md)
class BriefContract(BaseModel):
    tipo: Literal["brief"] = "brief"
    id_encargo: str
    cliente: str
    fecha: date
    pregunta_central: str = Field(min_length=10)
    decision_que_habilita: str = Field(min_length=10)
    alcance: list[str] = Field(min_length=1)
    exclusiones: list[str] = Field(default_factory=list)
    entregables_esperados: list[str] = Field(min_length=1)
    audiencia: str
    nivel_rigor: RigorLevel = "L3"
    dominios_involucrados: list[str] = Field(min_length=1)
    dominios_bloqueantes: list[str] = Field(default_factory=list)
    hipotesis_a_falsear: list[str] = Field(default_factory=list)
    puerta_1_aprobada: bool = False

    @model_validator(mode="after")
    def exigir_hipotesis_en_rigor_medio_alto(self) -> BriefContract:
        if self.nivel_rigor in ("L2", "L3", "L4") and len(self.hipotesis_a_falsear) < 1:
            raise ValueError("Encargos L2-L4 requieren al menos 1 hipótesis inicial a falsear.")
        return self


# 2. CONTRATO DE PLAN Y DE NODO (01_Plan.md y Nodos/N-XX.md)
class ModelRoutingSpec(BaseModel):
    eje_dominante: Literal[
        "extraccion_fiel",
        "abstraccion_conceptual",
        "coherencia_documental",
        "ejecucion_formal",
        "critica_adversarial",
    ]
    contexto_estimado_tokens: int = Field(gt=0)
    modelo_ejecutor: str
    justificacion_ejecutor: str
    modelo_qa_critico: str | None = None
    justificacion_qa: str | None = None
    presupuesto_max_usd: float = Field(gt=0.0, le=50.0)
    max_turns: int = Field(ge=1, le=40, default=14)


class PlanContract(BaseModel):
    tipo: Literal["plan"] = "plan"
    id_plan: str
    version: int = Field(ge=1, default=1)
    motivo_replanificacion: str | None = None
    encargo: str
    arquetipo_base: str
    nodos: list[str] = Field(min_length=1)
    presupuesto_total_estimado_usd: float = Field(ge=0.0)
    puerta_2_aprobada: bool = False

    @model_validator(mode="after")
    def exigir_motivo_si_v2(self) -> PlanContract:
        if self.version > 1 and not self.motivo_replanificacion:
            raise ValueError("Toda replanificación (version > 1) exige motivo_replanificacion.")
        return self


class NodeContract(BaseModel):
    id: str = Field(pattern=r"^N-\d{2,3}$")
    tipo: Literal["nodo"] = "nodo"
    encargo: str
    objetivo: str
    perfil_herramientas: ToolProfile
    rol_tarea: str
    dominio_validador: str
    requiere_computo: bool = False
    insumos: list[str] = Field(default_factory=list)
    entregables_esperados: list[str] = Field(min_length=1)
    contrato_aceptacion: list[str] = Field(min_length=1)
    criterio_suficiencia: str
    depende_de: list[str] = Field(default_factory=list)
    riesgo: Literal["bajo", "medio", "alto"] = "medio"
    estado: NodeState = "pendiente"
    rondas_qa: int = Field(ge=0, le=2, default=0)
    enrutamiento_modelo: ModelRoutingSpec


# 3. CONTRATO DE EVIDENCIA (30_Evidencia_Publica/E-*.md o Evidencia_Interna/EI-*.md)
class EvidenceContract(BaseModel):
    id: str
    tipo: Literal["evidencia"] = "evidencia"
    visibilidad: Literal["publica", "interna_encargo"]
    encargo_origen: str | None = None
    clase: Literal[
        "normativa",
        "dato",
        "literatura",
        "entrevista",
        "mercado",
        "documento_interno",
        "jurisprudencia",
        "otro",
    ]
    titulo: str
    emisor_o_autor: str
    fecha_publicacion: str | None = None
    fecha_verificacion: date
    vigencia_dias_max: int = Field(gt=0, default=180)
    url: str | None = None
    localizador_verificable: str = Field(min_length=3)   # ej. "Art. 14 párr. 2", "p. 18 Tabla 3", "celda B12"
    snapshot_local: str | None = None
    hash_snapshot: str | None = None
    fiabilidad: Literal["alta", "media", "baja"]
    cita_verificada: bool = False
    invalidada: bool = False
    motivo_invalidacion: str | None = None

    @model_validator(mode="after")
    def exigir_motivo_si_invalidada(self) -> EvidenceContract:
        if self.invalidada and not self.motivo_invalidacion:
            raise ValueError("Marcar invalidada=True exige registrar motivo_invalidacion.")
        return self


# 4. CONTRATO DE DERIVACIÓN OPCIONAL (Derivaciones/D-XX.json o .md)
class InputHashRef(BaseModel):
    id_o_ruta: str
    sha256: str


class DerivationContract(BaseModel):
    id: str
    tipo: Literal["derivacion"] = "derivacion"
    encargo: str
    metodo: str
    productor_modo: Literal["script", "estructuracion_analitica", "verificacion_codigo"]
    productor_ref: str
    productor_sha256: str | None = None
    insumos: list[InputHashRef] = Field(min_length=1)
    salidas: dict[str, Any] = Field(min_length=1)
    metadatos_metodo: dict[str, Any] = Field(default_factory=dict)
    invalidada: bool = False
    motivo_invalidacion: str | None = None

    @model_validator(mode="after")
    def exigir_motivo_si_derivacion_invalidada(self) -> DerivationContract:
        if self.invalidada and not self.motivo_invalidacion:
            raise ValueError("Marcar invalidada=True exige registrar motivo_invalidacion.")
        return self


# 5. CONTRATO UNIVERSAL DE HALLAZGO (Hallazgos/H-*.md)
class FindingContract(BaseModel):
    id: str
    tipo: Literal["hallazgo"] = "hallazgo"
    encargo: str
    dominio: str
    naturaleza: Literal["estrategico", "normativo", "empirico", "organizacional", "tecnico", "sintesis"]
    fuerza_afirmacion: EpistemicStrength = "inferencia_condicionada"
    afirmacion_corta: str = Field(min_length=15)
    sustento_evidencia: list[str] = Field(default_factory=list)
    sustento_derivaciones: list[str] = Field(default_factory=list)
    metodo: str
    valores_anclados: dict[str, Any] = Field(default_factory=dict)
    hash_insumos: str
    confianza: Literal["alta", "media", "baja"]
    estado_qa: FindingQAState = "pendiente"
    contrahipotesis_probadas: int = Field(ge=0)
    que_lo_refutaria: str = Field(min_length=10)
    promovible_a_capital: bool = False

    @model_validator(mode="after")
    def exigir_al_menos_un_sustento(self) -> FindingContract:
        if len(self.sustento_evidencia) + len(self.sustento_derivaciones) < 1:
            raise ValueError("Todo Hallazgo requiere al menos una fuente en sustento_evidencia o sustento_derivaciones.")
        return self


# 6. CONTRATO DE VEREDICTO DE QA (QA/V-*.md)
class QAIssue(BaseModel):
    id_defecto: str = Field(pattern=r"^DEF-\d{2}$")
    severidad: Literal["bloqueante", "mayor", "menor", "sugerencia"]
    categoria: Literal[
        "sustento_no_respalda_afirmacion",
        "inflacion_fuerza_epistemica",
        "violacion_limite_fuente",
        "salto_logico_o_metodologico",
        "omision_contrahipotesis",
        "incumplimiento_spec_o_test",
        "riesgo_seguridad_codigo",
        "formato_o_claridad",
    ]
    exige_cambio_material: bool = False   # Si True, Ronda 2 rechaza paráfrasis puramente cosméticas
    ubicacion: str
    evidencia: str
    remediacion: str


class QAVerdictContract(BaseModel):
    id: str
    tipo: Literal["veredicto_qa"] = "veredicto_qa"
    encargo: str
    artefacto_auditado: str
    ronda: int = Field(ge=1, le=2)
    modelo_auditor: str
    familia_auditor: str
    revision_ciega_verificada: bool = True
    fase_a_mecanica: Literal["aprobada", "fallida"]
    mejor_ataque: str = Field(min_length=20)
    hallazgos_critica: list[QAIssue] = Field(default_factory=list)
    resolucion_ronda_previa: dict[str, Literal["resuelto_materialmente", "persiste"]] = Field(default_factory=dict)
    decision: Literal["aceptar", "aceptar_con_correcciones", "rechazar"]

    @model_validator(mode="after")
    def prohibir_aceptar_con_bloqueantes(self) -> QAVerdictContract:
        tiene_bloqueante = any(h.severidad == "bloqueante" for h in self.hallazgos_critica)
        acepta = self.decision in ("aceptar", "aceptar_con_correcciones")
        if tiene_bloqueante and acepta:
            raise ValueError(
                f"Prohibido emitir decision={self.decision!r} si existe algún hallazgo bloqueante: "
                "un defecto bloqueante exige decision='rechazar'."
            )
        if acepta and any(est == "persiste" for est in self.resolucion_ronda_previa.values()):
            raise ValueError(
                f"Prohibido emitir decision={self.decision!r} en Ronda 2 si persiste un defecto de la ronda previa."
            )
        return self
```

### Secciones Markdown obligatorias validadas por encabezado

Además del frontmatter YAML, `guards.py` verifica que el cuerpo Markdown contenga exactamente los encabezados `##` requeridos por tipo:
* **Brief:** `## Contexto`, `## Hipótesis iniciales a falsear`, `## Criterios de fracaso`, `## Restricciones`.
* **Evidencia:** `## Síntesis sustantiva`, `## Extracto o referencia verificable`, `## Límites y sesgos de la fuente`.
* **Hallazgo:** `## Afirmación`, `## Sustento y trazabilidad`, `## Explicaciones rivales descartadas`, `## Condiciones de validez y límites`.

---

## 2. Máquina de Estados, Obsolescencia (`hash_insumos`) y Anti-Concesión Cosmética

### 2.1 Ciclo de vida determinista de un Nodo

```mermaid
stateDiagram-v2
    [*] --> pendiente
    pendiente --> en_curso : m37 run (dependencias satisfechas)
    en_curso --> bloqueado_presupuesto : Excede max_turns o presupuesto_max_usd
    en_curso --> entregado : Escribe entregables con contrato Pydantic válido
    entregado --> en_qa : Nivel L2-L4
    entregado --> aceptado : Nivel L1 (sin QA formal)
    en_qa --> aceptado : Fase A OK y Veredicto QA = aceptar / aceptar_con_correcciones
    en_qa --> rechazado_r1 : Ronda 1 falla Fase A o Veredicto = rechazar
    rechazado_r1 --> en_curso : Reintento automático con tabla de remediación QA
    en_qa --> escalado_humano : Ronda 2 rechazada o remediación cosmética detectada (tope 2 rondas)
    bloqueado_presupuesto --> escalado_humano : Requiere intervención humana
```

### 2.2 Algoritmo de `hash_insumos` (Invalidación automática en cascada)

Evita que un hallazgo con `estado_qa: aprobado` sobreviva en silencio cuando cambia una fuente o un cálculo aguas arriba:

1. Al guardar un `Hallazgo`, `guards.py` resuelve las rutas en disco de todos los wikilinks listados en `sustento_evidencia` y `sustento_derivaciones`, los ordena lexicográficamente y calcula:
   $$H_{\text{insumos}} = \text{SHA256}\Big(\bigoplus_{f \in \text{ordenados}} \big(\text{id}(f) \parallel \text{bytes}(f)\big)\Big)$$
2. El valor resultante (`sha256:<hex>`) se inyecta automáticamente en el campo `hash_insumos` del frontmatter.
3. Antes de cada `m37 run` y de cada `m37 compile`, `verify.py` recalcula $H_{\text{insumos}}$ para todo hallazgo del encargo. Si difiere del registrado y el hallazgo estaba aprobado, cambia su frontmatter en disco a `estado_qa: desactualizado` y bloquea la compilación hasta que vuelva a pasar QA.

### 2.3 Compuerta Determinista Anti-Concesión Cosmética en Ronda 2 (`verify.py`)

Mitiga en código el fenómeno documentado empíricamente (Dycke & Gurevych) donde un auditor LLM aprueba en Ronda 2 una simple reescritura cosmética aunque la falla de sustento siga intacta:

1. Cuando `V-*-R1.md` rechaza un `Hallazgo` con uno o más defectos `DEF-XX` marcados con `exige_cambio_material: true`, `verify.py` guarda la instantánea `(hash_insumos_r1, EPISTEMIC_RANK[fuerza_afirmacion_r1])`.
2. Antes de despachar al auditor LLM en Ronda 2, `verify.py` verifica mecánicamente que se cumpla **al menos una** de dos condiciones materiales:
   * **Cambio de sustento:** `hash_insumos_r2 != hash_insumos_r1` (se incorporó nueva evidencia `E-*`, se actualizó la derivación `D-*` o se sustituyó una fuente defectuosa); **o**
   * **Moderación epistémica explícita:** `EPISTEMIC_RANK[fuerza_afirmacion_r2] < EPISTEMIC_RANK[fuerza_afirmacion_r1]` (p. ej., el agente rebajó honestamente una `tesis_estructural_o_causal` a `inferencia_condicionada`).
3. Si ninguna se cumple (el agente solo maquilló la redacción), `verify.py` declara `fase_a_mecanica = "fallida"` por **remediación meramente cosmética** y envía el nodo directo a `escalado_humano` sin gastar tokens en una segunda llamada de QA.
4. Asimismo, si en una revisión sin cambio de `hash_insumos` un hallazgo intenta **subir** de nivel en `EPISTEMIC_RANK` (*inflación silenciosa de fuerza*), `verify.py` bloquea la entrega automáticamente.

---

## 3. Guardias de Seguridad en Proceso (`src/m37/guards.py`)

Toda herramienta expuesta a un LLM en `runner.py` está envuelta por `ExecutionGuard(ctx)`. A diferencia de sistemas basados en *hooks* de consola *fail-open*, **`ExecutionGuard` es *fail-closed* en proceso**: ninguna lectura ni escritura ocurre fuera de él.

### 3.1 Contexto inmutable de ejecución

```python
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class AgentExecutionContext:
    vault_root: Path
    workspace_root: Path
    active_engagement_id: str      # ej. "2026-10_ACME-entrada-mercado"
    active_client_id: str          # ej. "ACME"
    role_name: str                 # ej. "evidencia", "redaccion", "qa-critico"
    tool_profile: ToolProfile
    allow_surgical_edit: bool
    qa_allowed_paths: frozenset[Path] = frozenset()
```

### 3.2 Las 7 Reglas Duras de Aislamiento, Integridad y Sanitización

1. **Anti Path-Traversal:** Toda ruta recibida del LLM se resuelve con `target = (workspace_root / rel_path).resolve()`. Si `workspace_root` no es padre estricto de `target`, se lanza `PermissionDeniedError` inmediato.
2. **Muralla China entre Clientes y Encargos (en `read_file`, `grep_vault`, `list_files`):**
   * Si `target` cae dentro de `vault/10_Clientes/<C>/` y `<C> != active_client_id` → **BLOQUEADO**.
   * Si `target` cae dentro de `vault/20_Encargos/<E>/` y `<E> != active_engagement_id` → **BLOQUEADO**.
   * Si `target` cae dentro de `analisis/<E>/`, `codigo/<E>/` o `datos/encargos/<E>/` y `<E> != active_engagement_id` → **BLOQUEADO**.
3. **Aislamiento de Revisión Ciega para `qa_ciego`:**
   * `grep_vault` y `list_files` están deshabilitados.
   * `read_file` **únicamente** permite rutas incluidas en `ctx.qa_allowed_paths`, calculado determinísticamente por `verify.py` como la unión exacta de: (a) el artefacto auditado, (b) su esquema en `00_Sistema/Contratos/`, (c) la sección `## Rúbrica para QA Crítico` del dominio correspondiente y (d) los archivos exactos referenciados en `sustento_evidencia` y `sustento_derivaciones` —incluyendo, cuando una derivación tiene `productor_modo == "verificacion_codigo"`, los archivos fuente/test de `codigo/<encargo>/` y `.m37_verify_receipt.json` en **modo solo-lectura**— (más `V-*-R1.md` cuando `ronda == 2`). Lectura a `Nodos/`, `90_Bitacora/` o `10_Clientes/` es imposible.
4. **Protección de Visibilidad Cruzada en Evidencia (`write_note`):**
   * Si una nota declara `visibilidad: interna_encargo`, `guards.py` prohíbe escribirla en `30_Evidencia_Publica/` (evita filtrar documentos internos de un cliente al acervo público).
   * `30_Evidencia_Publica/` y `Evidencia_Interna/` son **estrictamente *append-only***: si `target.exists()` es verdadero, `write_note` y `edit_note` son rechazados (la única mutación permitida sobre evidencia existente es el comando administrativo `m37 quarantine`).
5. **Frontera Instrucción vs. Datos (*Breakout-Proof Indirect Prompt Injection Fence*):**
   * Toda salida de `web_fetch_clean` y toda lectura vía `read_file` sobre `30_Evidencia_Publica/`, `Evidencia_Interna/` o `referencias/` pasa por `sanitize_untrusted_payload(text, source_id)`:
     1. Escapa cualquier ocurrencia literal o con variantes de mayúsculas de `</datos_fuente_no_ejecutables>` reemplazándola por `&lt;/datos_fuente_no_ejecutables&gt;` (impidiendo ataques de escape de etiqueta).
     2. Envuelve el texto en `<datos_fuente_no_ejecutables fuente="{source_id}">\n...\n</datos_fuente_no_ejecutables>`.
   * El preámbulo fijo de `runner.py` instruye a todo modelo que cualquier directiva imperativa contenida dentro de `<datos_fuente_no_ejecutables>` es material documental bajo análisis y jamás una instrucción del sistema o del usuario.
6. **Cuarentena Física de Artefactos Invalidados (`invalidada: true` / `estado_qa: invalidado`):**
   * Si durante QA o verificación humana se detecta una fuente apócrifa, retractada o mal extraída (o una derivación con error metodológico), `m37 quarantine <ID> --reason "..."` marca `invalidada: true` en su frontmatter sin borrar el archivo (preservando pista de auditoría).
   * `guards.py` oculta y **bloquea la lectura** de cualquier archivo con `invalidada: true` a todos los agentes de tarea (`evidencia`, `analisis-sintesis`, `desarrollo-codigo`, `redaccion`), impidiendo que un agente vuelva a citarlo en rondas posteriores, y `verify.py` invalida en cascada cualquier `Hallazgo` que aún apunte a él.
7. **Confinamiento de Construcción de Código, Anti-Secretos y Recibo Criptográfico (`perfil_herramientas: construccion`):**
   * `write_code_file` y `edit_code_file` solo pueden escribir dentro de `codigo/<active_engagement_id>/` y tienen **prohibido** crear o alterar `.m37_verify_receipt.json` (reservado en exclusiva al kernel).
   * Antes de escribir en disco, `guards.py` ejecuta `scan_for_hardcoded_secrets(content, env_secrets)` rechazando con `SecretLeakError` cualquier archivo que contenga valores literales de llaves cargadas en `.env` o firmas de secretos (`-----BEGIN`, `sk-`, `AIza`, tokens Bearer hardcodeados).
   * `run_verification_cmd(cmd_kind, args)` ejecuta únicamente comandos de prueba/lint/build en *allowlist* (`pytest`, `unittest`, `ruff`, `mypy`, `npm test`, `cargo test`, `python -m`) con `cwd = codigo/<active_engagement_id>/`, `timeout = 120s`, entorno limpio sin API keys de `.env` y verificación de integridad fuera de `codigo/<active_engagement_id>/`. Al concluir, calcula el hash determinista del árbol de código (`tree_sha256`, excluyendo `__pycache__/`, `.pytest_cache/` y `.m37_verify_receipt.json`) y estampa `codigo/<active_engagement_id>/.m37_verify_receipt.json` con `{cmd, exit_code, tree_sha256, ts, summary}`.

### 3.3 Sandbox para `run_sandbox_script` (con y sin Git)

Cuando un nodo con `perfil_herramientas: analitico` y `requiere_computo: true` ejecuta un script en `analisis/<encargo>/`:
1. `guards.py` toma antes de ejecutar un **manifiesto rápido de integridad** `(ruta, mtime_ns, size, sha256)` de `vault/` y de otras carpetas fuera de `analisis/<encargo>/` y `vault/20_Encargos/<encargo>/Derivaciones/`.
2. Ejecuta `uv run python <script>` con `cwd = analisis/<encargo>/`, tiempo máximo (`timeout = 120s`) y variables de entorno filtradas (**sin pasar las API keys de `.env` al subproceso analítico**, evitando exfiltración accidental de llaves).
3. Al terminar, compara el manifiesto de archivos (y `git status --porcelain` si hay repositorio Git). Si el script tocó cualquier ruta fuera de su directorio permitido, restaura desde copia previa y aborta el turno con violación de seguridad.

### 3.4 Asignación Atómica de IDs Multiplataforma

Para soportar ejecución paralela futura sin colisiones en Windows ni Unix:
* `guards.allocate_next_id(prefix="E")` utiliza creación atómica de archivo cerrojo (`os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)`) de la librería estándar de Python para reservar el siguiente correlativo `E-YYYYMM-NNN` o `H-<SLUG>-NN`.

### 3.5 Compuerta Determinista *Evidence-Before-Claims* para Código (`verify.py`)

Convierte la disciplina de verificación previa al cierre (*Verification-Before-Completion* + SDD/TDD) en una compuerta formal ineludible cuando una `Derivacion` declara `productor_modo == "verificacion_codigo"`:

1. **Existencia y éxito del recibo:** `verify.py` lee `codigo/<encargo>/.m37_verify_receipt.json` y exige `exit_code == 0`.
2. **Inmutabilidad post-test (`tree_sha256`):** Recalcula en caliente el `tree_sha256` actual de `codigo/<encargo>/`. Si difiere del `tree_sha256` sellado en el recibo (es decir, el agente editó código después de correr los tests sin re-verificar), declara `fase_a_mecanica = "fallida"` inmediatamente.
3. **Cobertura trazable de requisitos EARS (`validador_plugin: "sdd_codigo"`):** Verifica que todo requisito funcional `RF-*` registrado en `metadatos_metodo["requisitos_ears"]` tenga al menos una función o caso de prueba mapeado en `salidas["cobertura_rf"]` cuyo identificador exista físicamente en los archivos de prueba de `codigo/<encargo>/`.

---

## 4. Costos y Trade-offs aceptados en `SPEC-03`

* **Costo 1 (Fricción deliberada de esquemas y `localizador_verificable`):** Toda nota de `Evidencia` exige declarar el localizador exacto (artículo, página, tabla o celda) y un frontmatter completo. Si el modelo olvida un campo, `write_note` devuelve la traza exacta de Pydantic v2 para autocorrección en 1 turno.
* **Costo 2 (Re-auditoría tras cambios menores en fuentes):** Como `hash_insumos` hashea los bytes de la evidencia citada, corregir una errata ortográfica en `E-0012.md` marca sus hallazgos dependientes como `desactualizado`. *Mitigación:* Comando explícito `m37 bless <H-ID> --reason "corrección tipográfica sin cambio sustantivo"` reservado exclusivamente al humano en CLI (registrado en `90_Bitacora/`).
* **Costo 3 (Bloqueo estricto de reescrituras cosméticas en Ronda 2 y código sin re-testear):** Ni un hallazgo rechazado con `exige_cambio_material: true` pasa con retórica, ni un archivo en `codigo/<encargo>/` pasa Fase A si se tocó 1 byte tras ejecutar `run_verification_cmd`. Esto obliga a correr siempre la suite completa antes de cerrar el nodo.

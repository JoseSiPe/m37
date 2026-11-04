"""M37 (Move 37) — SPEC-03: Agnostic Pydantic v2 Contracts & Markdown Note Parser."""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Literal

import frontmatter
import yaml
from pydantic import BaseModel, Field, ValidationError, model_validator

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
EpistemicStrength = Literal[
    "observacion_acotada",
    "inferencia_condicionada",
    "tesis_estructural_o_causal",
    "prescripcion_accionable",
]

EPISTEMIC_RANK: dict[str, int] = {
    "observacion_acotada": 0,
    "inferencia_condicionada": 1,
    "tesis_estructural_o_causal": 2,
    "prescripcion_accionable": 3,
}
RANK_TO_EPISTEMIC: dict[int, EpistemicStrength] = {
    0: "observacion_acotada",
    1: "inferencia_condicionada",
    2: "tesis_estructural_o_causal",
    3: "prescripcion_accionable",
}


class ContractValidationError(ValueError):
    """Raised when an artifact violates its Pydantic v2 schema or mandatory Markdown sections."""


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
CognitiveAxis = Literal[
    "extraccion_fiel",
    "abstraccion_conceptual",
    "coherencia_documental",
    "ejecucion_formal",
    "critica_adversarial",
]

# Clases de artefacto que un nodo puede estar obligado a producir (verificación mecánica de salida).
ArtifactKind = Literal["evidencia", "derivacion", "hallazgo", "entregable", "codigo"]


class ModelRoutingSpec(BaseModel):
    eje_dominante: CognitiveAxis
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
    metodo: str | None = None
    requiere_computo: bool = False
    requiere_edicion_quirurgica: bool = False
    insumos: list[str] = Field(default_factory=list)
    entregables_esperados: list[str] = Field(min_length=1)
    artefactos_requeridos: list[ArtifactKind] = Field(default_factory=list)
    contrato_aceptacion: list[str] = Field(min_length=1)
    criterio_suficiencia: str
    depende_de: list[str] = Field(default_factory=list)
    riesgo: Literal["bajo", "medio", "alto"] = "medio"
    estado: NodeState = "pendiente"
    rondas_qa: int = Field(ge=0, le=2, default=0)
    enrutamiento_modelo: ModelRoutingSpec


# 2-bis. CONTRATO DE ARQUETIPO DECLARATIVO (00_Sistema/Arquetipos/ARQ_*.md, frontmatter YAML)
class ArchetypeNodeSpec(BaseModel):
    """Plantilla de un nodo del DAG. Toda semántica de disciplina vive aquí (plugin), nunca en src/m37/."""

    id: str = Field(pattern=r"^N-\d{2,3}$")
    objetivo: str = Field(min_length=10)
    perfil_herramientas: Literal["documental", "analitico", "construccion", "editorial"]
    rol_tarea: str = Field(min_length=2)
    eje_dominante: CognitiveAxis
    metodo: str | None = None
    requiere_computo: bool = False
    requiere_edicion_quirurgica: bool = False
    depende_de: list[str] = Field(default_factory=list)
    entregables_esperados: list[str] = Field(min_length=1)
    artefactos_requeridos: list[ArtifactKind] = Field(min_length=1)
    criterio_suficiencia: str = "Artefactos validados sin afirmaciones huérfanas."
    contexto_estimado_tokens: int = Field(gt=0, default=24000)
    presupuesto_max_usd: float = Field(gt=0.0, le=50.0, default=0.75)
    max_turns: int = Field(ge=1, le=40, default=14)


class ArchetypeContract(BaseModel):
    tipo: Literal["arquetipo"] = "arquetipo"
    id_arquetipo: str
    descripcion: str = ""
    nodos: list[ArchetypeNodeSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def exigir_dag_topologico(self) -> ArchetypeContract:
        validate_dag_topological_order([(n.id, n.depende_de) for n in self.nodos])
        return self


def validate_dag_topological_order(nodes: list[tuple[str, list[str]]]) -> None:
    """Fail-closed DAG check: unique IDs and every dependency must appear EARLIER in the list (implies acyclic)."""
    seen: set[str] = set()
    for nid, deps in nodes:
        if nid in seen:
            raise ValueError(f"ID de nodo duplicado en el DAG: {nid!r}.")
        for raw_dep in deps:
            dep = extract_wikilink_id(str(raw_dep))
            if dep == nid:
                raise ValueError(f"El nodo {nid!r} no puede depender de sí mismo.")
            if dep not in seen:
                raise ValueError(
                    f"El nodo {nid!r} depende de {dep!r}, que no existe o no aparece antes en el orden del DAG."
                )
        seen.add(nid)


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
    localizador_verificable: str = Field(min_length=3)
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


import hashlib


def _contains_numeric_value(val: Any) -> bool:
    if isinstance(val, bool):
        return False
    if isinstance(val, (int, float)):
        return True
    if isinstance(val, dict):
        return any(_contains_numeric_value(v) for v in val.values())
    if isinstance(val, (list, tuple)):
        return any(_contains_numeric_value(v) for v in val)
    return False


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
        if self.productor_modo == "estructuracion_analitica" and _contains_numeric_value(self.salidas):
            raise ValueError(
                "Prohibido usar productor_modo='estructuracion_analitica' con salidas numéricas cuantitativas; "
                "toda cifra debe generarse con productor_modo='script' o 'verificacion_codigo' reproducible."
            )
        return self


# 5. CONTRATO UNIVERSAL DE HALLAZGO (Hallazgos/H-*.md)
class FindingContract(BaseModel):
    id: str
    tipo: Literal["hallazgo"] = "hallazgo"
    encargo: str
    dominio: str
    naturaleza: Literal[
        "estrategico",
        "normativo",
        "empirico",
        "organizacional",
        "tecnico",
        "sintesis",
    ]
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
            raise ValueError(
                "Todo Hallazgo requiere al menos una fuente en sustento_evidencia o sustento_derivaciones."
            )
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
    exige_cambio_material: bool = False
    ubicacion: str
    evidencia: str
    remediacion: str


class QAVerdictContract(BaseModel):
    id: str
    tipo: Literal["veredicto_qa"] = "veredicto_qa"
    encargo: str
    artefacto_auditado: str
    sha256_artefacto_auditado: str | None = None
    ronda: int = Field(ge=1, le=2)
    modelo_auditor: str
    familia_auditor: str
    revision_ciega_verificada: bool = True
    fase_a_mecanica: Literal["aprobada", "fallida"]
    mejor_ataque: str = Field(min_length=20)
    hallazgos_critica: list[QAIssue] = Field(default_factory=list)
    resolucion_ronda_previa: dict[str, Literal["resuelto_materialmente", "persiste"]] = Field(
        default_factory=dict
    )
    decision: Literal["aceptar", "aceptar_con_correcciones", "rechazar"]

    @model_validator(mode="after")
    def prohibir_aceptar_con_bloqueantes(self) -> QAVerdictContract:
        tiene_bloqueante = any(h.severidad == "bloqueante" for h in self.hallazgos_critica)
        if tiene_bloqueante and self.decision == "aceptar":
            raise ValueError("Prohibido emitir decision='aceptar' si existe algún hallazgo bloqueante.")
        if any(est == "persiste" for est in self.resolucion_ronda_previa.values()) and self.decision == "aceptar":
            raise ValueError(
                "Prohibido emitir decision='aceptar' en Ronda 2 si persiste un defecto bloqueante previo."
            )
        return self


CONTRACT_REGISTRY: dict[str, type[BaseModel]] = {
    "brief": BriefContract,
    "plan": PlanContract,
    "nodo": NodeContract,
    "arquetipo": ArchetypeContract,
    "evidencia": EvidenceContract,
    "derivacion": DerivationContract,
    "hallazgo": FindingContract,
    "veredicto_qa": QAVerdictContract,
}

REQUIRED_MARKDOWN_HEADINGS: dict[str, list[str]] = {
    "brief": [
        "## Contexto",
        "## Hipótesis iniciales a falsear",
        "## Criterios de fracaso",
        "## Restricciones",
    ],
    "evidencia": [
        "## Síntesis sustantiva",
        "## Extracto o referencia verificable",
        "## Límites y sesgos de la fuente",
    ],
    "hallazgo": [
        "## Afirmación",
        "## Sustento y trazabilidad",
        "## Explicaciones rivales descartadas",
        "## Condiciones de validez y límites",
    ],
}


def extract_wikilink_id(raw_ref: str) -> str:
    """Normalize '[[E-202610-001]]', '[[H-ACME-01#Afirmación]]', or 'E-202610-001' to bare ID."""
    cleaned = raw_ref.strip()
    match = re.match(r"^\[\[([^\]#|]+)(?:[#|][^\]]*)?\]\]$", cleaned)
    if match:
        cleaned = match.group(1).strip()
    return Path(cleaned).stem if cleaned.endswith((".md", ".json")) else cleaned


def parse_raw_artifact(content: str, suffix: str = ".md") -> tuple[dict[str, Any], str]:
    """Return (metadata_dict, markdown_body) from either a JSON file or a frontmatter Markdown note."""
    if suffix.lower() == ".json":
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ContractValidationError(f"JSON inválido en artefacto: {exc}") from exc
        if not isinstance(data, dict):
            raise ContractValidationError("El artefacto JSON debe ser un objeto clave-valor en raíz.")
        return data, ""

    stripped = content.lstrip("\ufeff")
    if not stripped.startswith("---"):
        return {}, stripped

    try:
        post = frontmatter.loads(stripped)
    except Exception as exc:
        raise ContractValidationError(f"Frontmatter YAML malformado: {exc}") from exc
    return dict(post.metadata), post.content


def dump_markdown_note(metadata: dict[str, Any], body: str) -> str:
    """Serialize metadata + Markdown body cleanly with UTF-8 friendly YAML."""
    yaml_str = yaml.safe_dump(
        metadata,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    ).strip()
    clean_body = body.lstrip("\r\n")
    return f"---\n{yaml_str}\n---\n\n{clean_body.rstrip()}\n"


def validate_markdown_headings(tipo: str, body: str) -> None:
    """Ensure required level-2 Markdown headings are present for the given contract type."""
    required = REQUIRED_MARKDOWN_HEADINGS.get(tipo, [])
    if not required:
        return
    present_lines = {line.strip() for line in body.splitlines() if line.strip().startswith("## ")}
    missing = [heading for heading in required if heading not in present_lines]
    if missing:
        raise ContractValidationError(
            f"Faltan encabezados Markdown obligatorios para '{tipo}': {', '.join(missing)}"
        )


def validate_artifact_content(
    content: str,
    *,
    suffix: str = ".md",
    expected_tipo: str | None = None,
) -> tuple[BaseModel, dict[str, Any], str]:
    """Validate artifact content against its Pydantic v2 schema and mandatory Markdown sections."""
    metadata, body = parse_raw_artifact(content, suffix=suffix)
    tipo = metadata.get("tipo") or expected_tipo
    if not tipo or tipo not in CONTRACT_REGISTRY:
        raise ContractValidationError(
            f"Campo 'tipo' ausente o no reconocido ({tipo!r}). Tipos válidos: {sorted(CONTRACT_REGISTRY)}"
        )
    if expected_tipo and tipo != expected_tipo:
        raise ContractValidationError(
            f"Se esperaba artefacto tipo={expected_tipo!r}, pero se recibió tipo={tipo!r}."
        )

    model_cls = CONTRACT_REGISTRY[tipo]
    try:
        validated = model_cls.model_validate(metadata)
    except ValidationError as exc:
        raise ContractValidationError(f"Error de contrato Pydantic ({model_cls.__name__}):\n{exc}") from exc

    if suffix.lower() != ".json":
        validate_markdown_headings(tipo, body)

    return validated, metadata, body


def compute_finding_substantive_sha256(content_or_path: str | Path) -> str:
    """Compute SHA-256 of a Finding note's substantive content (excluding kernel-managed estado_qa)."""
    if isinstance(content_or_path, Path):
        raw_text = content_or_path.read_text(encoding="utf-8")
        suffix = content_or_path.suffix
    else:
        raw_text = content_or_path
        suffix = ".md"
    meta, body = parse_raw_artifact(raw_text, suffix=suffix)
    canonical_meta = {k: v for k, v in meta.items() if k != "estado_qa"}
    canonical = dump_markdown_note(canonical_meta, body).replace("\r\n", "\n")
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


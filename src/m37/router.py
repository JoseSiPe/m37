"""M37 (Move 37) — SPEC-02: Dynamic 5-Axis Model Router & Cross-Family QA Guardrails."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from m37.config import ModelCatalog, ModelCatalogEntry
from m37.schemas import ModelRoutingSpec, NodeContract, RigorLevel


class RoutingGuardrailError(ValueError):
    """Raised when a node's ModelRoutingSpec violates deterministic routing guardrails."""


@dataclass
class ResolvedNodeRouting:
    executor_alias: str
    executor_entry: ModelCatalogEntry
    qa_alias: str | None
    qa_entry: ModelCatalogEntry | None
    allow_surgical_edit: bool
    mono_provider_degraded: bool = False
    warnings: list[str] = field(default_factory=list)


def resolve_orchestrator_model(
    catalog: ModelCatalog,
    active_models: Mapping[str, ModelCatalogEntry],
    env: Mapping[str, str],
    *,
    orchestrator_override: str | None = None,
) -> tuple[str, ModelCatalogEntry]:
    """SPEC-02 §3.1: Resolve Orchestrator model (--orchestrator > M37_ORCHESTRATOR > catalog default > active fallback)."""
    if not active_models:
        raise RoutingGuardrailError(
            "Pool activo vacío: ninguna credencial de Modelos.yaml está configurada en el entorno (.env)."
        )

    explicit = (orchestrator_override or "").strip()
    if explicit and explicit.lower() not in ("deterministic", "determinista"):
        if explicit not in catalog.modelos:
            raise RoutingGuardrailError(
                f"El modelo orquestador solicitado {explicit!r} no existe en Modelos.yaml."
            )
        if explicit not in active_models:
            needed_env = catalog.modelos[explicit].credencial_env
            raise RoutingGuardrailError(
                f"El modelo orquestador {explicit!r} está inactivo (falta {needed_env} en .env)."
            )
        return explicit, active_models[explicit]

    preferred = env.get("M37_ORCHESTRATOR", "").strip() or catalog.orquestador_por_defecto
    if preferred in active_models:
        return preferred, active_models[preferred]

    # Deterministic fallback: highest (abstraccion_conceptual + ejecucion_formal), tie-broken by lower cost
    ranked = sorted(
        active_models.items(),
        key=lambda kv: (
            kv[1].capacidades.abstraccion_conceptual + kv[1].capacidades.ejecucion_formal,
            -kv[1].costo_usd_1m_out,
        ),
        reverse=True,
    )
    return ranked[0]


def validate_node_routing(
    node: NodeContract,
    catalog: ModelCatalog,
    active_models: Mapping[str, ModelCatalogEntry],
    rigor: RigorLevel,
    *,
    max_node_budget_usd: float = 25.0,
) -> ResolvedNodeRouting:
    """SPEC-02 §3.3: Enforce the 4 deterministic routing guardrails before running any node."""
    spec = node.enrutamiento_modelo
    warnings: list[str] = []

    # Guardrail 1: Active pool membership & context window fit
    if spec.modelo_ejecutor not in catalog.modelos:
        raise RoutingGuardrailError(
            f"Nodo {node.id}: modelo_ejecutor={spec.modelo_ejecutor!r} no existe en Modelos.yaml."
        )
    if spec.modelo_ejecutor not in active_models:
        needed_env = catalog.modelos[spec.modelo_ejecutor].credencial_env
        raise RoutingGuardrailError(
            f"Nodo {node.id}: modelo_ejecutor={spec.modelo_ejecutor!r} está inactivo (falta {needed_env} en .env)."
        )

    exec_entry = active_models[spec.modelo_ejecutor]
    if spec.contexto_estimado_tokens > exec_entry.ventana_max_tokens:
        raise RoutingGuardrailError(
            f"Nodo {node.id}: contexto_estimado_tokens ({spec.contexto_estimado_tokens}) excede "
            f"ventana_max_tokens de {spec.modelo_ejecutor!r} ({exec_entry.ventana_max_tokens})."
        )

    # Guardrail 4: Hard USD budget ceiling
    if spec.presupuesto_max_usd <= 0 or spec.presupuesto_max_usd > max_node_budget_usd:
        raise RoutingGuardrailError(
            f"Nodo {node.id}: presupuesto_max_usd ({spec.presupuesto_max_usd}) fuera del rango permitido "
            f"(0, {max_node_budget_usd}]."
        )

    # Guardrail 2: Cross-family Blind QA (familia_qa != familia_ejecutor)
    active_families = {entry.familia for entry in active_models.values()}
    mono_degraded = False
    qa_alias = spec.modelo_qa_critico
    qa_entry: ModelCatalogEntry | None = None

    requires_qa = rigor in ("L3", "L4") and node.perfil_herramientas != "editorial"
    if requires_qa and not qa_alias:
        raise RoutingGuardrailError(
            f"Nodo {node.id}: rigor {rigor} exige declarar modelo_qa_critico para perfil {node.perfil_herramientas!r}."
        )

    if qa_alias:
        if qa_alias not in catalog.modelos:
            raise RoutingGuardrailError(
                f"Nodo {node.id}: modelo_qa_critico={qa_alias!r} no existe en Modelos.yaml."
            )
        if qa_alias not in active_models:
            needed_qa_env = catalog.modelos[qa_alias].credencial_env
            raise RoutingGuardrailError(
                f"Nodo {node.id}: modelo_qa_critico={qa_alias!r} está inactivo (falta {needed_qa_env} en .env)."
            )
        qa_entry = active_models[qa_alias]

        if len(active_families) >= 2:
            if qa_entry.familia == exec_entry.familia:
                raise RoutingGuardrailError(
                    f"Violación de QA Cruzado en nodo {node.id}: familia_qa ({qa_entry.familia!r}, {qa_alias!r}) "
                    f"== familia_ejecutor ({exec_entry.familia!r}, {spec.modelo_ejecutor!r}) habiendo "
                    f"{len(active_families)} familias activas ({sorted(active_families)})."
                )
        else:
            mono_degraded = True
            warnings.append(
                f"MODO DEGRADADO MONO-PROVEEDOR en {node.id}: solo la familia {exec_entry.familia!r} está activa; "
                "el QA Ciego operará con aislamiento de contexto pero sin diversidad de proveedor."
            )

    # Guardrail 3: Surgical edit capability
    allow_surgical = bool(exec_entry.limites_operativos.permitir_edit_quirurgico)
    if node.requiere_edicion_quirurgica and not allow_surgical:
        warnings.append(
            f"Nodo {node.id}: {spec.modelo_ejecutor!r} tiene permitir_edit_quirurgico=False; "
            "edit_note/edit_code_file deshabilitados automáticamente a favor de reescritura completa."
        )

    return ResolvedNodeRouting(
        executor_alias=spec.modelo_ejecutor,
        executor_entry=exec_entry,
        qa_alias=qa_alias,
        qa_entry=qa_entry,
        allow_surgical_edit=allow_surgical,
        mono_provider_degraded=mono_degraded,
        warnings=warnings,
    )


def compute_episodic_rejection_rates(
    episodios_path: Path | None,
    *,
    dominio: str | None = None,
    metodo: str | None = None,
) -> dict[str, tuple[float, int]]:
    """SPEC-02 §3.2 #4: Return {model_alias: (rejection_rate_r1, sample_size)} for matching dominio or metodo."""
    if episodios_path is None or not episodios_path.is_file():
        return {}

    dom_norm = (dominio or "").strip().lower()
    met_norm = (metodo or "").strip().lower().strip("[]")

    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # [rejected_r1, total]
    for raw_line in episodios_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("evento") not in ("nodo_ejecutado", "cierre_nodo"):
            continue
        if ev.get("aprobado_r1") is None and ev.get("veredicto_qa_r1") is None:
            continue

        ev_dom = str(ev.get("dominio") or "").strip().lower()
        ev_met = str(ev.get("metodo") or "").strip().lower().strip("[]")
        if dom_norm or met_norm:
            matches_dom = bool(dom_norm and ev_dom and (ev_dom == dom_norm or ev_dom.startswith(dom_norm) or dom_norm.startswith(ev_dom)))
            matches_met = bool(met_norm and ev_met and ev_met == met_norm)
            if not (matches_dom or matches_met):
                continue

        alias = str(ev.get("modelo") or "").strip()
        if not alias:
            continue
        approved = ev.get("aprobado_r1")
        if approved is None:
            verdict = str(ev.get("veredicto_qa_r1") or "").strip().lower()
            approved = verdict in ("aceptar", "aceptar_con_correcciones", "aprobado")
        counts[alias][1] += 1
        if not approved:
            counts[alias][0] += 1

    return {
        alias: (round(rej / total, 4), total)
        for alias, (rej, total) in counts.items()
        if total >= 1
    }


def recommend_routing_for_axis(
    active_models: Mapping[str, ModelCatalogEntry],
    eje_dominante: LiteralAxis | str,
    *,
    rigor: RigorLevel = "L3",
    dominio: str | None = None,
    metodo: str | None = None,
    episodios_path: Path | None = None,
    contexto_estimado_tokens: int = 24000,
    presupuesto_max_usd: float = 0.75,
    max_turns: int = 14,
    require_qa: bool = True,
) -> ModelRoutingSpec:
    """SPEC-02 §3.2: Multi-criteria routing (axis, 60% context margin, L1-L4 cost/rigor policy, >35% episodic QA penalty)."""
    eligible = [
        (alias, entry)
        for alias, entry in active_models.items()
        if entry.ventana_max_tokens >= contexto_estimado_tokens
    ]
    if not eligible:
        raise RoutingGuardrailError(
            f"Ningún modelo activo soporta contexto_estimado_tokens={contexto_estimado_tokens}."
        )

    # Rule 2 (SPEC-02 §3.2 #2): Prefer candidates where estimated context <= 60% of ventana_max_tokens
    comfortable = [
        (alias, entry)
        for alias, entry in eligible
        if contexto_estimado_tokens <= int(entry.ventana_max_tokens * 0.60)
    ]
    pool_after_ctx = comfortable if comfortable else eligible

    # Rule 4 (SPEC-02 §3.2 #4): Episodic penalty if R1 QA rejection rate > 35% for this dominio/metodo
    rejection_stats = compute_episodic_rejection_rates(
        episodios_path,
        dominio=dominio,
        metodo=metodo,
    )
    penalized_aliases = {
        alias: rate
        for alias, (rate, total) in rejection_stats.items()
        if total >= 1 and rate > 0.35
    }
    unpenalized = [(a, e) for a, e in pool_after_ctx if a not in penalized_aliases]
    candidate_pool = unpenalized if unpenalized else pool_after_ctx

    axis_name = str(eje_dominante)

    # Rule 3 (SPEC-02 §3.2 #3): Cost-rigor elasticity (L1..L4)
    if rigor == "L1":
        # L1 exploratory: minimize cost per token first among capable models (score >= 3), then axis score
        ranked_exec = sorted(
            candidate_pool,
            key=lambda kv: (
                1 if getattr(kv[1].capacidades, axis_name, 3) >= 3 else 0,
                -kv[1].costo_usd_1m_out,
                -kv[1].costo_usd_1m_in,
                getattr(kv[1].capacidades, axis_name, 3),
            ),
            reverse=True,
        )
    elif rigor == "L2":
        # L2 internal: economical models on ingestion/editorial, top capability on synthesis/formal execution
        if axis_name in ("extraccion_fiel", "coherencia_documental"):
            ranked_exec = sorted(
                candidate_pool,
                key=lambda kv: (
                    1 if getattr(kv[1].capacidades, axis_name, 3) >= 4 else 0,
                    -kv[1].costo_usd_1m_out,
                    getattr(kv[1].capacidades, axis_name, 3),
                ),
                reverse=True,
            )
        else:
            ranked_exec = sorted(
                candidate_pool,
                key=lambda kv: (
                    getattr(kv[1].capacidades, axis_name, 3),
                    -kv[1].costo_usd_1m_out,
                    kv[1].capacidades.ejecucion_formal,
                ),
                reverse=True,
            )
    else:
        # L3 / L4 high rigor: demand capability >= 4 on dominant axis, highest score, tie-break by formal execution & lower cost
        ranked_exec = sorted(
            candidate_pool,
            key=lambda kv: (
                1 if getattr(kv[1].capacidades, axis_name, 3) >= 4 else 0,
                getattr(kv[1].capacidades, axis_name, 3),
                kv[1].capacidades.ejecucion_formal,
                -kv[1].costo_usd_1m_out,
            ),
            reverse=True,
        )

    exec_alias, exec_entry = ranked_exec[0]

    qa_alias: str | None = None
    just_qa: str | None = None
    if require_qa:
        cross_family = [(a, e) for a, e in eligible if e.familia != exec_entry.familia]
        qa_pool = cross_family if cross_family else eligible
        ranked_qa = sorted(
            qa_pool,
            key=lambda kv: (kv[1].capacidades.critica_adversarial, -kv[1].costo_usd_1m_out),
            reverse=True,
        )
        qa_alias, qa_entry = ranked_qa[0]
        just_qa = (
            f"Familia distinta ({qa_entry.familia} != {exec_entry.familia}) con critica_adversarial={qa_entry.capacidades.critica_adversarial}/5."
            if cross_family
            else f"Modo mono-proveedor ({qa_entry.familia}); máxima critica_adversarial disponible ({qa_entry.capacidades.critica_adversarial}/5)."
        )

    axis_score = getattr(exec_entry.capacidades, axis_name, 3)
    notes: list[str] = [
        f"Rigor {rigor}, eje {axis_name}={axis_score}/5 (familia={exec_entry.familia}, costo_out=${exec_entry.costo_usd_1m_out}/1M)."
    ]
    if penalized_aliases and exec_alias not in penalized_aliases:
        avoided = ", ".join(f"{k} ({int(v * 100)}% rechazo R1)" for k, v in sorted(penalized_aliases.items()))
        notes.append(f"Penalización episódica >35% evitó: {avoided}.")

    return ModelRoutingSpec(
        eje_dominante=eje_dominante,  # type: ignore[arg-type]
        contexto_estimado_tokens=contexto_estimado_tokens,
        modelo_ejecutor=exec_alias,
        justificacion_ejecutor=" ".join(notes),
        modelo_qa_critico=qa_alias,
        justificacion_qa=just_qa,
        presupuesto_max_usd=presupuesto_max_usd,
        max_turns=max_turns,
    )


LiteralAxis = str


def summarize_episodic_level2(episodios_path: Path) -> list[dict[str, Any]]:
    """SPEC-05 §3.1: 0-LLM Python summary of empirical model approval rates and costs from episodios.jsonl."""
    if not episodios_path.is_file():
        return []

    buckets: dict[tuple[str, str, str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "ejecuciones": 0,
            "aprobados_r1": 0,
            "costo_total_usd": 0.0,
            "turnos_totales": 0,
            "tokens_in_totales": 0,
            "tokens_out_totales": 0,
        }
    )

    for raw_line in episodios_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("evento") not in ("nodo_ejecutado", "cierre_nodo"):
            continue
        key = (
            str(ev.get("modelo", "desconocido")),
            str(ev.get("perfil", "desconocido")),
            str(ev.get("dominio") or "general"),
            str(ev.get("metodo") or "ninguno"),
        )
        b = buckets[key]
        b["ejecuciones"] += 1
        if ev.get("aprobado_r1") is True or ev.get("veredicto_qa_r1") in ("aceptar", "aceptar_con_correcciones"):
            b["aprobados_r1"] += 1
        b["costo_total_usd"] += float(ev.get("costo_usd", 0.0))
        b["turnos_totales"] += int(ev.get("turnos", 0))
        b["tokens_in_totales"] += int(ev.get("tokens_in", 0))
        b["tokens_out_totales"] += int(ev.get("tokens_out", 0))

    summary: list[dict[str, Any]] = []
    for (modelo, perfil, dominio, metodo), stats in sorted(buckets.items()):
        n = stats["ejecuciones"]
        tasa_aprob = round(stats["aprobados_r1"] / n, 3) if n else 0.0
        summary.append(
            {
                "modelo": modelo,
                "perfil": perfil,
                "dominio": dominio,
                "metodo": metodo,
                "nodos_ejecutados": n,
                "tasa_aprobacion_r1": tasa_aprob,
                "tasa_rechazo_r1": round(1.0 - tasa_aprob, 3) if n else 0.0,
                "costo_medio_usd": round(stats["costo_total_usd"] / n, 4) if n else 0.0,
                "turnos_medios": round(stats["turnos_totales"] / n, 1) if n else 0.0,
                "tokens_in_totales": stats["tokens_in_totales"],
                "tokens_out_totales": stats["tokens_out_totales"],
            }
        )
    return summary

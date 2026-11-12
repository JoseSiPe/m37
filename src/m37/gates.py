"""M37 (Move 37) — Puertas humanas 1 y 2: evaluación, sellado y exigencia de operador interactivo.

Una puerta solo es válida si cumple las tres condiciones:
  1. `puerta_N_aprobada: true`
  2. `puerta_N_sha256` presente
  3. el hash coincide con el contenido sustantivo actual

Marcar `puerta_N_aprobada: true` a mano en el Markdown NO aprueba nada: sin sello no hay puerta.
El sellado se hace con `m37 approve`, que exige una terminal interactiva. Ningún agente (API o harness)
recibe una herramienta para aprobar puertas.

Límite conocido y declarado: un agente con shell y permisos del usuario puede, deliberadamente, invocar la
API de Python o falsificar archivos. Estas puertas impiden la auto-aprobación por deriva o por instrucción
del propio producto; no son un control criptográfico contra un proceso con los privilegios del operador.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from m37.config import WorkspacePaths
from m37.guards import write_utf8_lf
from m37.schemas import (
    BriefContract,
    NodeContract,
    PlanContract,
    compute_brief_substantive_sha256,
    compute_plan_dag_sha256,
    dump_markdown_note,
    extract_wikilink_id,
    parse_raw_artifact,
    validate_artifact_content,
    validate_dag_topological_order,
)


class HumanGateError(PermissionError):
    """Raised when a human gate is missing, unsealed, stale, or approval is attempted without a human at a terminal."""


def require_interactive_human(action: str) -> None:
    """Fail closed unless stdin and stdout are attached to an interactive terminal."""
    stdin_tty = bool(getattr(sys.stdin, "isatty", lambda: False)())
    stdout_tty = bool(getattr(sys.stdout, "isatty", lambda: False)())
    if not (stdin_tty and stdout_tty):
        raise HumanGateError(
            f"{action} es una decisión humana y exige una terminal interactiva. "
            "No puede ejecutarse desde un agente, un pipe ni un script."
        )


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def evaluate_gate_1(brief_meta: dict[str, Any], brief_body: str) -> tuple[bool, str]:
    """Return (valid, reason) for Puerta 1 against the live Brief content."""
    if not brief_meta.get("puerta_1_aprobada"):
        return False, "Puerta 1 pendiente: revisa 00_Brief.md y ejecuta `m37 approve <encargo> --gate 1`."
    sealed = brief_meta.get("puerta_1_sha256")
    if not sealed:
        return False, (
            "Puerta 1 sin sello: `puerta_1_aprobada: true` escrito a mano no aprueba nada. "
            "Ejecuta `m37 approve <encargo> --gate 1`."
        )
    if sealed != compute_brief_substantive_sha256(brief_meta, brief_body):
        return False, (
            "Puerta 1 invalidada: el contenido sustantivo de 00_Brief.md cambió después de aprobarse. "
            "Revísalo y vuelve a ejecutar `m37 approve <encargo> --gate 1`."
        )
    return True, "Puerta 1 vigente."


def plan_node_paths(enc_dir: Path, plan_meta: dict[str, Any]) -> list[Path]:
    return [enc_dir / "Nodos" / f"{extract_wikilink_id(str(n))}.md" for n in (plan_meta.get("nodos") or [])]


def evaluate_gate_2(enc_dir: Path) -> tuple[bool, str]:
    """Return (valid, reason) for Puerta 2 against the live plan and node definitions."""
    plan_path = enc_dir / "01_Plan.md"
    if not plan_path.is_file():
        return False, "Puerta 2 pendiente: no existe 01_Plan.md."
    plan_meta, plan_body = parse_raw_artifact(plan_path.read_text(encoding="utf-8"), suffix=".md")
    if not plan_meta.get("puerta_2_aprobada"):
        return False, "Puerta 2 pendiente: revisa 01_Plan.md y Nodos/ y ejecuta `m37 approve <encargo> --gate 2`."
    sealed = plan_meta.get("puerta_2_sha256")
    if not sealed:
        return False, (
            "Puerta 2 sin sello: `puerta_2_aprobada: true` escrito a mano no aprueba nada. "
            "Ejecuta `m37 approve <encargo> --gate 2`."
        )
    live = compute_plan_dag_sha256(plan_meta, plan_body, plan_node_paths(enc_dir, plan_meta))
    if sealed != live:
        return False, (
            "Puerta 2 invalidada: 01_Plan.md o la definición de Nodos/ (objetivo, perfil, ruteo, presupuesto o "
            "dependencias) cambió después de aprobarse. Revísalo y vuelve a ejecutar `m37 approve <encargo> --gate 2`."
        )
    return True, "Puerta 2 vigente."


def assert_gate_1(enc_dir: Path) -> BriefContract:
    brief_path = enc_dir / "00_Brief.md"
    if not brief_path.is_file():
        raise FileNotFoundError(f"No existe {brief_path}")
    brief_obj, brief_meta, brief_body = validate_artifact_content(
        brief_path.read_text(encoding="utf-8"),
        suffix=".md",
        expected_tipo="brief",
    )
    assert isinstance(brief_obj, BriefContract)
    ok, reason = evaluate_gate_1(brief_meta, brief_body)
    if not ok:
        raise HumanGateError(reason)
    return brief_obj


def assert_gate_2(enc_dir: Path) -> None:
    ok, reason = evaluate_gate_2(enc_dir)
    if not ok:
        raise HumanGateError(reason)


def approve_gate(
    paths: WorkspacePaths,
    engagement_id: str,
    gate: int,
    *,
    approver: str = "socio_humano",
) -> dict[str, Any]:
    """Seal Puerta 1 or 2 with the live content hash, approver and UTC timestamp.

    Programmatic entry point (tests, `m37 approve`). The CLI wrapper is the one that demands an interactive terminal.
    """
    who = approver.strip()
    if len(who) < 2:
        raise ValueError("El aprobador debe identificarse con al menos 2 caracteres.")
    enc_dir = paths.engagement_dir(engagement_id)

    if gate == 1:
        brief_path = enc_dir / "00_Brief.md"
        if not brief_path.is_file():
            raise FileNotFoundError(f"No existe {brief_path}")
        _, b_meta, b_body = validate_artifact_content(
            brief_path.read_text(encoding="utf-8"),
            suffix=".md",
            expected_tipo="brief",
        )
        sha = compute_brief_substantive_sha256(b_meta, b_body)
        b_meta["puerta_1_aprobada"] = True
        b_meta["puerta_1_sha256"] = sha
        b_meta["puerta_1_aprobado_por"] = who
        b_meta["puerta_1_fecha_utc"] = _now_utc_iso()
        write_utf8_lf(brief_path, dump_markdown_note(b_meta, b_body))
        return {"encargo": engagement_id, "puerta": 1, "sha256": sha, "aprobado_por": who}

    if gate == 2:
        assert_gate_1(enc_dir)
        plan_path = enc_dir / "01_Plan.md"
        if not plan_path.is_file():
            raise FileNotFoundError(f"No existe {plan_path}; ejecuta `m37 plan` antes de aprobar Puerta 2.")
        plan_obj, p_meta, p_body = validate_artifact_content(
            plan_path.read_text(encoding="utf-8"),
            suffix=".md",
            expected_tipo="plan",
        )
        assert isinstance(plan_obj, PlanContract)
        node_paths = plan_node_paths(enc_dir, p_meta)
        dag_pairs: list[tuple[str, list[str]]] = []
        for np in node_paths:
            if not np.is_file():
                raise FileNotFoundError(f"El plan referencia un nodo que no existe en disco: {np.name}")
            n_obj, _, _ = validate_artifact_content(np.read_text(encoding="utf-8"), suffix=".md", expected_tipo="nodo")
            assert isinstance(n_obj, NodeContract)
            dag_pairs.append((n_obj.id, list(n_obj.depende_de)))
        validate_dag_topological_order(dag_pairs)
        sha = compute_plan_dag_sha256(p_meta, p_body, node_paths)
        p_meta["puerta_2_aprobada"] = True
        p_meta["puerta_2_sha256"] = sha
        p_meta["puerta_2_aprobado_por"] = who
        p_meta["puerta_2_fecha_utc"] = _now_utc_iso()
        write_utf8_lf(plan_path, dump_markdown_note(p_meta, p_body))
        return {"encargo": engagement_id, "puerta": 2, "sha256": sha, "aprobado_por": who}

    raise ValueError("Solo existen las Puertas 1 y 2 como estado sellado (usa --gate 1 o --gate 2).")

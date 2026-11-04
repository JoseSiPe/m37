"""M37 Extension Validator: SDD + EARS + TDD + AppSec (`validador_plugin: sdd_codigo`)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from m37.guards import EXCLUDED_CODE_DIRS
from m37.verify import verify_sdd_code_derivation

if TYPE_CHECKING:
    from m37.config import WorkspacePaths
    from m37.schemas import DerivationContract

UNSAFE_CODE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("shell_true", re.compile(r"\bshell\s*=\s*True\b")),
    ("unsafe_pickle", re.compile(r"\bpickle\.loads?\s*\(")),
    ("unsafe_yaml_load", re.compile(r"\byaml\.load\s*\([^)]*\)(?!.*Loader)")),
)


def validate_derivation(
    paths: WorkspacePaths,
    engagement_id: str,
    deriv: DerivationContract,
) -> list[str]:
    """Run kernel SDD receipt + 100% RF-* test coverage check plus static AppSec hygiene scan."""
    errors = list(verify_sdd_code_derivation(paths, engagement_id, deriv))
    code_dir = (paths.codigo_root / engagement_id).resolve()
    if not code_dir.is_dir():
        return errors

    for py_file in sorted(code_dir.rglob("*.py")):
        rel = py_file.relative_to(code_dir)
        if any(part in EXCLUDED_CODE_DIRS for part in rel.parts):
            continue
        if py_file.name.startswith("test_") or "tests" in rel.parts:
            continue
        content = py_file.read_text(encoding="utf-8", errors="replace")
        for label, pat in UNSAFE_CODE_PATTERNS:
            if pat.search(content):
                errors.append(
                    f"Riesgo AppSec estático ({label}) detectado en codigo/{engagement_id}/{rel.as_posix()}."
                )
    return errors

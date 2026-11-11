"""M37 (Move 37) — SPEC-01 & SPEC-02: Workspace Resolution, .env & Model Catalog Loader."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import yaml
from pydantic import BaseModel, Field

from m37.schemas import RigorLevel


class PrivacyGateError(PermissionError):
    """Raised when attempting L3/L4 cloud execution without M37_DATA_PRIVACY_ACK=paid_billing."""


class GitLeakSafetyError(RuntimeError):
    """Raised when M37_VAULT points inside the shareable m37-core repository."""


class ModelCapabilities(BaseModel):
    extraccion_fiel: int = Field(ge=1, le=5)
    abstraccion_conceptual: int = Field(ge=1, le=5)
    coherencia_documental: int = Field(ge=1, le=5)
    ejecucion_formal: int = Field(ge=1, le=5)
    critica_adversarial: int = Field(ge=1, le=5)


class ModelOperationalLimits(BaseModel):
    permitir_edit_quirurgico: bool = False
    temperatura_recomendada: float = Field(ge=0.0, le=2.0, default=0.2)
    # Tope de tokens de salida por turno enviado al proveedor (C1: el runner lo lee en la ruta real de LiteLLM).
    max_output_tokens: int = Field(gt=0, default=8192)


class ModelCatalogEntry(BaseModel):
    litellm_id: str
    familia: str
    credencial_env: str | None = None
    ventana_max_tokens: int = Field(gt=0)
    costo_usd_1m_in: float = Field(ge=0.0)
    costo_usd_1m_out: float = Field(ge=0.0)
    capacidades: ModelCapabilities
    limites_operativos: ModelOperationalLimits = Field(default_factory=ModelOperationalLimits)
    notas_orquestador: str = ""


class ModelCatalog(BaseModel):
    version_catalogo: str
    orquestador_por_defecto: str
    modelos: dict[str, ModelCatalogEntry] = Field(min_length=1)


@dataclass(frozen=True)
class WorkspacePaths:
    workspace_root: Path
    vault_root: Path
    analisis_root: Path
    codigo_root: Path
    datos_root: Path
    render_root: Path
    referencias_root: Path

    @classmethod
    def from_root(cls, root: Path | str) -> WorkspacePaths:
        resolved = Path(root).expanduser().resolve()
        # Accept either the workspace root (containing vault/) or vault/ directly
        if resolved.name == "vault" and (resolved / "00_Sistema").exists():
            workspace_root = resolved.parent
            vault_root = resolved
        else:
            workspace_root = resolved
            vault_root = workspace_root / "vault"
        return cls(
            workspace_root=workspace_root,
            vault_root=vault_root,
            analisis_root=workspace_root / "analisis",
            codigo_root=workspace_root / "codigo",
            datos_root=workspace_root / "datos",
            render_root=workspace_root / "render",
            referencias_root=workspace_root / "referencias",
        )

    def engagement_dir(self, engagement_id: str) -> Path:
        return self.vault_root / "20_Encargos" / engagement_id

    def episodios_path(self) -> Path:
        return self.vault_root / "90_Bitacora" / "episodios.jsonl"

    def modelos_yaml_path(self) -> Path:
        return self.vault_root / "00_Sistema" / "Modelos.yaml"

    def duckdb_index_path(self) -> Path:
        return self.vault_root / ".indice" / "grafo.duckdb"


def _is_engine_checkout(candidate: Path) -> bool:
    """A real engine checkout has all three; a stray `starter_vault/` higher up the tree is not enough."""
    return (
        (candidate / "starter_vault" / "00_Sistema").is_dir()
        and (candidate / "extensions").is_dir()
        and (candidate / "src" / "m37").is_dir()
    )


def get_core_repo_root() -> Path:
    """Locate the engine root that holds `starter_vault/` and `extensions/`.

    Source checkout / editable install: the repository root.
    Wheel install: a private resource directory materialised from the packaged data (see `_packaged_resource_root`).
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if _is_engine_checkout(parent):
            return parent
    packaged = _packaged_resource_root()
    if packaged is not None:
        return packaged
    return here.parents[2]


def _packaged_resource_root() -> Path | None:
    """Wheel layout: m37/_starter_vault and m37/_extensions sit next to this file.

    Callers expect `<root>/starter_vault` and `<root>/extensions`, so expose them under a per-user cache directory
    keyed by version (site-packages may be read-only).
    """
    pkg_dir = Path(__file__).resolve().parent
    starter_src = pkg_dir / "_starter_vault"
    ext_src = pkg_dir / "_extensions"
    if not (starter_src / "00_Sistema").is_dir() or not ext_src.is_dir():
        return None
    import shutil

    from m37 import __version__

    base = Path(os.environ.get("M37_RESOURCE_CACHE") or (Path.home() / ".cache" / "m37")).expanduser()
    root = base / f"resources-{__version__}"
    marker = root / ".complete"
    if not marker.is_file():
        for src, name in ((starter_src, "starter_vault"), (ext_src, "extensions")):
            dest = root / name
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        marker.write_text("ok\n", encoding="utf-8")
    return root


def get_starter_vault_dir() -> Path:
    return get_core_repo_root() / "starter_vault"


def parse_dotenv_file(dotenv_path: Path) -> dict[str, str]:
    """Minimal zero-dependency .env parser supporting comments and quoted values."""
    result: dict[str, str] = {}
    if not dotenv_path.is_file():
        return result
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and ((val[0] == val[-1] == '"') or (val[0] == val[-1] == "'")):
            val = val[1:-1]
        result[key] = val
    return result


def load_effective_env(
    dotenv_path: Path | None = None,
    extra_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Merge .env file values with os.environ and explicit overrides (explicit > os.environ > .env)."""
    candidates = [dotenv_path] if dotenv_path else [Path.cwd() / ".env", get_core_repo_root() / ".env"]
    merged: dict[str, str] = {}
    for candidate in candidates:
        if candidate and candidate.is_file():
            merged.update(parse_dotenv_file(candidate))
            break
    merged.update(os.environ)
    if extra_env:
        merged.update(extra_env)
    return merged


def extract_secret_values(env: Mapping[str, str]) -> tuple[str, ...]:
    """Collect non-trivial secret values from env to prevent accidental leakage into code or logs."""
    secret_suffixes = ("_KEY", "_TOKEN", "_SECRET", "_PASSWORD")
    secrets: list[str] = []
    for k, v in env.items():
        if any(k.upper().endswith(sfx) for sfx in secret_suffixes):
            cleaned = v.strip()
            if len(cleaned) >= 8:
                secrets.append(cleaned)
    return tuple(sorted(set(secrets), key=len, reverse=True))


def resolve_workspace_paths(
    vault_override: Path | str | None = None,
    env: Mapping[str, str] | None = None,
) -> WorkspacePaths:
    effective = env if env is not None else load_effective_env()
    raw = str(vault_override) if vault_override else effective.get("M37_VAULT", "")
    if not raw:
        raise ValueError(
            "No se definió el workspace. Pasa --vault <ruta> o configura M37_VAULT en tu archivo .env."
        )
    return WorkspacePaths.from_root(raw)


def load_model_catalog(modelos_yaml: Path) -> ModelCatalog:
    if not modelos_yaml.is_file():
        raise FileNotFoundError(f"No se encontró el catálogo de modelos en {modelos_yaml}")
    raw = yaml.safe_load(modelos_yaml.read_text(encoding="utf-8"))
    return ModelCatalog.model_validate(raw)


def filter_active_models(
    catalog: ModelCatalog,
    env: Mapping[str, str],
) -> dict[str, ModelCatalogEntry]:
    """Return only models whose credencial_env is non-empty in env (or null for local models)."""
    active: dict[str, ModelCatalogEntry] = {}
    for alias, entry in catalog.modelos.items():
        if entry.credencial_env is None or env.get(entry.credencial_env, "").strip():
            active[alias] = entry
    return active


def check_privacy_gate(rigor: RigorLevel, env: Mapping[str, str]) -> None:
    """SPEC-01 §5: Abort L3/L4 execution before sending any token if paid_billing is not acknowledged."""
    ack = env.get("M37_DATA_PRIVACY_ACK", "demo_only").strip()
    if rigor in ("L3", "L4") and ack != "paid_billing":
        raise PrivacyGateError(
            f"BLOQUEO DE PRIVACIDAD ({rigor}): M37_DATA_PRIVACY_ACK={ack!r}. "
            "Los encargos L3/L4 de clientes reales exigen APIs con facturación vinculada "
            "(sin entrenamiento sobre prompts) y declarar M37_DATA_PRIVACY_ACK=paid_billing en .env."
        )


def _git_ignores_path(repo_root: Path, target: Path) -> bool:
    """True only when git itself confirms that `target` is ignored inside `repo_root`."""
    try:
        proc = subprocess.run(
            ["git", "check-ignore", "-q", str(target)],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def check_git_leak_safety(workspace_root: Path, core_repo_root: Path | None = None) -> tuple[bool, str]:
    """SPEC-01 §5: Ensure M37_VAULT is not inside the engine repository tree.

    Fail-closed (M6): a workspace inside a git-tracked engine checkout raises unless git confirms the
    path is ignored. The decision no longer depends on how the remote happens to be named.
    """
    core_root = (core_repo_root or get_core_repo_root()).resolve()
    ws_resolved = workspace_root.resolve()
    if ws_resolved == core_root or core_root in ws_resolved.parents:
        msg = (
            f"ADVERTENCIA DE FUGA GIT: El workspace ({ws_resolved}) está dentro del árbol del motor M37 ({core_root})."
        )
        is_git_checkout = (core_root / ".git").exists()
        if is_git_checkout and (ws_resolved == core_root or not _git_ignores_path(core_root, ws_resolved)):
            raise GitLeakSafetyError(
                msg + " El repositorio está bajo git y la ruta no está ignorada; mueve el workspace fuera del repositorio."
            )
        return False, msg
    return True, "Workspace físicamente separado del motor M37."

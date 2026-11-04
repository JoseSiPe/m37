"""M37 Extension Validator Example: Minimum Sample Size (`validador_plugin: muestra_minima_ejemplo`)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from m37.config import WorkspacePaths
    from m37.schemas import DerivationContract


def validate_derivation(
    paths: WorkspacePaths,
    engagement_id: str,
    deriv: DerivationContract,
) -> list[str]:
    """Example domain plugin checking n_muestra >= n_minimo declared in metadatos_metodo."""
    n_min = int(deriv.metadatos_metodo.get("n_minimo", 30))
    n_obs = deriv.salidas.get("n_muestra")
    if n_obs is None:
        return [f"La Derivación {deriv.id} debe reportar salidas['n_muestra'] (mínimo requerido: {n_min})."]
    if int(n_obs) < n_min:
        return [f"Muestra insuficiente en {deriv.id}: n_muestra={n_obs} < n_minimo={n_min}."]
    return []

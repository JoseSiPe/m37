"""Shared fixtures for the M37 test suite."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _legacy_shared_workspace_compute(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Most legacy tests build several clients inside one throwaway workspace and still run the process sandbox.

    The kernel now refuses that by default (C2). Tests that exercise the refusal itself opt out with
    `@pytest.mark.strict_isolation`.
    """
    if request.node.get_closest_marker("strict_isolation") is None:
        monkeypatch.setenv("M37_ALLOW_SHARED_WORKSPACE_COMPUTE", "1")
    else:
        monkeypatch.delenv("M37_ALLOW_SHARED_WORKSPACE_COMPUTE", raising=False)
    # Never let a developer's real sandbox configuration leak into the suite.
    monkeypatch.delenv("M37_SANDBOX_ALLOW_FFI", raising=False)
    monkeypatch.setenv("M37_SANDBOX_BACKEND", "process")


@pytest.fixture
def human_at_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate an operator at an interactive terminal for CLI commands that demand one."""
    from m37 import gates

    monkeypatch.setattr(gates, "require_interactive_human", lambda action: None)

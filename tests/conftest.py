"""Shared test setup.

ADR 0008 made startup fail-closed: Settings.from_env() refuses to
build an app with neither AUTH_ISSUER nor the explicit dev opt-out.
Tests are not deployments, so the default here is the explicit dev
opt-out (AUTH_DISABLED=true) with no issuer configured — tests that
exercise auth set the AUTH_* variables themselves with monkeypatch,
which overrides this fixture. Do not weaken the fail-closed rule in
settings.py to accommodate tests; set the variables here instead.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _auth_disabled_by_default(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "true")
    for var in ("AUTH_ISSUER", "AUTH_AUDIENCE", "AUTH_JWKS_URL"):
        monkeypatch.delenv(var, raising=False)
    yield

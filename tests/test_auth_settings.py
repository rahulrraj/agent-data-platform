"""Settings parsing for ADR 0008 auth variables (fail-closed startup).

The conftest fixture sets AUTH_DISABLED=true by default; tests here
that need auth on (or the fail-closed error) override it with
monkeypatch, which runs after the fixture.
"""

from __future__ import annotations

import pytest

from agent_api.config.settings import Settings

ISSUER = "https://example.okta.com"
AUDIENCE = "test-audience"


def _enable_auth(monkeypatch):
    monkeypatch.setenv("AUTH_ISSUER", ISSUER)
    monkeypatch.setenv("AUTH_AUDIENCE", AUDIENCE)
    monkeypatch.delenv("AUTH_DISABLED", raising=False)


def test_auth_vars_parsed(monkeypatch):
    _enable_auth(monkeypatch)
    monkeypatch.setenv("AUTH_JWKS_URL", "https://example.okta.com/oauth2/v1/keys")
    settings = Settings.from_env()
    assert settings.auth_issuer == ISSUER
    assert settings.auth_audience == AUDIENCE
    assert settings.auth_jwks_url == "https://example.okta.com/oauth2/v1/keys"
    assert settings.auth_disabled is False


def test_auth_jwks_url_defaults_empty(monkeypatch):
    _enable_auth(monkeypatch)
    assert Settings.from_env().auth_jwks_url == ""


def test_fail_closed_without_issuer_or_opt_out(monkeypatch):
    monkeypatch.delenv("AUTH_DISABLED", raising=False)
    monkeypatch.delenv("AUTH_ISSUER", raising=False)
    with pytest.raises(ValueError, match="AUTH_ISSUER.*AUTH_DISABLED"):
        Settings.from_env()


def test_dev_opt_out_allows_no_issuer(monkeypatch):
    monkeypatch.setenv("AUTH_DISABLED", "true")
    monkeypatch.delenv("AUTH_ISSUER", raising=False)
    settings = Settings.from_env()
    assert settings.auth_disabled is True
    assert settings.auth_issuer == ""
    assert settings.auth_audience == ""


def test_auth_disabled_only_exact_true_counts(monkeypatch):
    # "True", "1", "yes" do not opt out: fail-closed still applies.
    monkeypatch.setenv("AUTH_DISABLED", "True")
    monkeypatch.delenv("AUTH_ISSUER", raising=False)
    with pytest.raises(ValueError, match="AUTH_DISABLED"):
        Settings.from_env()


def test_issuer_must_be_https_url(monkeypatch):
    monkeypatch.delenv("AUTH_DISABLED", raising=False)
    for bad in ("http://example.okta.com", "example.okta.com", "https://", "ftp://example.okta.com"):
        monkeypatch.setenv("AUTH_ISSUER", bad)
        monkeypatch.setenv("AUTH_AUDIENCE", AUDIENCE)
        with pytest.raises(ValueError, match="AUTH_ISSUER"):
            Settings.from_env()


def test_audience_required_when_issuer_set(monkeypatch):
    monkeypatch.delenv("AUTH_DISABLED", raising=False)
    monkeypatch.setenv("AUTH_ISSUER", ISSUER)
    monkeypatch.delenv("AUTH_AUDIENCE", raising=False)
    with pytest.raises(ValueError, match="AUTH_AUDIENCE"):
        Settings.from_env()
    monkeypatch.setenv("AUTH_AUDIENCE", "   ")
    with pytest.raises(ValueError, match="AUTH_AUDIENCE"):
        Settings.from_env()


def test_audience_without_issuer_is_allowed_but_inert(monkeypatch):
    # An audience alone does not enable auth; the opt-out still governs.
    monkeypatch.setenv("AUTH_DISABLED", "true")
    monkeypatch.setenv("AUTH_AUDIENCE", AUDIENCE)
    settings = Settings.from_env()
    assert settings.auth_audience == AUDIENCE
    assert settings.auth_disabled is True


def test_bad_jwks_url_rejected(monkeypatch):
    _enable_auth(monkeypatch)
    monkeypatch.setenv("AUTH_JWKS_URL", "not-a-url")
    with pytest.raises(ValueError, match="AUTH_JWKS_URL"):
        Settings.from_env()


def test_auth_values_are_stripped(monkeypatch):
    monkeypatch.setenv("AUTH_ISSUER", f"  {ISSUER}  ")
    monkeypatch.setenv("AUTH_AUDIENCE", f" {AUDIENCE} ")
    monkeypatch.delenv("AUTH_DISABLED", raising=False)
    settings = Settings.from_env()
    assert settings.auth_issuer == ISSUER
    assert settings.auth_audience == AUDIENCE

"""Generic OIDC JWT validator (<Technology><PortName>, §2).

Validates RS256 JWTs against a cached JWKS: signature, issuer,
audience (the ``aud`` claim may be a string or a list), expiry, and a
non-empty ``sub``. Provider-agnostic — issuer, audience, and JWKS
location come from configuration only; no provider-specific literals
appear here.

Key fetching: the JWKS is fetched once and cached for
``jwks_ttl_seconds`` (default 1 hour), never per request. On an
unknown ``kid`` the JWKS is re-fetched once (key rotation) before
failing. When no explicit JWKS URL is given, the URL is derived from
the issuer via OIDC discovery (``/.well-known/openid-configuration``);
the discovery document is fetched once and its ``jwks_uri`` cached for
the validator's lifetime.

The fetch source is injectable (a ``fetcher`` callable taking a URL and
returning the parsed JSON document) so tests stub it — no network in
tests, and no live identity-provider calls anywhere in this module.
The default fetcher uses httpx with ``trust_env=False`` (the sandbox
proxy values break httpx parsing; see AGENTS.md).
"""

from __future__ import annotations

import json
import threading
import time
from typing import Callable

import httpx
import jwt
from jwt.algorithms import RSAAlgorithm

from agent_api.ports.token_validator import (
    TokenValidationError,
    TokenValidatorUnavailableError,
)

# How long a fetched JWKS stays cached before the next validation
# re-fetches it. One hour: short enough that a rotated key set is
# picked up promptly even without a `kid` miss, long enough that
# validation never fetches per request.
JWKS_TTL_SECONDS = 3600

_DISCOVERY_SUFFIX = "/.well-known/openid-configuration"


def _default_fetcher(url: str) -> dict:
    """Fetch a JSON document over HTTPS with a short timeout."""
    try:
        with httpx.Client(trust_env=False, timeout=10) as client:
            response = client.get(url)
            response.raise_for_status()
            document = response.json()
    except Exception as exc:  # noqa: BLE001 — any fetch failure is "unavailable"
        raise TokenValidatorUnavailableError(
            "could not fetch signing keys"
        ) from exc
    if not isinstance(document, dict):
        raise TokenValidatorUnavailableError("could not fetch signing keys")
    return document


class OidcTokenValidator:
    """TokenValidator backed by a generic OIDC provider's JWKS."""

    def __init__(
        self,
        issuer: str,
        audience: str,
        *,
        jwks_url: str | None = None,
        fetcher: Callable[[str], dict] | None = None,
        jwks_ttl_seconds: int = JWKS_TTL_SECONDS,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if not issuer or not issuer.startswith("https://"):
            raise ValueError(f"issuer must be a valid https URL, got {issuer!r}")
        if not audience:
            raise ValueError("audience must be a non-empty string")
        self._issuer = issuer
        self._audience = audience
        self._jwks_url = jwks_url or None
        self._fetcher = fetcher or _default_fetcher
        self._ttl = jwks_ttl_seconds
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._resolved_jwks_url: str | None = None
        self._keys: dict[str, object] | None = None
        self._keys_fetched_at: float = 0.0

    # -- key fetching ------------------------------------------------------

    def _resolve_jwks_url(self) -> str:
        """The JWKS URL: explicit, or derived once via OIDC discovery."""
        if self._jwks_url is not None:
            return self._jwks_url
        if self._resolved_jwks_url is None:
            document = self._fetch(self._issuer.rstrip("/") + _DISCOVERY_SUFFIX)
            jwks_uri = document.get("jwks_uri")
            if not isinstance(jwks_uri, str) or not jwks_uri:
                raise TokenValidatorUnavailableError(
                    "could not fetch signing keys"
                )
            self._resolved_jwks_url = jwks_uri
        return self._resolved_jwks_url

    def _fetch(self, url: str) -> dict:
        try:
            document = self._fetcher(url)
        except TokenValidatorUnavailableError:
            raise
        except Exception as exc:  # noqa: BLE001 — any fetch failure is "unavailable"
            raise TokenValidatorUnavailableError(
                "could not fetch signing keys"
            ) from exc
        if not isinstance(document, dict):
            raise TokenValidatorUnavailableError("could not fetch signing keys")
        return document

    def _fetch_keys(self) -> dict[str, object]:
        """Fetch the JWKS and index its RSA keys by `kid`."""
        document = self._fetch(self._resolve_jwks_url())
        raw_keys = document.get("keys")
        if not isinstance(raw_keys, list):
            raise TokenValidatorUnavailableError("could not fetch signing keys")
        keys: dict[str, object] = {}
        for jwk in raw_keys:
            if not isinstance(jwk, dict):
                continue
            kid = jwk.get("kid")
            if not isinstance(kid, str) or not kid:
                continue
            try:
                keys[kid] = RSAAlgorithm.from_jwk(json.dumps(jwk))
            except Exception:  # noqa: BLE001, S110 — an unusable key is skipped
                continue
        self._keys = keys
        self._keys_fetched_at = self._clock()
        return keys

    def _keys_cached(self) -> dict[str, object]:
        """The cached keys, re-fetching only once the TTL has elapsed."""
        if (
            self._keys is None
            or self._clock() - self._keys_fetched_at >= self._ttl
        ):
            return self._fetch_keys()
        return self._keys

    def _key_for_kid(self, kid: str) -> object:
        """The verification key for `kid`, re-fetching once on a miss.

        A `kid` miss on a cached JWKS means rotation: fetch fresh keys
        once, then fail if the `kid` is still unknown.
        """
        keys = self._keys_cached()
        if kid in keys:
            return keys[kid]
        keys = self._fetch_keys()
        if kid in keys:
            return keys[kid]
        raise TokenValidationError("invalid or expired token")

    # -- validation ----------------------------------------------------------

    def validate(self, token: str) -> str:
        """Return the token's principal (``sub``); see the port contract."""
        if not token or not token.strip():
            raise TokenValidationError("invalid or expired token")
        token = token.strip()
        try:
            header = jwt.get_unverified_header(token)
        except Exception as exc:  # noqa: BLE001 — any undecodable token is invalid
            raise TokenValidationError("invalid or expired token") from exc
        if header.get("alg") != "RS256":
            raise TokenValidationError("invalid or expired token")
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise TokenValidationError("invalid or expired token")
        with self._lock:
            key = self._key_for_kid(kid)
        try:
            payload = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
        except Exception as exc:  # noqa: BLE001 — expired/wrong iss/aud/signature are all 401s
            raise TokenValidationError("invalid or expired token") from exc
        subject = payload.get("sub")
        if not isinstance(subject, str) or not subject:
            raise TokenValidationError("invalid or expired token")
        return subject

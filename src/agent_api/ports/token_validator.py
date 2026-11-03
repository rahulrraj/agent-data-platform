"""Outbound port: validate a bearer token and extract its principal.

The core's contract for authentication (ADR 0008). The port is
provider-agnostic: it validates standard OIDC JWTs and knows nothing
about any identity provider. Port errors live beside the port (§5).
"""

from __future__ import annotations

from typing import Protocol


class TokenValidationError(Exception):
    """The token is missing, malformed, invalid, or expired (→ 401)."""


class TokenValidatorUnavailableError(Exception):
    """The authentication infrastructure could not be reached (→ 503).

    Distinct from an invalid token: the token may be fine, but the
    signing keys could not be fetched to check it.
    """


class TokenValidator(Protocol):
    """Contract every token-validation adapter must honor."""

    def validate(self, token: str) -> str:
        """Return the token's principal (the ``sub`` claim).

        Raises TokenValidationError for an invalid or expired token
        and TokenValidatorUnavailableError when the signing keys
        cannot be fetched. Never returns token material in an error.
        """
        ...

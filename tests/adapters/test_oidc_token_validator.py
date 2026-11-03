"""OIDC token validator tests: locally generated RSA keys + stubbed JWKS.

No network, no live identity provider, no committed key material —
keys are generated fresh in-process per test module. The JWKS source
is a stub fetcher, so every fetch is counted and controlled.
"""

from __future__ import annotations

import base64
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from agent_api.adapters.oidc_token_validator import OidcTokenValidator
from agent_api.ports.token_validator import (
    TokenValidationError,
    TokenValidatorUnavailableError,
)

ISSUER = "https://example.okta.com"
AUDIENCE = "test-audience"
JWKS_URL = "https://example.okta.com/oauth2/v1/keys"


def _b64url_int(value: int, length: int) -> str:
    return base64.urlsafe_b64encode(value.to_bytes(length, "big")).rstrip(b"=").decode()


def _keypair(kid: str):
    """A fresh RSA keypair plus its public JWK (kid-tagged)."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = private_key.public_key().public_numbers()
    jwk = {
        "kty": "RSA",
        "kid": kid,
        "use": "sig",
        "alg": "RS256",
        "n": _b64url_int(numbers.n, 256),
        "e": _b64url_int(numbers.e, 3),
    }
    return private_key, jwk


def _token(private_key, kid: str, **claims) -> str:
    payload = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "user-123",
        "exp": int(time.time()) + 600,
        "iat": int(time.time()),
    }
    payload.update(claims)
    return jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": kid})


class StubFetcher:
    """A JWKS source stub: records URLs, serves a scripted JWKS, can fail."""

    def __init__(self, jwks: dict, *, fail: Exception | None = None,
                 discovery_jwks_url: str | None = None):
        self.jwks = jwks
        self.fail = fail
        self.discovery_jwks_url = discovery_jwks_url
        self.calls: list[str] = []

    def __call__(self, url: str) -> dict:
        self.calls.append(url)
        if self.fail is not None:
            raise self.fail
        if url.endswith("/.well-known/openid-configuration"):
            return {"jwks_uri": self.discovery_jwks_url or JWKS_URL}
        return self.jwks


def _validator(fetcher, **kwargs) -> OidcTokenValidator:
    return OidcTokenValidator(
        ISSUER, AUDIENCE, jwks_url=JWKS_URL, fetcher=fetcher, **kwargs
    )


@pytest.fixture()
def keypair():
    return _keypair("key-1")


def test_valid_token_returns_sub(keypair):
    private_key, jwk = keypair
    fetcher = StubFetcher({"keys": [jwk]})
    assert _validator(fetcher).validate(_token(private_key, "key-1")) == "user-123"


def test_bad_signature_rejected(keypair):
    _, jwk = keypair
    other_key, _ = _keypair("key-1")  # same kid, different key material
    fetcher = StubFetcher({"keys": [jwk]})
    with pytest.raises(TokenValidationError):
        _validator(fetcher).validate(_token(other_key, "key-1"))


def test_wrong_algorithm_rejected(keypair):
    private_key, jwk = keypair
    fetcher = StubFetcher({"keys": [jwk]})
    token = jwt.encode(
        {"iss": ISSUER, "aud": AUDIENCE, "sub": "user-123",
         "exp": int(time.time()) + 600},
        "shared-secret-that-is-long-enough-for-hs256", algorithm="HS256", headers={"kid": "key-1"},
    )
    with pytest.raises(TokenValidationError):
        _validator(fetcher).validate(token)


def test_missing_kid_rejected(keypair):
    private_key, jwk = keypair
    fetcher = StubFetcher({"keys": [jwk]})
    token = jwt.encode(
        {"iss": ISSUER, "aud": AUDIENCE, "sub": "user-123",
         "exp": int(time.time()) + 600},
        private_key, algorithm="RS256",
    )
    with pytest.raises(TokenValidationError):
        _validator(fetcher).validate(token)


def test_wrong_issuer_rejected(keypair):
    private_key, jwk = keypair
    fetcher = StubFetcher({"keys": [jwk]})
    with pytest.raises(TokenValidationError):
        _validator(fetcher).validate(
            _token(private_key, "key-1", iss="https://evil.example")
        )


def test_wrong_audience_string_claim_rejected(keypair):
    private_key, jwk = keypair
    fetcher = StubFetcher({"keys": [jwk]})
    with pytest.raises(TokenValidationError):
        _validator(fetcher).validate(
            _token(private_key, "key-1", aud="other-audience")
        )


def test_audience_list_claim_accepted_when_it_contains_audience(keypair):
    private_key, jwk = keypair
    fetcher = StubFetcher({"keys": [jwk]})
    token = _token(private_key, "key-1", aud=["other-audience", AUDIENCE])
    assert _validator(fetcher).validate(token) == "user-123"


def test_audience_list_claim_rejected_when_it_lacks_audience(keypair):
    private_key, jwk = keypair
    fetcher = StubFetcher({"keys": [jwk]})
    with pytest.raises(TokenValidationError):
        _validator(fetcher).validate(
            _token(private_key, "key-1", aud=["other-audience"])
        )


def test_expired_token_rejected(keypair):
    private_key, jwk = keypair
    fetcher = StubFetcher({"keys": [jwk]})
    with pytest.raises(TokenValidationError):
        _validator(fetcher).validate(
            _token(private_key, "key-1", exp=int(time.time()) - 60)
        )


def test_missing_sub_rejected(keypair):
    private_key, jwk = keypair
    fetcher = StubFetcher({"keys": [jwk]})
    payload = {"iss": ISSUER, "aud": AUDIENCE, "exp": int(time.time()) + 600}
    token = jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": "key-1"})
    with pytest.raises(TokenValidationError):
        _validator(fetcher).validate(token)


def test_empty_sub_rejected(keypair):
    private_key, jwk = keypair
    fetcher = StubFetcher({"keys": [jwk]})
    with pytest.raises(TokenValidationError):
        _validator(fetcher).validate(_token(private_key, "key-1", sub=""))


def test_malformed_token_rejected(keypair):
    _, jwk = keypair
    fetcher = StubFetcher({"keys": [jwk]})
    with pytest.raises(TokenValidationError):
        _validator(fetcher).validate("not.a.jwt")
    with pytest.raises(TokenValidationError):
        _validator(fetcher).validate("")


def test_unknown_kid_refetches_once_and_succeeds_on_rotation():
    old_key, old_jwk = _keypair("key-1")
    new_key, new_jwk = _keypair("key-2")

    class RotatingFetcher:
        def __init__(self):
            self.calls = 0

        def __call__(self, url):
            self.calls += 1
            # First fetch serves the old key set; the rotation fetch
            # serves both keys.
            if self.calls == 1:
                return {"keys": [old_jwk]}
            return {"keys": [old_jwk, new_jwk]}

    fetcher = RotatingFetcher()
    validator = _validator(fetcher)
    assert validator.validate(_token(old_key, "key-1")) == "user-123"
    assert validator.validate(_token(new_key, "key-2")) == "user-123"
    assert fetcher.calls == 2


def test_unknown_kid_rejected_after_refetch(keypair):
    private_key, jwk = keypair
    fetcher = StubFetcher({"keys": [jwk]})
    with pytest.raises(TokenValidationError):
        _validator(fetcher).validate(_token(private_key, "no-such-kid"))
    # One fetch for the cache, one rotation re-fetch, then failure.
    assert len(fetcher.calls) == 2


def test_jwks_unreachable_maps_to_unavailable(keypair):
    private_key, _ = keypair
    fetcher = StubFetcher(
        {"keys": []}, fail=httpx.ConnectError("down", request=httpx.Request("GET", JWKS_URL))
    )
    with pytest.raises(TokenValidatorUnavailableError):
        _validator(fetcher).validate(_token(private_key, "key-1"))


def test_jwks_caching_second_validation_makes_no_second_fetch(keypair):
    private_key, jwk = keypair
    fetcher = StubFetcher({"keys": [jwk]})
    validator = _validator(fetcher)
    token = _token(private_key, "key-1")
    assert validator.validate(token) == "user-123"
    assert validator.validate(token) == "user-123"
    assert len(fetcher.calls) == 1


def test_jwks_refetched_after_ttl(keypair):
    private_key, jwk = keypair
    fetcher = StubFetcher({"keys": [jwk]})
    now = [1000.0]
    validator = _validator(fetcher, jwks_ttl_seconds=3600, clock=lambda: now[0])
    token = _token(private_key, "key-1")
    assert validator.validate(token) == "user-123"
    now[0] += 3599
    assert validator.validate(token) == "user-123"
    assert len(fetcher.calls) == 1
    now[0] += 2  # past the TTL: next validation re-fetches
    assert validator.validate(token) == "user-123"
    assert len(fetcher.calls) == 2


def test_discovery_derives_jwks_url_once(keypair):
    private_key, jwk = keypair
    fetcher = StubFetcher({"keys": [jwk]})
    validator = OidcTokenValidator(ISSUER, AUDIENCE, fetcher=fetcher)
    token = _token(private_key, "key-1")
    assert validator.validate(token) == "user-123"
    assert validator.validate(token) == "user-123"
    assert fetcher.calls == [
        f"{ISSUER}/.well-known/openid-configuration",
        JWKS_URL,
    ]


def test_discovery_failure_maps_to_unavailable(keypair):
    private_key, _ = keypair
    fetcher = StubFetcher(
        {"keys": []}, fail=httpx.ConnectError("down", request=httpx.Request("GET", ISSUER))
    )
    validator = OidcTokenValidator(ISSUER, AUDIENCE, fetcher=fetcher)
    with pytest.raises(TokenValidatorUnavailableError):
        validator.validate(_token(private_key, "key-1"))


def test_discovery_without_jwks_uri_maps_to_unavailable(keypair):
    private_key, _ = keypair

    class NoJwksUriFetcher:
        def __call__(self, url):
            return {} if "openid-configuration" in url else {"keys": []}

    validator = OidcTokenValidator(ISSUER, AUDIENCE, fetcher=NoJwksUriFetcher())
    with pytest.raises(TokenValidatorUnavailableError):
        validator.validate(_token(private_key, "key-1"))


def test_validator_rejects_empty_issuer_and_audience():
    with pytest.raises(ValueError):
        OidcTokenValidator("", AUDIENCE, fetcher=StubFetcher({"keys": []}))
    with pytest.raises(ValueError):
        OidcTokenValidator("http://example.okta.com", AUDIENCE, fetcher=StubFetcher({"keys": []}))
    with pytest.raises(ValueError):
        OidcTokenValidator(ISSUER, "", fetcher=StubFetcher({"keys": []}))

"""Unit tests for Entra ID token verification.

These tests never contact Microsoft or any live JWKS endpoint. We generate an
RSA keypair in-process, sign our own tokens with the private key, and hand the
matching public key to ``verify_id_token`` via its ``signing_key=`` parameter.
That exercises the real signature-and-claims verification path (PyJWT, RS256)
while keeping the test fully offline and deterministic.

Covered:
  * a well-formed token for the right tenant/app is accepted
  * a token signed by a different key is rejected (bad signature)
  * wrong audience, wrong issuer, expired token are each rejected
  * nonce mismatch is rejected
  * missing required claims (exp/aud/iss) are rejected
  * role resolution from group/app-role claims
"""

from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from overlay import auth
from overlay.auth import AuthError, EntraConfig, verify_id_token

pytestmark = pytest.mark.unit

TENANT = "11111111-2222-3333-4444-555555555555"
CLIENT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
NONCE = "nonce-abc-123"


@pytest.fixture(scope="module")
def keypair():
    """One RSA keypair for the module. Private signs tokens; public verifies."""

    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private, private.public_key()


@pytest.fixture(scope="module")
def other_key():
    """A second, unrelated private key used to forge a bad-signature token."""

    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def cfg():
    return EntraConfig(
        tenant_id=TENANT,
        client_id=CLIENT,
        client_secret="not-used-for-verification",
        redirect_uri="http://127.0.0.1:8800/api/auth/callback",
    )


def _make_token(private_key, cfg, *, overrides=None, drop=None):
    """Build a signed JWT with sensible defaults, applying overrides/removals."""

    now = int(time.time())
    claims = {
        "iss": cfg.issuer,
        "aud": cfg.client_id,
        "iat": now,
        "exp": now + 300,
        "nonce": NONCE,
        "email": "jane.doe@example.com",
        "name": "Jane Doe",
    }
    if overrides:
        claims.update(overrides)
    for key in drop or ():
        claims.pop(key, None)
    return jwt.encode(claims, private_key, algorithm="RS256")


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_valid_token_is_accepted(keypair, cfg):
    private, public = keypair
    token = _make_token(private, cfg)
    claims = verify_id_token(cfg, token, NONCE, signing_key=public)
    assert claims["email"] == "jane.doe@example.com"
    assert claims["aud"] == CLIENT
    assert claims["iss"] == cfg.issuer


def test_empty_expected_nonce_skips_nonce_check(keypair, cfg):
    # Some flows may not pin a nonce; an empty expected nonce must not falsely
    # reject a token that happens to carry one.
    private, public = keypair
    token = _make_token(private, cfg)
    claims = verify_id_token(cfg, token, "", signing_key=public)
    assert claims["email"] == "jane.doe@example.com"


# ---------------------------------------------------------------------------
# Rejection paths
# ---------------------------------------------------------------------------


def test_bad_signature_is_rejected(keypair, other_key, cfg):
    _, public = keypair
    forged = _make_token(other_key, cfg)  # signed by the wrong key
    with pytest.raises(AuthError):
        verify_id_token(cfg, forged, NONCE, signing_key=public)


def test_wrong_audience_is_rejected(keypair, cfg):
    private, public = keypair
    token = _make_token(private, cfg, overrides={"aud": "some-other-app"})
    with pytest.raises(AuthError):
        verify_id_token(cfg, token, NONCE, signing_key=public)


def test_wrong_issuer_is_rejected(keypair, cfg):
    private, public = keypair
    token = _make_token(
        private, cfg, overrides={"iss": "https://login.microsoftonline.com/evil/v2.0"}
    )
    with pytest.raises(AuthError):
        verify_id_token(cfg, token, NONCE, signing_key=public)


def test_expired_token_is_rejected(keypair, cfg):
    private, public = keypair
    now = int(time.time())
    token = _make_token(
        private, cfg, overrides={"iat": now - 1000, "exp": now - 500}
    )
    with pytest.raises(AuthError):
        verify_id_token(cfg, token, NONCE, signing_key=public)


def test_nonce_mismatch_is_rejected(keypair, cfg):
    private, public = keypair
    token = _make_token(private, cfg, overrides={"nonce": "a-different-nonce"})
    with pytest.raises(AuthError):
        verify_id_token(cfg, token, NONCE, signing_key=public)


def test_missing_exp_is_rejected(keypair, cfg):
    private, public = keypair
    token = _make_token(private, cfg, drop=["exp"])
    with pytest.raises(AuthError):
        verify_id_token(cfg, token, NONCE, signing_key=public)


def test_missing_audience_is_rejected(keypair, cfg):
    private, public = keypair
    token = _make_token(private, cfg, drop=["aud"])
    with pytest.raises(AuthError):
        verify_id_token(cfg, token, NONCE, signing_key=public)


def test_missing_issuer_is_rejected(keypair, cfg):
    private, public = keypair
    token = _make_token(private, cfg, drop=["iss"])
    with pytest.raises(AuthError):
        verify_id_token(cfg, token, NONCE, signing_key=public)


def test_wrong_algorithm_is_rejected(keypair, cfg):
    # A token signed with HS256 (symmetric) must not be accepted when we only
    # allow RS256 -- this is the classic algorithm-confusion defence.
    _, public = keypair
    now = int(time.time())
    hs_token = jwt.encode(
        {
            "iss": cfg.issuer,
            "aud": cfg.client_id,
            "iat": now,
            "exp": now + 300,
            "nonce": NONCE,
            "email": "jane.doe@example.com",
        },
        "shared-secret",
        algorithm="HS256",
    )
    with pytest.raises(AuthError):
        verify_id_token(cfg, hs_token, NONCE, signing_key=public)


# ---------------------------------------------------------------------------
# Config-derived values
# ---------------------------------------------------------------------------


def test_issuer_and_jwks_uri_carry_tenant(cfg):
    assert cfg.issuer == f"https://login.microsoftonline.com/{TENANT}/v2.0"
    assert cfg.jwks_uri == (
        f"https://login.microsoftonline.com/{TENANT}/discovery/v2.0/keys"
    )


# ---------------------------------------------------------------------------
# Role resolution from verified claims
# ---------------------------------------------------------------------------


def test_role_resolves_from_group_mapping(monkeypatch):
    monkeypatch.setenv("LOKI_ENTRA_ROLE_MAP", "grp-approvers=approver,grp-admins=admin")
    role = auth._resolve_role({"groups": ["grp-approvers"]})
    assert role == "approver"


def test_role_resolves_from_app_role_claim(monkeypatch):
    monkeypatch.setenv("LOKI_ENTRA_ROLE_MAP", "Loki.Admin=admin")
    role = auth._resolve_role({"roles": ["Loki.Admin"]})
    assert role == "admin"


def test_unmapped_user_gets_default_role(monkeypatch):
    monkeypatch.setenv("LOKI_ENTRA_ROLE_MAP", "grp-approvers=approver")
    monkeypatch.setattr(auth, "DEFAULT_ROLE", "operator")
    role = auth._resolve_role({"groups": ["grp-unknown"]})
    assert role == "operator"

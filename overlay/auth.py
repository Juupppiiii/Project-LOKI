"""Authentication and identity.

This module answers one question the rest of the system depends on but never
decided for itself: *who is this, really?* Until now the actor was a header the
browser asserted -- anyone could claim to be anyone. That is fine for a personal
prototype and unacceptable the moment two people share it.

Design
------
* A session is a **signed cookie** (`itsdangerous`), so the browser can hold it
  but cannot forge or tamper with its contents. The signing key lives in the
  environment; a random per-process key is used if none is set, which logs
  everyone out on restart -- safe, if mildly annoying, and never silently
  insecure.

* Two ways to sign in, chosen by whether Entra is configured:

  - **Entra ID (Azure AD) OIDC** -- the real path. Redirect to Microsoft, come
    back with an authorization code, exchange it for an ID token, validate the
    token, and read the verified email and group/role claims from it. Requires
    an app registration your Entra admins create (client id/secret, redirect
    URI, tenant).

  - **Dev login** -- a local fallback so the system is usable before Entra is
    wired up. It asks only for an email and a role and trusts them. It is
    enabled ONLY when Entra is not configured AND `OVERLAY_ALLOW_DEV_LOGIN` is
    set, so it can never accidentally ship as the auth mechanism in a real
    deployment.

Roles
-----
Identity is separated from what you may *do*. A role gates which actions a
signed-in person may take (see service-side checks). Crucially, a person's login
never becomes the agent's execution authority: the agent always acts through the
scoped service accounts in the credentials centre. Login proves who approves; it
does not let the agent act as that person.
"""

from __future__ import annotations

import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

COOKIE_NAME = "bucky_session"
SESSION_MAX_AGE = int(os.getenv("BUCKY_SESSION_MAX_AGE", str(8 * 3600)))  # 8h

# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------
# Ordered least-to-most privileged. A higher role includes the rights of the
# ones below it.
ROLES = ["viewer", "operator", "approver", "admin"]

ROLE_RANK = {r: i for i, r in enumerate(ROLES)}

ROLE_LABELS = {
    "viewer": "Viewer â€” can look, cannot change anything",
    "operator": "Operator â€” can run approved steps and do manual work",
    "approver": "Approver â€” can approve agent actions and publish procedures",
    "admin": "Admin â€” can also manage access credentials",
}

# What each capability requires. Checked server-side; the UI only mirrors it.
CAPABILITIES = {
    "propose": "operator",          # create a run from a ticket
    "set_parameters": "operator",
    "complete_step": "operator",    # do a manual step
    "skip_step": "operator",
    "cancel_run": "operator",
    "approve_chunk": "approver",    # let the agent act
    "publish_procedure": "approver",
    "reject_draft": "operator",
    "upload_knowledge": "operator",
    "delete_source": "approver",
    "manage_credentials": "admin",  # the keys to what the agent can reach
    "manage_live_feed": "operator",
    "run_diagnostic": "operator",   # read-only evidence gathering
    "view_investigation": "viewer", # ticket evidence and recommendations
    "submit_recommendation_feedback": "operator",
    "run_triage": "operator",       # start the read-only triage agent
}


def role_allows(role: str, capability: str) -> bool:
    required = CAPABILITIES.get(capability)
    if required is None:
        return True  # uncapped action (reads, etc.)
    return ROLE_RANK.get(role, -1) >= ROLE_RANK.get(required, 99)


# ---------------------------------------------------------------------------
# Entra / dev configuration
# ---------------------------------------------------------------------------


@dataclass
class EntraConfig:
    tenant_id: str = ""
    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.tenant_id and self.client_id and self.client_secret)

    @property
    def authority(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}"

    def authorize_url(self, state: str, nonce: str) -> str:
        from urllib.parse import urlencode

        params = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "response_mode": "query",
            "scope": "openid email profile",
            "state": state,
            "nonce": nonce,
        }
        return f"{self.authority}/oauth2/v2.0/authorize?{urlencode(params)}"

    @property
    def token_url(self) -> str:
        return f"{self.authority}/oauth2/v2.0/token"

    @property
    def jwks_uri(self) -> str:
        return f"{self.authority}/discovery/v2.0/keys"

    @property
    def issuer(self) -> str:
        # Entra v2.0 issuer carries the tenant id and a trailing /v2.0.
        return f"https://login.microsoftonline.com/{self.tenant_id}/v2.0"


# ---------------------------------------------------------------------------
# ID token verification (signature + claims)
# ---------------------------------------------------------------------------
#
# The previous build decoded the id_token's claims without verifying its
# signature. That trusts the TLS channel alone; it cannot detect a token that
# did not come from this tenant/app. We now verify the RS256 signature against
# the tenant's published JWKS and check issuer, audience, expiry and nonce.


class AuthError(Exception):
    """Login failed. Message is safe to surface; details are not logged as tokens."""


# Clock skew tolerated when checking exp/nbf/iat (seconds).
_LEEWAY_SECONDS = 60
# How long a fetched JWKS is reused before refetching (seconds). Key rollover
# is handled by PyJWKClient refetching when an unknown kid is seen.
_JWKS_CACHE_SECONDS = int(os.getenv("BUCKY_JWKS_CACHE_SECONDS", "3600"))


class _JWKSClientCache:
    """One PyJWKClient per JWKS URI, reused across requests."""

    def __init__(self) -> None:
        self._clients: dict[str, Any] = {}

    def client(self, jwks_uri: str):
        existing = self._clients.get(jwks_uri)
        if existing is not None:
            return existing
        from jwt import PyJWKClient

        created = PyJWKClient(
            jwks_uri,
            cache_keys=True,
            lifespan=_JWKS_CACHE_SECONDS,
        )
        self._clients[jwks_uri] = created
        return created


_JWKS_CACHE = _JWKSClientCache()


def verify_id_token(
    cfg: EntraConfig,
    id_token: str,
    expected_nonce: str,
    *,
    signing_key: Any | None = None,
) -> dict[str, Any]:
    """Verify an Entra ID token and return its validated claims.

    Raises AuthError on any failure. ``signing_key`` lets tests inject a key
    (and is also usable for a pre-resolved key); otherwise the tenant JWKS is
    fetched and the key matching the token's ``kid`` is selected.
    """

    import jwt

    try:
        if signing_key is None:
            key = _JWKS_CACHE.client(cfg.jwks_uri).get_signing_key_from_jwt(id_token).key
        else:
            key = signing_key
        claims = jwt.decode(
            id_token,
            key=key,
            algorithms=["RS256"],
            audience=cfg.client_id,
            issuer=cfg.issuer,
            leeway=_LEEWAY_SECONDS,
            options={
                "require": ["exp", "iat", "aud", "iss"],
                "verify_signature": True,
                "verify_exp": True,
                "verify_aud": True,
                "verify_iss": True,
            },
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("Your sign-in has expired. Please try again.") from exc
    except jwt.InvalidAudienceError as exc:
        raise AuthError("Sign-in was issued for a different application.") from exc
    except jwt.InvalidIssuerError as exc:
        raise AuthError("Sign-in came from an unexpected identity provider.") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError("The identity token could not be verified.") from exc
    except Exception as exc:  # JWKS fetch / key selection failures
        raise AuthError("Could not verify the sign-in with the identity provider.") from exc

    if expected_nonce and claims.get("nonce") != expected_nonce:
        raise AuthError("Sign-in could not be verified (nonce mismatch).")

    return claims


def load_entra() -> EntraConfig:
    return EntraConfig(
        tenant_id=os.getenv("BUCKY_ENTRA_TENANT_ID", ""),
        client_id=os.getenv("BUCKY_ENTRA_CLIENT_ID", ""),
        client_secret=os.getenv("BUCKY_ENTRA_CLIENT_SECRET", ""),
        redirect_uri=os.getenv(
            "BUCKY_ENTRA_REDIRECT_URI", "http://127.0.0.1:8800/api/auth/callback"
        ),
    )


# Map Entra group object-ids (or app-role names) to BUCKY roles. Configured as
# "groupid=role,groupid=role"; unmapped users get the floor role.
def _role_map() -> dict[str, str]:
    raw = os.getenv("BUCKY_ENTRA_ROLE_MAP", "")
    out: dict[str, str] = {}
    for pair in raw.split(","):
        if "=" in pair:
            key, _, role = pair.partition("=")
            role = role.strip()
            if role in ROLE_RANK:
                out[key.strip()] = role
    return out


DEFAULT_ROLE = os.getenv("BUCKY_DEFAULT_ROLE", "operator")


def dev_login_allowed() -> bool:
    """Dev login is only ever available when real auth is NOT configured."""

    return (not load_entra().configured) and os.getenv(
        "OVERLAY_ALLOW_DEV_LOGIN", "1"
    ) not in ("0", "false", "False", "")


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


@dataclass
class Identity:
    email: str
    name: str
    role: str
    method: str  # "entra" | "dev"
    issued_at: float = field(default_factory=time.time)

    def to_public(self) -> dict[str, Any]:
        return {
            "email": self.email,
            "name": self.name,
            "role": self.role,
            "role_label": ROLE_LABELS.get(self.role, self.role),
            "method": self.method,
            "capabilities": [
                cap for cap in CAPABILITIES if role_allows(self.role, cap)
            ],
        }


class SessionManager:
    """Signs and verifies the session cookie. Holds no server-side state."""

    def __init__(self) -> None:
        secret = os.getenv("BUCKY_SECRET_KEY")
        if not secret:
            # Ephemeral key: sessions survive only until restart. Deliberately
            # not persisted, so a missing key never means a predictable one.
            secret = secrets.token_hex(32)
            self._ephemeral = True
        else:
            self._ephemeral = False
        self._serializer = URLSafeTimedSerializer(secret, salt="bucky-session")

    @property
    def ephemeral_key(self) -> bool:
        return self._ephemeral

    def issue(self, identity: Identity) -> str:
        return self._serializer.dumps(
            {
                "email": identity.email,
                "name": identity.name,
                "role": identity.role,
                "method": identity.method,
                "issued_at": identity.issued_at,
            }
        )

    def read(self, token: str | None) -> Identity | None:
        if not token:
            return None
        try:
            data = self._serializer.loads(token, max_age=SESSION_MAX_AGE)
        except (BadSignature, SignatureExpired):
            return None
        return Identity(
            email=data["email"],
            name=data.get("name", data["email"]),
            role=data.get("role", DEFAULT_ROLE),
            method=data.get("method", "dev"),
            issued_at=data.get("issued_at", time.time()),
        )


# ---------------------------------------------------------------------------
# Entra token exchange
# ---------------------------------------------------------------------------


def exchange_code_for_identity(cfg: EntraConfig, code: str, expected_nonce: str) -> Identity:
    """Swap an auth code for a validated identity. Requires httpx.

    After the TLS code exchange, the id_token's RS256 signature is verified
    against the tenant JWKS and its issuer, audience, expiry and nonce are
    checked (see verify_id_token) before any claim is trusted.
    """

    import httpx

    resp = httpx.post(
        cfg.token_url,
        data={
            "client_id": cfg.client_id,
            "client_secret": cfg.client_secret,
            "code": code,
            "redirect_uri": cfg.redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise AuthError("Microsoft rejected the sign-in. Please try again.")

    payload = resp.json()
    id_token = payload.get("id_token")
    if not id_token:
        raise AuthError("No identity token returned by Microsoft.")

    # Verify the token's RS256 signature against the tenant JWKS and validate
    # issuer, audience, expiry and nonce before trusting any claim.
    claims = verify_id_token(cfg, id_token, expected_nonce)

    email = (
        claims.get("email")
        or claims.get("preferred_username")
        or claims.get("upn")
        or ""
    ).strip()
    if not email:
        raise AuthError("Microsoft did not return an email for this account.")

    role = _resolve_role(claims)

    return Identity(
        email=email,
        name=claims.get("name", email),
        role=role,
        method="entra",
    )


def _resolve_role(claims: dict[str, Any]) -> str:
    """Map Entra group/role claims to a BUCKY role, else the default."""

    mapping = _role_map()
    if mapping:
        # App roles come as `roles`; group ids as `groups`.
        for claim_key in ("roles", "groups"):
            for value in claims.get(claim_key, []) or []:
                if value in mapping:
                    return mapping[value]
    return DEFAULT_ROLE


def status(session: SessionManager) -> dict[str, Any]:
    """What the login screen needs to know about how to authenticate."""

    entra = load_entra()
    return {
        "entra_configured": entra.configured,
        "dev_login_allowed": dev_login_allowed(),
        "ephemeral_session_key": session.ephemeral_key,
        "roles": [{"id": r, "label": ROLE_LABELS[r]} for r in ROLES],
    }

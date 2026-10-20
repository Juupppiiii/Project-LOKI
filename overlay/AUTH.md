# Authentication & roles (BUCKY)

Login proves **who is approving**. It does **not** give the agent that person's
access â€” the agent only ever acts through the scoped service accounts in the
credentials centre. These are two different things on purpose.

## Sign-in methods

BUCKY picks one automatically:

- **Microsoft Entra ID (work account)** â€” used when Entra is configured. You
  are redirected to Microsoft, sign in as normal, and return with a verified
  identity. This needs an app registration your Entra admins create.
- **Local dev login** â€” the fallback when Entra is not configured. Asks for an
  email and a role and trusts them. Fine for a prototype; it is **not** real
  authentication. It is automatically disabled the moment Entra is configured.

## Enabling enforcement

By default (`BUCKY_ENFORCE_AUTH` unset) the app still accepts the old
header-actor, so local use is unchanged. Turn on real enforcement with:

```powershell
$env:BUCKY_ENFORCE_AUTH = "1"       # writes now require a signed-in session
$env:BUCKY_SECRET_KEY   = "<a long random string>"   # keeps sessions across restarts
```

With enforcement on, the `X-Actor` header is ignored â€” identity comes only from
the signed session cookie, so it cannot be spoofed.

## Configuring Entra (your Azure AD admins)

Create an app registration, then set:

```powershell
$env:BUCKY_ENTRA_TENANT_ID     = "<tenant guid>"
$env:BUCKY_ENTRA_CLIENT_ID     = "<application id>"
$env:BUCKY_ENTRA_CLIENT_SECRET = "<client secret>"
$env:BUCKY_ENTRA_REDIRECT_URI  = "https://<host>/api/auth/callback"
# optional: map Entra groups/app-roles to BUCKY roles
$env:BUCKY_ENTRA_ROLE_MAP      = "<group-guid>=approver,<group-guid>=admin"
$env:BUCKY_DEFAULT_ROLE        = "operator"
```

The redirect URI must be registered on the app registration exactly as above.

## Roles

Least to most privileged; higher includes lower:

| Role | Can |
|---|---|
| **viewer** | read only |
| **operator** | create runs, do manual steps, upload handbooks, run the live feed |
| **approver** | also approve agent actions and publish procedures |
| **admin** | also manage access credentials |

Every capability is checked **server-side** (`require_capability`). The UI only
mirrors the result; it cannot grant anything the server would refuse.

## What was verified

18/18 end-to-end checks with enforcement on:

- writes blocked when signed out (401); reads still work
- viewer cannot create a run; operator can, but cannot approve; approver can
- credentials are admin-only
- audit is attributed to the **signed-in** identity
- a spoofed `X-Actor` header is ignored â€” the session identity wins
- an executed step is recorded as `actor=agent` with `triggered_by` the person
  â€” i.e. the agent still acts as itself, never as the signed-in user
- connectors remain disabled; execution is still simulated
- logout clears the session and blocks writes again

## Honest limitations

- The Entra id_token is trusted because it comes directly from Microsoft's token
  endpoint over TLS in response to our request, and the nonce ties it to the
  login we started. Full JWKS **signature validation** against the tenant is the
  remaining hardening step before production.
- Sessions are signed cookies with no server-side revocation list; signing out
  clears the cookie, and a stolen cookie is valid until it expires
  (`BUCKY_SESSION_MAX_AGE`, default 8h). Rotating `BUCKY_SECRET_KEY` invalidates
  all sessions at once.
- Dev login is trust-on-assertion by design. Never run a real deployment on it â€”
  configure Entra.

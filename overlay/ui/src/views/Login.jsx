import { useEffect, useState } from "react";

import { api } from "../api.js";
import { Callout, Message } from "../components/primitives.jsx";

/* Sign-in screen.
 *
 * Shows the work-account button when Entra is configured; otherwise a local
 * dev login so the app is usable before SSO is wired up. Whichever path, the
 * server establishes a verified session -- the identity is no longer a typed
 * box the browser can spoof.
 */

export default function Login({ onSignedIn }) {
  const [status, setStatus] = useState(null);
  const [msg, setMsg] = useState(null);
  const [busy, setBusy] = useState(false);

  // dev-login fields
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("operator");

  useEffect(() => {
    api
      .authStatus()
      .then(setStatus)
      .catch((e) => setMsg({ kind: "error", text: e.message }));
  }, []);

  async function workAccount() {
    setBusy(true);
    setMsg({ kind: "", text: "Redirecting to Microsoftâ€¦" });
    try {
      const { authorize_url } = await api.loginUrl();
      window.location.href = authorize_url;
    } catch (err) {
      setMsg({ kind: "error", text: err.message });
      setBusy(false);
    }
  }

  async function devLogin() {
    if (!email.includes("@")) {
      setMsg({ kind: "error", text: "Enter a valid email address." });
      return;
    }
    setBusy(true);
    setMsg({ kind: "", text: "Signing inâ€¦" });
    try {
      const me = await api.devLogin(email.trim(), role);
      onSignedIn(me);
    } catch (err) {
      setMsg({ kind: "error", text: err.message });
      setBusy(false);
    }
  }

  return (
    <div className="login-screen">
      <div className="login-card">
        <div className="login-brand">
          <span className="brand-mark" aria-hidden="true">
            L
          </span>
          <h1>BUCKY</h1>
        </div>
        <p className="hint" style={{ textAlign: "center", marginBottom: "var(--sp-5)" }}>
          Human-in-the-loop ticket automation. Sign in to continue.
        </p>

        {!status ? (
          <Message>Loadingâ€¦</Message>
        ) : (
          <>
            {status.entra_configured ? (
              <button
                type="button"
                className="btn btn-primary btn-block"
                disabled={busy}
                onClick={workAccount}
              >
                Sign in with your work account
              </button>
            ) : null}

            {status.dev_login_allowed ? (
              <>
                {status.entra_configured ? (
                  <div className="login-or">or</div>
                ) : (
                  <Callout kind="info" title="Local sign-in">
                    Your work-account sign-in (Microsoft Entra) is not configured
                    on this server yet, so this uses a local login. It is fine for
                    a prototype; it is not real authentication.
                  </Callout>
                )}

                <div className="field" style={{ marginTop: "var(--sp-4)" }}>
                  <label htmlFor="login-email">Email</label>
                  <input
                    id="login-email"
                    className="input"
                    type="email"
                    placeholder="you@tpicap.com"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && devLogin()}
                  />
                </div>

                <div className="field">
                  <label htmlFor="login-role">Role</label>
                  <select
                    id="login-role"
                    className="select"
                    value={role}
                    onChange={(e) => setRole(e.target.value)}
                  >
                    {(status.roles || []).map((r) => (
                      <option key={r.id} value={r.id}>
                        {r.label}
                      </option>
                    ))}
                  </select>
                  <p className="hint">
                    Determines what you may do â€” only an approver can approve
                    agent actions, only an admin can manage access credentials.
                  </p>
                </div>

                <button
                  type="button"
                  className="btn btn-primary btn-block"
                  disabled={busy}
                  onClick={devLogin}
                >
                  Sign in
                </button>
              </>
            ) : null}

            {!status.entra_configured && !status.dev_login_allowed ? (
              <Callout kind="danger" title="No sign-in method available">
                Entra is not configured and dev login is disabled. Set the Entra
                environment variables, or allow dev login, on the server.
              </Callout>
            ) : null}

            <Message kind={msg?.kind}>{msg?.text}</Message>

            {status.ephemeral_session_key ? (
              <p className="hint" style={{ marginTop: "var(--sp-4)" }}>
                Note: no session key is set on the server, so everyone is signed
                out when it restarts. Set <code>BUCKY_SECRET_KEY</code> to keep
                sessions across restarts.
              </p>
            ) : null}
          </>
        )}
      </div>

      <p className="login-foot hint">
        Signing in proves who approves. It does not give the agent your access â€”
        the agent only ever acts through the scoped service accounts in the
        credentials centre.
      </p>
    </div>
  );
}

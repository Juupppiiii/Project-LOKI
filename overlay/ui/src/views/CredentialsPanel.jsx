import { useCallback, useEffect, useState } from "react";

import { api } from "../api.js";
import { Badge, Callout, Message } from "../components/primitives.jsx";
import { shortTime } from "../vocabulary.js";

/* Access credentials centre.
 *
 * One place to see what the agent can reach, as whom, and within what bounds.
 *
 * A record holds a POINTER to a secret, never the secret. If a value is pasted
 * it goes to the OS credential store and only the pointer is kept, which is why
 * nothing here can ever display a password back to you.
 */

const HOW = {
  ref: "Point at a secret that already exists",
  value: "Paste a value and store it in Windows Credential Manager",
};

export default function CredentialsPanel({ busy }) {
  const [connectors, setConnectors] = useState([]);
  const [rows, setRows] = useState([]);
  const [msg, setMsg] = useState(null);
  const [adding, setAdding] = useState(false);

  const [how, setHow] = useState("ref");
  const [form, setForm] = useState({
    name: "",
    connector: "ssh",
    account: "",
    scope: "",
    permissions: "",
    note: "",
    secret_ref: "",
    secret_value: "",
  });

  const load = useCallback(async () => {
    const [c, r] = await Promise.all([api.connectors(), api.credentials()]);
    setConnectors(c.connectors);
    setRows(r.credentials);
  }, []);

  useEffect(() => {
    load().catch((e) => setMsg({ kind: "error", text: e.message }));
  }, [load]);

  const chosen = connectors.find((c) => c.id === form.connector);

  async function submit() {
    setMsg({ kind: "", text: "Saving…" });
    try {
      const body = {
        name: form.name,
        connector: form.connector,
        account: form.account,
        scope: form.scope.split(",").map((s) => s.trim()).filter(Boolean),
        permissions: form.permissions
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean),
        note: form.note,
      };
      if (how === "value") body.secret_value = form.secret_value;
      else body.secret_ref = form.secret_ref;

      const created = await api.addCredential(body);
      setMsg({
        kind: "ok",
        text: `Registered. Stored as ${created.secret_ref} — the value itself is not kept here.`,
      });
      setForm({
        name: "",
        connector: form.connector,
        account: "",
        scope: "",
        permissions: "",
        note: "",
        secret_ref: "",
        secret_value: "",
      });
      setAdding(false);
      await load();
    } catch (err) {
      setMsg({ kind: "error", text: err.message });
    }
  }

  async function run(fn, okText) {
    setMsg({ kind: "", text: "Working…" });
    try {
      const r = await fn();
      setMsg({
        kind: r?.ok === false ? "error" : "ok",
        text: typeof okText === "function" ? okText(r) : okText,
      });
      await load();
    } catch (err) {
      setMsg({ kind: "error", text: err.message });
    }
  }

  return (
    <>
      <Callout kind="info" title="What this holds">
        A pointer to each secret, the account it acts as, and the hosts it is
        allowed to touch. <strong>Secret values are never stored here</strong> —
        pasted values go to Windows Credential Manager, and nothing on this
        screen can display one back.
      </Callout>

      <div className="row" style={{ marginTop: "var(--sp-4)" }}>
        <h3 style={{ margin: 0 }}>Registered access</h3>
        <span className="spacer" />
        <button
          type="button"
          className="btn btn-primary btn-sm"
          onClick={() => setAdding((v) => !v)}
        >
          {adding ? "Cancel" : "Add access"}
        </button>
      </div>

      <Message kind={msg?.kind}>{msg?.text}</Message>

      {/* ------------------------------ add form ------------------------------ */}
      {adding ? (
        <div className="card card-sunken" style={{ marginTop: "var(--sp-3)" }}>
          <div className="field">
            <label htmlFor="c-name">Name</label>
            <input
              id="c-name"
              className="input"
              placeholder="Linux read-only — stage 1"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
          </div>

          <div className="field">
            <label htmlFor="c-connector">System</label>
            <select
              id="c-connector"
              className="select"
              value={form.connector}
              onChange={(e) => setForm({ ...form, connector: e.target.value })}
            >
              {connectors.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.label}
                  {c.implemented ? "" : " — connector not built yet"}
                </option>
              ))}
            </select>
            {chosen ? (
              <p className="hint">
                Reads {chosen.reads}. Writes {chosen.writes}.
              </p>
            ) : null}
          </div>

          <div className="field">
            <label htmlFor="c-account">Acts as</label>
            <input
              id="c-account"
              className="input"
              placeholder={
                form.connector === "winrm" ? "CORP\\svc-overlay" : "svc-overlay"
              }
              value={form.account}
              onChange={(e) => setForm({ ...form, account: e.target.value })}
            />
          </div>

          <div className="field">
            <label htmlFor="c-scope">Allowed on</label>
            <input
              id="c-scope"
              className="input"
              placeholder={chosen?.scope_hint || "hostnames, comma separated"}
              value={form.scope}
              onChange={(e) => setForm({ ...form, scope: e.target.value })}
            />
            <p className="hint">
              Comma separated, and required. An unscoped credential is an
              unbounded one.
            </p>
          </div>

          <div className="field">
            <label htmlFor="c-perms">Expected rights</label>
            <input
              id="c-perms"
              className="input"
              placeholder="read systemctl, read df"
              value={form.permissions}
              onChange={(e) =>
                setForm({ ...form, permissions: e.target.value })
              }
            />
            <p className="hint">
              What this account should be able to do, for review. The system does
              not grant these — your platform team does.
            </p>
          </div>

          <div className="field">
            <label htmlFor="c-how">Where the secret lives</label>
            <select
              id="c-how"
              className="select"
              value={how}
              onChange={(e) => setHow(e.target.value)}
            >
              <option value="ref">{HOW.ref}</option>
              <option value="value">{HOW.value}</option>
            </select>
          </div>

          {how === "ref" ? (
            <div className="field">
              <label htmlFor="c-ref">Reference</label>
              <input
                id="c-ref"
                className="input mono"
                placeholder="env:SN_PASSWORD  ·  delinea:IT/Overlay/svc-ad"
                value={form.secret_ref}
                onChange={(e) =>
                  setForm({ ...form, secret_ref: e.target.value })
                }
              />
              <p className="hint">
                <code>env:VARNAME</code> reads it from the server environment.{" "}
                <code>delinea:path</code> is the right long-term home, though no
                Delinea connector exists yet.
              </p>
            </div>
          ) : (
            <div className="field">
              <label htmlFor="c-val">Value</label>
              <input
                id="c-val"
                type="password"
                className="input"
                autoComplete="new-password"
                placeholder="stored in Windows Credential Manager"
                value={form.secret_value}
                onChange={(e) =>
                  setForm({ ...form, secret_value: e.target.value })
                }
              />
              <p className="hint">
                Sent once, written to the OS credential store, and replaced with
                a pointer. It is not written to this application's database and
                cannot be read back here.
              </p>
            </div>
          )}

          <button
            type="button"
            className="btn btn-primary btn-block"
            disabled={busy}
            onClick={submit}
          >
            Register access
          </button>
        </div>
      ) : null}

      {/* ------------------------------ the list ------------------------------ */}
      {!rows.length ? (
        <p className="hint" style={{ marginTop: "var(--sp-3)" }}>
          Nothing registered. The agent has no access of its own, which is why
          execution is simulated.
        </p>
      ) : (
        <div style={{ marginTop: "var(--sp-3)" }}>
          {rows.map((c) => (
            <div key={c.credential_id} className="cred-row">
              <div className="cred-main">
                <div className="cred-top">
                  <span className="cred-name">{c.name}</span>
                  <Badge tone={c.enabled ? "bucket-active" : "neutral"} pill>
                    {c.enabled ? "enabled" : "disabled"}
                  </Badge>
                  <Badge tone="neutral">{c.connector}</Badge>
                  {c.secret?.resolvable ? (
                    <Badge tone="R0">secret found</Badge>
                  ) : (
                    <Badge tone="R2">secret missing</Badge>
                  )}
                </div>

                <div className="cred-meta">
                  acts as <strong>{c.account}</strong> · allowed on{" "}
                  {(c.scope || []).join(", ") || "—"}
                </div>

                <div className="cred-meta">
                  secret: <span className="mono">{c.secret_ref}</span>{" "}
                  <span className="secret-mask">••••••••</span>
                </div>

                {(c.permissions || []).length ? (
                  <div className="cred-meta">
                    expected rights: {c.permissions.join(", ")}
                  </div>
                ) : null}

                <div className="cred-meta">{c.secret?.detail}</div>

                {c.last_tested_at ? (
                  <div className="cred-meta">
                    last checked {shortTime(c.last_tested_at)} —{" "}
                    {c.last_test_ok ? "ok" : "failed"}: {c.last_test_detail}
                  </div>
                ) : null}
              </div>

              <div className="cred-actions">
                <button
                  type="button"
                  className="btn btn-sm"
                  disabled={busy}
                  onClick={() =>
                    run(
                      () => api.testCredential(c.credential_id),
                      (r) => `${c.name}: ${r.detail}`
                    )
                  }
                >
                  Check
                </button>
                <button
                  type="button"
                  className="btn btn-sm"
                  disabled={busy}
                  onClick={() =>
                    run(
                      () => api.toggleCredential(c.credential_id, !c.enabled),
                      c.enabled ? "Disabled." : "Enabled."
                    )
                  }
                >
                  {c.enabled ? "Disable" : "Enable"}
                </button>
                <button
                  type="button"
                  className="btn btn-danger btn-sm"
                  disabled={busy}
                  onClick={() => {
                    if (!confirm(`Remove access "${c.name}"?`)) return;
                    run(() => api.deleteCredential(c.credential_id), "Removed.");
                  }}
                >
                  Remove
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      <h3>Why it works this way</h3>
      <p className="hint">
        This application has no sign-in yet — identity is a header the browser
        asserts. A table of service-account passwords inside it would be the most
        valuable target in the estate, reachable by anyone who can reach the
        port. Keeping only pointers means that even full access to this database
        yields no usable secret.
      </p>
      <p className="hint" style={{ marginTop: "var(--sp-2)" }}>
        Registering access here does not switch execution on. Connectors are
        still disabled, so a credential is a declaration of intent that can be
        reviewed before anything uses it.
      </p>
    </>
  );
}

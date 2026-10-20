import { useCallback, useEffect, useState } from "react";

import { api, setActor } from "./api.js";
import Tabs from "./components/Tabs.jsx";
import ThemeToggle from "./components/ThemeToggle.jsx";
import { Callout, Spinner } from "./components/primitives.jsx";
import InvestigationView from "./views/InvestigationView.jsx";
import KnowledgeView from "./views/KnowledgeView.jsx";
import LiveView from "./views/LiveView.jsx";
import Login from "./views/Login.jsx";
import QueueView from "./views/QueueView.jsx";

import "./styles/components.css";

/* Application shell.
 *
 * Owns only what is shared: the signed-in identity, the run list, the summary
 * counts, and which tab is open. Each view fetches its own detail. No view
 * decides policy -- that is all server-side.
 */

export default function App() {
  const [tab, setTab] = useState("queue");

  // Who is signed in. null = unknown (checking), false = not signed in.
  const [me, setMe] = useState(null);

  const [runs, setRuns] = useState([]);
  const [summary, setSummary] = useState(null);
  const [busy, setBusy] = useState(false);
  const [fatal, setFatal] = useState(null);
  const [loaded, setLoaded] = useState(false);

  // Establish the session on load: who does the server say I am?
  useEffect(() => {
    api
      .me()
      .then((r) => {
        if (r.authenticated) {
          setActor(r.email); // keep the header actor in sync for any legacy path
          setMe(r);
        } else {
          setMe(false);
        }
      })
      .catch(() => setMe(false));
  }, []);

  async function signOut() {
    try {
      await api.logout();
    } catch {
      /* ignore */
    }
    setMe(false);
  }

  const refresh = useCallback(async () => {
    setBusy(true);
    try {
      const [r, s] = await Promise.all([api.listRuns(500), api.summary()]);
      setRuns(r.runs);
      setSummary(s);
      setFatal(null);
    } catch (err) {
      setFatal(err.message);
    } finally {
      setBusy(false);
      setLoaded(true);
    }
  }, []);

  // Only load data once signed in.
  useEffect(() => {
    if (me) refresh();
  }, [me, refresh]);

  const needsYou = runs.filter((r) =>
    ["needs_parameters", "awaiting_approval", "ready", "paused_for_human", "proposed"].includes(
      r.state
    )
  ).length;

  const liveCount = Object.values(summary?.live?.counts || {}).reduce(
    (total, count) => total + Number(count || 0),
    0
  );
  const tabs = [
    { id: "queue", label: "Queue", count: needsYou },
    { id: "investigate", label: "Investigate", count: liveCount },
    { id: "knowledge", label: "Knowledge", count: summary?.pending_drafts ?? 0 },
    { id: "live", label: "Live", count: summary?.live?.counts?.new ?? 0 },
  ];

  // Still checking the session.
  if (me === null) {
    return (
      <div style={{ display: "grid", placeItems: "center", height: "100vh" }}>
        <Spinner label="Loading" />
      </div>
    );
  }

  // Not signed in.
  if (me === false) {
    return (
      <Login
        onSignedIn={(who) => {
          setActor(who.email);
          setMe(who);
        }}
      />
    );
  }

  return (
    <div className="app">
      <div className="mode-banner" role="status">
        Execution defaults to <strong>simulation</strong> â€” real connectors
        require explicit opt-in. Advisory AI (Opus 5) is available for
        grounded step recommendations.
      </div>

      <header className="app-header">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            L
          </span>
          <h1>BUCKY</h1>
        </div>

        <Tabs tabs={tabs} active={tab} onChange={setTab} />

        <div className="header-right">
          {busy ? <Spinner label="Refreshing" /> : null}

          <div className="identity" title={me.role_label}>
            <span className="identity-email">{me.email}</span>
            <span className="identity-role">{me.role}</span>
          </div>

          <button
            type="button"
            className="btn btn-quiet btn-sm"
            onClick={refresh}
            disabled={busy}
          >
            Refresh
          </button>

          <button type="button" className="btn btn-quiet btn-sm" onClick={signOut}>
            Sign out
          </button>

          <ThemeToggle />
        </div>
      </header>

      <main
        className="app-main"
        id={`panel-${tab}`}
        role="tabpanel"
        aria-labelledby={`tab-${tab}`}
      >
        {fatal ? (
          <div style={{ padding: "1rem" }}>
            <Callout kind="danger" title="Cannot reach the server">
              <p style={{ marginTop: "0.2rem" }}>{fatal}</p>
              <p className="hint" style={{ marginTop: "0.4rem" }}>
                Check the API is running, then press Refresh.
              </p>
            </Callout>
          </div>
        ) : !loaded ? (
          <div style={{ padding: "2rem", textAlign: "center" }}>
            <Spinner label="Loading" />
          </div>
        ) : tab === "queue" ? (
          <QueueView runs={runs} onRefresh={refresh} busy={busy} me={me} />
        ) : tab === "investigate" ? (
          <InvestigationView onRefresh={refresh} />
        ) : tab === "knowledge" ? (
          <KnowledgeView onRefresh={refresh} busy={busy} me={me} />
        ) : (
          <LiveView onRefresh={refresh} busy={busy} me={me} />
        )}
      </main>

      <footer className="app-footer">
        <div className="footer-facts">
          {summary ? (
            <>
              <span>{summary.procedures_loaded} procedures</span>
              <span>{summary.published_procedures} published</span>
              <span>{summary.dictionary_entries} dictionary terms</span>
              <span>
                connectors{" "}
                {summary.connectors_enabled ? "ENABLED" : "disabled"}
              </span>
              {summary.live?.enabled ? (
                <span>
                  live feed {summary.live.running ? "polling" : "idle"}
                </span>
              ) : null}
            </>
          ) : null}
        </div>
        {(summary?.registry_errors || []).length ? (
          <span style={{ color: "var(--danger)" }}>
            {summary.registry_errors.length} procedure load error(s)
          </span>
        ) : null}
      </footer>
    </div>
  );
}

import { useCallback, useEffect, useMemo, useState } from "react";

import { api, ApiError } from "../api.js";
import {
  FacetSelect,
  facetOptions,
  SearchInput,
} from "../components/FilterBar.jsx";
import {
  Badge,
  Callout,
  Checkbox,
  EmptyState,
  Message,
  Panel,
} from "../components/primitives.jsx";
import { liveStatus as liveStatusInfo, shortTime } from "../vocabulary.js";

/* Live ticket intake.
 *
 * Read-only: the server only ever issues GET against the ServiceNow Table API.
 * Reading live tickets does not enable live execution, and the panel states both
 * facts rather than leaving them to be assumed.
 */

export default function LiveView({ onRefresh, busy }) {
  const [status, setStatus] = useState(null);
  const [cfg, setCfg] = useState(null);
  const [queryOptions, setQueryOptions] = useState([]);
  const [tickets, setTickets] = useState([]);
  const [msg, setMsg] = useState(null);
  const [advanced, setAdvanced] = useState(false);

  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [group, setGroup] = useState("");
  const [matchedOnly, setMatchedOnly] = useState(false);

  const load = useCallback(async () => {
    const [s, c, t] = await Promise.all([
      api.liveStatus(),
      api.liveConfig(),
      api.liveTickets(500),
    ]);
    setStatus(s);
    setCfg((prev) => prev ?? c); // don't clobber in-progress edits
    setTickets(t.tickets);
  }, []);

  useEffect(() => {
    load().catch((e) => setMsg({ kind: "error", text: e.message }));
    api
      .liveQueryOptions()
      .then((o) => setQueryOptions(o.filters))
      .catch(() => setQueryOptions([]));
  }, [load]);

  // A dropdown or the group field changed: update the structured filters.
  function setFilter(key, value) {
    setCfg((c) => ({ ...c, filters: { ...(c.filters || {}), [key]: value } }));
  }

  async function run(fn, okText) {
    setMsg({ kind: "", text: "Working…" });
    try {
      const r = await fn();
      setMsg({ kind: "ok", text: typeof okText === "function" ? okText(r) : okText });
      const [s, t] = await Promise.all([api.liveStatus(), api.liveTickets(500)]);
      setStatus(s);
      setTickets(t.tickets);
      await onRefresh();
    } catch (err) {
      setMsg({ kind: "error", text: err.message });
    }
  }

  async function save() {
    const changes = {
      table: cfg.table?.trim(),
      // Structured filters are the source of truth; the server derives the
      // encoded query from them. raw_query is the advanced override.
      filters: cfg.filters,
      group: (cfg.group || "").trim(),
      raw_query: (cfg.raw_query || "").trim(),
      interval_seconds: Number(cfg.interval_seconds),
      page_size: Number(cfg.page_size),
      auto_propose: !!cfg.auto_propose,
      only_groups: (cfg.only_groups || []).filter(Boolean),
      enabled: !!cfg.enabled,
    };
    await run(async () => {
      const fresh = await api.updateLiveConfig(changes);
      setCfg(fresh);
      return fresh;
    }, "Settings saved.");
  }

  async function createRun(ticket) {
    try {
      await api.proposeFromLive(ticket.live_id);
      setMsg({ kind: "ok", text: "Run created. See the Queue tab." });
    } catch (err) {
      if (!(err instanceof ApiError) || err.info?.code !== "duplicate_run") {
        setMsg({ kind: "error", text: err.message });
        return;
      }
      const again = confirm(
        `${err.info.ticket_id} already has a run in progress ` +
          `(${String(err.info.existing_state).replace(/_/g, " ")}).\n\n` +
          `Create a second run anyway?`
      );
      if (!again) {
        setMsg({ kind: "", text: "Left as is. The existing run is in the Queue tab." });
        return;
      }
      try {
        await api.proposeFromLive(ticket.live_id, true);
        setMsg({ kind: "ok", text: "Second run created deliberately." });
      } catch (e2) {
        setMsg({ kind: "error", text: e2.message });
        return;
      }
    }
    const [s, t] = await Promise.all([api.liveStatus(), api.liveTickets(500)]);
    setStatus(s);
    setTickets(t.tickets);
    await onRefresh();
  }

  const statusOptions = useMemo(() => {
    const counts = new Map();
    for (const t of tickets)
      counts.set(t.status, (counts.get(t.status) || 0) + 1);
    return [...counts.entries()].map(([value, count]) => ({
      value,
      label: liveStatusInfo(value).word,
      count,
    }));
  }, [tickets]);

  const groupOptions = useMemo(
    () => facetOptions(tickets, (t) => t.payload?.assignment_group),
    [tickets]
  );

  const visible = useMemo(() => {
    const n = search.trim().toLowerCase();
    return tickets.filter((t) => {
      if (statusFilter && t.status !== statusFilter) return false;
      if (matchedOnly && !t.match?.matched) return false;
      if (group && (t.payload?.assignment_group || "") !== group) return false;
      if (n) {
        const hay = [
          t.ticket_number,
          t.payload?.short_description,
          t.payload?.assignment_group,
          t.match?.procedure_name,
        ]
          .join(" ")
          .toLowerCase();
        if (!hay.includes(n)) return false;
      }
      return true;
    });
  }, [tickets, search, statusFilter, group, matchedOnly]);

  const matchedCount = tickets.filter((t) => t.match?.matched).length;

  if (!status || !cfg) {
    return (
      <div className="layout layout-single">
        <Panel title="Live ticket feed">
          <EmptyState>Loading…</EmptyState>
        </Panel>
      </div>
    );
  }

  return (
    <div className="layout layout-wide">
      {/* ---------------- config ---------------- */}
      <Panel
        title="Live ticket feed"
        badge={
          <Badge
            tone={status.running ? "bucket-active" : "neutral"}
            pill
          >
            {status.enabled ? (status.running ? "polling" : "enabled, idle") : "off"}
          </Badge>
        }
      >
        {!status.credentials_present ? (
          <Callout kind="warn" title="Credentials not configured">
            <p style={{ marginTop: "0.2rem" }}>
              Set <code>SERVICENOW_INSTANCE</code>,{" "}
              <code>SERVICENOW_USERNAME</code> and{" "}
              <code>SERVICENOW_PASSWORD</code> in <code>.env</code>, then restart
              the server.
            </p>
          </Callout>
        ) : null}

        {status.last_error ? (
          <Callout kind="danger" title="Last error">
            <p style={{ marginTop: "0.2rem" }}>{status.last_error}</p>
          </Callout>
        ) : null}

        <dl className="kv" style={{ marginTop: "0.6rem" }}>
          <dt>Instance</dt>
          <dd>{status.instance || "—"}</dd>
          <dt>Access</dt>
          <dd>{status.access}</dd>
          <dt>Execution</dt>
          <dd>{status.execution}</dd>
          <dt>Last poll</dt>
          <dd>
            {status.last_poll_at
              ? `${shortTime(status.last_poll_at)} — fetched ${status.last_fetched}, new ${status.last_new}`
              : "never"}
          </dd>
          <dt>Ingested total</dt>
          <dd>{status.total_ingested ?? 0}</dd>
        </dl>

        <h3>Which tickets to pull</h3>

        {queryOptions.map((f) => (
          <div className="field" key={f.key}>
            <label htmlFor={`live-${f.key}`}>{f.label}</label>
            <select
              id={`live-${f.key}`}
              className="select"
              value={(cfg.filters || {})[f.key] ?? f.default}
              disabled={status.using_advanced_query}
              onChange={(e) => setFilter(f.key, e.target.value)}
            >
              {f.options.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
            <p className="hint">{f.help}</p>
          </div>
        ))}

        <div className="field">
          <label htmlFor="live-group">Assignment group (optional)</label>
          <input
            id="live-group"
            className="input"
            placeholder="Any group — e.g. Global Linux Support"
            value={cfg.group ?? ""}
            disabled={status.using_advanced_query}
            onChange={(e) => setCfg({ ...cfg, group: e.target.value })}
          />
          <p className="hint">
            Leave empty to pull every group. Type one group to focus a pilot.
          </p>
        </div>

        {/* Plain-English preview of what the dropdowns will fetch. */}
        <div className="callout callout-info" style={{ marginBottom: "var(--sp-4)" }}>
          <strong>This will pull</strong>
          {status.using_advanced_query
            ? " tickets matching your advanced query below."
            : ` ${status.query_summary}.`}
        </div>

        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr 1fr",
            gap: "0.75rem",
          }}
        >
          <div className="field">
            <label htmlFor="live-interval">Poll every (seconds)</label>
            <input
              id="live-interval"
              type="number"
              min="30"
              className="input"
              value={cfg.interval_seconds ?? 120}
              onChange={(e) =>
                setCfg({ ...cfg, interval_seconds: e.target.value })
              }
            />
          </div>
          <div className="field">
            <label htmlFor="live-page">Tickets per poll</label>
            <input
              id="live-page"
              type="number"
              min="1"
              max="200"
              className="input"
              value={cfg.page_size ?? 50}
              onChange={(e) => setCfg({ ...cfg, page_size: e.target.value })}
            />
          </div>
        </div>

        <div className="field">
          <label htmlFor="live-groups">
            Only these assignment groups (optional)
          </label>
          <input
            id="live-groups"
            className="input"
            placeholder="Global Wintel Server Support, Global Linux Support"
            value={(cfg.only_groups || []).join(", ")}
            onChange={(e) =>
              setCfg({
                ...cfg,
                only_groups: e.target.value.split(",").map((g) => g.trim()),
              })
            }
          />
          <p className="hint">Leave empty to take every group.</p>
        </div>

        <Checkbox
          id="live-auto"
          checked={!!cfg.auto_propose}
          onChange={(v) => setCfg({ ...cfg, auto_propose: v })}
        >
          Automatically create a run when a ticket matches{" "}
          <em>(it still needs approval before anything runs)</em>
        </Checkbox>

        <Checkbox
          id="live-enabled"
          checked={!!cfg.enabled}
          onChange={(v) => setCfg({ ...cfg, enabled: v })}
        >
          Keep polling in the background
        </Checkbox>

        {/* Advanced: the raw query and table, for the rare case the dropdowns
            do not cover. Hidden by default so the common path stays simple. */}
        <details
          open={advanced || status.using_advanced_query}
          onToggle={(e) => setAdvanced(e.target.open)}
          style={{ marginTop: "var(--sp-3)" }}
        >
          <summary>Advanced</summary>

          <div className="field" style={{ marginTop: "var(--sp-3)" }}>
            <label htmlFor="live-table">Table (from the ticket type)</label>
            <input
              id="live-table"
              className="input mono"
              value={status.table ?? cfg.table ?? ""}
              readOnly
            />
            <p className="hint">
              Set automatically by the ticket type you chose above. Each type is
              a different ServiceNow table. Use a custom query below to read a
              table not listed.
            </p>
          </div>

          <div className="field">
            <label htmlFor="live-raw">Custom query (overrides the dropdowns)</label>
            <input
              id="live-raw"
              className="input mono"
              placeholder="leave empty to use the dropdowns above"
              value={cfg.raw_query ?? ""}
              onChange={(e) => setCfg({ ...cfg, raw_query: e.target.value })}
            />
            <p className="hint">
              A ServiceNow encoded query. When set, it takes over from the
              dropdowns. In ServiceNow, filter a list, right-click the breadcrumb
              and choose <em>Copy query</em> to get one.
            </p>
          </div>

          <div className="callout" style={{ background: "var(--surface-2)" }}>
            <strong>Effective query</strong>
            <code style={{ display: "block", marginTop: "0.3rem", wordBreak: "break-all" }}>
              {status.query || "(empty — pulls everything)"}
            </code>
          </div>
        </details>

        <div className="row" style={{ marginTop: "0.6rem" }}>
          <button
            type="button"
            className="btn btn-primary"
            disabled={busy}
            onClick={save}
          >
            Save settings
          </button>
          <button
            type="button"
            className="btn"
            disabled={busy}
            onClick={() =>
              run(
                () => api.liveProbe(),
                (r) =>
                  `Connected to ${r.instance}, table ${r.table}. Sample: ${r.sample_number || "none returned"}.`
              )
            }
          >
            Test connection
          </button>
          <button
            type="button"
            className="btn btn-run"
            disabled={busy}
            onClick={() =>
              run(
                () => api.livePoll(),
                (r) => `Fetched ${r.fetched}, ${r.new} new.`
              )
            }
          >
            Poll now
          </button>
        </div>
        <Message kind={msg?.kind}>{msg?.text}</Message>
      </Panel>

      {/* ---------------- inbox ---------------- */}
      <Panel
        title="Inbox"
        badge={
          <span className="badge badge-pill">
            {visible.length === tickets.length
              ? `${tickets.length} total · ${matchedCount} actionable`
              : `${visible.length} of ${tickets.length}`}
          </span>
        }
      >
        <div className="filter-controls">
          <SearchInput
            value={search}
            onChange={setSearch}
            placeholder="Search ticket, description, group…"
          />
          <FacetSelect
            value={statusFilter}
            onChange={setStatusFilter}
            options={statusOptions}
            allLabel={`All statuses (${tickets.length})`}
            label="Filter by status"
          />
          <FacetSelect
            value={group}
            onChange={setGroup}
            options={groupOptions}
            allLabel="All groups"
            label="Filter by assignment group"
          />
        </div>
        <Checkbox
          id="matched-only"
          checked={matchedOnly}
          onChange={setMatchedOnly}
        >
          Only show tickets a procedure can handle
        </Checkbox>

        {!tickets.length ? (
          <EmptyState>
            Nothing pulled in yet. Press “Poll now”, or turn on background
            polling.
          </EmptyState>
        ) : !visible.length ? (
          <EmptyState>No tickets match these filters.</EmptyState>
        ) : (
          visible.map((t) => (
            <InboxRow
              key={t.live_id}
              ticket={t}
              busy={busy}
              onCreate={() => createRun(t)}
              onIgnore={() => {
                const reason = prompt("Why ignore this ticket?") ?? "";
                run(
                  () => api.ignoreLiveTicket(t.live_id, reason),
                  "Dismissed."
                );
              }}
            />
          ))
        )}
      </Panel>
    </div>
  );
}

/* ------------------------------------------------------------- inbox row */

function InboxRow({ ticket, onCreate, onIgnore, busy }) {
  const m = ticket.match || {};
  const p = ticket.payload || {};
  const matched = !!m.matched;
  const info = liveStatusInfo(ticket.status);

  return (
    <div className={`inbox-row${matched ? " inbox-match" : ""}`}>
      <div className="inbox-main">
        <div className="inbox-head">
          <strong>{ticket.ticket_number}</strong>
          <Badge tone={info.tone} title={info.help} pill>
            {info.word}
          </Badge>
          {matched ? (
            <Badge tone="agent">{m.procedure_name}</Badge>
          ) : null}
        </div>

        <p className="inbox-desc">
          {p.short_description || "(no description)"}
        </p>

        <div className="inbox-meta">
          {p.assignment_group || "no group"}
          {p.cmdb_ci ? ` · CI ${p.cmdb_ci}` : ""}
          {p.priority ? ` · P${p.priority}` : ""}
          {` · fetched ${shortTime(ticket.fetched_at)}`}
        </div>

        {(m.missing_parameters || []).length ? (
          <p className="hint">
            Missing details: {m.missing_parameters.join(", ")}
          </p>
        ) : null}

        {(m.dictionary_terms_applied || []).length ? (
          <p className="hint">
            Dictionary helped: {m.dictionary_terms_applied.join(", ")}
          </p>
        ) : null}

        {m.auto_propose_error ? (
          <p className="hint" style={{ color: "var(--danger)" }}>
            Auto-create failed: {m.auto_propose_error}
          </p>
        ) : null}
      </div>

      <div className="inbox-actions">
        {ticket.run_id ? (
          <span className="hint">run created</span>
        ) : matched ? (
          <button
            type="button"
            className="btn btn-ok btn-sm"
            disabled={busy}
            onClick={onCreate}
          >
            Create run
          </button>
        ) : null}
        {ticket.status !== "ignored" ? (
          <button
            type="button"
            className="btn btn-sm"
            disabled={busy}
            onClick={onIgnore}
          >
            Ignore
          </button>
        ) : null}
      </div>
    </div>
  );
}

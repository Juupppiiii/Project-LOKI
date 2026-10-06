import { useEffect, useMemo, useState } from "react";

import { api, ApiError } from "../api.js";
import {
  FacetSelect,
  facetOptions,
  SearchInput,
  Segmented,
  SortSelect,
} from "../components/FilterBar.jsx";
import {
  EmptyState,
  Message,
  Panel,
  Progress,
  StateTag,
} from "../components/primitives.jsx";
import { BUCKETS, state as stateInfo } from "../vocabulary.js";
import RunDetail from "./RunDetail.jsx";

const SORTS = [
  { value: "newest", label: "Newest first" },
  { value: "oldest", label: "Oldest first" },
  { value: "remaining", label: "Most steps left" },
  { value: "progress", label: "Closest to done" },
];

/* The work queue.
 *
 * Opens on "Needs you" because that is the only list that matters most of the
 * time. Filtering and sorting happen here in the browser over the runs already
 * loaded; every decision about what may be approved or run stays on the server.
 */

export default function QueueView({ runs, onRefresh, busy }) {
  const [bucket, setBucket] = useState("you");
  const [search, setSearch] = useState("");
  const [group, setGroup] = useState("");
  const [sort, setSort] = useState("newest");

  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [audit, setAudit] = useState([]);

  const [tickets, setTickets] = useState([]);
  const [pickIndex, setPickIndex] = useState("");
  const [intakeMsg, setIntakeMsg] = useState(null);
  const [adding, setAdding] = useState(false);

  /* -- sample tickets for the picker -- */
  useEffect(() => {
    let alive = true;
    api
      .sampleTickets(60)
      .then((r) => alive && setTickets(r.tickets))
      .catch(() => alive && setTickets([]));
    return () => {
      alive = false;
    };
  }, []);

  /* -- keep the open run in sync when the list refreshes -- */
  useEffect(() => {
    if (!selectedId) return;
    let alive = true;
    Promise.all([api.getRun(selectedId), api.audit(selectedId)])
      .then(([run, a]) => {
        if (!alive) return;
        setDetail(run);
        setAudit(a.events);
      })
      .catch(() => {
        if (!alive) return;
        setDetail(null);
        setSelectedId(null);
      });
    return () => {
      alive = false;
    };
  }, [selectedId, runs]);

  const counts = useMemo(() => {
    const c = { you: 0, active: 0, problem: 0, done: 0, all: runs.length };
    for (const r of runs) {
      const b = stateInfo(r.state).bucket;
      if (b in c) c[b] += 1;
    }
    return c;
  }, [runs]);

  const groupOptions = useMemo(
    () => facetOptions(runs, (r) => r.assignment_group),
    [runs]
  );

  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const remaining = (r) => r.total_step_count - r.completed_step_count;

    const sorters = {
      newest: (a, b) => (a.created_at < b.created_at ? 1 : -1),
      oldest: (a, b) => (a.created_at > b.created_at ? 1 : -1),
      remaining: (a, b) => remaining(b) - remaining(a),
      progress: (a, b) => remaining(a) - remaining(b),
    };

    return runs
      .filter((r) => {
        if (bucket !== "all" && stateInfo(r.state).bucket !== bucket) return false;
        if (group && (r.assignment_group || "") !== group) return false;
        if (needle) {
          const hay = [
            r.ticket_id,
            r.ticket.short_description,
            r.procedure_name,
            r.assignment_group,
          ]
            .join(" ")
            .toLowerCase();
          if (!hay.includes(needle)) return false;
        }
        return true;
      })
      .sort(sorters[sort] || sorters.newest);
  }, [runs, bucket, group, search, sort]);

  async function addTicket() {
    const ticket = tickets[Number(pickIndex)];
    if (!ticket) {
      setIntakeMsg({ kind: "error", text: "Pick a ticket first." });
      return;
    }

    setAdding(true);
    setIntakeMsg({ kind: "", text: "Matching…" });

    // Strip the client-side annotation before sending.
    const { _match, ...clean } = ticket;

    try {
      let run;
      try {
        run = await api.createRun(clean);
      } catch (err) {
        if (!(err instanceof ApiError) || err.info?.code !== "duplicate_run") {
          throw err;
        }
        const openExisting = confirm(
          `${err.info.ticket_id} already has a run in progress ` +
            `(${String(err.info.existing_state).replace(/_/g, " ")}).\n\n` +
            `OK — open the existing run\n` +
            `Cancel — create a second run anyway`
        );
        if (openExisting) {
          await onRefresh();
          setSelectedId(err.info.existing_run_id);
          setIntakeMsg({
            kind: "ok",
            text: "Opened the run that already exists.",
          });
          return;
        }
        run = await api.createRun(clean, true);
      }

      setIntakeMsg({ kind: "ok", text: `Matched ${run.procedure_name}.` });
      await onRefresh();
      setSelectedId(run.run_id);
    } catch (err) {
      setIntakeMsg({ kind: "error", text: err.message });
    } finally {
      setAdding(false);
    }
  }

  const matchable = tickets.filter((t) => t._match?.matched);
  const unmatchable = tickets.filter((t) => !t._match?.matched);

  return (
    <div className="layout">
      {/* ---------------- queue ---------------- */}
      <Panel
        title="Queue"
        badge={<span className="badge badge-pill">{runs.length} total</span>}
        scroll={false}
      >
        <div className="filter-bar">
          <Segmented
            options={BUCKETS}
            value={bucket}
            onChange={setBucket}
            counts={counts}
            label="Filter by what needs doing"
          />
          <div className="filter-controls">
            <SearchInput
              value={search}
              onChange={setSearch}
              placeholder="Search ticket, description, group…"
            />
            <FacetSelect
              value={group}
              onChange={setGroup}
              options={groupOptions}
              allLabel="All groups"
              label="Filter by assignment group"
            />
            <SortSelect value={sort} onChange={setSort} options={SORTS} />
          </div>
          <p className="filter-showing">
            {visible.length === runs.length
              ? `Showing all ${visible.length}`
              : `Showing ${visible.length} of ${runs.length}`}
          </p>
        </div>

        {/* intake */}
        <div
          style={{
            padding: "0.75rem 1rem",
            borderBottom: "1px solid var(--line)",
          }}
        >
          <label className="label" htmlFor="ticket-picker">
            Add a ticket from extracted data
          </label>
          <select
            id="ticket-picker"
            className="select"
            value={pickIndex}
            onChange={(e) => setPickIndex(e.target.value)}
          >
            <option value="">Choose a ticket…</option>
            {matchable.length ? (
              <optgroup label={`A procedure applies (${matchable.length})`}>
                {matchable.map((t) => (
                  <option key={t.number} value={tickets.indexOf(t)}>
                    {t.number} — {(t.short_description || "").slice(0, 60)} ·{" "}
                    {t._match.procedure_name}
                  </option>
                ))}
              </optgroup>
            ) : null}
            {unmatchable.length ? (
              <optgroup
                label={`No procedure yet — stays with a person (${unmatchable.length})`}
              >
                {unmatchable.map((t) => (
                  <option key={t.number} value={tickets.indexOf(t)}>
                    {t.number} — {(t.short_description || "").slice(0, 60)}
                  </option>
                ))}
              </optgroup>
            ) : null}
          </select>
          <p className="hint" style={{ marginTop: "0.3rem" }}>
            {matchable.length} of {tickets.length} scanned tickets have a
            codified procedure.
          </p>
          <button
            type="button"
            className="btn btn-primary btn-block"
            style={{ marginTop: "0.4rem" }}
            disabled={adding || busy}
            onClick={addTicket}
          >
            {adding ? "Matching…" : "Match & add to queue"}
          </button>
          <Message kind={intakeMsg?.kind}>{intakeMsg?.text}</Message>
        </div>

        {/* rows */}
        <div style={{ overflowY: "auto", minHeight: 0, flex: "1 1 auto" }}>
          {!runs.length ? (
            <EmptyState>
              Nothing queued yet. Add a ticket above, or pull real ones from the
              Live tab.
            </EmptyState>
          ) : !visible.length ? (
            <EmptyState
              action={
                <button
                  type="button"
                  className="btn btn-quiet"
                  onClick={() => {
                    setBucket("all");
                    setSearch("");
                    setGroup("");
                  }}
                >
                  Clear filters
                </button>
              }
            >
              Nothing under{" "}
              <strong>{BUCKETS.find((b) => b.id === bucket)?.label}</strong>
              {search ? ` matching “${search}”` : ""}.
            </EmptyState>
          ) : (
            <ul className="run-list">
              {visible.map((run) => (
                <li key={run.run_id}>
                  <button
                    type="button"
                    className="run-item"
                    aria-current={run.run_id === selectedId}
                    onClick={() => setSelectedId(run.run_id)}
                  >
                    <span className="run-item-top">
                      <span className="run-ticket truncate">
                        {run.ticket_id}
                        {run.runs_for_ticket > 1 ? (
                          <span
                            className="dup-flag"
                            title={`This ticket has ${run.runs_for_ticket} runs. Check you are working the right one.`}
                          >
                            {run.runs_for_ticket} runs
                          </span>
                        ) : null}
                      </span>
                      <StateTag code={run.state} />
                    </span>
                    <p className="run-desc clamp-2">
                      {run.ticket.short_description || "(no description)"}
                    </p>
                    <Progress
                      done={run.completed_step_count}
                      total={run.total_step_count}
                    />
                    <span className="run-meta">
                      <span className="badge tone-neutral">
                        {run.completed_step_count}/{run.total_step_count} steps
                      </span>
                      <span className="badge tone-agent">
                        {run.ai_step_count} agent
                      </span>
                      <span className="badge tone-neutral">
                        {run.human_step_count} you
                      </span>
                      {run.assignment_group ? (
                        <span className="badge tone-neutral truncate">
                          {run.assignment_group}
                        </span>
                      ) : null}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </Panel>

      {/* ---------------- detail ---------------- */}
      <Panel
        title={
          detail
            ? `${detail.ticket_id} — ${detail.procedure_name}`
            : "Select a ticket"
        }
        badge={detail ? <StateTag code={detail.state} /> : null}
      >
        {detail ? (
          <RunDetail
            run={detail}
            audit={audit}
            busy={busy}
            onChanged={onRefresh}
          />
        ) : (
          <EmptyState>
            Pick a ticket from the queue to see the step-by-step plan and what
            the agent is allowed to do.
          </EmptyState>
        )}
      </Panel>
    </div>
  );
}

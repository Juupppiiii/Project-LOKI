import { useCallback, useEffect, useMemo, useState } from "react";

import { api } from "../api.js";
import { SearchInput } from "../components/FilterBar.jsx";
import {
  Badge,
  Callout,
  Checkbox,
  EmptyState,
  Message,
  Panel,
  Spinner,
} from "../components/primitives.jsx";
import { shortTime } from "../vocabulary.js";

/* Investigation workspace.
 *
 * The interface is an incident case file, not a chat transcript. Information is
 * separated by provenance: observed ServiceNow facts, retrieved history,
 * deterministic inference, read-only diagnostics, and human validation. The UI
 * renders server decisions; it never promotes a recommendation or authorises a
 * workflow itself.
 */

const VERDICTS = [
  ["accepted", "Useful and correct"],
  ["partially_useful", "Partly useful"],
  ["rejected", "Not useful"],
  ["insufficient_evidence", "Not enough evidence"],
];

const EMPTY_FEEDBACK = {
  verdict: "accepted",
  time_saved_minutes: "",
  human_intervention: false,
  recurrence: "",
  useful_steps: "",
  wrong_steps: "",
  actual_resolution: "",
  notes: "",
};

function titleCase(value = "") {
  return String(value).replaceAll("_", " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function percent(value) {
  return value == null ? "—" : `${Math.round(Number(value) * 100)}%`;
}

function downloadMarkdown(pack) {
  const blob = new Blob([pack.markdown || ""], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${pack.ticket_ref || "ticket"}-evidence-pack.md`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export default function InvestigationView({ onRefresh }) {
  const [tickets, setTickets] = useState([]);
  const [selectedId, setSelectedId] = useState("");
  const [pack, setPack] = useState(null);
  const [summary, setSummary] = useState(null);
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [packBusy, setPackBusy] = useState(false);
  const [opusBusy, setOpusBusy] = useState(false);
  const [actionBusy, setActionBusy] = useState(false);
  const [message, setMessage] = useState(null);
  const [serviceName, setServiceName] = useState("Spooler");
  const [feedback, setFeedback] = useState(EMPTY_FEEDBACK);
  const [triageRuns, setTriageRuns] = useState([]);
  const [triageBusy, setTriageBusy] = useState(false);

  const loadIndex = useCallback(async () => {
    setLoading(true);
    try {
      const [ticketResponse, feedbackSummary] = await Promise.all([
        api.liveTickets(500),
        api.feedbackSummary(),
      ]);
      const nextTickets = ticketResponse.tickets || [];
      setTickets(nextTickets);
      setSummary(feedbackSummary);
      setSelectedId((current) => current || nextTickets[0]?.live_id || "");
      setMessage(null);
    } catch (error) {
      setMessage({ kind: "error", text: error.message });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadIndex();
  }, [loadIndex]);

  const loadPack = useCallback(async (liveId, includeOpus = false) => {
    if (!liveId) return;
    includeOpus ? setOpusBusy(true) : setPackBusy(true);
    try {
      const response = await api.evidencePack(liveId, includeOpus);
      setPack(response);
      setMessage(null);
    } catch (error) {
      setMessage({ kind: "error", text: error.message });
    } finally {
      includeOpus ? setOpusBusy(false) : setPackBusy(false);
    }
  }, []);

  const loadTriage = useCallback(async (liveId) => {
    if (!liveId) return;
    try {
      const response = await api.listTriage(liveId);
      setTriageRuns(response.triage_runs || []);
    } catch {
      setTriageRuns([]);
    }
  }, []);

  useEffect(() => {
    setPack(null);
    setFeedback(EMPTY_FEEDBACK);
    setTriageRuns([]);
    if (selectedId) {
      loadPack(selectedId);
      loadTriage(selectedId);
    }
  }, [selectedId, loadPack, loadTriage]);

  async function runTriage() {
    if (!selectedId) return;
    setTriageBusy(true);
    try {
      const result = await api.runTriage(selectedId);
      setMessage({
        kind: "ok",
        text: `Triage complete: ${titleCase(result.classification)}${
          result.routed_to_human ? " — routed to a human." : "."
        }`,
      });
      await loadTriage(selectedId);
    } catch (error) {
      setMessage({ kind: "error", text: error.message });
    } finally {
      setTriageBusy(false);
    }
  }

  const selectedTicket = useMemo(
    () => tickets.find((ticket) => ticket.live_id === selectedId) || null,
    [tickets, selectedId]
  );

  const visibleTickets = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) return tickets;
    return tickets.filter((ticket) =>
      [
        ticket.ticket_number,
        ticket.payload?.short_description,
        ticket.payload?.assignment_group,
        ticket.payload?.cmdb_ci,
      ]
        .join(" ")
        .toLowerCase()
        .includes(needle)
    );
  }, [tickets, search]);

  async function runDiagnostic(operationId) {
    if (!selectedTicket) return;
    const target = selectedTicket.payload?.cmdb_ci;
    if (!target) {
      setMessage({ kind: "error", text: "This ticket has no configuration item to target." });
      return;
    }
    const parameters =
      operationId === "service_status"
        ? { target, service_name: serviceName.trim() }
        : { target };
    setActionBusy(true);
    try {
      const result = await api.runDiagnostic(
        operationId,
        parameters,
        selectedTicket.ticket_number
      );
      setMessage({
        kind: "ok",
        text: `${titleCase(operationId)} collected (${result.simulated ? "simulation" : "live read-only"}).`,
      });
      await loadPack(selectedId);
    } catch (error) {
      setMessage({ kind: "error", text: error.message });
    } finally {
      setActionBusy(false);
    }
  }

  async function saveFeedback(event) {
    event.preventDefault();
    if (!pack) return;
    const procedureId = pack.inferred_recommendation?.candidates?.[0]?.procedure_id || "";
    setActionBusy(true);
    try {
      await api.saveRecommendationFeedback({
        ticket_ref: pack.ticket_ref,
        live_id: pack.live_id,
        procedure_id: procedureId,
        verdict: feedback.verdict,
        useful_steps: feedback.useful_steps
          .split("\n")
          .map((item) => item.trim())
          .filter(Boolean),
        wrong_steps: feedback.wrong_steps
          .split("\n")
          .map((item) => item.trim())
          .filter(Boolean),
        actual_resolution: feedback.actual_resolution,
        notes: feedback.notes,
        human_intervention: feedback.human_intervention,
        time_saved_minutes:
          feedback.time_saved_minutes === "" ? null : Number(feedback.time_saved_minutes),
        recurrence: feedback.recurrence === "" ? null : feedback.recurrence === "yes",
      });
      setFeedback(EMPTY_FEEDBACK);
      setMessage({ kind: "ok", text: "Recommendation feedback recorded." });
      await Promise.all([loadPack(selectedId), loadIndex(), onRefresh?.()]);
    } catch (error) {
      setMessage({ kind: "error", text: error.message });
    } finally {
      setActionBusy(false);
    }
  }

  async function copyPack() {
    try {
      await navigator.clipboard.writeText(pack?.markdown || "");
      setMessage({ kind: "ok", text: "Evidence pack copied as Markdown." });
    } catch {
      setMessage({ kind: "error", text: "Clipboard access was blocked. Download the pack instead." });
    }
  }

  if (loading && !tickets.length) {
    return (
      <div className="layout layout-single">
        <Panel title="Investigation workspace">
          <EmptyState><Spinner label="Loading investigations" /></EmptyState>
        </Panel>
      </div>
    );
  }

  return (
    <div className="investigation-layout">
      <Panel
        title="Ticket docket"
        badge={<Badge pill>{visibleTickets.length} tickets</Badge>}
        className="investigation-docket"
      >
        <SearchInput
          value={search}
          onChange={setSearch}
          placeholder="Find ticket, CI, or group…"
        />
        <p className="hint investigation-docket-hint">
          Select a ticket to assemble its evidence case file.
        </p>
        <div className="case-list" aria-label="Live tickets">
          {visibleTickets.length ? (
            visibleTickets.map((ticket) => (
              <button
                type="button"
                key={ticket.live_id}
                className="case-list-item"
                aria-pressed={ticket.live_id === selectedId}
                onClick={() => setSelectedId(ticket.live_id)}
              >
                <span className="case-list-number">{ticket.ticket_number}</span>
                <span className="case-list-title">
                  {ticket.payload?.short_description || "No description"}
                </span>
                <span className="case-list-meta">
                  {ticket.payload?.assignment_group || "No group"}
                  {ticket.payload?.cmdb_ci ? ` · ${ticket.payload.cmdb_ci}` : ""}
                </span>
              </button>
            ))
          ) : (
            <EmptyState>No tickets match this search.</EmptyState>
          )}
        </div>
      </Panel>

      <Panel
        title={pack ? `Case file · ${pack.ticket_ref}` : "Evidence case file"}
        className="investigation-file"
        badge={
          pack ? (
            <Badge tone={pack.inferred_recommendation?.abstained ? "neutral" : "agent"} pill>
              {pack.inferred_recommendation?.abstained ? "insufficient evidence" : "recommendation ready"}
            </Badge>
          ) : null
        }
        actions={packBusy ? <Spinner label="Building evidence pack" /> : null}
      >
        <Message kind={message?.kind}>{message?.text}</Message>
        {!selectedId ? (
          <EmptyState>Pull or select a live ticket to begin an investigation.</EmptyState>
        ) : !pack ? (
          <EmptyState><Spinner label="Building evidence pack" /></EmptyState>
        ) : (
          <article className="case-file">
            <header className="case-file-hero">
              <div>
                <div className="row">
                  <strong className="case-file-number">{pack.ticket_ref}</strong>
                  <Badge pill>{pack.status.replaceAll("_", " ")}</Badge>
                  <Badge tone="R0">read-only investigation</Badge>
                </div>
                <h2>{selectedTicket?.payload?.short_description || "No description"}</h2>
                <p>
                  {selectedTicket?.payload?.assignment_group || "No assignment group"}
                  {selectedTicket?.payload?.cmdb_ci
                    ? ` · CI ${selectedTicket.payload.cmdb_ci}`
                    : " · no CI"}
                </p>
              </div>
              <div className="case-file-actions">
                <button type="button" className="btn btn-primary" onClick={copyPack}>
                  Copy pack
                </button>
                <button type="button" className="btn" onClick={() => downloadMarkdown(pack)}>
                  Download .md
                </button>
                <button
                  type="button"
                  className="btn btn-run"
                  disabled={opusBusy}
                  onClick={() => loadPack(selectedId, true)}
                >
                  {opusBusy ? "Asking Opus…" : pack.opus ? "Refresh Opus advice" : "Ask Opus"}
                </button>
              </div>
            </header>

            <div className="provenance-strip" aria-label="Evidence provenance">
              <Provenance label="Observed" value={pack.observed_facts.length} help="ServiceNow facts" />
              <Provenance
                label="Retrieved"
                value={pack.retrieved_evidence.retrieved_ticket_count}
                help="historical tickets"
              />
              <Provenance
                label="Inferred"
                value={pack.inferred_recommendation.candidates.length}
                help="ranked candidates"
              />
              <Provenance label="Validated" value={pack.feedback.length} help="human feedback" />
            </div>

            <CaseSection title="Observed facts" source="ServiceNow">
              <dl className="evidence-ledger">
                {pack.observed_facts.map((fact) => (
                  <div key={fact.field}>
                    <dt>{titleCase(fact.field)}</dt>
                    <dd>{String(fact.value)}</dd>
                  </div>
                ))}
              </dl>
            </CaseSection>

            <CaseSection title="Ranked recommendation" source="Deterministic inference">
              <Callout
                kind={pack.inferred_recommendation.abstained ? "warn" : "info"}
                title={titleCase(pack.inferred_recommendation.decision)}
              >
                <p>{pack.inferred_recommendation.reason}</p>
              </Callout>
              <div className="candidate-ledger">
                {pack.inferred_recommendation.candidates.slice(0, 5).map((candidate, index) => (
                  <CandidateRow key={candidate.procedure_id} candidate={candidate} rank={index + 1} />
                ))}
              </div>
            </CaseSection>

            <CaseSection title="Triage agent" source="Read-only investigation">
              <Callout kind="info" title="Agentic read-only triage">
                <p>
                  The triage agent runs a bounded, read-only investigation of this
                  ticket (connectivity, name resolution, neighbour flap, change
                  window) and drafts a recommendation. It executes nothing; a human
                  approves any disposition.
                </p>
              </Callout>
              <div className="diagnostic-toolbar">
                <button
                  type="button"
                  className="btn btn-run"
                  disabled={triageBusy}
                  onClick={runTriage}
                >
                  {triageBusy ? "Investigating…" : "Run triage"}
                </button>
              </div>
              {triageRuns.length ? (
                <div className="triage-ledger">
                  {triageRuns.map((run) => (
                    <TriageRunCard key={run.triage_id} run={run} />
                  ))}
                </div>
              ) : (
                <EmptyState>No triage has been run for this ticket yet.</EmptyState>
              )}
            </CaseSection>

            <CaseSection title="Read-only diagnostics" source="Diagnostic gateway">
              <div className="diagnostic-toolbar">
                <div className="field diagnostic-service-field">
                  <label htmlFor="investigation-service">Windows service</label>
                  <input
                    id="investigation-service"
                    className="input mono"
                    value={serviceName}
                    onChange={(event) => setServiceName(event.target.value)}
                  />
                </div>
                <button
                  type="button"
                  className="btn"
                  disabled={actionBusy || !selectedTicket?.payload?.cmdb_ci}
                  onClick={() => runDiagnostic("service_status")}
                >
                  Read service status
                </button>
                <button
                  type="button"
                  className="btn"
                  disabled={actionBusy || !selectedTicket?.payload?.cmdb_ci}
                  onClick={() => runDiagnostic("disk_free")}
                >
                  Read disk space
                </button>
              </div>
              {!selectedTicket?.payload?.cmdb_ci ? (
                <p className="hint">Add a configuration item before targeting diagnostics.</p>
              ) : null}
              {pack.diagnostics.length ? (
                <div className="diagnostic-ledger">
                  {pack.diagnostics.map((event) => (
                    <div key={event.event_id} className="diagnostic-result">
                      <div>
                        <strong>{titleCase(event.detail?.operation_id || event.event)}</strong>
                        <span>{shortTime(event.created_at)} · {event.actor}</span>
                      </div>
                      <pre>{JSON.stringify(event.detail?.evidence || event.detail, null, 2)}</pre>
                    </div>
                  ))}
                </div>
              ) : (
                <EmptyState>No diagnostics collected for this ticket.</EmptyState>
              )}
            </CaseSection>

            <CaseSection title="Historical resolution patterns" source="Retrieved evidence">
              {pack.retrieved_evidence.resolution_clusters.length ? (
                <div className="cluster-ledger">
                  {pack.retrieved_evidence.resolution_clusters.slice(0, 6).map((cluster, index) => (
                    <div key={`${cluster.representative}-${index}`}>
                      <Badge pill>{cluster.count} cases</Badge>
                      <p>{cluster.representative}</p>
                      <span>{(cluster.tickets || []).join(", ") || "No ticket citations"}</span>
                    </div>
                  ))}
                </div>
              ) : (
                <EmptyState>No similar historical resolutions found.</EmptyState>
              )}
            </CaseSection>

            {pack.opus ? (
              <CaseSection title="Opus advisory analysis" source={pack.opus.backend}>
                <div className="opus-dossier">{pack.opus.recommendation}</div>
                <p className="hint">Advisory only. Opus cannot authorize or execute work.</p>
              </CaseSection>
            ) : null}

            <div className="case-file-split">
              <CaseSection title="Missing information" source="Completeness check">
                <SimpleList items={pack.missing_information} empty="Nothing missing was identified." />
              </CaseSection>
              <CaseSection title="Limitations" source="System disclosure">
                <SimpleList items={pack.limitations} empty="No limitations recorded." />
              </CaseSection>
            </div>

            <CaseSection title="Recommendation feedback" source="Human validation">
              <div className="feedback-summary-row">
                <span><strong>{summary?.total || 0}</strong> reviews</span>
                <span><strong>{percent(summary?.useful_rate)}</strong> useful</span>
                <span><strong>{summary?.estimated_minutes_saved || 0}</strong> minutes saved</span>
                <span><strong>{percent(summary?.human_intervention_rate)}</strong> needed intervention</span>
              </div>

              <form className="feedback-form" onSubmit={saveFeedback}>
                <div className="field">
                  <label htmlFor="feedback-verdict">Was the recommendation useful?</label>
                  <select
                    id="feedback-verdict"
                    className="select"
                    value={feedback.verdict}
                    onChange={(event) => setFeedback({ ...feedback, verdict: event.target.value })}
                  >
                    {VERDICTS.map(([value, label]) => (
                      <option key={value} value={value}>{label}</option>
                    ))}
                  </select>
                </div>
                <div className="feedback-form-grid">
                  <div className="field">
                    <label htmlFor="feedback-time">Estimated minutes saved</label>
                    <input
                      id="feedback-time"
                      type="number"
                      min="0"
                      max="1440"
                      className="input"
                      value={feedback.time_saved_minutes}
                      onChange={(event) =>
                        setFeedback({ ...feedback, time_saved_minutes: event.target.value })
                      }
                    />
                  </div>
                  <div className="field">
                    <label htmlFor="feedback-recurrence">Did the issue recur?</label>
                    <select
                      id="feedback-recurrence"
                      className="select"
                      value={feedback.recurrence}
                      onChange={(event) => setFeedback({ ...feedback, recurrence: event.target.value })}
                    >
                      <option value="">Not known yet</option>
                      <option value="no">No</option>
                      <option value="yes">Yes</option>
                    </select>
                  </div>
                </div>
                <Checkbox
                  id="feedback-human"
                  checked={feedback.human_intervention}
                  onChange={(value) => setFeedback({ ...feedback, human_intervention: value })}
                >
                  A human had to intervene beyond the recommendation
                </Checkbox>
                <div className="feedback-form-grid">
                  <div className="field">
                    <label htmlFor="feedback-useful">Useful steps (one per line)</label>
                    <textarea
                      id="feedback-useful"
                      className="input"
                      rows="3"
                      value={feedback.useful_steps}
                      onChange={(event) => setFeedback({ ...feedback, useful_steps: event.target.value })}
                    />
                  </div>
                  <div className="field">
                    <label htmlFor="feedback-wrong">Wrong or unnecessary steps</label>
                    <textarea
                      id="feedback-wrong"
                      className="input"
                      rows="3"
                      value={feedback.wrong_steps}
                      onChange={(event) => setFeedback({ ...feedback, wrong_steps: event.target.value })}
                    />
                  </div>
                </div>
                <div className="field">
                  <label htmlFor="feedback-resolution">Actual resolution</label>
                  <textarea
                    id="feedback-resolution"
                    className="input"
                    rows="3"
                    value={feedback.actual_resolution}
                    onChange={(event) => setFeedback({ ...feedback, actual_resolution: event.target.value })}
                  />
                </div>
                <div className="field">
                  <label htmlFor="feedback-notes">Review notes</label>
                  <textarea
                    id="feedback-notes"
                    className="input"
                    rows="2"
                    value={feedback.notes}
                    onChange={(event) => setFeedback({ ...feedback, notes: event.target.value })}
                  />
                </div>
                <button type="submit" className="btn btn-primary" disabled={actionBusy}>
                  {actionBusy ? "Saving…" : "Save feedback"}
                </button>
              </form>

              {pack.feedback.length ? (
                <div className="feedback-history">
                  <h3>Previous reviews for this ticket</h3>
                  {pack.feedback.map((item) => (
                    <div key={item.feedback_id}>
                      <Badge tone={item.verdict === "accepted" ? "bucket-active" : "neutral"}>
                        {titleCase(item.verdict)}
                      </Badge>
                      <p>{item.notes || item.actual_resolution || "No written note."}</p>
                      <span>{item.created_by} · {shortTime(item.created_at)}</span>
                    </div>
                  ))}
                </div>
              ) : null}
            </CaseSection>
          </article>
        )}
      </Panel>
    </div>
  );
}

function Provenance({ label, value, help }) {
  return (
    <div>
      <strong>{value}</strong>
      <span>{label}</span>
      <small>{help}</small>
    </div>
  );
}

function CaseSection({ title, source, children }) {
  return (
    <section className="case-section">
      <div className="case-section-head">
        <h3>{title}</h3>
        <span>{source}</span>
      </div>
      {children}
    </section>
  );
}

function CandidateRow({ candidate, rank }) {
  const evidence = candidate.evidence || {};
  return (
    <div className="candidate-row">
      <span className="candidate-rank">{rank}</span>
      <div>
        <div className="candidate-title">
          <strong>{candidate.procedure_name}</strong>
          <Badge tone={candidate.confidence_band === "high" ? "bucket-active" : "neutral"} pill>
            {Math.round(candidate.confidence * 100)}% · {candidate.confidence_band}
          </Badge>
        </div>
        <p>{(evidence.signals || []).map(titleCase).join(" · ") || "No strong signal"}</p>
        {(evidence.matched_keywords || []).length ? (
          <small>Matched: {evidence.matched_keywords.join(", ")}</small>
        ) : null}
        {(evidence.historical_tickets || []).length ? (
          <small>
            Evidence: {evidence.historical_tickets.map((ticket) => ticket.number).join(", ")}
          </small>
        ) : null}
      </div>
    </div>
  );
}

function SimpleList({ items, empty }) {
  return items.length ? (
    <ul className="case-simple-list">
      {items.map((item) => <li key={item}>{item}</li>)}
    </ul>
  ) : (
    <p className="hint">{empty}</p>
  );
}

function TriageRunCard({ run }) {
  const verdict = run.verdict || {};
  const routed = run.routed_to_human;
  return (
    <div className="triage-run">
      <div className="triage-run-head">
        <div className="row">
          <Badge tone={routed ? "neutral" : "bucket-active"} pill>
            {titleCase(run.classification)}
          </Badge>
          {run.host ? <Badge tone="R0">{run.host}</Badge> : null}
          {run.simulated ? <Badge tone="neutral">simulated reads</Badge> : null}
          {routed ? <Badge tone="warn">routed to human</Badge> : null}
        </div>
        <span>
          {run.steps_used} step{run.steps_used === 1 ? "" : "s"} · {shortTime(run.created_at)} · {run.created_by}
        </span>
      </div>

      {verdict.summary ? <p className="triage-summary">{verdict.summary}</p> : null}

      {verdict.recommended_disposition ? (
        <Callout
          kind={routed ? "warn" : "info"}
          title="Recommended disposition (needs human approval)"
        >
          <p>{verdict.recommended_disposition}</p>
        </Callout>
      ) : null}

      {verdict.draft_work_note ? (
        <div className="triage-note">
          <div className="case-section-head">
            <h4>Draft work note</h4>
            <span>not posted — for human review</span>
          </div>
          <p>{verdict.draft_work_note}</p>
        </div>
      ) : null}

      {(run.transcript || []).length ? (
        <details className="triage-transcript">
          <summary>Investigation transcript ({run.transcript.length} read-only checks)</summary>
          <ol>
            {run.transcript.map((entry, index) => (
              <li key={index}>
                <strong>{titleCase(entry.tool_id || "step")}</strong>
                {entry.ok === false ? (
                  <span className="triage-step-error"> — refused: {entry.error}</span>
                ) : (
                  <pre>{JSON.stringify(entry.evidence ?? {}, null, 2)}</pre>
                )}
              </li>
            ))}
          </ol>
        </details>
      ) : null}

      <p className="hint">
        Planner: {run.planner}. Read-only — this authorises nothing.
      </p>
    </div>
  );
}

import { useEffect, useMemo, useState } from "react";

import { api } from "../api.js";
import {
  Callout,
  Message,
  OwnerBadge,
  Progress,
  RiskBadge,
  StateTag,
} from "../components/primitives.jsx";
import {
  isFinished,
  needsAttention,
  owner as ownerWord,
  shortTime,
  state as stateInfo,
} from "../vocabulary.js";
import RecommendPanel from "./RecommendPanel.jsx";
import EfficiencyPanel from "./EfficiencyPanel.jsx";

/* One ticket, worked step by step.
 *
 * Leads with the single thing the reader should do next, then the plan grouped
 * into approval chunks, then the history. Reference detail (match scoring,
 * procedure version) is collapsed because it is rarely acted on.
 */

export default function RunDetail({ run, audit, onChanged, busy }) {
  const [params, setParams] = useState(run.parameters);
  const [evidence, setEvidence] = useState({});
  const [msg, setMsg] = useState(null);

  // Reset local edits when a different run is selected.
  useEffect(() => {
    setParams(run.parameters);
    setEvidence({});
    setMsg(null);
  }, [run.run_id, run.updated_at]);

  const info = stateInfo(run.state);
  const nextStep = useMemo(
    () => run.steps.find((s) => !isFinished(s)),
    [run.steps]
  );

  async function act(fn, okText) {
    setMsg({ kind: "", text: "Working…" });
    try {
      await fn();
      setMsg(okText ? { kind: "ok", text: okText } : null);
      await onChanged();
    } catch (err) {
      setMsg({ kind: "error", text: err.message });
    }
  }

  const paramKeys = useMemo(() => {
    const keys = new Set([
      ...Object.keys(run.parameters),
      ...run.missing_parameters,
    ]);
    return [...keys];
  }, [run.parameters, run.missing_parameters]);

  return (
    <>
      {/* ---- what to do next ---- */}
      <div className={`next-action next-${info.bucket}`}>
        <div className="next-head">
          <strong>{info.word}</strong>
          {nextStep ? (
            <span className="hint">
              next: step {nextStep.sequence} of {run.total_step_count}, handled by{" "}
              <strong>{ownerWord(nextStep.owner)}</strong>
            </span>
          ) : null}
        </div>
        <p className="next-what">{info.action}</p>
        <Progress
          done={run.completed_step_count}
          total={run.total_step_count}
          large
        />
        <p className="hint" style={{ marginTop: "0.3rem" }}>
          {run.completed_step_count} of {run.total_step_count} steps done ·{" "}
          {run.ai_step_count} can be done by the agent · {run.human_step_count}{" "}
          need a person
        </p>
      </div>

      {/* ---- AI advisory recommendation (opt-in, advisory only) ---- */}
      <RecommendPanel ticket={run.ticket} />

      {/* ---- efficiency advice (opt-in, annotates only) ---- */}
      <EfficiencyPanel runId={run.run_id} />

      {/* ---- reference detail ---- */}
      <details className="detail-meta">
        <summary>Ticket &amp; matching details</summary>
        <dl className="kv" style={{ marginTop: "0.4rem" }}>
          <dt>Assignment group</dt>
          <dd>{run.ticket.assignment_group || "—"}</dd>
          <dt>Configuration item</dt>
          <dd>{run.ticket.cmdb_ci || "—"}</dd>
          <dt>Procedure</dt>
          <dd>
            <code>{run.procedure_id}</code>{" "}
            <span className="hint">{run.procedure_version}</span>
          </dd>
          <dt>Why it matched</dt>
          <dd>
            score {run.match_score}
            {run.matched_keywords.length
              ? ` · keywords: ${run.matched_keywords.join(", ")}`
              : ""}
          </dd>
          {run.alternatives.length ? (
            <>
              <dt>Also considered</dt>
              <dd>
                {run.alternatives
                  .map((a) => `${a.procedure_name} (${a.score})`)
                  .join(", ")}
              </dd>
            </>
          ) : null}
        </dl>
      </details>

      <p className="ticket-desc">
        {run.ticket.short_description || "(no description)"}
      </p>

      {/* ---- parameters ---- */}
      <h3>Details the steps need</h3>
      {run.missing_parameters.length ? (
        <Callout kind="warn" title="Missing required values">
          <p style={{ marginTop: "0.2rem" }}>
            {run.missing_parameters.join(", ")} — nothing can be approved until
            these are filled in.
          </p>
        </Callout>
      ) : null}

      <div className="param-grid" style={{ marginTop: "0.5rem" }}>
        {paramKeys.map((key) => {
          const missing = run.missing_parameters.includes(key);
          return (
            <div key={key}>
              <label htmlFor={`p-${key}`}>{key}</label>
              <input
                id={`p-${key}`}
                className={`input${missing ? " input-missing" : ""}`}
                value={params[key] ?? ""}
                aria-invalid={missing || undefined}
                onChange={(e) =>
                  setParams({ ...params, [key]: e.target.value })
                }
              />
            </div>
          );
        })}
      </div>

      <div className="row" style={{ marginTop: "0.6rem" }}>
        <button
          type="button"
          className="btn"
          disabled={busy}
          onClick={() =>
            act(
              () => api.setParameters(run.run_id, params),
              "Saved. Any approval already given no longer applies."
            )
          }
        >
          Save details
        </button>
        <span className="hint">
          Changing these invalidates an approval already given.
        </span>
      </div>

      <Message kind={msg?.kind}>{msg?.text}</Message>

      {/* ---- the plan ---- */}
      <h3>The plan</h3>
      <p className="hint">
        Steps are grouped so one approval never covers a bigger change than you
        reviewed. The agent stops at every group it is not allowed to run.
      </p>

      {run.chunks.map((chunk) => (
        <Chunk
          key={chunk.chunk_id}
          chunk={chunk}
          run={run}
          nextStep={nextStep}
          evidence={evidence}
          setEvidence={setEvidence}
          act={act}
          busy={busy}
        />
      ))}

      {/* ---- history ---- */}
      <h3>History</h3>
      <ul className="audit">
        {audit.map((e) => (
          <li
            key={e.event_id}
            className={e.event.includes("refused") ? "audit-refused" : ""}
          >
            <time>{shortTime(e.created_at)}</time>
            <span>
              <span className="audit-event">{e.event}</span>{" "}
              <span className="audit-actor">· {e.actor}</span>
              {e.detail?.reason ? (
                <>
                  <br />
                  <span className="audit-actor">{e.detail.reason}</span>
                </>
              ) : null}
            </span>
          </li>
        ))}
      </ul>

      <div className="row" style={{ marginTop: "1rem" }}>
        <button
          type="button"
          className="btn btn-quiet"
          disabled={busy}
          onClick={() => {
            const reason = prompt("Why are you cancelling this run?");
            if (reason === null) return;
            act(() => api.cancelRun(run.run_id, reason), "Run cancelled.");
          }}
        >
          Cancel this run
        </button>
      </div>
    </>
  );
}

/* ------------------------------------------------------------------ chunk */

function Chunk({ chunk, run, nextStep, evidence, setEvidence, act, busy }) {
  const steps = chunk.step_ids
    .map((id) => run.steps.find((s) => s.step_id === id))
    .filter(Boolean);

  const allDone = steps.every(isFinished);
  const anyReady = steps.some((s) => s.state === "ready");
  const isNext = nextStep && chunk.chunk_id === nextStep.chunk_id;
  const agentChunk = chunk.owner === "ai" && chunk.policy !== "manual_only";
  const blocked = run.missing_parameters.length > 0;

  let controls;
  if (allDone) {
    controls = <StateTag code="completed" />;
  } else if (!agentChunk) {
    controls = (
      <span className="badge tone-bucket-you badge-pill">You do this</span>
    );
  } else if (anyReady) {
    controls = (
      <button
        type="button"
        className="btn btn-run btn-sm"
        disabled={busy}
        onClick={() =>
          act(
            () => api.executeChunk(run.run_id, chunk.chunk_id),
            "Done. The agent stopped where it had to."
          )
        }
      >
        Run these steps
      </button>
    );
  } else {
    controls = (
      <>
        <button
          type="button"
          className="btn btn-ok btn-sm"
          disabled={blocked || busy}
          title={blocked ? "Fill in the missing details first" : undefined}
          onClick={() =>
            act(
              () =>
                api.decideChunk(
                  run.run_id,
                  chunk.chunk_id,
                  "approved",
                  "approved in overlay"
                ),
              "Approved. Press Run when ready."
            )
          }
        >
          Let the agent do this
        </button>
        <button
          type="button"
          className="btn btn-sm"
          disabled={busy}
          onClick={() => {
            const reason = prompt("Why are you refusing this group?");
            if (reason === null) return;
            act(
              () =>
                api.decideChunk(
                  run.run_id,
                  chunk.chunk_id,
                  "rejected",
                  reason
                ),
              "Refused."
            );
          }}
        >
          Refuse
        </button>
      </>
    );
  }

  return (
    <div className={`chunk${isNext ? " chunk-next" : ""}`}>
      <div className="chunk-head">
        <span className="chunk-title">
          {chunk.label}
          {isNext ? <span className="up-next">up next</span> : null}
        </span>
        <span className="row">
          <RiskBadge code={chunk.risk} />
          <OwnerBadge owner={chunk.owner} />
          {controls}
        </span>
      </div>
      <div className="chunk-body">
        {steps.map((step) => (
          <Step
            key={step.step_id}
            step={step}
            run={run}
            isNextActionable={nextStep?.step_id === step.step_id}
            evidence={evidence}
            setEvidence={setEvidence}
            act={act}
            busy={busy}
          />
        ))}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------- step */

function Step({ step, run, isNextActionable, evidence, setEvidence, act, busy }) {
  const done = isFinished(step);
  const attention = needsAttention(step);
  const canComplete =
    !done && (step.owner === "human" || attention) && isNextActionable;

  const cls = done ? "step step-done" : attention ? "step step-attention" : "step";

  return (
    <div className={cls}>
      <span className="step-num">{step.sequence}</span>
      <div className="step-main">
        <div className="step-desc">{step.description}</div>
        <p className="step-rendered">{step.rendered}</p>

        <div className="step-badges">
          <RiskBadge code={step.risk} />
          <OwnerBadge owner={step.owner} />
          {done ? (
            <span className="badge tone-neutral">done</span>
          ) : attention ? (
            <span className="badge tone-R2">needs you</span>
          ) : null}
        </div>

        {step.owner === "human" && !done ? (
          <p className="step-why">{step.risk_reason}</p>
        ) : null}

        {step.outcome ? <p className="step-outcome">→ {step.outcome}</p> : null}

        {step.evidence ? (
          <p className="step-evidence">
            Evidence: {step.evidence}
            {step.completed_by ? ` (${step.completed_by})` : ""}
          </p>
        ) : null}

        {canComplete ? (
          <div className="manual-form">
            <label className="sr-only" htmlFor={`ev-${step.step_id}`}>
              What did you do for step {step.sequence}?
            </label>
            <input
              id={`ev-${step.step_id}`}
              className="input"
              placeholder="What did you do? (required)"
              value={evidence[step.step_id] ?? ""}
              onChange={(e) =>
                setEvidence({ ...evidence, [step.step_id]: e.target.value })
              }
            />
            <button
              type="button"
              className="btn btn-sm"
              disabled={busy}
              onClick={() => {
                const text = (evidence[step.step_id] ?? "").trim();
                if (!text) return;
                act(
                  () => api.completeStep(run.run_id, step.step_id, text),
                  "Recorded."
                );
              }}
            >
              Mark done
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );
}

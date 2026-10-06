import { useState } from "react";

import { api } from "../api.js";
import { Badge, Callout, Spinner } from "../components/primitives.jsx";

/* Advisory efficiency view for a run. Shows agent/human step split, historical
 * resolution time, and per-step annotations. Never reorders or removes steps;
 * it is a read-only overlay on the plan the planner already built.
 */
export default function EfficiencyPanel({ runId }) {
  const [state, setState] = useState("idle");
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  async function load() {
    setState("loading");
    try {
      const r = await api.efficiencyAdvice(runId);
      setData(r);
      setState("done");
    } catch (err) {
      setError(err.message);
      setState("error");
    }
  }

  return (
    <section className="efficiency-panel">
      <div className="efficiency-head">
        <div>
          <strong>Efficiency advice</strong>
          <span className="hint" style={{ marginLeft: "0.5rem" }}>
            annotates only — never changes step order or removes steps
          </span>
        </div>
        {state !== "done" ? (
          <button
            type="button"
            className="btn btn-quiet btn-sm"
            onClick={load}
            disabled={state === "loading"}
          >
            {state === "loading" ? "Loading…" : "Show advice"}
          </button>
        ) : null}
      </div>

      {state === "loading" ? (
        <div style={{ padding: "0.5rem" }}>
          <Spinner label="Analysing plan" />
        </div>
      ) : null}

      {state === "error" ? (
        <Callout kind="danger" title="Could not load efficiency advice">
          <p style={{ marginTop: "0.2rem" }}>{error}</p>
        </Callout>
      ) : null}

      {state === "done" && data ? (
        <>
          <div className="efficiency-summary">
            <Badge tone="ok" outline>
              {data.agent_steps} agent step{data.agent_steps !== 1 ? "s" : ""}
            </Badge>
            <Badge tone="warn" outline>
              {data.human_steps} human step{data.human_steps !== 1 ? "s" : ""}
            </Badge>
            <Badge tone="info" outline>
              {Math.round(data.agent_share * 100)}% automatable
            </Badge>
            {data.first_human_handoff_sequence ? (
              <Badge tone="neutral" outline>
                human handoff at step {data.first_human_handoff_sequence}
              </Badge>
            ) : null}
          </div>

          <div className="efficiency-steps">
            {data.steps.map((s) => (
              <div
                key={s.step_id}
                className={`efficiency-step efficiency-${s.category}`}
              >
                <span className="efficiency-seq">{s.sequence}.</span>
                <span className="efficiency-action">{s.action}</span>
                <Badge
                  tone={s.category === "automatable" ? "ok" : "warn"}
                  outline
                  pill
                >
                  {s.category === "automatable" ? "agent" : "human"}
                </Badge>
                <span className="efficiency-advice">{s.advice}</span>
                {s.streamline_hint ? (
                  <div className="efficiency-hint">{s.streamline_hint}</div>
                ) : null}
              </div>
            ))}
          </div>

          <p className="hint efficiency-foot">
            {data.note}
          </p>
        </>
      ) : null}
    </section>
  );
}

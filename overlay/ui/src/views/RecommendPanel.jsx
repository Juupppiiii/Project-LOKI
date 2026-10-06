import { useState } from "react";

import { api } from "../api.js";
import { Badge, Callout, Spinner } from "../components/primitives.jsx";

/* Advisory step recommendation, grounded in real solved tickets.
 *
 * This panel is deliberately opt-in: it calls the server (and, when enabled, a
 * Bedrock model) only when the reader presses the button, never on load. The
 * result is ADVISORY -- it approves and executes nothing, and does not change
 * the authoritative procedure match. The panel makes that explicit so no one
 * mistakes a recommendation for an action.
 */
export default function RecommendPanel({ ticket }) {
  const [state, setState] = useState("idle"); // idle | loading | done | error
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  async function run() {
    setState("loading");
    setError(null);
    try {
      const r = await api.recommendSteps(ticket);
      setData(r);
      setState("done");
    } catch (err) {
      setError(err.message);
      setState("error");
    }
  }

  const solved = data?.grounding?.solved_tickets || [];
  const live = data?.model_available;

  return (
    <section className="recommend-panel">
      <div className="recommend-head">
        <div>
          <strong>AI advisory</strong>
          <span className="hint" style={{ marginLeft: "0.5rem" }}>
            grounded in solved tickets — recommends only, never acts
          </span>
        </div>
        <button
          type="button"
          className="btn btn-quiet btn-sm"
          onClick={run}
          disabled={state === "loading"}
        >
          {state === "loading"
            ? "Thinking…"
            : state === "done"
            ? "Regenerate"
            : "Recommend steps"}
        </button>
      </div>

      {state === "loading" ? (
        <div style={{ padding: "0.75rem" }}>
          <Spinner label="Consulting history + model" />
        </div>
      ) : null}

      {state === "error" ? (
        <Callout kind="danger" title="Could not get a recommendation">
          <p style={{ marginTop: "0.2rem" }}>{error}</p>
        </Callout>
      ) : null}

      {state === "done" && data ? (
        <>
          <div className="recommend-meta">
            <Badge tone={live ? "ok" : "neutral"} outline>
              {live ? `model: ${data.backend}` : "offline — no live model"}
            </Badge>
            <Badge tone="info" outline>
              advisory
            </Badge>
            {data.authoritative_match ? (
              <Badge tone="neutral" outline>
                matched: {data.authoritative_match}
              </Badge>
            ) : (
              <Badge tone="warn" outline>
                no sanctioned procedure matched
              </Badge>
            )}
          </div>

          {!live ? (
            <Callout kind="info" title="No live model attached">
              <p style={{ marginTop: "0.2rem" }}>
                The recommendation below is the real retrieved history only.
                Enable a Bedrock model to add a synthesised, critiqued plan.
              </p>
            </Callout>
          ) : null}

          <div className="recommend-body">
            {data.recommendation}
          </div>

          {solved.length ? (
            <div className="recommend-evidence">
              <div className="recommend-evidence-head">
                Grounded in {solved.length} similar solved ticket
                {solved.length === 1 ? "" : "s"}:
              </div>
              <ul>
                {solved.map((s) => (
                  <li key={s.number}>
                    <span className="mono">{s.number}</span>
                    <span className="recommend-sim">
                      {(s.similarity * 100).toFixed(0)}% similar
                    </span>
                    <span className="recommend-sd">{s.short_description}</span>
                    {s.resolution ? (
                      <div className="recommend-fix">fix: {s.resolution}</div>
                    ) : null}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          <p className="hint recommend-foot">
            Advisory only. A human still approves every step, and the agent only
            runs steps it is explicitly granted.
          </p>
        </>
      ) : null}
    </section>
  );
}

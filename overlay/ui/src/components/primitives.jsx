/* Small presentational building blocks.
 *
 * Deliberately dumb: they take values and render them. No fetching, no policy.
 * Every colour-carrying component also renders words, so colour is never the
 * only signal.
 */

import { risk as riskInfo, state as stateInfo } from "../vocabulary.js";

/* ---------------------------------------------------------------- badges */

export function Badge({ children, tone = "neutral", outline = false, title, pill = false }) {
  return (
    <span
      className={`badge tone-${tone}${outline ? " badge-outline" : ""}${pill ? " badge-pill" : ""}`}
      title={title}
    >
      {children}
    </span>
  );
}

/** A step or group's risk, shown as words with the code in the tooltip. */
export function RiskBadge({ code }) {
  const info = riskInfo(code);
  return (
    <Badge tone={info.tone} title={`${code} — ${info.help}`}>
      {info.word}
    </Badge>
  );
}

/** A run's state, coloured by which bucket it falls into. */
export function StateTag({ code, title }) {
  const info = stateInfo(code);
  return (
    <Badge tone={`bucket-${info.bucket}`} title={title || info.action} pill>
      {info.word}
    </Badge>
  );
}

/** Who performs something: the agent, or you. */
export function OwnerBadge({ owner }) {
  const isAgent = owner === "ai";
  return (
    <Badge tone={isAgent ? "agent" : "neutral"} outline={!isAgent}>
      {isAgent ? "agent" : "you"}
    </Badge>
  );
}

/* -------------------------------------------------------------- progress */

export function Progress({ done, total, large = false }) {
  const pct = total ? Math.round((done / total) * 100) : 0;
  return (
    <span
      className={`progress${large ? " progress-lg" : ""}`}
      role="img"
      aria-label={`${done} of ${total} steps done`}
    >
      <span className="progress-bar" style={{ width: `${pct}%` }} />
    </span>
  );
}

/* -------------------------------------------------------------- messages */

export function Callout({ kind = "info", title, children }) {
  return (
    <div className={`callout callout-${kind}`}>
      {title ? <strong>{title}</strong> : null}
      {children}
    </div>
  );
}

/** A short status line under a control. `kind` is "", "ok" or "error". */
export function Message({ kind = "", children }) {
  if (!children) return <p className="msg" aria-live="polite" />;
  return (
    <p className={`msg${kind ? ` msg-${kind}` : ""}`} aria-live="polite">
      {children}
    </p>
  );
}

export function EmptyState({ children, action }) {
  return (
    <div className="empty">
      <p>{children}</p>
      {action ? <div style={{ marginTop: "0.75rem" }}>{action}</div> : null}
    </div>
  );
}

export function Spinner({ label = "Loading" }) {
  return (
    <span className="spinner" role="status" aria-label={label}>
      <span className="spinner-dot" />
      <span className="spinner-dot" />
      <span className="spinner-dot" />
    </span>
  );
}

/* ------------------------------------------------------------ containers */

export function Panel({ title, badge, children, actions, scroll = true, className = "" }) {
  return (
    <section className={`panel ${className}`}>
      <div className="panel-head">
        <h2>{title}</h2>
        <div className="row">
          {badge}
          {actions}
        </div>
      </div>
      <div className={scroll ? "panel-body" : "panel-body panel-body-flush"}>
        {children}
      </div>
    </section>
  );
}

/* ---------------------------------------------------------------- inputs */

export function Field({ label, hint, htmlFor, children }) {
  return (
    <div className="field">
      {label ? <label htmlFor={htmlFor}>{label}</label> : null}
      {children}
      {hint ? <p className="hint">{hint}</p> : null}
    </div>
  );
}

export function Checkbox({ checked, onChange, children, id }) {
  return (
    <label className="check" htmlFor={id}>
      <input
        id={id}
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span>{children}</span>
    </label>
  );
}

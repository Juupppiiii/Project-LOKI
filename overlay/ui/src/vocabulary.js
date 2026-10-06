/* One place that turns server codes into words a person can read.
 *
 * The server owns the codes (R0, awaiting_approval, ...). This owns how they
 * read. Keeping the mapping here means the UI never invents policy, and a new
 * server state degrades to its raw name rather than breaking the page.
 */

export const RISK = {
  R0: {
    word: "Read-only",
    help: "Looks at things without changing anything.",
    tone: "R0",
  },
  R1: {
    word: "Low impact",
    help: "A small, reversible change such as posting a note on the ticket.",
    tone: "R1",
  },
  R2: {
    word: "Real change",
    help: "Changes a running system, so it needs its own separate approval.",
    tone: "R2",
  },
  R3: {
    word: "High impact",
    help: "Destructive or hard to undo. Always done by a person.",
    tone: "R3",
  },
  RX: {
    word: "Unclassified",
    help: "Safety cannot be proven from the instruction, so a person does it.",
    tone: "RX",
  },
};

export function risk(code) {
  return RISK[code] || { word: code || "unknown", help: "", tone: "RX" };
}

/* Run states, grouped into what a person actually needs to do about them. */
export const STATE = {
  needs_parameters: {
    word: "Needs details",
    bucket: "you",
    action: "Fill in the missing values before anything can be approved.",
  },
  awaiting_approval: {
    word: "Needs approval",
    bucket: "you",
    action: "Review the next group of steps, then approve or refuse it.",
  },
  ready: {
    word: "Ready to run",
    bucket: "you",
    action: "Approved and waiting. Press Run to let the agent do it.",
  },
  paused_for_human: {
    word: "Your turn",
    bucket: "you",
    action: "The agent has stopped. This step is yours to do.",
  },
  proposed: {
    word: "New",
    bucket: "you",
    action: "Newly matched. Review the plan below.",
  },
  running: {
    word: "Running",
    bucket: "active",
    action: "The agent is working through the approved steps.",
  },
  blocked: {
    word: "Blocked",
    bucket: "problem",
    action: "Something was refused. Re-approve it, or cancel the run.",
  },
  failed: {
    word: "Failed",
    bucket: "problem",
    action: "A step failed. Check the history at the bottom.",
  },
  completed: {
    word: "Done",
    bucket: "done",
    action: "All steps are complete.",
  },
  cancelled: {
    word: "Cancelled",
    bucket: "done",
    action: "This run was cancelled.",
  },
};

export function state(code) {
  return (
    STATE[code] || {
      word: String(code || "").replace(/_/g, " "),
      bucket: "all",
      action: "",
    }
  );
}

/* The filter buckets shown above the queue. */
export const BUCKETS = [
  {
    id: "you",
    label: "Needs you",
    help: "Waiting on a decision or an action from a person.",
  },
  { id: "active", label: "Running", help: "The agent is working." },
  { id: "problem", label: "Problems", help: "Blocked or failed." },
  { id: "done", label: "Finished", help: "Completed or cancelled." },
  { id: "all", label: "All", help: "Every ticket in the queue." },
];

/* Live inbox statuses. */
export const LIVE_STATUS = {
  new: {
    word: "Ready to triage",
    help: "A procedure matched this ticket.",
    tone: "R1",
  },
  no_match: {
    word: "No procedure",
    help: "Nothing codified yet, so it stays with a person.",
    tone: "RX",
  },
  proposed: {
    word: "In the queue",
    help: "A run was already created for this.",
    tone: "R0",
  },
  ignored: {
    word: "Dismissed",
    help: "Someone chose to skip this.",
    tone: "RX",
  },
};

export function liveStatus(code) {
  return (
    LIVE_STATUS[code] || {
      word: String(code || "").replace(/_/g, " "),
      help: "",
      tone: "RX",
    }
  );
}

/** Who owns a step, in words rather than "ai" / "human". */
export function owner(value) {
  return value === "ai" ? "agent" : "you";
}

/** Step states that mean the step is finished either way. */
export const TERMINAL_STEP_STATES = ["succeeded", "skipped"];

export function isFinished(step) {
  return TERMINAL_STEP_STATES.includes(step.state);
}

export function needsAttention(step) {
  return ["needs_human", "blocked", "failed"].includes(step.state);
}

/** Trim an ISO timestamp down to something readable in a table. */
export function shortTime(iso) {
  if (!iso) return "";
  return String(iso).replace("T", " ").replace("+00:00", "Z");
}

"""Generate the BUCKY architecture document as a PDF.

Uses fpdf (already installed). Pure layout code -- no network, no app imports --
so it can run anywhere. Content is drawn from the real modules in overlay/.
"""

from fpdf import FPDF

# ---- palette (matches the app's Apple-ish tokens) -------------------------
INK = (29, 29, 31)
INK2 = (72, 72, 74)
INK3 = (134, 134, 139)
ACCENT = (0, 113, 227)
OK = (29, 131, 72)
WARN = (138, 83, 0)
DANGER = (196, 36, 28)
LINE = (219, 219, 224)
SURFACE2 = (245, 245, 247)
CARD = (250, 250, 252)

PAGE_W = 210
MARGIN = 18
CONTENT_W = PAGE_W - 2 * MARGIN

# The built-in Helvetica is latin-1 only, so map the few typographic characters
# used in the copy to safe equivalents rather than embedding a Unicode font.
_SUBS = {
    "\u2014": "-",   # em dash
    "\u2013": "-",   # en dash
    "\u2019": "'",   # curly apostrophe
    "\u2018": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u2026": "...",
    "\u2022": chr(149),  # bullet -> latin-1 bullet
    "\u00a0": " ",
}


def s(text: str) -> str:
    for a, b in _SUBS.items():
        text = text.replace(a, b)
    return text


class Doc(FPDF):
    # Sanitize all text at the drawing boundary, so callers never worry about it.
    def cell(self, *args, **kwargs):
        if len(args) >= 3 and isinstance(args[2], str):
            args = (*args[:2], s(args[2]), *args[3:])
        if "text" in kwargs and isinstance(kwargs["text"], str):
            kwargs["text"] = s(kwargs["text"])
        return super().cell(*args, **kwargs)

    def multi_cell(self, *args, **kwargs):
        if len(args) >= 3 and isinstance(args[2], str):
            args = (*args[:2], s(args[2]), *args[3:])
        if "text" in kwargs and isinstance(kwargs["text"], str):
            kwargs["text"] = s(kwargs["text"])
        return super().multi_cell(*args, **kwargs)

    def header(self):
        if self.page_no() == 1:
            return
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*INK3)
        self.set_xy(MARGIN, 10)
        self.cell(CONTENT_W / 2, 5, "BUCKY â€” System Architecture", align="L")
        self.set_xy(MARGIN + CONTENT_W / 2, 10)
        self.cell(CONTENT_W / 2, 5, "Human-in-the-loop ticket automation", align="R")
        self.set_draw_color(*LINE)
        self.line(MARGIN, 16, PAGE_W - MARGIN, 16)
        # leave the cursor below the header rule so section content never
        # collides with the running header
        self.set_xy(MARGIN, 22)

    def footer(self):
        if self.page_no() == 1:
            return
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*INK3)
        self.set_xy(MARGIN, -14)
        self.cell(CONTENT_W / 2, 5, f"Page {self.page_no()}", align="L")
        self.set_xy(MARGIN + CONTENT_W / 2, -14)
        self.cell(CONTENT_W / 2, 5, "Simulation only â€” no live execution", align="R")


def h1(pdf, text):
    pdf.set_x(MARGIN)
    pdf.set_font("Helvetica", "B", 16)
    pdf.set_text_color(*INK)
    pdf.multi_cell(CONTENT_W, 8, text)
    pdf.ln(1)
    pdf.set_draw_color(*ACCENT)
    pdf.set_line_width(0.6)
    y = pdf.get_y()
    pdf.line(MARGIN, y, MARGIN + 22, y)
    pdf.set_line_width(0.2)
    pdf.ln(4)


def h2(pdf, text):
    if pdf.get_y() > 250:
        pdf.add_page()
    pdf.ln(2)
    pdf.set_x(MARGIN)
    pdf.set_font("Helvetica", "B", 12)
    pdf.set_text_color(*INK)
    pdf.multi_cell(CONTENT_W, 6.5, text)
    pdf.ln(1.5)


def body(pdf, text, color=INK2):
    pdf.set_x(MARGIN)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*color)
    pdf.multi_cell(CONTENT_W, 5.2, text)
    pdf.ln(1.5)


def bullet(pdf, label, text):
    pdf.set_x(MARGIN)
    start_y = pdf.get_y()
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(*INK)
    label_w = 44
    pdf.multi_cell(label_w, 5.0, label)
    after_label_y = pdf.get_y()
    pdf.set_xy(MARGIN + label_w, start_y)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*INK2)
    pdf.multi_cell(CONTENT_W - label_w, 5.0, text)
    pdf.set_y(max(after_label_y, pdf.get_y()))
    pdf.ln(1)


def dot(pdf, text):
    pdf.set_x(MARGIN)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(*ACCENT)
    pdf.cell(5, 5.0, chr(149))
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(*INK2)
    pdf.set_x(MARGIN + 5)
    pdf.multi_cell(CONTENT_W - 5, 5.0, text)
    pdf.ln(0.6)


def layer_box(pdf, name, role, tone=ACCENT):
    """A single stacked layer block for the layering diagram."""
    x, y = MARGIN, pdf.get_y()
    h = 11
    pdf.set_fill_color(*CARD)
    pdf.set_draw_color(*LINE)
    pdf.rect(x, y, CONTENT_W, h, style="DF")
    # accent tab
    pdf.set_fill_color(*tone)
    pdf.rect(x, y, 2.2, h, style="F")
    pdf.set_xy(x + 6, y + 1.4)
    pdf.set_font("Courier", "B", 10)
    pdf.set_text_color(*INK)
    pdf.cell(34, 4, name)
    pdf.set_xy(x + 46, y + 1.4)
    pdf.set_font("Helvetica", "", 8.6)
    pdf.set_text_color(*INK2)
    pdf.multi_cell(CONTENT_W - 52, 4, role)
    pdf.set_y(y + h + 1.6)


def flow_step(pdf, n, title, text, last=False):
    x, y = MARGIN, pdf.get_y()
    r = 4
    cx = x + r
    pdf.set_fill_color(*ACCENT)
    pdf.ellipse(x, y, 2 * r, 2 * r, style="F")
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(255, 255, 255)
    pdf.set_xy(x, y + 1.1)
    pdf.cell(2 * r, 5, str(n), align="C")
    pdf.set_xy(x + 2 * r + 5, y - 0.5)
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(*INK)
    pdf.cell(0, 5, title)
    pdf.set_xy(x + 2 * r + 5, y + 4.6)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*INK2)
    pdf.multi_cell(CONTENT_W - 2 * r - 5, 4.6, text)
    end_y = pdf.get_y()
    if not last:
        pdf.set_draw_color(*LINE)
        pdf.line(cx, y + 2 * r, cx, end_y + 2)
    pdf.set_y(end_y + 3)


def risk_row(pdf, code, meaning, who, tone):
    x, y = MARGIN, pdf.get_y()
    pdf.set_fill_color(*tone)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", 9)
    pdf.rect(x, y, 12, 6, style="F")
    pdf.set_xy(x, y + 0.9)
    pdf.cell(12, 4.2, code, align="C")
    pdf.set_xy(x + 15, y)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*INK2)
    pdf.cell(95, 6, meaning)
    pdf.set_xy(x + 112, y)
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*INK)
    pdf.cell(0, 6, who)
    pdf.set_y(y + 8)


def callout(pdf, title, text, tone=WARN, bg=(253, 243, 227)):
    pdf.ln(1)
    x, y = MARGIN, pdf.get_y()
    pdf.set_font("Helvetica", "", 9)
    # measure height
    pdf.set_xy(x + 6, y + 5)
    lines = pdf.multi_cell(CONTENT_W - 10, 4.6, text, dry_run=True, output="LINES")
    h = 9 + len(lines) * 4.6
    pdf.set_fill_color(*bg)
    pdf.rect(x, y, CONTENT_W, h, style="F")
    pdf.set_fill_color(*tone)
    pdf.rect(x, y, 2.2, h, style="F")
    pdf.set_xy(x + 6, y + 2)
    pdf.set_font("Helvetica", "B", 9.5)
    pdf.set_text_color(*INK)
    pdf.cell(0, 4.5, title)
    pdf.set_xy(x + 6, y + 6.5)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*INK2)
    pdf.multi_cell(CONTENT_W - 10, 4.6, text)
    pdf.set_y(y + h + 2)


# ===========================================================================
pdf = Doc()
pdf.set_auto_page_break(auto=True, margin=18)
pdf.set_margins(MARGIN, 22, MARGIN)

# ---- cover ----------------------------------------------------------------
pdf.add_page()
pdf.ln(38)
# logo mark
pdf.set_fill_color(*INK)
pdf.rect(MARGIN, pdf.get_y(), 16, 16, style="F")
pdf.set_font("Helvetica", "B", 15)
pdf.set_text_color(255, 255, 255)
pdf.set_xy(MARGIN, pdf.get_y() + 4)
pdf.cell(16, 8, "L", align="C")
pdf.set_xy(MARGIN + 22, pdf.get_y() - 4)
pdf.set_font("Helvetica", "B", 30)
pdf.set_text_color(*INK)
pdf.cell(0, 16, "BUCKY")
pdf.ln(24)
pdf.set_font("Helvetica", "B", 17)
pdf.set_text_color(*INK)
pdf.multi_cell(CONTENT_W, 8, "System Architecture")
pdf.ln(2)
pdf.set_font("Helvetica", "", 12)
pdf.set_text_color(*INK2)
pdf.multi_cell(CONTENT_W, 6, "Human-in-the-loop ticket automation for ServiceNow")
pdf.ln(10)

pdf.set_draw_color(*LINE)
pdf.line(MARGIN, pdf.get_y(), PAGE_W - MARGIN, pdf.get_y())
pdf.ln(6)
pdf.set_font("Helvetica", "", 10)
pdf.set_text_color(*INK2)
pdf.multi_cell(
    CONTENT_W, 5.4,
    "BUCKY watches the tickets a team receives, works out which ones follow a "
    "known procedure, and lets a person hand the safe, repetitive steps to an "
    "agent while keeping the complex or high-impact work with a human. The "
    "agent is granted work one step at a time, never a whole ticket.",
)
pdf.ln(4)
callout(
    pdf,
    "Execution is simulation only",
    "Ticket data can be read live from ServiceNow, but nothing is written to any "
    "target system. There are no connectors and live execution is hard-disabled. "
    "What is real today is the decision, approval, and audit machinery.",
    tone=OK, bg=(232, 246, 237),
)
pdf.ln(2)
pdf.set_font("Helvetica", "", 8.5)
pdf.set_text_color(*INK3)
pdf.multi_cell(CONTENT_W, 4.4,
               "Built on the Phase 1 Discovery framework (System 1) and the "
               "System 2 procedure engine. This document describes System 3, "
               "the task overlay, and its authentication layer.")

# ---- 1. the idea ----------------------------------------------------------
pdf.add_page()
h1(pdf, "1  What BUCKY does")
body(pdf,
     "A support team gets a stream of tickets. Many are the same handful of "
     "problems solved the same way every time. BUCKY turns those repeatable "
     "resolutions into structured procedures, matches incoming tickets to them, "
     "and runs them under human control.")

h2(pdf, "The core principle")
callout(
    pdf,
    "The agent is granted work at the step level, never the ticket level.",
    "A single ticket moves back and forth between the agent and a person. The "
    "agent does the read-only checks and low-risk actions it is explicitly "
    "allowed to; it stops and hands back the moment a step needs human "
    "judgement or carries real risk. Neither party owns the whole ticket.",
    tone=ACCENT, bg=(234, 243, 255),
)

h2(pdf, "The three systems")
bullet(pdf, "System 1  Discovery",
       "Pulls ServiceNow history, finds which task types are repetitive and "
       "worth automating, and mines how they were actually resolved.")
bullet(pdf, "System 2  Procedures",
       "Holds the rulebook: each resolution is a structured procedure with a "
       "trigger and ordered steps. Matches a ticket to a procedure.")
bullet(pdf, "System 3  Task overlay",
       "The subject of this document. Adds per-step risk, human approval, a "
       "resumable run state, an audit trail, live intake, and sign-in.")

# ---- 2. the big picture / whole plan --------------------------------------
pdf.add_page()
h1(pdf, "2  The big picture")
body(pdf,
     "Read end to end, the whole plan is one line: tickets come in, the three "
     "systems turn them into controlled work, and each step is done by whoever "
     "is allowed to do it - the agent for the safe, repetitive steps, a person "
     "for everything with judgement or real risk. The same ticket moves back "
     "and forth; neither side owns it.")

h2(pdf, "The pipeline, start to finish")
flow_step(pdf, 1, "Discover (System 1)",
          "Pull ServiceNow history, find the task types that repeat often enough "
          "to be worth automating, and mine how they were actually resolved.")
flow_step(pdf, 2, "Codify (System 2)",
          "Turn each repeatable resolution into a structured procedure - a "
          "trigger plus ordered steps - and match an incoming ticket to one.")
flow_step(pdf, 3, "Plan and classify (System 3)",
          "Expand the procedure into steps, classify each by risk and owner, and "
          "group them into approval chunks that never cross a risk boundary.")
flow_step(pdf, 4, "Approve (human)",
          "An approver authorises a chunk. The approval binds by digest to "
          "exactly what was shown, so any later edit forces re-approval.")
flow_step(pdf, 5, "Execute (agent, simulated today)",
          "The agent runs the approved chunk through a scoped service account, "
          "then stops at the first human-owned or higher-risk step.")
flow_step(pdf, 6, "Hand off and resume (human)",
          "A person completes the steps the agent may not do, with evidence, and "
          "the run resumes exactly where it paused - or closes.", last=True)

callout(
    pdf,
    "One promise holds the whole thing together",
    "The agent is granted work at the step level, never the ticket level. That "
    "single rule is why there is a risk model, why approval is mandatory, why "
    "state is resumable, and why execution sits behind a wall.",
    tone=ACCENT, bg=(234, 243, 255),
)

# ---- 3. layered architecture ---------------------------------------------
pdf.add_page()
h1(pdf, "3  Layered architecture")
body(pdf,
     "The system is built in layers, each depending only on those beneath it. "
     "The rule that holds it together: all policy lives server-side, and the "
     "browser is a thin client that renders responses and calls back. Swapping "
     "the UI (browser today, a desktop app later) changes nothing below it.")

h2(pdf, "Server layers (Python / FastAPI)")
layer_box(pdf, "api.py", "HTTP contract every client speaks; authentication and role gates", ACCENT)
layer_box(pdf, "service.py", "Application authority â€” every use case and all rule enforcement", ACCENT)
layer_box(pdf, "auth.py", "Sessions, identity, roles; Entra SSO with a dev fallback", OK)
layer_box(pdf, "planner.py", "Ticket + procedure into an approvable, chunked plan", INK2)
layer_box(pdf, "risk.py", "Classifies each step; default-deny", WARN)
layer_box(pdf, "registry.py", "Effective rule set: built-in + published procedures", INK2)
layer_box(pdf, "knowledge.py", "Parses uploaded handbooks into reviewable drafts", INK2)
layer_box(pdf, "credentials.py", "Access references and scope â€” pointers to secrets, never secrets", DANGER)
layer_box(pdf, "live.py", "Read-only ServiceNow polling and query builder", OK)
layer_box(pdf, "simulator.py", "Step execution â€” simulation only; where connectors will attach", DANGER)
layer_box(pdf, "store.py", "Persistence behind a repository seam (SQLite today)", INK2)
layer_box(pdf, "domain.py", "Pure model: runs, steps, risk, state machines â€” no framework", INK3)

pdf.ln(1)
h2(pdf, "Client")
layer_box(pdf, "ui/  (React)", "Thin browser client; holds no business logic; light/dark themes", ACCENT)
body(pdf,
     "Because the client holds no logic, a tampered browser gains nothing â€” the "
     "server re-checks every rule. The same property makes a future desktop app "
     "a small change: point it at the API and ship the built assets.")

# ---- 3. data flow ---------------------------------------------------------
pdf.add_page()
h1(pdf, "4  How a ticket flows through")
body(pdf, "From a raw ServiceNow ticket to work that is done under control:")
pdf.ln(1)

flow_step(pdf, 1, "Intake",
          "A ticket arrives from the live ServiceNow feed (read-only) or the "
          "extracted data set. Reference fields are flattened to plain names.")
flow_step(pdf, 2, "Match",
          "The engine scores the ticket against the rule set and picks a "
          "procedure, or leaves it with a human if nothing matches confidently.")
flow_step(pdf, 3, "Plan",
          "The procedure is expanded into steps. Each step is classified by "
          "risk and owner, then grouped into approval chunks that never cross a "
          "risk boundary.")
flow_step(pdf, 4, "Approve",
          "A person with the approver role reviews a chunk and authorises it. "
          "The approval is bound by a digest to exactly what was shown.")
flow_step(pdf, 5, "Execute (simulated)",
          "The agent runs the approved chunk, then stops at the first "
          "human-owned or higher-risk step. Every step is recorded.")
flow_step(pdf, 6, "Hand off",
          "Control returns to a person for the steps the agent may not do. They "
          "complete them with evidence, and the run continues or closes.", last=True)

pdf.ln(2)
h2(pdf, "State is persistent and resumable")
body(pdf,
     "A run is stored, not held in memory, so it can pause for a human for as "
     "long as needed and resume exactly where it stopped. The run state is "
     "derived from its steps, which are the single source of truth.")

# ---- 4. risk model --------------------------------------------------------
pdf.add_page()
h1(pdf, "5  The risk model")
body(pdf,
     "Every step is classified before anyone can act on it. Classification is "
     "default-deny: anything not explicitly recognised as safe becomes RX and "
     "stays with a human. Colour is never the only signal â€” each level carries "
     "a plain-language label in the UI.")
pdf.ln(2)

risk_row(pdf, "R0", "Read-only observation â€” looks, changes nothing", "Agent (with approval)", OK)
risk_row(pdf, "R1", "Low impact, additive or reversible (e.g. a work note)", "Agent (with approval)", ACCENT)
risk_row(pdf, "R2", "Real operational change (e.g. restart a service)", "Agent (own approval)", WARN)
risk_row(pdf, "R3", "High impact or destructive (e.g. reboot, delete)", "Human only", DANGER)
risk_row(pdf, "RX", "Unclassified â€” safety cannot be proven", "Human only", INK3)

pdf.ln(3)
h2(pdf, "Why arbitrary commands stay human")
body(pdf,
     "A read-only 'Get-PSDrive C' and a destructive 'Remove-Item' are the same "
     "kind of command to the parser, so a raw command cannot justify agent "
     "execution on its own. It maps to RX until a person explicitly classifies "
     "it. That is why a disk-cleanup procedure splits into agent checks, human "
     "cleanup, then agent verification.")

callout(
    pdf,
    "Uploads cannot grant themselves execution rights",
    "A handbook may declare a step low-risk. When the action is one whose safety "
    "cannot be verified, that claim is flagged for the reviewer before "
    "publishing â€” the one route by which an upload could try to widen what the "
    "agent may do.",
    tone=WARN, bg=(253, 243, 227),
)

# ---- 5. safety controls ---------------------------------------------------
pdf.add_page()
h1(pdf, "6  Safety controls")
body(pdf, "Enforced on the server, so no client can bypass them:")
pdf.ln(1)

for text in [
    "No execution without an active approval; a refused attempt is itself audited.",
    "Human-owned chunks cannot be approved for the agent.",
    "An approval binds to a digest of the procedure version, steps, rendered text "
    "and parameters. Editing any of them invalidates it and forces re-approval.",
    "Exactly one active approval per chunk; a superseded one can never authorise a run.",
    "Steps run in order; the agent refuses to jump ahead of outstanding work.",
    "R2 needs its own individual approval and cannot hide inside a low-risk chunk.",
    "R3 and RX are never agent-executed â€” refused by policy and again by the simulator.",
    "Manual completion requires evidence.",
    "The audit log is append-only, and distinguishes what acted (agent), who "
    "triggered it, and who approved it.",
    "A ticket cannot be forked by accident; a second run needs a deliberate override.",
]:
    dot(pdf, text)

pdf.ln(2)
h2(pdf, "The execution boundary")
callout(
    pdf,
    "Live execution is hard-disabled",
    "simulator.py has no network, filesystem or credential access, and the "
    "System 2 live-execute path raises unconditionally. Turning on real "
    "connectors is a separate, deliberate project requiring allowlisted "
    "adapters, rollback, and a security review â€” not a config flag.",
    tone=DANGER, bg=(253, 236, 235),
)

# ---- 7. execution and connectors ------------------------------------------
pdf.add_page()
h1(pdf, "7  Execution and connectors - who does the job")
body(pdf,
     "This is the part the whole design builds toward: turning an approved step "
     "into a real change on a real system. Today it is simulated. The plan for "
     "making it live is settled in shape, and deliberately gated.")

h2(pdf, "Who executes each step")
bullet(pdf, "Agent  (R0-R2)",
       "Runs read-only, reversible, and approved operational steps. It acts as "
       "actor=agent with triggered_by set to the person and the approver on "
       "record - never borrowing anyone's personal identity.")
bullet(pdf, "Human  (R3 / RX)",
       "Performs destructive, high-impact, or unclassified steps by hand and "
       "records evidence. The run then resumes with the agent for remaining "
       "safe steps.")
body(pdf,
     "service.py decides the actor per step, server-side. The agent reaches a "
     "target only through the narrow, scoped service accounts in the "
     "credentials centre - so a login never becomes the agent's authority.")

h2(pdf, "The execution seam")
body(pdf,
     "All execution funnels through simulator.py's execute_step(). When "
     "connectors arrive they sit behind it, gated on an allowlisted "
     "(connector, operation) pair. The secret is resolved from its pointer at "
     "the moment of use (credentials.resolve()), the action is applied, "
     "postconditions are checked, and a rollback path is available. The "
     "approval check that service.py performs stays exactly where it is.")

h2(pdf, "Connector plan")
bullet(pdf, "ServiceNow",
       "Implemented, read-only today (work notes and state changes are the "
       "write plan). This is the one connector that already talks to a system.")
bullet(pdf, "WinRM / SSH",
       "Windows and Linux hosts: service restarts, file removal, reboots. "
       "Planned - the highest-risk writes, so heavily gated.")
bullet(pdf, "Active Directory",
       "Unlock, password reset, group membership. Planned.")
bullet(pdf, "vCenter / HTTP",
       "VM power operations and generic HTTP endpoints. Planned.")

callout(
    pdf,
    "Live execution is hard-disabled - and stays that way until it is earned",
    "simulator.py has no network, filesystem or credential access, and "
    "CONNECTORS_ENABLED is False. Enabling real connectors is a separate, "
    "security-reviewed project: allowlisted adapters, credential resolution at "
    "point of use, postcondition checks, and rollback - not a config flag.",
    tone=DANGER, bg=(253, 236, 235),
)

# ---- 8. authentication ----------------------------------------------------
pdf.add_page()
h1(pdf, "8  Authentication and roles")
body(pdf,
     "Sign-in establishes a verified identity so every approval and audit entry "
     "is attributable and cannot be spoofed. Crucially, a person's login is "
     "kept separate from what the agent may reach.")

h2(pdf, "Two sign-in methods")
bullet(pdf, "Entra ID (SSO)",
       "The real path. Redirect to Microsoft, return with a verified identity "
       "and role claims. Needs an app registration from your Azure AD admins.")
bullet(pdf, "Dev login",
       "A local fallback used only when Entra is not configured, so it can "
       "never accidentally ship as the real auth mechanism.")

h2(pdf, "Roles gate what a person may do")
risk_row(pdf, "V", "Viewer â€” read only", "", INK3)
risk_row(pdf, "O", "Operator â€” create runs, manual steps, uploads", "", ACCENT)
risk_row(pdf, "A", "Approver â€” also approve agent actions, publish", "", OK)
risk_row(pdf, "AD", "Admin â€” also manage access credentials", "", DANGER)

pdf.ln(3)
callout(
    pdf,
    "Login does not become the agent's authority",
    "Your role decides what you may approve. The agent never borrows your "
    "identity or permissions â€” it acts through the narrow, scoped service "
    "accounts in the credentials centre. An executed step is recorded as "
    "actor=agent with triggered_by set to the person. This is what stops a "
    "malicious ticket from turning your access into a weapon.",
    tone=ACCENT, bg=(234, 243, 255),
)

h2(pdf, "Credentials are pointers, never secrets")
body(pdf,
     "The credentials centre stores a reference to each secret (an environment "
     "variable, the OS credential store, or the enterprise vault) plus the "
     "scope it may act on. A pasted value is written to Windows Credential "
     "Manager and reduced to a pointer. No secret value is ever kept in the "
     "application database, returned by the API, or written to the audit log.")

# ---- 7. current state ------------------------------------------------------
pdf.add_page()
h1(pdf, "9  Current state and the roadmap")
h2(pdf, "Working and verified today")
for text in [
    "Discovery, procedure matching, and parameter extraction against real data.",
    "Per-step risk classification and chunked, digest-bound approval.",
    "Persistent, resumable runs with an append-only audit trail.",
    "Uploadable handbooks with a human review-and-publish gate.",
    "Read-only live ServiceNow feed across incidents, changes, requests, problems.",
    "Sign-in with role-gated actions; identity cannot be spoofed via a header.",
    "React UI with light and dark themes; a dependency-free fallback client.",
]:
    dot(pdf, text)

h2(pdf, "Deliberately not built yet")
for text in [
    "Real connectors. Execution is simulated; enabling it is a separate project.",
    "Full JWKS signature validation on the Entra token, and session revocation.",
    "Separation of duties (one person can currently both upload and publish).",
    "A transactional server database and a background worker for scale.",
]:
    dot(pdf, text)

h2(pdf, "Roadmap: from simulation to live")
bullet(pdf, "Now",
       "Decision, approval, and audit machinery is real; execution is simulated "
       "behind the wall; ServiceNow is read-only.")
bullet(pdf, "Next",
       "Harden auth (JWKS validation, session revocation, separation of duties) "
       "and move to a transactional database with a background worker.")
bullet(pdf, "Then",
       "First real connector behind execute_step() - most likely ServiceNow "
       "writes - with allowlisted operations, credential resolution at point of "
       "use, postcondition checks, and rollback, under security review.")
bullet(pdf, "Later",
       "Broaden connectors (WinRM, SSH, Active Directory, vCenter, HTTP) one at "
       "a time, each gated and reviewed, lowest risk first.")

pdf.ln(3)
pdf.set_draw_color(*LINE)
pdf.line(MARGIN, pdf.get_y(), PAGE_W - MARGIN, pdf.get_y())
pdf.ln(4)
pdf.set_font("Helvetica", "I", 9)
pdf.set_text_color(*INK3)
pdf.multi_cell(CONTENT_W, 4.6,
               "This is a faithful summary of the system as built. Where a "
               "capability is described as simulated or not yet present, that is "
               "stated plainly rather than implied to be complete.")

from pathlib import Path as _Path  # noqa: E402  (imperative build script; import follows document assembly)
_root = _Path(__file__).resolve().parent.parent
(_root / "output").mkdir(exist_ok=True)
out = str(_root / "output" / "BUCKY_Architecture.pdf")
pdf.output(out)
print("wrote", out)

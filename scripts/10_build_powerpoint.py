"""Generate the LOKI project PowerPoint deck.

A stakeholder-facing walkthrough of the whole project: the problem, the three
systems, how a ticket flows, the risk model, who executes the work, the
connector plan, the business value (from real discovery data), a proven live
demo, current state, and the roadmap.

Content is faithful to the code in overlay/ and the data in
automation_candidates.csv. Execution is simulated today; the deck says so.

    python scripts/build_powerpoint.py
"""

from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE

# ---- palette (matches the PDFs) -------------------------------------------
INK = RGBColor(0x1D, 0x1D, 0x1F)
INK2 = RGBColor(0x48, 0x48, 0x4A)
INK3 = RGBColor(0x86, 0x86, 0x8B)
ACCENT = RGBColor(0x00, 0x71, 0xE3)
OK = RGBColor(0x1D, 0x83, 0x48)
WARN = RGBColor(0x8A, 0x53, 0x00)
DANGER = RGBColor(0xC4, 0x24, 0x1C)
LINE = RGBColor(0xCD, 0xCD, 0xD4)
CARD = RGBColor(0xFA, 0xFA, 0xFC)
SURFACE = RGBColor(0xF5, 0xF5, 0xF7)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
BLUEBG = RGBColor(0xEA, 0xF3, 0xFF)
GREENBG = RGBColor(0xE8, 0xF6, 0xED)
REDBG = RGBColor(0xFD, 0xEC, 0xEB)
AMBERBG = RGBColor(0xFD, 0xF3, 0xE3)

prs = Presentation()
prs.slide_width = Inches(13.333)
prs.slide_height = Inches(7.5)
SW, SH = prs.slide_width, prs.slide_height
BLANK = prs.slide_layouts[6]

FONT = "Calibri"


# ---- primitives -----------------------------------------------------------
def slide():
    return prs.slides.add_slide(BLANK)


def rect(s, x, y, w, h, fill=None, line=None, line_w=0.75, shape=MSO_SHAPE.RECTANGLE):
    sp = s.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
    sp.shadow.inherit = False
    if fill is None:
        sp.fill.background()
    else:
        sp.fill.solid()
        sp.fill.fore_color.rgb = fill
    if line is None:
        sp.line.fill.background()
    else:
        sp.line.color.rgb = line
        sp.line.width = Pt(line_w)
    return sp


def text(s, x, y, w, h, runs, align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP,
         space_after=4, line_spacing=1.0):
    """runs: list of paragraphs; each paragraph is a list of (txt, size, bold, color)."""
    tb = s.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.vertical_anchor = anchor
    for i, para in enumerate(runs):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.space_after = Pt(space_after)
        p.space_before = Pt(0)
        p.line_spacing = line_spacing
        if isinstance(para, tuple):
            para = [para]
        for (txt, size, bold, color) in para:
            r = p.add_run()
            r.text = txt
            r.font.size = Pt(size)
            r.font.bold = bold
            r.font.name = FONT
            r.font.color.rgb = color
    return tb


def bg(s, color=WHITE):
    rect(s, -0.1, -0.1, 13.53, 7.7, fill=color)


def header(s, num, title, subtitle=None):
    bg(s, WHITE)
    text(s, 0.55, 0.32, 0.9, 0.7, [[(num, 30, True, ACCENT)]])
    text(s, 1.35, 0.34, 11.4, 0.7, [[(title, 26, True, INK)]])
    rect(s, 0.6, 1.12, 1.4, 0.05, fill=ACCENT)
    if subtitle:
        text(s, 1.37, 0.92, 11.4, 0.4, [[(subtitle, 12.5, False, INK2)]])
    # footer
    rect(s, 0.55, 7.06, 12.23, 0.02, fill=LINE)
    text(s, 0.55, 7.08, 6, 0.3, [[("Project LOKI", 8.5, False, INK3)]])
    text(s, 6.7, 7.08, 6.08, 0.3,
         [[("Simulation only - no live execution", 8.5, False, INK3)]],
         align=PP_ALIGN.RIGHT)


def card(s, x, y, w, h, title, body, tone=ACCENT, fill=CARD, title_size=13,
         body_size=10.5):
    rect(s, x, y, w, h, fill=fill, line=LINE, line_w=0.75)
    rect(s, x, y, 0.07, h, fill=tone)
    runs = [[(title, title_size, True, INK)]]
    if body:
        runs.append([(body, body_size, False, INK2)])
    text(s, x + 0.18, y + 0.12, w - 0.32, h - 0.2, runs, space_after=4,
         line_spacing=1.02)


def chip(s, x, y, w, h, label, tone, fill):
    rect(s, x, y, w, h, fill=fill, line=tone, line_w=1.0,
         shape=MSO_SHAPE.ROUNDED_RECTANGLE)
    text(s, x, y, w, h, [[(label, 11, True, tone)]], align=PP_ALIGN.CENTER,
         anchor=MSO_ANCHOR.MIDDLE)


def arrow_right(s, x, y, w, color=INK3):
    a = s.shapes.add_shape(MSO_SHAPE.RIGHT_ARROW, Inches(x), Inches(y),
                           Inches(w), Inches(0.18))
    a.shadow.inherit = False
    a.fill.solid()
    a.fill.fore_color.rgb = color
    a.line.fill.background()


# ===========================================================================
# 1 — COVER
# ===========================================================================
s = slide()
bg(s, INK)
rect(s, 0.9, 2.35, 1.15, 1.15, fill=ACCENT)
text(s, 0.9, 2.5, 1.15, 0.9, [[("L", 44, True, WHITE)]], align=PP_ALIGN.CENTER)
text(s, 2.35, 2.15, 10, 1.4, [[("LOKI", 66, True, WHITE)]])
text(s, 2.42, 3.55, 10.4, 0.7,
     [[("Human-in-the-loop ticket automation", 22, True, RGBColor(0xDC, 0xDC, 0xE2))]])
text(s, 2.42, 4.35, 9.8, 0.9,
     [[("Recognise repetitive tickets, plan the fix, and let an agent run the "
        "safe steps under human approval - one step at a time.", 13.5, False,
        RGBColor(0xAA, 0xAA, 0xB2))]], line_spacing=1.15)
chip(s, 2.42, 5.35, 2.9, 0.5, "Simulation only", OK, GREENBG)
chip(s, 5.5, 5.35, 3.1, 0.5, "Server-side policy", ACCENT, BLUEBG)
chip(s, 8.78, 5.35, 3.2, 0.5, "Governed + audited", WARN, AMBERBG)
text(s, 2.42, 6.35, 10, 0.4,
     [[("Project overview - problem, architecture, value, and roadmap", 11, False,
        INK3)]])

# ===========================================================================
# 2 — THE PROBLEM
# ===========================================================================
s = slide()
header(s, "1", "The problem", "A service desk drowns in tickets that are the same request, solved the same way.")
text(s, 0.6, 1.45, 7.0, 2.2, [
    [("Support teams get a constant stream of tickets. A large share are "
      "not novel - they are the same handful of requests (access, unlocks, "
      "provisioning, server housekeeping) handled the same way, over and over.",
      14, False, INK2)],
    [("That repetitive work is slow, inconsistent between engineers, and "
      "expensive - yet too routine to need human judgement on every step.",
      14, False, INK2)],
], line_spacing=1.18)
card(s, 0.6, 4.0, 3.7, 1.5, "Repetitive", "Thousands of near-identical tickets a quarter.", tone=ACCENT, fill=BLUEBG)
card(s, 4.5, 4.0, 3.7, 1.5, "Manual + slow", "Each re-solved by hand, minutes at a time.", tone=WARN, fill=AMBERBG)
card(s, 8.4, 4.0, 4.3, 1.5, "Auditable by law", "Regulated shops must record who did what.", tone=OK, fill=GREENBG)
# big stat
rect(s, 8.4, 1.45, 4.35, 2.3, fill=SURFACE, line=LINE)
text(s, 8.4, 1.62, 4.35, 1.1, [[("1,313", 46, True, ACCENT)]], align=PP_ALIGN.CENTER)
text(s, 8.4, 2.75, 4.35, 0.9, [
    [("times a single request type appears", 12, False, INK2)],
    [("in this project's own discovery data", 11, False, INK3)],
], align=PP_ALIGN.CENTER, space_after=1)

# ===========================================================================
# 3 — WHAT LOKI IS
# ===========================================================================
s = slide()
header(s, "2", "What LOKI is", "A governed automation platform - a web app, not a script.")
text(s, 0.6, 1.45, 12.1, 1.0, [
    [("LOKI turns repeatable resolutions into structured procedures, matches "
      "incoming tickets to them, and runs them under human control. The core "
      "promise:", 14, False, INK2)],
], line_spacing=1.15)
rect(s, 0.6, 2.5, 12.15, 0.95, fill=BLUEBG, line=None)
rect(s, 0.6, 2.5, 0.09, 0.95, fill=ACCENT)
text(s, 0.85, 2.6, 11.8, 0.8, [
    [("The agent is granted work at the step level, never the ticket level.", 16, True, INK)],
    [("A single ticket moves back and forth between agent and human. Neither owns the whole thing.", 12, False, INK2)],
], space_after=3)
card(s, 0.6, 3.85, 4.0, 1.5, "Python / FastAPI server", "Holds all logic and policy. The authority on every rule.", tone=ACCENT)
card(s, 4.75, 3.85, 4.0, 1.5, "React web UI", "A browser dashboard. Holds no logic - it just calls the server.", tone=ACCENT)
card(s, 8.9, 3.85, 3.85, 1.5, "SQLite store", "Remembers every run, approval, and audit entry.", tone=INK2)
text(s, 0.6, 5.55, 12.1, 1.0, [
    [("Not an LLM. ", 13, True, DANGER),
     ("The \"agent\" is a deterministic rules-and-policy engine. Matching is "
      "keyword scoring; risk is a fixed lookup table. No model decides anything - "
      "which is exactly what makes it auditable and safe to review.", 13, False, INK2)],
], line_spacing=1.15)

# ===========================================================================
# 4 — THE THREE SYSTEMS
# ===========================================================================
s = slide()
header(s, "3", "The three systems", "Discovery finds the work, Procedures codify it, the Task overlay runs it safely.")
y = 1.7
card(s, 0.6, y, 3.85, 3.1, "SYSTEM 1", "", tone=INK2, fill=SURFACE, title_size=13)
text(s, 0.78, y + 0.55, 3.5, 2.4, [
    [("Discovery", 16, True, INK)],
    [("Mines ServiceNow history to find which task types repeat often enough to "
      "be worth automating, and how they were actually resolved.", 11.5, False, INK2)],
], line_spacing=1.12)
card(s, 4.72, y, 3.85, 3.1, "SYSTEM 2", "", tone=ACCENT, fill=BLUEBG, title_size=13)
text(s, 4.9, y + 0.55, 3.5, 2.4, [
    [("Procedures", 16, True, INK)],
    [("The rulebook. Each resolution is a structured procedure - a trigger plus "
      "ordered steps. Matches an incoming ticket to one.", 11.5, False, INK2)],
], line_spacing=1.12)
card(s, 8.84, y, 3.9, 3.1, "SYSTEM 3", "", tone=OK, fill=GREENBG, title_size=13)
text(s, 9.02, y + 0.55, 3.55, 2.4, [
    [("Task overlay", 16, True, INK)],
    [("The heart of LOKI. Adds per-step risk, human approval, resumable runs, an "
      "audit trail, live intake, and sign-in.", 11.5, False, INK2)],
], line_spacing=1.12)
arrow_right(s, 4.47, y + 1.4, 0.24, INK3)
arrow_right(s, 8.59, y + 1.4, 0.24, INK3)
text(s, 0.6, 5.3, 12.1, 0.8, [
    [("Together: a ticket comes in -> matched to a known procedure -> expanded "
      "into risk-classified steps -> approved -> run under control.", 13.5, False, INK2)],
], line_spacing=1.15)

# ===========================================================================
# 5 — HOW A TICKET FLOWS
# ===========================================================================
s = slide()
header(s, "4", "How a ticket flows through", "Control bounces between agent and human - one step at a time.")
steps = [
    ("1  Intake", "Live feed or data set", INK3, SURFACE),
    ("2  Match", "Score vs the rule set", INK3, SURFACE),
    ("3  Plan", "Risk-classify + chunk", INK3, SURFACE),
    ("4  Approve", "A person authorises", OK, GREENBG),
    ("5  Execute", "Agent runs (simulated)", ACCENT, BLUEBG),
    ("6  Hand off", "Human + evidence", OK, GREENBG),
]
x = 0.6
w = 1.9
gap = 0.12
for i, (t, d, tone, fill) in enumerate(steps):
    card(s, x, 1.9, w, 1.4, t, d, tone=tone, fill=fill, title_size=13, body_size=9.5)
    if i < len(steps) - 1:
        arrow_right(s, x + w + 0.005, 2.5, gap + 0.02, INK3)
    x += w + gap
text(s, 0.6, 3.55, 12.15, 0.5, [
    [("<- resume: a run is stored, so it can pause for a human indefinitely and "
      "resume exactly where it stopped", 11.5, True, WARN)]])
card(s, 0.6, 4.25, 12.15, 1.05, "State is persistent and resumable",
     "A run is derived from its steps (the single source of truth), not held in memory. "
     "That is what lets a ticket move back and forth without losing its place.",
     tone=INK2, fill=SURFACE)
card(s, 0.6, 5.45, 12.15, 1.15, "Every action is bound and audited",
     "Approval is sealed with a digest of exactly what was shown; editing any step invalidates it. "
     "The audit log records what acted (agent), who triggered it, and who approved it.",
     tone=ACCENT, fill=BLUEBG)

# ===========================================================================
# 6 — THE RISK MODEL / WHO DOES WHAT
# ===========================================================================
s = slide()
header(s, "5", "The risk model - who does the job", "Default-deny: anything not proven safe stays with a human.")
rows = [
    ("R0", "Read-only observation (check, connect)", "Agent, with approval", OK),
    ("R1", "Low impact, reversible (a work note)", "Agent, with approval", ACCENT),
    ("R2", "Real operational change (restart a service)", "Agent, its own approval", WARN),
    ("R3", "High impact or destructive (reboot, delete)", "Human only", DANGER),
    ("RX", "Unclassified - safety cannot be proven", "Human only", INK3),
]
y = 1.75
for code, meaning, who, tone in rows:
    rect(s, 0.6, y, 0.95, 0.62, fill=tone)
    text(s, 0.6, y, 0.95, 0.62, [[(code, 15, True, WHITE)]], align=PP_ALIGN.CENTER, anchor=MSO_ANCHOR.MIDDLE)
    rect(s, 1.62, y, 7.3, 0.62, fill=CARD, line=LINE)
    text(s, 1.78, y, 7.1, 0.62, [[(meaning, 12, False, INK2)]], anchor=MSO_ANCHOR.MIDDLE)
    rect(s, 8.99, y, 3.76, 0.62, fill=SURFACE, line=LINE)
    text(s, 9.15, y, 3.6, 0.62, [[(who, 12, True, tone)]], anchor=MSO_ANCHOR.MIDDLE)
    y += 0.72
text(s, 0.6, 5.5, 12.15, 1.1, [
    [("Why arbitrary commands stay human. ", 12.5, True, INK),
     ("A read-only Get-PSDrive and a destructive Remove-Item are the same kind of "
      "command to the parser - so a raw command can never justify agent execution "
      "on its own. It maps to RX until a person classifies it.", 12.5, False, INK2)],
], line_spacing=1.15)

# ===========================================================================
# 7 — EXECUTION & CONNECTORS
# ===========================================================================
s = slide()
header(s, "6", "Execution and connectors", "How an approved step becomes real work - the plan, and today's reality.")
# actor split
card(s, 0.6, 1.7, 6.0, 1.25, "AGENT executes R0-R2 (approved)",
     "Acts as a scoped service account - actor=agent, triggered_by=person. Stops at the first human/higher-risk step.",
     tone=ACCENT, fill=BLUEBG, title_size=13, body_size=10.5)
card(s, 6.75, 1.7, 6.0, 1.25, "HUMAN executes R3 / RX",
     "Destructive, high-impact, or unclassified steps done by hand, with recorded evidence. Run then resumes.",
     tone=OK, fill=GREENBG, title_size=13, body_size=10.5)
# gateway
card(s, 0.6, 3.15, 12.15, 1.0, "simulator.py -> execute_step()  -  the connector gateway",
     "Gated on an allowlisted (connector, operation) pair. Resolves the secret at the moment of use, applies the "
     "action, checks postconditions, can roll back. TODAY: returns [SIM] outcomes - CONNECTORS_ENABLED = False.",
     tone=DANGER, fill=REDBG, title_size=12.5, body_size=10.5)
# connectors
conns = [("ServiceNow", "live", OK, GREENBG), ("WinRM", "planned", INK3, SURFACE),
         ("SSH", "planned", INK3, SURFACE), ("Active Dir.", "planned", INK3, SURFACE),
         ("vCenter", "planned", INK3, SURFACE), ("HTTP API", "planned", INK3, SURFACE)]
x = 0.6
w = 1.94
for name, st, tone, fill in conns:
    card(s, x, 4.35, w, 1.0, name, st, tone=tone, fill=fill, title_size=12, body_size=10)
    x += w + 0.06
text(s, 0.6, 5.6, 12.15, 1.0, [
    [("Live execution is hard-disabled by design. ", 12.5, True, DANGER),
     ("Turning on real connectors is a separate, security-reviewed project - "
      "allowlisted adapters, credential resolution, postcondition checks, rollback. "
      "Not a config flag.", 12.5, False, INK2)],
], line_spacing=1.15)

# ===========================================================================
# 8 — BUSINESS VALUE (volume)
# ===========================================================================
s = slide()
header(s, "7", "Business value", "The payoff is the repetitive majority - and the data ranks it for us.")
text(s, 0.6, 1.5, 6.0, 0.5, [[("Top repetitive tasks (real discovery data)", 13, True, INK)]])
tasks = [("URL / Website access", 1313), ("Confluence access", 1138),
         ("Jira Cloud access", 886), ("Shared folder access", 806),
         ("Firewall policy change", 533), ("Shared mailbox access", 446),
         ("Distribution list edits", 441), ("Unlock GitLab account", 328)]
maxv = 1313
y = 2.05
barx = 2.75
barmax = 3.5
for name, v in tasks:
    text(s, 0.6, y - 0.04, 2.1, 0.35, [[(name, 9.5, False, INK2)]])
    bw = barmax * (v / maxv)
    rect(s, barx, y, max(bw, 0.05), 0.26, fill=ACCENT)
    text(s, barx + max(bw, 0.05) + 0.05, y - 0.04, 0.9, 0.32, [[(f"{v:,}", 9.5, True, INK)]])
    y += 0.42
# right column value logic
card(s, 6.9, 1.5, 5.85, 2.55, "Where the value comes from", "", tone=ACCENT, fill=BLUEBG, title_size=13)
text(s, 7.08, 2.05, 5.5, 2.1, [
    [("- Recognise a repeatable ticket, propose the known fix - no rediscovery.", 11.5, False, INK2)],
    [("- Agent takes the safe, boring steps; human keeps judgement + risk.", 11.5, False, INK2)],
    [("- Consistency + speed + a clean audit trail that passes review.", 11.5, False, INK2)],
    [("- Highest-volume tasks are additive/reversible - the safest to automate.", 11.5, False, INK2)],
], line_spacing=1.12, space_after=6)
card(s, 6.9, 4.25, 5.85, 1.05, "Agent does", "look / connect / check, post work notes, restart a named service", tone=ACCENT, fill=WHITE, title_size=12)
card(s, 6.9, 5.4, 5.85, 1.05, "Human does", "reboot, delete data, power-off a VM, anything needing judgement", tone=OK, fill=WHITE, title_size=12)
text(s, 0.6, 5.75, 6.0, 0.9, [
    [("These are access / provisioning requests: high volume, reversible - the "
      "highest-payoff, lowest-risk class to automate.", 10.5, False, INK3)],
], line_spacing=1.1)

# ===========================================================================
# 9 — PROVEN: THE LIVE DEMO
# ===========================================================================
s = slide()
header(s, "8", "Proven end to end (demo)", "We ran it: propose -> approve -> execute, with a full audit trail.")
text(s, 0.6, 1.45, 12.1, 0.5, [
    [("A \"Netprobe service stopped\" ticket, driven through the real service - the "
      "one procedure whose every step is agent-runnable:", 12.5, False, INK2)]])
# terminal-style block
rect(s, 0.6, 2.05, 12.15, 3.35, fill=INK, line=None)
demo_lines = [
    ("RUN run_d315... | procedure: Restart a Windows service | state: awaiting_approval", RGBColor(0x9C,0xDC,0xFE)),
    ("PLAN (per-step risk and owner):", WHITE),
    ("  1. connect          R0  ai    :: open a remoting session", RGBColor(0xB5,0xCE,0xA8)),
    ("  2. check            R0  ai    :: confirm service not running", RGBColor(0xB5,0xCE,0xA8)),
    ("  3. restart_service  R2  ai    :: restart the service", RGBColor(0xDC,0xDC,0xAA)),
    ("  4. check            R0  ai    :: confirm service running", RGBColor(0xB5,0xCE,0xA8)),
    ("  5. notify           R1  ai    :: post work note", RGBColor(0xB5,0xCE,0xA8)),
    ("APPROVED by bob_approver -> agent RAN each chunk", RGBColor(0x9C,0xDC,0xFE)),
    ("FINAL run state: completed", RGBColor(0x4E,0xC9,0xB0)),
    ("AUDIT: run.proposed(alice) | chunk.approved(bob) | step.executed(agent) x4", RGBColor(0x86,0x86,0x8B)),
]
ty = 2.2
for line, col in demo_lines:
    text(s, 0.8, ty, 11.8, 0.3, [[(line, 11, False, col)]])
    ty += 0.31
text(s, 0.6, 5.6, 12.1, 1.0, [
    [("The only simulated part is the last inch. ", 12.5, True, DANGER),
     ("Matching, planning, the risk split, approval gating, execution order and "
      "audit are all real. restart_service returned a canned result instead of "
      "touching the box. Run it yourself: ", 12.5, False, INK2),
     ("python scripts/demo_agent_run.py", 12.5, True, ACCENT)],
], line_spacing=1.15)

# ===========================================================================
# 10 — CURRENT STATE & ROADMAP
# ===========================================================================
s = slide()
header(s, "9", "Current state and roadmap", "Honest about what is real, what is simulated, and what comes next.")
card(s, 0.6, 1.7, 6.0, 3.05, "Working and verified today", "", tone=OK, fill=GREENBG, title_size=13)
text(s, 0.78, 2.25, 5.65, 2.4, [
    [("- Discovery, matching, parameter extraction on real data", 11, False, INK2)],
    [("- Per-step risk + chunked, digest-bound approval", 11, False, INK2)],
    [("- Persistent, resumable runs + append-only audit", 11, False, INK2)],
    [("- Uploadable handbooks with a human publish gate", 11, False, INK2)],
    [("- Read-only live ServiceNow feed", 11, False, INK2)],
    [("- Role-gated sign-in; identity can't be spoofed by a header", 11, False, INK2)],
], line_spacing=1.14, space_after=4)
card(s, 6.75, 1.7, 6.0, 3.05, "Not built yet (by design)", "", tone=DANGER, fill=REDBG, title_size=13)
text(s, 6.93, 2.25, 5.65, 2.4, [
    [("- Real connectors - execution is simulated", 11, False, INK2)],
    [("- Full token signature validation + session revocation", 11, False, INK2)],
    [("- Separation of duties (upload vs publish)", 11, False, INK2)],
    [("- A transactional DB + background worker for scale", 11, False, INK2)],
    [("- A worker for long waits (e.g. 7-day decommission soak)", 11, False, INK2)],
], line_spacing=1.14, space_after=4)
# roadmap strip
road = [("Now", "Decide/approve/audit real; execution simulated", OK, GREENBG),
        ("Next", "Harden auth; move to a transactional DB + worker", ACCENT, BLUEBG),
        ("Then", "First real connector behind execute_step()", WARN, AMBERBG),
        ("Later", "Broaden connectors, one at a time, lowest risk first", DANGER, REDBG)]
x = 0.6
w = 2.96
for t, d, tone, fill in road:
    card(s, x, 5.0, w, 1.55, t, d, tone=tone, fill=fill, title_size=13, body_size=10)
    x += w + 0.06

# ===========================================================================
# 11 — CLOSING / SUMMARY
# ===========================================================================
s = slide()
bg(s, INK)
text(s, 0.9, 1.5, 11.5, 1.0, [[("Why LOKI makes sense", 34, True, WHITE)]])
rect(s, 0.95, 2.5, 1.4, 0.06, fill=ACCENT)
text(s, 0.95, 2.85, 11.4, 3.0, [
    [("It is a governed automation platform for the repetitive majority of "
      "tickets, where the win is speed, consistency, and a clean audit trail - "
      "and a human stays in control of anything risky.", 16, False, RGBColor(0xDC,0xDC,0xE2))],
    [("", 8, False, WHITE)],
    [("The hard, security-reviewable chassis already works. Most automation "
      "projects get flashy execution running and never survive the security "
      "review. LOKI did it the other way round.", 16, False, RGBColor(0xDC,0xDC,0xE2))],
], line_spacing=1.2, space_after=10)
text(s, 0.95, 5.7, 11.4, 0.8, [
    [("It becomes genuinely useful the moment two things happen: procedures for "
      "the top-volume tasks, and one low-risk connector behind execute_step().",
      14, True, WHITE)],
], line_spacing=1.15)

# ---- speaker notes (travel with the file, shown in Presenter View) --------
NOTES = [
    # 1 cover
    "Opening: LOKI is human-in-the-loop ticket automation. The one line to "
    "remember - the agent is granted work one step at a time, never a whole "
    "ticket. Execution is simulated today; the governance around it is real.",
    # 2 problem
    "Service desks are flooded with tickets that are the same request solved the "
    "same way. It's slow, inconsistent, and expensive - but too routine to need "
    "human judgement on every step. The 1,313 stat is from our own discovery "
    "data: one request type, 1,313 times.",
    # 3 what loki is
    "LOKI is a web app, not a script: a FastAPI server that holds all logic, a "
    "React UI that holds none, and a SQLite store for runs and audit. Be explicit: "
    "there is NO LLM. The 'agent' is a deterministic rules engine - matching is "
    "keyword scoring, risk is a lookup table. That's what makes it reviewable.",
    # 4 three systems
    "Three systems in a line. System 1 Discovery mines history to find repetitive "
    "work. System 2 Procedures is the rulebook that matches a ticket. System 3, the "
    "Task overlay, is the heart of LOKI - risk, approval, resumable runs, audit.",
    # 5 flow
    "Walk the six stages: intake, match, plan, approve, execute, hand off. The key "
    "idea is the resume loop - a run is stored and derived from its steps, so it "
    "can pause for a human indefinitely and pick up exactly where it left off. "
    "Approvals are digest-bound; edit a step and the approval is void.",
    # 6 risk
    "Default-deny is the whole safety story. R0/R1/R2 the agent may run with "
    "approval; R3 and RX are human-only. Arbitrary commands are RX on purpose: a "
    "read-only and a destructive command look identical to the parser, so a "
    "command alone can never authorise the agent.",
    # 7 execution
    "This answers 'who actually does the job'. Agent does R0-R2 as a scoped service "
    "account; human does R3/RX by hand. All execution funnels through "
    "execute_step(), gated on an allowlisted connector+operation. ServiceNow is the "
    "only live connector (read-only); the rest are planned. Live execution is "
    "hard-disabled and enabling it is a separate security-reviewed project.",
    # 8 value
    "The payoff is the repetitive majority. The bars are real discovery data - the "
    "top tasks are access and provisioning requests, which are additive and "
    "reversible: the safest, highest-payoff class to automate. Note these are NOT "
    "the Wintel procedures currently coded - that's the gap to close.",
    # 9 demo
    "This is proof, not a promise. We drove the real service end to end - propose, "
    "approve, execute - and it reached 'completed' with a full audit trail showing "
    "actor=agent vs the approver. The only simulated part is the last inch: "
    "restart_service returned a canned result. Anyone can reproduce it with "
    "python scripts/demo_agent_run.py.",
    # 10 roadmap
    "Be honest here - it builds credibility. Left: what's real. Right: what's "
    "deliberately not built. The roadmap: harden auth and storage, then build ONE "
    "low-risk connector behind execute_step(), then broaden connectors lowest-risk "
    "first. Each with security review.",
    # 11 closing
    "Close on the value judgement: LOKI is a governed platform for repetitive work "
    "with a human in control of risk. The hard, review-passing chassis already "
    "works - most projects get execution flashy and fail the security review; LOKI "
    "did it the other way. It becomes useful with top-task procedures + one "
    "connector.",
]
for sl, note in zip(prs.slides, NOTES):
    sl.notes_slide.notes_text_frame.text = note

from pathlib import Path as _Path
_root = _Path(__file__).resolve().parent.parent
(_root / "output").mkdir(exist_ok=True)
out = str(_root / "output" / "LOKI_Project_Overview.pptx")
prs.save(out)
print("wrote", out, "-", len(prs.slides._sldIdLst), "slides with notes")

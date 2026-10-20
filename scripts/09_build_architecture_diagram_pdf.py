"""Generate a visual BUCKY architecture DIAGRAM as a PDF.

Boxes-and-arrows drawings (not prose): a layered component stack, the
ticket data-flow pipeline, and the client/server trust boundary. Pure
layout code using fpdf 2.x -- no network, no app imports.

    python scripts/build_architecture_diagram_pdf.py
"""

from fpdf import FPDF

# ---- palette --------------------------------------------------------------
INK = (29, 29, 31)
INK2 = (72, 72, 74)
INK3 = (134, 134, 139)
ACCENT = (0, 113, 227)
OK = (29, 131, 72)
WARN = (138, 83, 0)
DANGER = (196, 36, 28)
LINE = (205, 205, 212)
CARD = (250, 250, 252)
WHITE = (255, 255, 255)

# ---- landscape A4 ---------------------------------------------------------
PAGE_W, PAGE_H = 297, 210
MARGIN = 14
CONTENT_W = PAGE_W - 2 * MARGIN

_SUBS = {
    "\u2014": "-", "\u2013": "-", "\u2019": "'", "\u2018": "'",
    "\u201c": '"', "\u201d": '"', "\u2026": "...", "\u2022": chr(149),
    "\u00a0": " ", "\u2192": "->",
}


def s(text: str) -> str:
    for a, b in _SUBS.items():
        text = text.replace(a, b)
    return text


class Doc(FPDF):
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

    def footer(self):
        self.set_font("Helvetica", "", 7.5)
        self.set_text_color(*INK3)
        third = CONTENT_W / 3
        self.set_xy(MARGIN, -11)
        self.cell(third, 4, "BUCKY - System Architecture (diagram)", align="L")
        self.set_xy(MARGIN + third, -11)
        self.cell(third, 4, "Simulation only - no live execution", align="C")
        self.set_xy(MARGIN + 2 * third, -11)
        self.cell(third, 4, f"Page {self.page_no()}", align="R")


def text_at(pdf, x, y, txt, size=9, style="", color=INK, align="L", w=0):
    pdf.set_xy(x, y)
    pdf.set_font("Helvetica", style, size)
    pdf.set_text_color(*color)
    pdf.cell(w, size * 0.42, txt, align=align)


def wrapped(pdf, x, y, w, txt, size=7.5, style="", color=INK2, lh=3.4, align="L"):
    pdf.set_xy(x, y)
    pdf.set_font("Helvetica", style, size)
    pdf.set_text_color(*color)
    pdf.multi_cell(w, lh, txt, align=align)


def box(pdf, x, y, w, h, title, subtitle="", tone=ACCENT, fill=CARD,
        title_size=9.5, sub_size=7, tab=True):
    pdf.set_fill_color(*fill)
    pdf.set_draw_color(*LINE)
    pdf.set_line_width(0.25)
    pdf.rect(x, y, w, h, style="DF")
    if tab:
        pdf.set_fill_color(*tone)
        pdf.rect(x, y, 2.0, h, style="F")
    pad = 4.5 if tab else 3
    if subtitle:
        text_at(pdf, x + pad, y + 2.4, title, size=title_size, style="B", color=INK)
        wrapped(pdf, x + pad, y + 2.4 + title_size * 0.5, w - pad - 2, subtitle,
                size=sub_size, color=INK2, lh=sub_size * 0.5)
    else:
        # vertically-centred single title
        text_at(pdf, x + pad, y + h / 2 - title_size * 0.22, title,
                size=title_size, style="B", color=INK)


def pill(pdf, x, y, w, h, label, tone, fill):
    pdf.set_fill_color(*fill)
    pdf.set_draw_color(*tone)
    pdf.set_line_width(0.3)
    pdf.rect(x, y, w, h, style="DF", round_corners=True, corner_radius=1.6)
    text_at(pdf, x, y + h / 2 - 1.6, label, size=7.6, style="B", color=tone,
            align="C", w=w)


def arrow(pdf, x1, y1, x2, y2, color=INK3, width=0.4, head=1.6):
    pdf.set_draw_color(*color)
    pdf.set_line_width(width)
    pdf.line(x1, y1, x2, y2)
    import math
    ang = math.atan2(y2 - y1, x2 - x1)
    for da in (math.radians(150), math.radians(-150)):
        pdf.line(x2, y2, x2 + head * math.cos(ang + da), y2 + head * math.sin(ang + da))
    pdf.set_line_width(0.25)


def dbl_arrow(pdf, x1, y1, x2, y2, color=ACCENT, width=0.5):
    arrow(pdf, x1, y1, x2, y2, color, width)
    arrow(pdf, x2, y2, x1, y1, color, width)


def page_title(pdf, num, title, sub):
    text_at(pdf, MARGIN, MARGIN, f"{num}", size=22, style="B", color=ACCENT)
    text_at(pdf, MARGIN + 11, MARGIN + 1.5, title, size=17, style="B", color=INK)
    wrapped(pdf, MARGIN + 11, MARGIN + 8.5, CONTENT_W - 11, sub, size=8.5,
            color=INK2, lh=3.8)
    pdf.set_draw_color(*LINE)
    pdf.set_line_width(0.3)
    pdf.line(MARGIN, MARGIN + 15.5, PAGE_W - MARGIN, MARGIN + 15.5)


# ===========================================================================
pdf = Doc(orientation="L", format="A4")
pdf.set_auto_page_break(auto=False, margin=12)
pdf.set_margins(MARGIN, MARGIN, MARGIN)

# ---- COVER ----------------------------------------------------------------
pdf.add_page()
pdf.set_fill_color(*INK)
pdf.rect(0, 0, PAGE_W, PAGE_H, style="F")
# logo mark
pdf.set_fill_color(*ACCENT)
pdf.rect(MARGIN + 4, 66, 22, 22, style="F")
text_at(pdf, MARGIN + 4, 73.5, "L", size=26, style="B", color=WHITE, align="C", w=22)
text_at(pdf, MARGIN + 32, 66, "BUCKY", size=46, style="B", color=WHITE)
text_at(pdf, MARGIN + 33, 92, "System Architecture - Diagrams", size=16, style="B",
        color=(220, 220, 226))
wrapped(pdf, MARGIN + 33, 102, 200,
        "Human-in-the-loop ticket automation for ServiceNow. The agent is granted "
        "work at the step level, never the ticket level.",
        size=10.5, color=(170, 170, 178), lh=5)
# tag strip
pill(pdf, MARGIN + 33, 122, 60, 8, "Simulation only", OK, (232, 246, 237))
pill(pdf, MARGIN + 97, 122, 66, 8, "Server-side policy", ACCENT, (234, 243, 255))
pill(pdf, MARGIN + 167, 122, 70, 8, "Read-only live feed", WARN, (253, 243, 227))
text_at(pdf, MARGIN + 4, PAGE_H - 22, "Three views: layered components, ticket data flow, and the client/server trust boundary.",
        size=9, color=(150, 150, 158))

# ===========================================================================
# PAGE 1 - BIG PICTURE / WHOLE PLAN (one-page overview)
# ===========================================================================
pdf.add_page()
page_title(pdf, "1", "The big picture - whole plan on one page",
           "Tickets in, controlled work out. Three systems in a line; the agent is granted work one step at a time and hands the "
           "rest to a human. Everything below the wall is the execution plan - simulated today, live via connectors as the target state.")

# --- top: three systems end-to-end ----------------------------------------
sy0 = 42
sysw = (CONTENT_W - 2 * 10) / 3
sysh = 26
sys_boxes = [
    ("SYSTEM 1", "Discovery", "Pulls ServiceNow history, finds repetitive task types worth automating, mines how they were resolved.", INK2),
    ("SYSTEM 2", "Procedures", "The rulebook: each resolution is a structured procedure with a trigger + ordered steps. Matches a ticket.", ACCENT),
    ("SYSTEM 3", "Task overlay", "Per-step risk, human approval, resumable runs, audit trail, live intake, sign-in. (This document.)", OK),
]
sxs = []
for i, (tag, name, desc, tone) in enumerate(sys_boxes):
    x = MARGIN + i * (sysw + 10)
    box(pdf, x, sy0, sysw, sysh, f"{tag}  -  {name}", desc, tone=tone,
        fill=(245, 245, 247), title_size=9.5, sub_size=7)
    sxs.append((x, x + sysw))
for i in range(2):
    arrow(pdf, sxs[i][1] + 1, sy0 + sysh / 2, sxs[i + 1][0] - 1, sy0 + sysh / 2, INK3, 0.5)

# --- middle: the ticket lifecycle as a single ribbon -----------------------
ry = sy0 + sysh + 12
text_at(pdf, MARGIN, ry - 6, "A ticket's life (control bounces between agent and human, one step at a time):", size=8.5, style="B", color=INK)
stages = [
    ("Intake", "live feed / data set", INK3),
    ("Match", "score vs rule set", INK3),
    ("Plan", "risk + chunk", INK3),
    ("Approve", "human authorises", OK),
    ("Execute", "agent, simulated", ACCENT),
    ("Hand off", "human + evidence", OK),
]
n = len(stages)
sw = (CONTENT_W - (n - 1) * 6) / n
sh = 16
for i, (t, d, tone) in enumerate(stages):
    x = MARGIN + i * (sw + 6)
    box(pdf, x, ry, sw, sh, t, d, tone=tone, fill=CARD, title_size=8.6, sub_size=6.4)
    if i < n - 1:
        arrow(pdf, x + sw, ry + sh / 2, x + sw + 6, ry + sh / 2, INK3, 0.4)
# resume loop
text_at(pdf, MARGIN + CONTENT_W - 74, ry + sh + 2, "<- resume: run continues where it stopped", size=7, style="B", color=WARN)

# --- execution wall --------------------------------------------------------
wy = ry + sh + 12
pdf.set_draw_color(*DANGER)
pdf.set_line_width(0.6)
pdf.set_dash_pattern(dash=1.6, gap=1.3)
pdf.line(MARGIN, wy, PAGE_W - MARGIN, wy)
pdf.set_dash_pattern()
text_at(pdf, MARGIN, wy - 4.5, "EXECUTION WALL", size=8, style="B", color=DANGER)
text_at(pdf, MARGIN + 30, wy - 4.2, "- today everything below is SIMULATED (CONNECTORS_ENABLED = False); enabling live is a separate, security-reviewed project", size=7, color=INK2)

# --- below wall: who executes + connectors + targets -----------------------
by = wy + 5
# left: actor split
half = (CONTENT_W - 8) / 2
box(pdf, MARGIN, by, half, 15, "AGENT does R0-R2 (approved)",
    "acts as scoped service accounts, actor=agent, triggered_by=person; stops at first human/higher-risk step",
    tone=ACCENT, fill=(234, 243, 255), title_size=8.6, sub_size=6.6)
box(pdf, MARGIN + half + 8, by, half, 15, "HUMAN does R3 / RX / judgement",
    "destructive, high-impact or unclassified steps done by hand with recorded evidence",
    tone=OK, fill=(232, 246, 237), title_size=8.6, sub_size=6.6)

# gateway
gy = by + 18
box(pdf, MARGIN, gy, CONTENT_W, 12,
    "simulator.py -> execute_step()  [ connector gateway: allowlisted (connector, operation), resolve secret at use, check postconditions, rollback ]",
    "", tone=DANGER, fill=(253, 236, 235), title_size=8, tab=True)

# connectors row
ccy = gy + 15
conns = [
    ("ServiceNow", OK, True), ("WinRM", INK3, False), ("SSH", INK3, False),
    ("Active Dir.", INK3, False), ("vCenter", INK3, False), ("HTTP API", INK3, False),
]
cn = len(conns)
cw = (CONTENT_W - (cn - 1) * 5) / cn
for i, (name, tone, impl) in enumerate(conns):
    x = MARGIN + i * (cw + 5)
    fill = (232, 246, 237) if impl else (247, 247, 249)
    box(pdf, x, ccy, cw, 13, name, "live" if impl else "planned", tone=tone, fill=fill,
        title_size=8, sub_size=6.2)
    arrow(pdf, x + cw / 2, gy + 12, x + cw / 2, ccy, INK3, 0.3)

# targets
ty = ccy + 16
box(pdf, MARGIN, ty, CONTENT_W, 11,
    "TARGET SYSTEMS: ServiceNow, Windows/Linux hosts, Active Directory, vCenter, HTTP endpoints",
    "reached only via a scoped service account + allowlisted operation; secrets are pointers, never stored/returned/logged",
    tone=INK2, fill=(245, 245, 247), title_size=8.4, sub_size=6.6)

# ===========================================================================
# PAGE 2 - LAYERED COMPONENT DIAGRAM
# ===========================================================================
pdf.add_page()
page_title(pdf, "2", "Layered component architecture",
           "Each layer depends only on those beneath it. All policy lives server-side; the browser is a thin client. "
           "Arrows show call direction (top calls down).")

top = 40
col_x = MARGIN
col_w = CONTENT_W
# Client band
box(pdf, col_x, top, col_w, 13, "CLIENT  -  ui/ (React)",
    "Thin browser client - light/dark themes - holds no business logic - renders API responses and calls back",
    tone=ACCENT, fill=(234, 243, 255), title_size=10, sub_size=7.5)
# HTTP boundary
mid = top + 13
arrow(pdf, PAGE_W / 2 - 20, mid + 1, PAGE_W / 2 - 20, mid + 6.5, ACCENT, 0.5)
arrow(pdf, PAGE_W / 2 + 20, mid + 6.5, PAGE_W / 2 + 20, mid + 1, ACCENT, 0.5)
text_at(pdf, PAGE_W / 2 - 16, mid + 2.2, "HTTPS / JSON  (the only contract)", size=7.5,
        style="B", color=ACCENT)

sy = mid + 8
# transport + auth row
box(pdf, col_x, sy, col_w * 0.66 - 2, 13, "api.py  -  transport",
    "FastAPI HTTP contract every client speaks; authentication + role gates", tone=ACCENT)
box(pdf, col_x + col_w * 0.66 + 2, sy, col_w * 0.34 - 2, 13, "auth.py",
    "Sessions, identity, roles; Entra SSO + dev fallback", tone=OK)
sy += 15
# service authority - full width, emphasised
box(pdf, col_x, sy, col_w, 13, "service.py  -  APPLICATION AUTHORITY",
    "Every use case and ALL rule enforcement. No client can bypass it - the server re-checks every rule.",
    tone=DANGER, fill=(253, 243, 227), title_size=10, sub_size=7.5)
sy += 15

# domain-services grid (2 rows x 4)
labels = [
    ("planner.py", "ticket + procedure -> approvable, chunked plan", INK2),
    ("risk.py", "classifies each step; default-deny", WARN),
    ("registry.py", "effective rule set: built-in + published", INK2),
    ("knowledge.py", "parses uploaded handbooks into drafts", INK2),
    ("credentials.py", "pointers to secrets, never secrets", DANGER),
    ("live.py", "read-only ServiceNow polling", OK),
    ("simulator.py", "step execution - SIMULATION ONLY", DANGER),
    ("store.py", "persistence behind a repository seam (SQLite)", INK2),
]
gap = 3
gw = (col_w - 3 * gap) / 4
gh = 15
for i, (name, role, tone) in enumerate(labels):
    r, c = divmod(i, 4)
    gx = col_x + c * (gw + gap)
    gy = sy + r * (gh + gap)
    box(pdf, gx, gy, gw, gh, name, role, tone=tone, title_size=8.6, sub_size=6.6)
sy += 2 * (gh + gap) + 1

# foundation
box(pdf, col_x, sy, col_w, 12, "domain.py  -  PURE MODEL",
    "Runs, steps, risk, state machines. No framework, no DB, no transport - the single source of truth for state.",
    tone=INK3, fill=(245, 245, 247), title_size=9.5, sub_size=7)

# legend
ly = sy + 15
text_at(pdf, col_x, ly, "Tone legend:", size=7.5, style="B", color=INK)
legend = [("policy authority", DANGER), ("network / SSO", OK), ("transport / UI", ACCENT), ("supporting", INK2), ("pure model", INK3)]
lx = col_x + 26
for name, tone in legend:
    pdf.set_fill_color(*tone)
    pdf.rect(lx, ly - 0.3, 3.5, 3.5, style="F")
    text_at(pdf, lx + 5, ly, name, size=7.5, color=INK2)
    lx += 5 + pdf.get_string_width(name) + 8

# ===========================================================================
# PAGE 2 - TICKET DATA FLOW
# ===========================================================================
pdf.add_page()
page_title(pdf, "3", "How a ticket flows through",
           "From a raw ServiceNow ticket to controlled work. Control bounces between agent and human - neither owns the whole ticket.")

# swimlane labels
lane_top = 44
lane_h = 34
agent_y = lane_top
human_y = lane_top + lane_h + 8
sys_y = human_y + lane_h + 8

for (yy, name, tone, fill) in [
    (agent_y, "AGENT", ACCENT, (234, 243, 255)),
    (human_y, "HUMAN", OK, (232, 246, 237)),
    (sys_y, "SYSTEM", INK3, (245, 245, 247)),
]:
    pdf.set_fill_color(*fill)
    pdf.rect(MARGIN, yy, 20, lane_h, style="F")
    text_at(pdf, MARGIN, yy + lane_h / 2 - 2, name, size=8, style="B", color=tone,
            align="C", w=20)

# six pipeline stages, placed by owner lane
lane_area_x = MARGIN + 24
lane_area_w = PAGE_W - MARGIN - lane_area_x
step_w = 40
step_h = 26
n = 6
gap = (lane_area_w - n * step_w) / (n - 1)

stages = [
    ("1  Intake", "Ticket arrives from live feed (read-only) or data set. Reference fields flattened.", "sys"),
    ("2  Match", "Scored against the rule set; picks a procedure or leaves it with a human.", "sys"),
    ("3  Plan", "Expanded into steps; each classified by risk + owner, grouped into chunks.", "sys"),
    ("4  Approve", "An approver reviews a chunk and authorises it. Bound by digest to what was shown.", "human"),
    ("5  Execute", "Agent runs the approved chunk (simulated), stops at first human/higher-risk step.", "agent"),
    ("6  Hand off", "Control returns to a person for steps the agent may not do; completed with evidence.", "human"),
]
lane_y = {"agent": agent_y, "human": human_y, "sys": sys_y}
lane_tone = {"agent": ACCENT, "human": OK, "sys": INK3}
lane_fill = {"agent": (234, 243, 255), "human": (232, 246, 237), "sys": CARD}

centers = []
for i, (title, desc, lane) in enumerate(stages):
    x = lane_area_x + i * (step_w + gap)
    y = lane_y[lane] + (lane_h - step_h) / 2
    box(pdf, x, y, step_w, step_h, title, desc, tone=lane_tone[lane],
        fill=lane_fill[lane], title_size=9, sub_size=6.6)
    centers.append((x, y, x + step_w, y + step_h, x + step_w / 2, y + step_h / 2))

# connect consecutive stages
for i in range(n - 1):
    _, _, rx, _, _, rcy = centers[i]
    lx, _, _, _, _, lcy = centers[i + 1]
    # route with an elbow if lanes differ
    midx = (rx + lx) / 2
    pdf.set_draw_color(*INK3)
    pdf.set_line_width(0.4)
    pdf.line(rx, rcy, midx, rcy)
    pdf.line(midx, rcy, midx, lcy)
    arrow(pdf, midx, lcy, lx, lcy, INK3, 0.4)

# feedback loop 6 -> 5 (resume)
_, _, _, _, c5x, _ = centers[4]
_, y6, _, _, c6x, _ = centers[5]
loop_y = human_y - 6
pdf.set_draw_color(*WARN)
pdf.set_line_width(0.4)
pdf.line(centers[5][4], centers[5][1], centers[5][4], loop_y)
pdf.line(centers[5][4], loop_y, centers[4][4], loop_y)
arrow(pdf, centers[4][4], loop_y, centers[4][4], centers[4][3], WARN, 0.4)
text_at(pdf, (centers[4][4] + centers[5][4]) / 2 - 18, loop_y - 4,
        "resume - run continues where it stopped", size=7, style="B", color=WARN)

# note band
ny = sys_y + lane_h + 8
box(pdf, MARGIN, ny, CONTENT_W, 16,
    "State is persistent and resumable",
    "A run is stored, not held in memory: it can pause for a human indefinitely and resume exactly where it stopped. "
    "Run state is derived from its steps (domain.py), which are the single source of truth.",
    tone=INK2, fill=(245, 245, 247), title_size=9.5, sub_size=7.5)

# ===========================================================================
# PAGE 3 - TRUST BOUNDARY / DEPLOYMENT + RISK
# ===========================================================================
pdf.add_page()
page_title(pdf, "4", "Trust boundary and the execution wall",
           "What a client may reach, and where live execution is hard-disabled. Everything sensitive stays server-side.")

# left: client zone
zt = 44
zh = 96
lz_x = MARGIN
lz_w = 78
pdf.set_draw_color(*ACCENT)
pdf.set_line_width(0.5)
pdf.set_fill_color(234, 243, 255)
pdf.rect(lz_x, zt, lz_w, zh, style="DF", round_corners=True, corner_radius=2)
text_at(pdf, lz_x + 3, zt + 3, "CLIENT ZONE (untrusted)", size=8.5, style="B", color=ACCENT)
box(pdf, lz_x + 5, zt + 12, lz_w - 10, 16, "React UI", "renders responses; holds no logic", tone=ACCENT, title_size=9, sub_size=6.8)
box(pdf, lz_x + 5, zt + 32, lz_w - 10, 16, "Fallback client", "dependency-free; served at /legacy", tone=ACCENT, title_size=9, sub_size=6.8)
box(pdf, lz_x + 5, zt + 52, lz_w - 10, 16, "Future desktop / CLI / bot", "point at API base; ship built assets", tone=INK2, title_size=8.6, sub_size=6.8)
wrapped(pdf, lz_x + 5, zt + 72, lz_w - 10,
        "A tampered client gains nothing: the server re-checks every rule.",
        size=7.2, style="I", color=INK2, lh=3.3)

# boundary line
bx = lz_x + lz_w + 16
pdf.set_draw_color(*DANGER)
pdf.set_line_width(0.6)
pdf.set_dash_pattern(dash=1.5, gap=1.2)
pdf.line(bx, zt - 2, bx, zt + zh + 2)
pdf.set_dash_pattern()
text_at(pdf, bx - 9, zt - 7.5, "TRUST BOUNDARY", size=8, style="B", color=DANGER)
dbl_arrow(pdf, lz_x + lz_w + 1, zt + 20, bx - 1, zt + 20, ACCENT, 0.5)
text_at(pdf, lz_x + lz_w, zt + 15.5, "HTTPS/JSON only", size=6.8, style="B", color=ACCENT)

# right: server zone
sz_x = bx + 12
sz_w = PAGE_W - MARGIN - sz_x
pdf.set_draw_color(*OK)
pdf.set_line_width(0.5)
pdf.set_fill_color(248, 251, 249)
pdf.rect(sz_x, zt, sz_w, zh, style="DF", round_corners=True, corner_radius=2)
text_at(pdf, sz_x + 3, zt + 3, "SERVER ZONE (authority)", size=8.5, style="B", color=OK)

# api gate
box(pdf, sz_x + 5, zt + 12, sz_w - 10, 11, "api.py + auth.py  -  gate",
    "authenticates the actor, enforces roles, then hands to service.py", tone=ACCENT, title_size=8.6, sub_size=6.6)
arrow(pdf, sz_x + sz_w / 2, zt + 23, sz_x + sz_w / 2, zt + 27, INK3, 0.4)
box(pdf, sz_x + 5, zt + 27, sz_w - 10, 12, "service.py  -  all rule enforcement",
    "no execution without a valid, digest-bound approval; policy is here, not in the client", tone=DANGER, fill=(253, 243, 227), title_size=8.6, sub_size=6.6)

# three server sub-stores
sub_w = (sz_w - 10 - 2 * 4) / 3
sby = zt + 43
box(pdf, sz_x + 5, sby, sub_w, 16, "store.py", "runs, steps, audit (append-only)", tone=INK2, title_size=8.2, sub_size=6.4)
box(pdf, sz_x + 5 + sub_w + 4, sby, sub_w, 16, "credentials.py", "scoped pointers to secrets", tone=DANGER, title_size=8.2, sub_size=6.4)
box(pdf, sz_x + 5 + 2 * (sub_w + 4), sby, sub_w, 16, "live.py", "read-only GET to ServiceNow", tone=OK, title_size=8.2, sub_size=6.4)

# execution wall
ewy = sby + 21
pdf.set_fill_color(253, 236, 235)
pdf.set_draw_color(*DANGER)
pdf.set_line_width(0.5)
pdf.rect(sz_x + 5, ewy, sz_w - 10, 21, style="DF")
pdf.set_fill_color(*DANGER)
pdf.rect(sz_x + 5, ewy, 2.2, 21, style="F")
text_at(pdf, sz_x + 10, ewy + 2.5, "simulator.py  -  EXECUTION WALL (hard-disabled)", size=8.6, style="B", color=DANGER)
wrapped(pdf, sz_x + 10, ewy + 8, sz_w - 18,
        "No network, filesystem or credential access. The System 2 live-execute path raises unconditionally. "
        "Real connectors are a separate, deliberate project - not a config flag.",
        size=6.8, color=INK2, lh=3.1)

# external systems below, dashed link (read-only)
exy = zt + zh + 8
box(pdf, sz_x + sz_w - 62, exy, 62, 12, "ServiceNow", "external - read-only source", tone=OK, fill=(232, 246, 237), title_size=8.4, sub_size=6.4)
pdf.set_draw_color(*OK)
pdf.set_dash_pattern(dash=1.5, gap=1.2)
pdf.set_line_width(0.4)
pdf.line(sz_x + sz_w - 31, zt + zh, sz_x + sz_w - 31, exy)
pdf.set_dash_pattern()
text_at(pdf, sz_x + sz_w - 30, (zt + zh + exy) / 2 - 1.5, "GET only", size=6.6, style="B", color=OK)

# risk ladder (bottom-left)
ry = zt + zh + 8
text_at(pdf, MARGIN, ry, "Per-step risk model (default-deny):", size=8.5, style="B", color=INK)
ladder = [
    ("R0", "read-only", OK), ("R1", "reversible", ACCENT),
    ("R2", "real change", WARN), ("R3", "destructive", DANGER),
    ("RX", "unclassified", INK3),
]
rlx = MARGIN
rly = ry + 5
pw = 33
for code, meaning, tone in ladder:
    pill(pdf, rlx, rly, pw, 9, f"{code}  {meaning}", tone, WHITE)
    rlx += pw + 3
text_at(pdf, MARGIN, rly + 12, "R0-R2 agent-runnable with approval  |  R3, RX human-only, refused by policy and again by the simulator.",
        size=7.2, color=INK2)

# ===========================================================================
# PAGE 4 - EXECUTION LAYER & CONNECTORS (who does the job)
# ===========================================================================
pdf.add_page()
page_title(pdf, "5", "Execution layer and connectors - who does the job",
           "The full plan for step execution. Today simulator.py returns simulated outcomes; the boxes below the wall are the "
           "target state. An executor picks the actor per step, then acts through a scoped service account via one connector.")

# --- decision: who executes this step? (top band) -------------------------
dt = 42
box(pdf, MARGIN, dt, CONTENT_W, 12, "service.py  -  decides the actor for each step (never the client, never the ticket)",
    "R0/R1 with chunk approval and R2 with its own approval -> AGENT   |   R3, RX, or anything unclassified -> HUMAN. One step at a time.",
    tone=DANGER, fill=(253, 243, 227), title_size=9.5, sub_size=7.4)

# two actor lanes
ay = dt + 16
lane_w = (CONTENT_W - 10) / 2
# AGENT
box(pdf, MARGIN, ay, lane_w, 30, "AGENT executes (R0-R2, approved)",
    "", tone=ACCENT, fill=(234, 243, 255), title_size=9.5)
wrapped(pdf, MARGIN + 4.5, ay + 8, lane_w - 8,
        "Acts as actor=agent with triggered_by=the person and the approver on record. "
        "Never borrows a human's identity - it uses the narrow, scoped service accounts "
        "in the credentials centre. Stops at the first human/higher-risk step.",
        size=7, color=INK2, lh=3.2)
# HUMAN
hx = MARGIN + lane_w + 10
box(pdf, hx, ay, lane_w, 30, "HUMAN executes (R3, RX, judgement)",
    "", tone=OK, fill=(232, 246, 237), title_size=9.5)
wrapped(pdf, hx + 4.5, ay + 8, lane_w - 8,
        "An operator performs destructive, high-impact, or unclassified steps by hand and "
        "records evidence (manual completion requires it). The run then resumes with the agent "
        "for any remaining safe steps. Neither party owns the whole ticket.",
        size=7, color=INK2, lh=3.2)

# both feed the executor seam
seam_y = ay + 34
arrow(pdf, MARGIN + lane_w / 2, ay + 30, MARGIN + lane_w / 2, seam_y, ACCENT, 0.5)
arrow(pdf, hx + lane_w / 2, ay + 30, hx + lane_w / 2, seam_y, OK, 0.5)

# executor / connector gateway
box(pdf, MARGIN, seam_y, CONTENT_W, 15,
    "simulator.py -> execute_step()   [ CONNECTOR GATEWAY - the execution seam ]",
    "Gated on an allowlisted (connector, operation) pair. Resolves the secret via credentials.resolve() at the moment "
    "of use, applies the action, checks postconditions, and can roll back. TODAY: returns [SIM] outcomes; CONNECTORS_ENABLED = False.",
    tone=DANGER, fill=(253, 236, 235), title_size=9, sub_size=7)

# execution wall marker
wy = seam_y + 17.5
pdf.set_draw_color(*DANGER)
pdf.set_line_width(0.6)
pdf.set_dash_pattern(dash=1.6, gap=1.3)
pdf.line(MARGIN, wy, PAGE_W - MARGIN, wy)
pdf.set_dash_pattern()
text_at(pdf, MARGIN, wy - 4, "EXECUTION WALL - hard-disabled today; enabling it is a separate, security-reviewed project", size=7.4, style="B", color=DANGER)
text_at(pdf, PAGE_W - MARGIN - 34, wy + 1.5, "TARGET STATE below", size=7.4, style="BI", color=INK3)

# connector cards (6) with implemented/planned status
cy = wy + 6
connectors = [
    ("ServiceNow", "work notes, state changes", "IMPLEMENTED (read-only)", OK, True),
    ("Windows  WinRM", "restart svc, remove file, reboot", "planned", INK3, False),
    ("Linux  SSH", "service start, file removal", "planned", INK3, False),
    ("Active Directory", "unlock, reset, group membership", "planned", INK3, False),
    ("VMware vCenter", "power operations", "planned", INK3, False),
    ("Generic HTTP API", "endpoint-defined", "planned", INK3, False),
]
cn = 3
cgap = 5
cw = (CONTENT_W - (cn - 1) * cgap) / cn
ch = 20
for i, (name, writes, status, tone, impl) in enumerate(connectors):
    r, c = divmod(i, cn)
    cx = MARGIN + c * (cw + cgap)
    ccy = cy + r * (ch + 4)
    fill = (232, 246, 237) if impl else (247, 247, 249)
    box(pdf, cx, ccy, cw, ch, name, "", tone=tone, fill=fill, title_size=9)
    wrapped(pdf, cx + 4.5, ccy + 7.5, cw - 8, f"writes: {writes}", size=6.6, color=INK2, lh=3)
    pill(pdf, cx + cw - 40, ccy + ch - 6.5, 36, 5, status, tone, WHITE)
    # arrow from gateway down to each connector (first row only, visual)
    if r == 0:
        arrow(pdf, cx + cw / 2, seam_y + 15, cx + cw / 2, ccy, INK3, 0.35)

# external targets strip
ey = cy + 2 * (ch + 4) + 1
box(pdf, MARGIN, ey, CONTENT_W, 11,
    "TARGET SYSTEMS  (ServiceNow  -  Windows/Linux hosts  -  Active Directory  -  vCenter  -  HTTP endpoints)",
    "Reached only through a scoped service account + allowlisted operation. No secret is ever stored in the app DB, returned by the API, or logged.",
    tone=INK2, fill=(245, 245, 247), title_size=8.6, sub_size=6.8)

# ===========================================================================
# PAGE 6 - BUSINESS VALUE + HOW TO TEST IT
# ===========================================================================
pdf.add_page()
page_title(pdf, "6", "Why BUCKY is valuable - and how to prove it",
           "The value is governed, audited automation of the repetitive majority of tickets. Numbers below are from this project's own "
           "discovery data (automation_candidates.csv); the agent/human split is enforced by risk.py.")

# --- left column: the volume case -----------------------------------------
colw = (CONTENT_W - 8) / 2
vy = 42
text_at(pdf, MARGIN, vy, "Top repetitive tasks in the real data (volume)", size=9.5, style="B", color=INK)
vy += 6
top_tasks = [
    ("URL / Website access request", "1,313"),
    ("Confluence Cloud access", "1,138"),
    ("Jira Cloud access", "886"),
    ("Shared folder access", "806"),
    ("Firewall policy standard change", "533"),
    ("Shared mailbox access", "446"),
    ("Add / remove from distribution list", "441"),
    ("Unlock GitLab account", "328"),
    ("Create account in Active Directory", "130"),
]
maxv = 1313
bar_x = MARGIN + 62
bar_maxw = colw - 62 - 14
for name, count in top_tasks:
    text_at(pdf, MARGIN, vy + 0.4, name, size=6.8, color=INK2)
    w = bar_maxw * (int(count.replace(",", "")) / maxv)
    pdf.set_fill_color(*ACCENT)
    pdf.rect(bar_x, vy, max(w, 0.6), 3.2, style="F")
    text_at(pdf, bar_x + max(w, 0.6) + 1.5, vy + 0.4, count, size=6.6, style="B", color=INK)
    vy += 5.4
wrapped(pdf, MARGIN, vy + 1, colw,
        "These are access / provisioning requests: high volume, additive, reversible - the safest and highest-payoff class to automate. "
        "Each is the same request solved the same way, thousands of times.",
        size=7, style="I", color=INK3, lh=3.2)

# --- right column: the value logic + agent/human split ---------------------
rx = MARGIN + colw + 8
ry = 42
text_at(pdf, rx, ry, "Where the value comes from", size=9.5, style="B", color=INK)
ry += 6
for t in [
    "Recognise a repeatable ticket and propose the known procedure - no rediscovery.",
    "Agent takes the safe, boring steps (checks, notes, a service restart).",
    "Human keeps judgement + destructive steps (reboot, delete, power-off).",
    "Every action approved + audited: who triggered, who approved, exactly what ran.",
    "Consistency + speed + a clean audit trail that passes a security review.",
]:
    dot_x = rx
    pdf.set_fill_color(*ACCENT)
    pdf.ellipse(dot_x, ry + 0.6, 1.6, 1.6, style="F")
    wrapped(pdf, dot_x + 4, ry, colw - 4, t, size=7.2, color=INK2, lh=3.2)
    ry = pdf.get_y() + 1.2

ry += 2
text_at(pdf, rx, ry, "Agent does vs human does (enforced by risk.py)", size=9, style="B", color=INK)
ry += 5.5
box(pdf, rx, ry, colw, 12, "AGENT (R0-R2, approved)",
    "look / connect / check, post work notes, restart a named service", tone=ACCENT,
    fill=(234, 243, 255), title_size=8.4, sub_size=6.6)
ry += 14
box(pdf, rx, ry, colw, 12, "HUMAN (R3 / RX)",
    "reboot, delete data, power-off a VM, anything unclassified or needing judgement", tone=OK,
    fill=(232, 246, 237), title_size=8.4, sub_size=6.6)

# --- bottom band: how to test it TODAY -------------------------------------
ty = 150
pdf.set_draw_color(*LINE)
pdf.set_line_width(0.3)
pdf.line(MARGIN, ty - 3, PAGE_W - MARGIN, ty - 3)
text_at(pdf, MARGIN, ty, "How to test it today (execution is simulated - safe to run anywhere)", size=9.5, style="B", color=INK)
steps_test = [
    ("1  Run the demo", "python scripts/demo_agent_run.py - drives propose -> approve -> execute on a sample ticket and prints the plan, outcomes, and audit trail."),
    ("2  Or use the app", "python -m uvicorn overlay.api:app --port 8800, open the browser, pick a ticket, Match & add to queue, approve a chunk, press run."),
    ("3  Watch the split", "The 'Restart a Windows service' ticket runs end-to-end as the agent; disk cleanup / reboot / delete stop and hand off to a human."),
    ("4  Go live later", "Build one connector behind execute_step() (e.g. AD unlock), point a credential at a scoped service account - the same approval + audit apply."),
]
n = len(steps_test)
tw = (CONTENT_W - (n - 1) * 5) / n
for i, (h, d) in enumerate(steps_test):
    x = MARGIN + i * (tw + 5)
    tone = OK if i < 3 else DANGER
    fill = (232, 246, 237) if i < 3 else (253, 236, 235)
    box(pdf, x, ty + 5, tw, 30, h, d, tone=tone, fill=fill, title_size=8.4, sub_size=6.6)

from pathlib import Path as _Path  # noqa: E402  (imperative build script; import follows document assembly)
_root = _Path(__file__).resolve().parent.parent
(_root / "output").mkdir(exist_ok=True)
out = str(_root / "output" / "BUCKY_Architecture_Diagram.pdf")
pdf.output(out)
print("wrote", out)

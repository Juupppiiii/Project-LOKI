"""Auto-draft LOKI handbook procedures from mined resolution patterns, using
Claude (Opus 5 on Bedrock) grounded in the real example resolutions.

Each draft is written as a handbook markdown file under overlay/examples/auto/.
These are DRAFTS: a human still uploads, reviews, and publishes them, and the
risk model still classifies every step (destructive actions stay human-only).
The model drafts; it approves and executes nothing.

Run (requires LLM enabled + AWS creds):
    set LOKI_LLM_ENABLED=1
    python scripts/autodraft_procedures.py --top 8
    python scripts/autodraft_procedures.py --top 8 --min-count 100
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from overlay import knowledge_llm as kllm  # noqa: E402

PATTERNS = ROOT / "knowledge" / "tickets" / "resolution_patterns.json"
OUT_DIR = ROOT / "knowledge" / "handbooks"

SYSTEM = (
    "You draft IT operations runbooks ('procedures') for a human-in-the-loop "
    "automation tool called LOKI. You are given a RECURRING resolution pattern "
    "with real example close/work notes. Produce ONE procedure in the exact "
    "markdown format specified. Rules you must follow:\n"
    "- Ground the steps in what the real resolutions actually did; do not invent "
    "capabilities.\n"
    "- Use ONLY these step actions: check, connect, run_command, restart_service, "
    "reboot, delete_profile, notify, manual.\n"
    "- Mark any DESTRUCTIVE or IRREVERSIBLE step (reboot, delete, terminate, data "
    "loss) as a human step by using the `reboot`/`delete_profile`/`manual` action "
    "or adding `| risk: R3`. Read-only checks are `check | ... | risk: R0`.\n"
    "- Prefer check steps before any change. Keep it 3-7 steps.\n"
    "- Output ONLY the markdown, no preamble."
)

TEMPLATE_HINT = (
    "FORMAT (follow exactly):\n"
    "# <procedure title>\n\n"
    "keywords: <comma-separated trigger phrases>\n"
    "group: <assignment group>\n"
    "params: <comma-separated parameter names, or omit>\n"
    "notes: Auto-drafted from <N> similar resolutions (e.g. <ticket numbers>). "
    "Advisory draft; human review required.\n\n"
    "## Steps\n\n"
    "1. check | <description> | risk: R0\n"
    "2. <action> | <description>\n"
    "...\n"
)


def _slug(text: str) -> str:
    return re.sub(r"[^\w\-]", "_", text.lower())[:50].strip("_") or "procedure"


def draft_one(pattern: dict, backend) -> str:
    examples = "\n".join(
        f"- {r[:300]}" for r in pattern.get("example_resolutions", [])[:5]
    )
    user = (
        f"RECURRING PATTERN ({pattern['count']} tickets)\n"
        f"Assignment group: {pattern['assignment_group']}\n"
        f"Category: {pattern['category']}\n"
        f"Close code: {pattern['close_code']}\n"
        f"Example ticket numbers: {', '.join(pattern['example_numbers'][:5])}\n\n"
        f"REAL EXAMPLE RESOLUTIONS:\n{examples}\n\n"
        f"{TEMPLATE_HINT}"
    )
    return backend.complete(SYSTEM, user, max_tokens=900)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--top", type=int, default=8, help="how many patterns to draft")
    ap.add_argument("--min-count", type=int, default=50, help="skip patterns below this frequency")
    args = ap.parse_args()

    if not PATTERNS.exists():
        print(f"patterns not found: {PATTERNS}; run build_knowledge_json.py first", file=sys.stderr)
        return 2

    if not kllm.llm_enabled():
        print(
            "LOKI_LLM_ENABLED is not set. Auto-drafting needs a live model to "
            "synthesise procedures. Set LOKI_LLM_ENABLED=1 and provide AWS creds.",
            file=sys.stderr,
        )
        return 2

    backend = kllm.make_backend()
    if isinstance(backend, kllm.EchoBackend):
        print("No live model backend available; aborting.", file=sys.stderr)
        return 2

    patterns = json.loads(PATTERNS.read_text(encoding="utf-8"))
    patterns = [p for p in patterns if p["count"] >= args.min_count][: args.top]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Drafting {len(patterns)} procedures with {backend.name}...\n")

    written = []
    for i, pat in enumerate(patterns, 1):
        label = f"{pat['assignment_group']} / {pat['category']} / {pat['close_code']}"
        print(f"[{i}/{len(patterns)}] {pat['count']}x  {label}")
        try:
            md = draft_one(pat, backend)
        except kllm.LLMUnavailable as exc:
            print(f"    skipped (model error: {exc})")
            continue
        name = _slug(f"{pat['assignment_group']}_{pat['category']}")
        path = OUT_DIR / f"auto_{i:02d}_{name}.md"
        path.write_text(md.strip() + "\n", encoding="utf-8")
        written.append(path.name)
        print(f"    -> {path.name}")

    print(f"\nDrafted {len(written)} procedures into {OUT_DIR}")
    print("These are DRAFTS. Review each, then upload via the Knowledge tab to "
          "turn them into reviewable drafts, and publish the good ones.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Analysis stage for the Phase 1 Discovery framework.

Reads the extracted CSVs and produces:

    output/dataset_summary.csv              record counts per table
    output/top_tasks.csv                    most frequent short descriptions
    output/top_assignment_groups.csv        busiest assignment groups
    output/top_automation_opportunities.csv scored automation candidates
    automation_candidates.csv               dashboard-facing view (keyword, frequency, automation_score)

All paths come from config so this stays in step with the rest of the pipeline.
"""

import re
from collections import Counter
from pathlib import Path

import pandas as pd

import config

DATA_DIR = Path(config.OUTPUT_DIR)
OUTPUT_DIR = Path(config.ANALYSIS_DIR)

# Journal-entry prefix ServiceNow adds to work_notes/comments, e.g.
# "2026-09-15 15:38:01 - Stephen Mullan (Work notes)".
_JOURNAL_PREFIX = re.compile(
    r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\s*-\s*[^\(]*\([^\)]*\)"
)
_HTML_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")

# Very common English words plus ticket boilerplate to ignore when mining
# resolution phrases, so the signal is the action, not the filler.
_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "to", "of", "in", "on", "for",
    "with", "is", "are", "was", "were", "be", "been", "being", "this", "that",
    "it", "as", "at", "by", "from", "has", "have", "had", "not", "no", "yes",
    "i", "we", "you", "he", "she", "they", "his", "her", "their", "our",
    "please", "thanks", "thank", "hi", "hello", "regards", "kind", "dear",
    "will", "can", "could", "would", "should", "have", "been", "done",
    "user", "ticket", "incident", "issue", "problem", "request", "team",
    "am", "pm", "utc", "gmt", "re", "fw", "test", "undefined", "null", "none",
}


def _clean_text(value):
    """Strip HTML, journal timestamps/author prefixes and collapse whitespace."""

    if not isinstance(value, str):
        return ""

    text = _JOURNAL_PREFIX.sub(" ", value)
    text = _HTML_TAG.sub(" ", text)
    text = text.replace("&nbsp;", " ").replace("&#64;", "@")
    text = _WS.sub(" ", text).strip()
    return text


def score(freq):
    """Map a task frequency to a coarse automation-priority score."""

    if freq >= 100:
        return 100
    if freq >= 50:
        return 90
    if freq >= 20:
        return 80
    if freq >= 10:
        return 70
    return 50


def _load_csvs():
    """Load every CSV in the data directory keyed by file stem."""

    files = {}

    for csv_file in sorted(DATA_DIR.glob("*.csv")):

        try:
            df = pd.read_csv(csv_file, low_memory=False)
            files[csv_file.stem] = df
            print(f"Loaded {csv_file.name}: {len(df):,} rows")
        except Exception as exc:
            print(f"Failed {csv_file.name}: {exc}")

    return files


def _combine_column(files, column):
    """Concatenate a single column across all frames that contain it."""

    frames = [
        df[[column]] for df in files.values() if column in df.columns
    ]

    if not frames:
        return pd.DataFrame(columns=[column])

    return pd.concat(frames, ignore_index=True).dropna()


def _mine_resolution_text(files):
    """Mine incident close_notes for recurring resolution language.

    Scoped to the incidents table only (the manual, human-resolved tickets).
    The automated change/task tables' work_notes are machine job logs and
    would drown the human signal, so they are intentionally excluded.

    Produces:
      - resolution_summaries.csv : most frequent cleaned close_notes (the
        "how it was fixed" summary), scored as automation candidates.
      - resolution_keywords.csv  : most frequent action words across the
        resolution summaries, a rough signal of repeatable manual work.
    """

    incidents = files.get("incidents")
    if incidents is None or "close_notes" not in incidents.columns:
        print("\nNo incident close_notes found; skipping resolution mining.")
        return

    # close_notes is the concise human resolution summary. Cap length so a
    # rare pathological entry cannot dominate, and drop trivially short notes.
    raw = incidents["close_notes"].dropna().tolist()
    cleaned = []
    for t in raw:
        c = _clean_text(t)
        if 4 < len(c) <= 500:
            cleaned.append(c)

    if not cleaned:
        print("\nIncident close_notes empty after cleaning; skipping.")
        return

    # --- recurring resolution summaries ---------------------------------
    summary_counts = (
        pd.Series(cleaned).value_counts().reset_index()
    )
    summary_counts.columns = ["resolution_summary", "frequency"]
    summary_counts["automation_score"] = summary_counts["frequency"].apply(score)
    summary_counts.head(500).to_csv(
        OUTPUT_DIR / "resolution_summaries.csv", index=False
    )

    # --- resolution action keywords -------------------------------------
    word_counter = Counter()
    for text in cleaned:
        words = re.findall(r"[a-zA-Z][a-zA-Z\-]{2,}", text.lower())
        for w in words:
            if w not in _STOPWORDS:
                word_counter[w] += 1

    keywords = pd.DataFrame(
        word_counter.most_common(200), columns=["keyword", "frequency"]
    )
    keywords["automation_score"] = keywords["frequency"].apply(score)
    keywords.to_csv(OUTPUT_DIR / "resolution_keywords.csv", index=False)

    print("\nTop Resolution Summaries (how tickets were fixed)")
    print(summary_counts.head(15).to_string(index=False))
    print("\nTop Resolution Keywords")
    print(keywords.head(20).to_string(index=False))


def run_analysis():

    OUTPUT_DIR.mkdir(exist_ok=True)

    files = _load_csvs()

    if not files:
        print("No CSVs found in "
              f"'{DATA_DIR}'. Run extraction first.")
        return

    # --- dataset summary -------------------------------------------------
    summary_df = pd.DataFrame(
        [{"table": name, "records": len(df)} for name, df in files.items()]
    )
    summary_df.to_csv(OUTPUT_DIR / "dataset_summary.csv", index=False)
    print("\nDataset Summary")
    print(summary_df)

    # --- top tasks + automation opportunities ---------------------------
    tasks = _combine_column(files, "short_description")

    if not tasks.empty:

        top_tasks = (
            tasks["short_description"].value_counts().reset_index()
        )
        top_tasks.columns = ["task", "frequency"]
        top_tasks.to_csv(OUTPUT_DIR / "top_tasks.csv", index=False)

        opportunities = top_tasks.copy()
        opportunities["automation_score"] = opportunities["frequency"].apply(score)
        opportunities.to_csv(
            OUTPUT_DIR / "top_automation_opportunities.csv", index=False
        )

        # Dashboard-facing view. dashboard.py expects a "keyword" column.
        candidates = opportunities.rename(columns={"task": "keyword"})
        candidates.to_csv("automation_candidates.csv", index=False)

        print("\nTop Automation Opportunities")
        print(opportunities.head(25))
    else:
        print("\nNo 'short_description' column found; skipping task analysis.")

    # --- assignment groups ----------------------------------------------
    groups = _combine_column(files, "assignment_group")

    if not groups.empty:
        group_counts = (
            groups["assignment_group"].value_counts().reset_index()
        )
        group_counts.columns = ["assignment_group", "count"]
        group_counts.to_csv(
            OUTPUT_DIR / "top_assignment_groups.csv", index=False
        )
        print("\nTop Assignment Groups")
        print(group_counts.head(20))

    # --- resolution text mining -----------------------------------------
    _mine_resolution_text(files)

    print("\nAnalysis complete")
    print("Generated:")
    print(f"  {OUTPUT_DIR / 'dataset_summary.csv'}")
    print(f"  {OUTPUT_DIR / 'top_tasks.csv'}")
    print(f"  {OUTPUT_DIR / 'top_assignment_groups.csv'}")
    print(f"  {OUTPUT_DIR / 'top_automation_opportunities.csv'}")
    print(f"  {OUTPUT_DIR / 'resolution_summaries.csv'}")
    print(f"  {OUTPUT_DIR / 'resolution_keywords.csv'}")
    print("  automation_candidates.csv")


if __name__ == "__main__":
    run_analysis()

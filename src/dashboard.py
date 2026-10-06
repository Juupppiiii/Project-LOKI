"""Streamlit dashboard for the Phase 1 Discovery framework.

Reads the analyzer output (automation_candidates.csv) and, when available,
the dataset summary. Run the pipeline first:

    python src/main.py            # full run
    python src/main.py --only analyze

Then launch:

    streamlit run src/dashboard.py
"""

from pathlib import Path

import pandas as pd
import streamlit as st

import config

st.set_page_config(page_title="Phase 1 Discovery Dashboard", layout="wide")

st.title("Phase 1 Discovery Dashboard")

candidates_file = Path("automation_candidates.csv")
summary_file = Path(config.ANALYSIS_DIR) / "dataset_summary.csv"

if not candidates_file.exists():
    st.warning(
        "automation_candidates.csv not found. "
        "Run `python src/main.py --only analyze` first."
    )
    st.stop()

df = pd.read_csv(candidates_file)

# --- headline metrics ---------------------------------------------------
col1, col2, col3 = st.columns(3)
col1.metric("Candidate keywords", f"{len(df):,}")
if "frequency" in df.columns:
    col2.metric("Total occurrences", f"{int(df['frequency'].sum()):,}")
if "automation_score" in df.columns:
    high = int((df["automation_score"] >= 90).sum())
    col3.metric("High-priority (score >= 90)", f"{high:,}")

# --- dataset summary ----------------------------------------------------
if summary_file.exists():
    st.subheader("Dataset Summary")
    st.dataframe(pd.read_csv(summary_file), use_container_width=True)

# --- automation candidates ----------------------------------------------
st.subheader("Automation Candidates")

top_n = st.slider("Show top N", min_value=5, max_value=100, value=25, step=5)
top_df = df.head(top_n)

st.dataframe(top_df, use_container_width=True)

# Chart: the analyzer emits a "keyword" column; guard in case the schema
# changes so the dashboard degrades gracefully instead of crashing.
label_col = "keyword" if "keyword" in df.columns else df.columns[0]

if "frequency" in top_df.columns:
    st.bar_chart(top_df.set_index(label_col)["frequency"])

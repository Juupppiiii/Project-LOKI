"""Mine closed incidents for recurring, substantive fixes worth codifying.

Reads data/closed_incidents.csv (produced by pull_closed_incidents.py), strips
boilerplate / empty resolutions, clusters what remains by meaning, and ranks the
clusters as candidate BUCKY procedures with example tickets.

Read-only and offline: it touches no network and reuses the project's own
semantic vectorizer for clustering (same embeddings the advisory matcher uses).

    python scripts/mine_resolutions.py
    python scripts/mine_resolutions.py --in data/closed_incidents.csv --min-cluster 3
    python scripts/mine_resolutions.py --top 25 --out output/resolution_candidates.csv

Output: a ranked CSV (one row per candidate cluster) plus an on-screen summary.
Honest by design: it reports how little of the raw data is actually substantive,
so nobody mistakes 5,000 tickets for 5,000 runbooks.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
for p in (str(ROOT), str(SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from overlay import semantic_engine  # noqa: E402  (reuse its vectorizer)

# Phrases that mark a non-resolution: SLED auto-acks, "this is ok", tests, etc.
BOILERPLATE = re.compile(
    r"resolved from sled|this is ok|this is oks|already raised|midnight reset|"
    r"mail send|^ok$|^oks$|^test$|^raised$|no action required|^resolved$",
    re.I,
)
_WS = re.compile(r"\s+")
_RESOLVED_BY = re.compile(r"resolved by:\s*\S+", re.I)
_WORD = re.compile(r"[a-zA-Z]{4,}")


def _norm(s: object) -> str:
    return _WS.sub(" ", str(s or "")).strip()


def _resolution_core(close_notes: str, work_notes: str) -> str:
    """The part of the resolution that actually describes a fix.

    Strips the 'Resolved By: <person>' stamp and known boilerplate, then joins
    whatever substantive text remains from close_notes and work_notes.
    """

    cn = _RESOLVED_BY.sub("", close_notes)
    cn = BOILERPLATE.sub("", cn).strip(" .-")
    wn = BOILERPLATE.sub("", work_notes).strip(" .-")
    return _norm(f"{cn} {wn}")


def _is_substantive(core: str) -> bool:
    return len(core) >= 40 and bool(_WORD.search(core))


def load_substantive(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path).fillna("")
    df["close_notes"] = df["close_notes"].map(_norm)
    df["work_notes"] = df["work_notes"].map(_norm)
    df["resolution_core"] = [
        _resolution_core(c, w) for c, w in zip(df["close_notes"], df["work_notes"])
    ]
    df["substantive"] = df["resolution_core"].map(_is_substantive)
    return df


def cluster(df: pd.DataFrame, threshold: float, min_cluster: int) -> list[dict]:
    """Greedy similarity clustering over the problem text + resolution core.

    Uses the project's numpy TF-IDF + char-ngram vectorizer so this stays
    offline and matches the advisory matcher's notion of 'similar'. Greedy
    single-pass clustering is enough to surface recurring patterns; it is not
    meant to be a definitive taxonomy.
    """

    texts = [
        _norm(f"{r.short_description} {r.resolution_core}")
        for r in df.itertuples()
    ]
    if not texts:
        return []

    vec = semantic_engine._NumpyVectorizer().fit(texts)
    mat = vec.embed(texts)  # L2-normalised rows -> dot product == cosine

    n = len(texts)
    assigned = [-1] * n
    centroids: list[np.ndarray] = []
    members: list[list[int]] = []

    for i in range(n):
        v = mat[i]
        best_c, best_sim = -1, 0.0
        for c, cen in enumerate(centroids):
            sim = float(cen @ v)
            if sim > best_sim:
                best_c, best_sim = c, sim
        if best_c >= 0 and best_sim >= threshold:
            members[best_c].append(i)
            assigned[i] = best_c
            # Running mean centroid, renormalised.
            cen = centroids[best_c] + v
            centroids[best_c] = cen / (np.linalg.norm(cen) or 1.0)
        else:
            centroids.append(v)
            members.append([i])
            assigned[i] = len(centroids) - 1

    clusters = []
    for idxs in members:
        if len(idxs) < min_cluster:
            continue
        sub = df.iloc[idxs]
        groups = sub["assignment_group"].replace("", "(none)").value_counts()
        clusters.append(
            {
                "size": len(idxs),
                "top_group": groups.index[0] if len(groups) else "(none)",
                "group_share": round(float(groups.iloc[0]) / len(idxs), 2) if len(groups) else 0.0,
                "label": _label(sub),
                "example_numbers": "; ".join(sub["number"].head(3).tolist()),
                "example_short": sub["short_description"].iloc[0][:120],
                "example_resolution": sub["resolution_core"].iloc[0][:200],
            }
        )
    clusters.sort(key=lambda c: c["size"], reverse=True)
    return clusters


def _label(sub: pd.DataFrame) -> str:
    """A crude human label: the most common salient words in short_description."""

    stop = set(
        "the a an of to on in for and or is are has have with from this that "
        "has been not no inc".split()
    )
    counts: dict[str, int] = {}
    for sd in sub["short_description"]:
        for w in re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", str(sd).lower()):
            if w not in stop:
                counts[w] = counts.get(w, 0) + 1
    top = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:4]
    return " / ".join(w for w, _ in top) or "(unlabelled)"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--in", dest="inp", default=str(ROOT / "data" / "closed_incidents.csv"))
    ap.add_argument("--out", default=str(ROOT / "output" / "resolution_candidates.csv"))
    ap.add_argument("--threshold", type=float, default=0.45, help="cluster similarity")
    ap.add_argument("--min-cluster", type=int, default=3, help="min tickets per candidate")
    ap.add_argument("--top", type=int, default=25, help="candidates to show")
    ap.add_argument(
        "--exclude-group",
        action="append",
        default=[],
        help="assignment group substring to exclude (repeatable), e.g. 'Liquidnet NOC'",
    )
    args = ap.parse_args()

    path = Path(args.inp)
    if not path.exists():
        print(f"input not found: {path}; run pull_closed_incidents.py first", file=sys.stderr)
        return 2

    df = load_substantive(path)
    if args.exclude_group:
        before = len(df)
        for frag in args.exclude_group:
            df = df[~df["assignment_group"].str.contains(frag, case=False, na=False)]
        df = df.reset_index(drop=True)
        print(f"excluded {before - len(df):,} rows from groups matching {args.exclude_group}")
    total = len(df)
    sub = df[df["substantive"]].reset_index(drop=True)
    print("=" * 64)
    print(f"MINING {total:,} closed incidents")
    print("=" * 64)
    print(f"  substantive resolutions : {len(sub):,}  ({len(sub)/total:.0%})")
    print(f"  boilerplate / empty     : {total - len(sub):,}  ({1 - len(sub)/total:.0%})")

    clusters = cluster(sub, args.threshold, args.min_cluster)
    print(f"  recurring clusters (>= {args.min_cluster}) : {len(clusters)}")
    covered = sum(c["size"] for c in clusters)
    print(f"  tickets in those clusters : {covered:,}  ({covered/total:.0%} of all)")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(clusters).to_csv(out, index=False)
    print(f"\nSaved {len(clusters)} candidate procedures -> {out}")

    print("\n" + "-" * 64)
    print(f"Top {min(args.top, len(clusters))} candidate procedures (by frequency):")
    print("-" * 64)
    for i, c in enumerate(clusters[: args.top], 1):
        print(f"\n{i:>2}. [{c['size']} tickets] {c['label']}")
        print(f"     owner group : {c['top_group']} ({c['group_share']:.0%} of cluster)")
        print(f"     example     : {c['example_numbers']}")
        print(f"     problem     : {c['example_short']}")
        print(f"     resolution  : {c['example_resolution']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

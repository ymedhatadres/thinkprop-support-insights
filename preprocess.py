"""One-shot preprocessor: ThinkProp Freshdesk xlsx export -> data/tickets.parquet.

Mirrors the AccessRP pipeline. ThinkProp's export carries the same Freshdesk
schema; the categorical engine for this report is the agent-labelled
`custom_fields.cf_inquiry_type328109` field (see lib/themes.py).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

# All ticket exports we want to merge into one parquet, in load order.
# When a ticket id appears in more than one source, the row with the most
# recent `updated_at` is kept.
SOURCE_FILES = [
    Path("/Users/yahya/Downloads/May_FreshDesk_Export/ThinkProp/tickets_full_export_MERGED.xlsx"),
]
OUT_PARQUET = Path(__file__).parent / "data" / "tickets.parquet"
SHEET = "tickets"

STATUS_MAP = {
    2: "Open",
    3: "Pending",
    4: "Resolved",
    5: "Closed",
    6: "Waiting on Customer",
    7: "Waiting on Third Party",
}
PRIORITY_MAP = {1: "Low", 2: "Medium", 3: "High", 4: "Urgent"}
SOURCE_MAP = {
    1: "Email",
    2: "Portal",
    3: "Phone",
    7: "Chat",
    9: "Feedback Widget",
    10: "Outbound Email",
}

TIMESTAMP_COLS = [
    "created_at",
    "updated_at",
    "due_by",
    "fr_due_by",
    "nr_due_by",
    "stats.agent_responded_at",
    "stats.requester_responded_at",
    "stats.first_responded_at",
    "stats.status_updated_at",
    "stats.reopened_at",
    "stats.resolved_at",
    "stats.closed_at",
    "stats.pending_since",
    "custom_fields.cf_date",
]

# Heavy / unused columns dropped before writing parquet.
DROP_HEAVY = [
    "conversations",
    "description",
    "attachments",
    "attachments_local",
    "structured_description",
    "source_additional_info",
    "associated_tickets_list",
]


def _read_one(path: Path) -> pd.DataFrame:
    print(f"Reading {path} ...")
    one = pd.read_excel(path, sheet_name=SHEET, engine="openpyxl")
    print(f"  loaded {len(one):,} rows x {one.shape[1]} cols")
    return one


def main() -> int:
    missing = [p for p in SOURCE_FILES if not p.exists()]
    if missing:
        for p in missing:
            print(f"ERROR: source not found: {p}", file=sys.stderr)
        return 1

    frames = [_read_one(p) for p in SOURCE_FILES]
    df = pd.concat(frames, ignore_index=True, sort=False)
    print(f"\nConcatenated: {len(df):,} rows across {len(frames)} sources")

    # Parse updated_at early so we can dedupe by latest.
    if "updated_at" in df.columns:
        df["updated_at"] = pd.to_datetime(df["updated_at"], utc=True, errors="coerce")

    if "id" in df.columns:
        before = len(df)
        df = (
            df.sort_values("updated_at", na_position="first")
              .drop_duplicates(subset=["id"], keep="last")
              .reset_index(drop=True)
        )
        print(f"Deduped by id (latest updated_at wins): {before:,} -> {len(df):,}")

    for col in TIMESTAMP_COLS:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")

    df["status_label"] = df["status"].map(STATUS_MAP).fillna(df["status"].astype("string"))
    df["priority_label"] = df["priority"].map(PRIORITY_MAP).fillna(df["priority"].astype("string"))
    df["source_label"] = df["source"].map(SOURCE_MAP).fillna(df["source"].astype("string"))

    for col in df.columns:
        if col.startswith("custom_fields.") and df[col].dtype == "object":
            df[col] = df[col].astype("string").str.strip().replace({"": pd.NA, "None": pd.NA})

    if "description_text" in df.columns:
        df["description_text"] = (
            df["description_text"].astype("string").str.slice(0, 2000)
        )

    if "created_at" in df.columns:
        created_naive = df["created_at"].dt.tz_convert("UTC").dt.tz_localize(None)
        df["created_date"] = created_naive.dt.date
        df["created_week"] = created_naive.dt.to_period("W-MON").dt.start_time
        df["created_hour"] = created_naive.dt.hour
        df["created_weekday"] = created_naive.dt.day_name()
        df["created_month"] = created_naive.dt.to_period("M").astype(str)
        df["quarter"] = created_naive.dt.to_period("Q").astype(str).str.replace(
            r"(\d{4})Q(\d)", r"\1 Q\2", regex=True
        )

    if {"stats.resolved_at", "created_at"}.issubset(df.columns):
        delta = df["stats.resolved_at"] - df["created_at"]
        df["resolution_hours"] = delta.dt.total_seconds() / 3600.0

    if "sentiment_score" in df.columns:
        s = pd.to_numeric(df["sentiment_score"], errors="coerce")
        df["sentiment_score"] = s
        lo, hi, mean = s.min(skipna=True), s.max(skipna=True), s.mean()
        print(f"  sentiment_score range: [{lo}, {hi}]  mean={mean:.1f}")
        # ThinkProp's Freshdesk sentiment runs ~6..97, mean ~58 — noticeably
        # higher than AccessRP. Recalibrated thresholds so the buckets keep
        # their meaning relative to this distribution.
        df["sentiment_bucket"] = pd.cut(
            s,
            bins=[-float("inf"), 40, 65, float("inf")],
            labels=["Negative", "Neutral", "Positive"],
        )

    df = df.drop(columns=[c for c in DROP_HEAVY if c in df.columns])

    OUT_PARQUET.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT_PARQUET, compression="snappy", index=False)
    print(f"Wrote {OUT_PARQUET} ({OUT_PARQUET.stat().st_size / 1e6:.1f} MB)")

    print("\n=== summary ===")
    print(f"rows: {len(df):,}")
    if "created_at" in df.columns:
        print(f"date range: {df['created_at'].min()} .. {df['created_at'].max()}")
    for col in [
        "custom_fields.cf_inquiry_type328109",
        "custom_fields.cf_service",
        "custom_fields.cf_products",
        "status_label",
        "source_label",
        "sentiment_bucket",
    ]:
        if col not in df.columns:
            continue
        vc = df[col].value_counts(dropna=False).head(10)
        print(f"\n{col} (top 10):")
        for k, v in vc.items():
            print(f"  {str(k)[:45]:45s} {v:>6,}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

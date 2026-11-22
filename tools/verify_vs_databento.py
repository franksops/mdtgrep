#!/usr/bin/env python3
"""
Compare an MDTGrep tick export against a Databento trades dataset.

MDTGrep writes the underlying intraday trade records of a Sierra Chart chart to
CSV.  This script quantifies how close that export is to the equivalent
Databento ``trades`` data so the result can be trusted for backtesting.

What is compared
----------------
*   trade count (Sierra trades vs Databento trades)
*   trade timestamps (aligned within a tolerance, default 1 ms)
*   trade price (exact and within a tolerance)
*   trade size / volume
*   trade side (B / S / N)

Usage
-----
    python tools/verify_vs_databento.py \
        --sierra "C:/SierraChart/SCAssistData/ESM6_bars_ticks.csv" \
        --databento es_trades.parquet \
        --price-tol 0.0001

Databento input can be any file that pandas can read (.csv, .csv.gz, .csv.zst,
.parquet).  A file produced by the Databento CLI works directly::

    databento.trades.save_to_csv("es_trades.csv")

The Databento ``trades`` schema columns used here are ``ts_event`` (UTC
nanoseconds), ``price``, ``size``, ``side`` and ``action``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import pandas as pd
except ImportError:  # pragma: no cover
    sys.exit("pandas is required: pip install pandas pyarrow")


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_sierra(path: Path) -> pd.DataFrame:
    """Load the MDTGrep tick export."""
    df = pd.read_csv(path)

    if "unix_ts_ms" not in df.columns:
        sys.exit(f"{path}: missing the unix_ts_ms column, this does not look like an MDTGrep tick export")

    df["ts_ms"] = df["unix_ts_ms"].astype("int64")

    if "price" not in df.columns:
        sys.exit(f"{path}: missing the price column")

    if "side" not in df.columns:
        # Older MDTGrep exports have no side column, derive it from the
        # ask/bid volume split so the comparison still works.
        df["side"] = "N"
        if "ask_vol" in df.columns and "bid_vol" in df.columns:
            df["side"] = df.apply(
                lambda r: "B" if r["ask_vol"] > 0 and r["bid_vol"] == 0
                else "S" if r["bid_vol"] > 0 and r["ask_vol"] == 0
                else "N",
                axis=1,
            )

    if "volume" not in df.columns:
        df["volume"] = 0

    return df.sort_values("ts_ms").reset_index(drop=True)


def load_databento(path: Path, tolerance_ms: int) -> pd.DataFrame:
    """Load the Databento trades data."""
    df = pd.read_csv(path)

    if "ts_event" not in df.columns:
        sys.exit(f"{path}: missing the ts_event column, this does not look like Databento trades data")

    # UTC nanoseconds -> milliseconds
    df["ts_ms"] = (df["ts_event"].astype("int64") // 1_000_000).astype("int64")

    if "action" in df.columns:
        # 'T' = trade.  'C' = cancel, 'X' = trade correction, 'B' = backfill,
        # 'F' = fill.  Only regular trades are compared.
        trades = df[df["action"].astype(str).str.upper() == "T"].copy()
        dropped = len(df) - len(trades)
        if dropped:
            print(f"  Databento: kept {len(trades)} trades, ignored {dropped} non-trade messages")
    else:
        trades = df.copy()

    if "size" not in trades.columns:
        trades["size"] = 0

    if "side" not in trades.columns:
        trades["side"] = "N"

    return trades.sort_values("ts_ms").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def compare(sierra: pd.DataFrame, db: pd.DataFrame, tolerance_ms: int, price_tol: float) -> None:
    print()
    print("=" * 74)
    print("MDTGrep vs Databento trade comparison")
    print("=" * 74)

    # ---------------------------------------------------------------- counts
    n_sc = len(sierra)
    n_db = len(db)

    multi = 0
    if "num_trades" in sierra.columns:
        multi = int((sierra["num_trades"] > 1).sum())

    print()
    print("Trades")
    print(f"  Sierra (MDTGrep)      : {n_sc:>12,}")
    print(f"  Databento             : {n_db:>12,}")
    if n_db:
        print(f"  Difference            : {n_sc - n_db:>12,}  ({100.0 * (n_sc - n_db) / n_db:+.3f}%)")
    if multi:
        print(f"  Sierra records with >1 trade (aggregated): {multi:,}")

    # ------------------------------------------------------------ time range
    print()
    print("Time range (UTC)")
    if n_sc:
        print(f"  Sierra   : {pd.to_datetime(sierra['ts_ms'].min(), unit='ms')} -> {pd.to_datetime(sierra['ts_ms'].max(), unit='ms')}")
    if n_db:
        print(f"  Databento: {pd.to_datetime(db['ts_ms'].min(), unit='ms')} -> {pd.to_datetime(db['ts_ms'].max(), unit='ms')}")

    if not n_sc or not n_db:
        print()
        print("Nothing to compare.")
        return

    # --------------------------------------------------------------- matching
    left = sierra.sort_values("ts_ms")
    right = db.sort_values("ts_ms")

    merged = pd.merge_asof(
        left,
        right[["ts_ms", "price", "size", "side"]].rename(
            columns={"price": "db_price", "size": "db_size", "side": "db_side"}
        ),
        on="ts_ms",
        direction="nearest",
        tolerance=tolerance_ms,  # integer tolerance, in the units of ts_ms
    )

    matched = merged[merged["db_price"].notna()].copy()
    unmatched_sc = merged[merged["db_price"].isna()].copy()

    # Reverse matching, so that Databento trades without a Sierra counterpart
    # are found with the same tolerance.
    reverse = pd.merge_asof(
        right,
        left[["ts_ms", "price"]].rename(columns={"price": "sc_price"}),
        on="ts_ms",
        direction="nearest",
        tolerance=tolerance_ms,
    )
    db_unmatched = reverse[reverse["sc_price"].isna()].copy()

    print()
    print(f"Matching (tolerance {tolerance_ms} ms)")
    print(f"  Matched               : {len(matched):>12,}")
    print(f"  Sierra unmatched      : {len(unmatched_sc):>12,}")
    print(f"  Databento unmatched   : {len(db_unmatched):>12,}")

    if len(matched) == 0:
        print()
        print("No trades matched. The two datasets do not overlap in time, or the")
        print("timestamps are in different time zones.")
        return

    # ------------------------------------------------------------------ price
    price_diff = (matched["price"].astype(float) - matched["db_price"].astype(float)).abs()
    exact = int((price_diff == 0).sum())
    within = int((price_diff <= price_tol).sum())

    print()
    print("Price")
    print(f"  Exact match           : {exact:>12,}  ({100.0 * exact / len(matched):.3f}%)")
    print(f"  Within {price_tol:<10}: {within:>12,}  ({100.0 * within / len(matched):.3f}%)")
    print(f"  Max absolute difference: {price_diff.max():>11.6f}")
    if price_diff.max() > 0:
        print(f"  Mean absolute difference: {price_diff.mean():>10.6f}")

    # ----------------------------------------------------------------- volume
    sc_vol = matched["volume"].astype(float)
    db_vol = matched["db_size"].astype(float)
    vol_same = int((sc_vol == db_vol).sum())

    print()
    print("Volume / size")
    print(f"  Exact match           : {vol_same:>12,}  ({100.0 * vol_same / len(matched):.3f}%)")
    vol_diff = (sc_vol - db_vol).abs()
    if vol_diff.max() > 0:
        print(f"  Max absolute difference: {vol_diff.max():>11.0f}")
        print(f"  Total Sierra volume   : {sc_vol.sum():>12,.0f}")
        print(f"  Total Databento volume: {db_vol.sum():>12,.0f}")

    # ------------------------------------------------------------------- side
    sc_side = matched["side"].astype(str).str.upper()
    db_side = matched["db_side"].astype(str).str.upper()
    side_same = int((sc_side == db_side).sum())

    print()
    print("Side")
    print(f"  Exact match           : {side_same:>12,}  ({100.0 * side_same / len(matched):.3f}%)")

    print()
    print("  Confusion matrix (Sierra rows / Databento columns)")
    print()
    print("           " + "".join(f"{c:>10}" for c in ["B", "S", "N"]))
    for a in ["B", "S", "N"]:
        row = [int(((sc_side == a) & (db_side == b)).sum()) for b in ["B", "S", "N"]]
        print(f"    {a}   " + "".join(f"{v:>10,}" for v in row))

    # ------------------------------------------------------------- unmatched
    if len(unmatched_sc):
        print()
        print("Examples of Sierra trades with no Databento match")
        print(unmatched_sc[["ts_ms", "price", "volume"]].head(10).to_string(index=False))

    if len(db_unmatched):
        print()
        print("Examples of Databento trades with no Sierra match")
        print(db_unmatched[["ts_ms", "price", "size", "side"]].head(10).to_string(index=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare an MDTGrep tick export against Databento trades.")
    parser.add_argument("--sierra", required=True, type=Path, help="MDTGrep tick CSV")
    parser.add_argument("--databento", required=True, type=Path, help="Databento trades file (csv/parquet)")
    parser.add_argument("--tol-ms", type=int, default=1, help="timestamp matching tolerance in ms (default 1)")
    parser.add_argument("--price-tol", type=float, default=0.00001, help="price tolerance (default 0.00001)")
    args = parser.parse_args()

    for path in (args.sierra, args.databento):
        if not path.exists():
            sys.exit(f"file not found: {path}")

    print("Loading data...")
    sierra = load_sierra(args.sierra)
    db = load_databento(args.databento, args.tol_ms)

    compare(sierra, db, args.tol_ms, args.price_tol)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Validate an MDTGrep export.

Runs the integrity checks that matter for backtesting, on either the bar export
or the tick export:

*   timestamp monotonicity, duplicates, bar spacing
*   the CME session structure (open/close, the index halt, the maintenance break)
*   OHLC integrity and the price tick grid
*   ask_vol + bid_vol == volume
*   num_trades <= volume and lots per trade
*   the open_interest column (a duplicate of num_trades on intraday charts)
*   the time base of the timestamps

Usage
-----
    python tools/validate_export.py C:/SierraChart/SCAssistData/ESM6_bars.csv
    python tools/validate_export.py prev_version_data/*.csv --source-tz America/New_York
    python tools/validate_export.py *_ticks.csv --kind ticks
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    import pandas as pd
except ImportError:  # pragma: no cover
    sys.exit("pandas is required: pip install pandas")

# CME equity index futures (ES) session in Eastern time and the two daily breaks.
ET_OPEN = "18:00"
ET_CLOSE = "17:00"
ET_INDEX_HALT = ("16:15", "16:30")   # the 15 minute index complex halt
ET_MAINTENANCE = ("17:00", "18:00")  # the daily maintenance break


def load(path: Path, kind: str, source_tz: str | None) -> pd.DataFrame:
    df = pd.read_csv(path)

    if "unix_ts_ms" not in df.columns:
        sys.exit(f"{path}: missing the unix_ts_ms column")

    df["ts"] = df["unix_ts_ms"].astype("int64")

    # The digits of the timestamp are the wall clock reading of the chart.
    df["digits"] = pd.to_datetime(df["ts"], unit="ms", utc=True).dt.tz_localize(None)

    if source_tz:
        from zoneinfo import ZoneInfo
        epoch = pd.Timestamp("1970-01-01", tz="UTC")
        localized = df["digits"].dt.tz_localize(ZoneInfo(source_tz))
        df["utc"] = ((localized - epoch) // pd.Timedelta(milliseconds=1)).astype("int64")
        df["utc"] = pd.to_datetime(df["utc"], unit="ms", utc=True)
    else:
        df["utc"] = pd.to_datetime(df["ts"], unit="ms", utc=True)

    df["et"] = df["utc"].dt.tz_convert("America/New_York").dt.tz_localize(None)

    return df


def session_bounds(df: pd.DataFrame) -> tuple[set, set]:
    """Session open/close times of the trading days, in the chart wall clock."""
    w = df["digits"]
    td = w.dt.normalize() + pd.to_timedelta((w.dt.hour >= 17).astype(int), unit="D")
    grouped = df.assign(td=td)
    opens = set(grouped.groupby("td")["digits"].min().dt.strftime("%H:%M"))
    closes = set(grouped.groupby("td")["digits"].max().dt.strftime("%H:%M"))
    return opens, closes


def check_timestamps(df: pd.DataFrame, label: str, source_tz: str | None) -> None:
    ts = df["ts"]
    d = ts.diff().dropna()
    opens, closes = session_bounds(df)

    print(f"\n  --- timestamps ({label}) ---")
    print(f"     first (wall clock)   : {df['digits'].iloc[0]}")
    print(f"     last  (wall clock)   : {df['digits'].iloc[-1]}")
    if source_tz:
        print(f"     first (true UTC)     : {df['utc'].iloc[0]}")
        print(f"     last  (true UTC)     : {df['utc'].iloc[-1]}")
    print(f"     strictly increasing  : {bool((d > 0).all())}")
    print(f"     duplicate timestamps : {int((d == 0).sum())}")
    if len(d):
        mode = d.mode().iloc[0]
        print(f"     bar spacing          : mode {mode / 60000:.2f} min")
        gaps = d[d > mode]
        if len(gaps):
            print(f"     larger gaps          : {len(gaps):,} (top: "
                  f"{ {int(k/60000): int(v) for k, v in gaps.value_counts().head(5).items()} } minutes)")

    print(f"     session open (wall)  : {sorted(opens)[:4]}")
    print(f"     session close (wall) : {sorted(closes)[:4]}")

    # The CME equity index session opens at 18:00 and closes at 17:00 Eastern.
    # In UTC that is 23:00 -> 22:00 in winter and 22:00 -> 21:00 in summer.
    if "18:00" in opens:
        print("     TIME BASE            : the wall clock shows 18:00, so the chart was set to Eastern.")
        print("                           These timestamps are the wall clock stored as if UTC, so they")
        print("                           are 4-5 hours behind true UTC. Set the chart Time Zone to UTC")
        print("                           (Chart >> Chart Settings >> Advanced Settings) and re-export,")
        print("                           or pass --source-tz to convert them.")
    elif opens & {"23:00", "22:00"}:
        print("     TIME BASE            : UTC - the wall clock matches the CME session in UTC. Correct.")
    else:
        print(f"     TIME BASE            : unexpected session open {sorted(opens)[:4]}")


def check_bars(df: pd.DataFrame) -> None:
    print("\n  --- bar data ---")

    ohlc_bad = ((df["high"] < df[["open", "close"]].max(axis=1) - 1e-9)
                | (df["low"] > df[["open", "close"]].min(axis=1) + 1e-9)
                | (df["high"] < df["low"] - 1e-9))
    print(f"     OHLC violations      : {int(ohlc_bad.sum())}")

    grid = ((df["close"] * 4) % 1 == 0).all()
    print(f"     on the 0.25 tick grid: {bool(grid)}")

    if {"volume", "ask_vol", "bid_vol"}.issubset(df.columns):
        split_bad = df["ask_vol"] + df["bid_vol"] != df["volume"]
        print(f"     ask+bid == volume    : {bool((~split_bad).all())}  ({int(split_bad.sum())} exceptions)")
        if split_bad.any():
            worst = df[split_bad].assign(unclassified=df["volume"] - df["ask_vol"] - df["bid_vol"])
            print(f"     max unclassified     : {int(worst['unclassified'].max())} contracts in a bar")

    if {"volume", "num_trades"}.issubset(df.columns):
        print(f"     num_trades <= volume : {bool((df['num_trades'] <= df['volume']).all())}")
        lpt = (df["volume"] / df["num_trades"]).replace([float("inf"), -float("inf")], float("nan")).dropna()
        if len(lpt):
            print(f"     lots per trade       : mean {lpt.mean():.2f}  median {lpt.median():.2f}  p95 {lpt.quantile(.95):.2f}")

    if "open_interest" in df.columns:
        if "num_trades" in df.columns and (df["open_interest"] == df["num_trades"]).all():
            print("     open_interest        : GARBAGE - identical to num_trades (Sierra returns the trade")
            print("                           count for intraday charts). Drop this column.")
        elif (df["open_interest"] == 0).all():
            print("     open_interest        : all zeros (correct for intraday charts)")
        else:
            print("     open_interest        : present, differs from num_trades")


def check_ticks(df: pd.DataFrame) -> None:
    print("\n  --- tick data ---")
    print(f"     rows                 : {len(df):,}")
    if "num_trades" in df.columns:
        agg = int((df["num_trades"] > 1).sum())
        print(f"     aggregated records   : {agg:,} (num_trades > 1 - the data was not stored at 1 Tick)")
    if "side" in df.columns:
        print(f"     side distribution     : {df['side'].value_counts().to_dict()}")
    if {"ask_vol", "bid_vol"}.issubset(df.columns):
        tot_a = float(df["ask_vol"].sum())
        tot_b = float(df["bid_vol"].sum())
        if tot_a + tot_b > 0:
            print(f"     buy/sell volume      : {tot_a:,.0f} / {tot_b:,.0f}  ({100*tot_a/(tot_a+tot_b):.1f}% buy)")
    if {"bid", "ask"}.issubset(df.columns):
        have = int(((df["bid"] > 0) & (df["ask"] > 0)).sum())
        print(f"     records with quotes  : {have:,} ({100*have/len(df):.1f}%)")
    if "price" in df.columns:
        grid = ((df["price"] * 4) % 1 == 0).all()
        print(f"     on the 0.25 tick grid: {bool(grid)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate an MDTGrep export.")
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--kind", choices=["auto", "bars", "ticks"], default="auto")
    parser.add_argument("--source-tz", default=None,
                        help="time zone the chart was set to; used to flag a wrong time base")
    args = parser.parse_args()

    for path in args.files:
        if not path.exists():
            sys.exit(f"file not found: {path}")

        kind = args.kind
        if kind == "auto":
            kind = "ticks" if "price" in pd.read_csv(path, nrows=1).columns else "bars"

        print("=" * 78)
        print(f"{path.name}   ({kind} export)")
        print("=" * 78)

        df = load(path, kind, args.source_tz)
        check_timestamps(df, kind, args.source_tz)
        if kind == "bars":
            check_bars(df)
        else:
            check_ticks(df)

        print()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Repair an MDTGrep bar export produced by the previous version of the study.

The previous version had two problems that this script fixes:

1.  Timestamps were written as the chart's wall clock time encoded as a Unix
    timestamp ("18:00 Eastern" was stored as 18:00 UTC).  The values are
    therefore 4-5 hours behind true UTC and the offset changes with daylight
    saving time.  This script converts them to real UTC instants.

    Use --source-tz to match the time zone the chart was set to.  For CME
    futures charts in Eastern time the default is correct.

2.  The open_interest column duplicated the num_trades column.  Sierra Chart
    only provides open interest for daily and higher timeframe charts and
    returns the trade count in that array for intraday charts, so the column
    carries no information.  It is dropped by default.

The price, volume and trade count columns are passed through unchanged.

Usage
-----
    python tools/fix_prev_version_data.py prev_version_data/*.csv
    python tools/fix_prev_version_data.py --keep-open-interest --source-tz America/Chicago in.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    sys.exit("Python 3.9+ with the zoneinfo module is required")

import pandas as pd


def convert_timestamps(ts_ms: pd.Series, source_tz: str) -> pd.Series:
    """Convert 'wall clock as if UTC' millisecond values into real UTC epochs.

    The value is interpreted as a wall clock reading in ``source_tz`` and
    converted to the corresponding UTC instant.  Daylight saving time is
    applied, so the same file can span the DST transition.
    """
    naive = pd.to_datetime(ts_ms.astype("int64"), unit="ms", utc=True).dt.tz_localize(None)
    localized = naive.dt.tz_localize(ZoneInfo(source_tz), ambiguous="raise", nonexistent="raise")
    epoch = pd.Timestamp("1970-01-01", tz="UTC")
    return ((localized.dt.tz_convert("UTC") - epoch) // pd.Timedelta(milliseconds=1)).astype("int64")


def main() -> None:
    parser = argparse.ArgumentParser(description="Repair an MDTGrep bar export.")
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--source-tz", default="America/New_York",
                        help="time zone the chart was set to (default America/New_York)")
    parser.add_argument("--keep-open-interest", action="store_true",
                        help="keep the open_interest column (it duplicates num_trades on intraday charts)")
    parser.add_argument("--out-dir", type=Path, default=None,
                        help="output directory (default: next to the input file)")
    parser.add_argument("--suffix", default="_utc", help="suffix for the output file name")
    args = parser.parse_args()

    for path in args.files:
        if not path.exists():
            sys.exit(f"file not found: {path}")

        df = pd.read_csv(path)

        if "unix_ts_ms" not in df.columns:
            sys.exit(f"{path}: missing the unix_ts_ms column")

        before = pd.to_datetime(df["unix_ts_ms"].astype("int64").iloc[0], unit="ms", utc=True)
        df["unix_ts_ms"] = convert_timestamps(df["unix_ts_ms"], args.source_tz)
        after = pd.to_datetime(df["unix_ts_ms"].iloc[0], unit="ms", utc=True)

        dropped = ""
        if not args.keep_open_interest and "open_interest" in df.columns:
            if "num_trades" in df.columns and (df["open_interest"] == df["num_trades"]).all():
                df = df.drop(columns=["open_interest"])
                dropped = ", dropped open_interest (duplicate of num_trades)"
            else:
                dropped = ", kept open_interest (it differs from num_trades)"

        for col in ("volume", "ask_vol", "bid_vol", "num_trades"):
            if col in df.columns:
                df[col] = df[col].round().astype("int64")

        out_dir = args.out_dir or path.parent
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{path.stem}{args.suffix}.csv"
        df.to_csv(out, index=False)

        print(f"{path.name} -> {out}")
        print(f"    first bar {before} -> {after} UTC{dropped}")
        print(f"    {len(df):,} rows")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Build the MDTGrep data lake: exported CSV -> validated Parquet.

This is the ingest stage of the pipeline described in docs/SPEC.md.  It is
idempotent: every source file is fingerprinted, and a file that has not
changed since the last run is skipped unless --force is given.

Stages per file
---------------
1.  read the CSV (and its metadata sidecar, when there is one)
2.  normalise the schema - the trading_day column is derived for exports that
    were made before it existed
3.  validate the data quality invariants
4.  derive the session table and the gap table
5.  write partitioned Parquet

Output layout
-------------
    <out>/bars_1m/symbol=<SYMBOL>/year=<YYYY>/part-*.parquet
    <out>/trades/symbol=<SYMBOL>/year=<YYYY>/part-*.parquet
    <reports>/<file>.json        one validation report per source file
    <out>/_state.json            the ingest state, used to skip unchanged files

Usage
-----
    python tools/build_parquet.py
    python tools/build_parquet.py --source market_data --out data/parquet --force
    python tools/build_parquet.py --kind ticks
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    import pandas as pd
    import pyarrow as pa
    import pyarrow.dataset as ds
except ImportError:  # pragma: no cover
    sys.exit("pyarrow and pandas are required: pip install pandas pyarrow")

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cme_calendar  # noqa: E402

EASTERN = ZoneInfo("America/New_York")

# The CME equity index session runs 23 hours: 18:00 Eastern to 17:00 Eastern.
EXPECTED_SESSION_MINUTES = 1380

# A session shorter than this many minutes below the median is reported as a gap.
SHORT_SESSION_TOLERANCE_MINUTES = 60

# The build fails when more than this share of the bars has an unclassified
# volume split.  A handful of bars is normal - Sierra Chart cannot classify
# every trade when the trade is between the bid and the ask.
VOLUME_SPLIT_FAILURE_RATIO = 0.001


# ---------------------------------------------------------------------------
# Trading day
# ---------------------------------------------------------------------------

def derive_trading_day(ts_ms: pd.Series) -> pd.Series:
    """Derive the exchange trading day of a UTC millisecond timestamp.

    The CME session opens at 18:00 and closes at 17:00 Eastern, so anything
    from 18:00 Eastern onwards belongs to the next trading day.  This is the
    same rule the study uses, so legacy exports can be brought up to date.
    """
    utc = pd.to_datetime(ts_ms.astype("int64"), unit="ms", utc=True)
    eastern = utc.dt.tz_convert(EASTERN)
    day = eastern.dt.normalize().dt.tz_localize(None)
    evening = eastern.dt.hour >= 18
    return (day + pd.to_timedelta(evening.astype(int), unit="D")).dt.date


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

BAR_COLUMNS = [
    "ts_ms", "trading_day", "open", "high", "low", "close",
    "volume", "ask_vol", "bid_vol", "num_trades", "open_interest",
]

TICK_COLUMNS = [
    "ts_ms", "trading_day", "price", "volume", "ask_vol", "bid_vol",
    "delta", "num_trades", "side", "bid", "ask",
]

BAR_SCHEMA = pa.schema([
    ("ts_ms", pa.int64()),
    ("trading_day", pa.date32()),
    ("open", pa.float64()),
    ("high", pa.float64()),
    ("low", pa.float64()),
    ("close", pa.float64()),
    ("volume", pa.int64()),
    ("ask_vol", pa.int64()),
    ("bid_vol", pa.int64()),
    ("num_trades", pa.int64()),
    ("open_interest", pa.int64()),
])

TICK_SCHEMA = pa.schema([
    ("ts_ms", pa.int64()),
    ("trading_day", pa.date32()),
    ("price", pa.float64()),
    ("volume", pa.int64()),
    ("ask_vol", pa.int64()),
    ("bid_vol", pa.int64()),
    ("delta", pa.int64()),
    ("num_trades", pa.int64()),
    ("side", pa.string()),
    ("bid", pa.float64()),
    ("ask", pa.float64()),
])


def normalise(df: pd.DataFrame, kind: str) -> pd.DataFrame:
    """Bring a CSV into the canonical schema."""
    df = df.copy()

    df["ts_ms"] = df["unix_ts_ms"].astype("int64")
    df = df.drop(columns=["unix_ts_ms"])

    if "trading_day" not in df.columns:
        df["trading_day"] = derive_trading_day(df["ts_ms"])
    df["trading_day"] = pd.to_datetime(df["trading_day"], format="%Y-%m-%d").dt.date

    if kind == "bars":
        for c in ("open", "high", "low", "close"):
            df[c] = df[c].astype("float64")
        for c in ("volume", "ask_vol", "bid_vol", "num_trades", "open_interest"):
            df[c] = df[c].round().astype("int64")
        df = df[BAR_COLUMNS]
    else:
        df["price"] = df["price"].astype("float64")
        for c in ("bid", "ask"):
            if c in df.columns:
                df[c] = df[c].astype("float64")
        for c in ("volume", "ask_vol", "bid_vol", "delta", "num_trades"):
            df[c] = df[c].round().astype("int64")
        df["side"] = df["side"].astype("string").fillna("N").astype(str)
        df = df[TICK_COLUMNS]

    return df.sort_values("ts_ms").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate(df: pd.DataFrame, kind: str) -> tuple[dict, list[str]]:
    """Check the data quality invariants.  Returns the report and the failures."""
    checks: dict[str, object] = {}
    failures: list[str] = []

    ts = df["ts_ms"]
    d = ts.diff().dropna()

    checks["rows"] = int(len(df))
    checks["strictly_increasing"] = bool((d > 0).all())
    if not checks["strictly_increasing"]:
        failures.append("timestamps are not strictly increasing")

    checks["duplicate_timestamps"] = int((d == 0).sum())
    if checks["duplicate_timestamps"]:
        failures.append(f"{checks['duplicate_timestamps']} duplicate timestamps")

    spacing = int(d.mode().iloc[0]) if len(d) else 0
    checks["bar_spacing_ms"] = spacing

    # Session and trading day structure.
    days = sorted(set(df["trading_day"]))
    checks["trading_days"] = len(days)
    checks["first_trading_day"] = str(days[0]) if days else None
    checks["last_trading_day"] = str(days[-1]) if days else None

    weekend = [str(x) for x in days if x.weekday() >= 5]
    checks["weekend_trading_days"] = weekend
    if weekend:
        failures.append(f"trading days on a weekend: {weekend}")

    # Sessions: one row per trading day.
    session_df = df.groupby("trading_day").agg(
        open_ts=("ts_ms", "min"),
        close_ts=("ts_ms", "max"),
        bars=("ts_ms", "size"),
    )
    session_df["span_minutes"] = (session_df["close_ts"] - session_df["open_ts"]) // 60000 + 1
    median_span = int(session_df["span_minutes"].median()) if len(session_df) else 0
    checks["median_session_minutes"] = median_span

    short = session_df[session_df["span_minutes"] < median_span - SHORT_SESSION_TOLERANCE_MINUTES]

    # The covered range, needed to tell a range edge from a real gap.
    covered = sorted(set(df["trading_day"]))

    # A short session is either a scheduled early close or a real gap in the
    # data.  The CME calendar tells them apart.  Anything the calendar cannot
    # explain is reported as a gap for review rather than silently accepted.
    calendar_cache: dict[int, dict] = {}

    def calendar_for(year: int) -> dict:
        if year not in calendar_cache:
            calendar_cache[year] = cme_calendar.calendar(year)
        return calendar_cache[year]

    short_sessions = []
    # The first and the last session of a file are cut by the chart date
    # range, so a short one there is not a data gap.
    edge_days = {str(covered[0]), str(covered[-1])} if covered else set()

    for d, r in short.iterrows():
        if str(d) in edge_days:
            continue
        calendar_kind = calendar_for(d.year).get(d, None)
        session_kind = "early_close" if calendar_kind == "early_close" else "gap"
        short_sessions.append({
            "trading_day": str(d),
            "span_minutes": int(r["span_minutes"]),
            "missing_minutes": int(median_span - r["span_minutes"]),
            "kind": session_kind,
            "calendar": calendar_kind or "unexplained",
        })

    checks["edge_sessions"] = [
        {"trading_day": str(d), "span_minutes": int(r["span_minutes"])}
        for d, r in short.iterrows() if str(d) in edge_days
    ]
    checks["edge_days"] = sorted(edge_days)

    checks["short_sessions"] = short_sessions
    checks["gaps"] = [s for s in short_sessions if s["kind"] == "gap"]
    checks["early_closes"] = [s for s in short_sessions if s["kind"] == "early_close"]

    # Weekdays inside the covered range with no session at all.
    if len(covered) > 1:
        day = covered[0]
        while day <= covered[-1]:
            if day.weekday() < 5 and day not in set(covered):
                cal = calendar_for(day.year).get(day, "unexplained")
                checks.setdefault("missing_weekdays", []).append({
                    "date": str(day),
                    "calendar": cal,
                })
            day += dt.timedelta(days=1)

    if checks["gaps"]:
        failures.append(f"{len(checks['gaps'])} session(s) with a data gap")

    if kind == "bars":
        ohlc_bad = ((df["high"] < df[["open", "close"]].max(axis=1) - 1e-9)
                    | (df["low"] > df[["open", "close"]].min(axis=1) + 1e-9)
                    | (df["high"] < df["low"] - 1e-9))
        checks["ohlc_violations"] = int(ohlc_bad.sum())
        if checks["ohlc_violations"]:
            failures.append(f"{checks['ohlc_violations']} OHLC violations")

        split_bad = df["ask_vol"] + df["bid_vol"] != df["volume"]
        checks["volume_split_exceptions"] = int(split_bad.sum())
        split_ratio = checks["volume_split_exceptions"] / max(len(df), 1)
        checks["volume_split_exception_ratio"] = round(split_ratio, 8)
        if split_ratio > VOLUME_SPLIT_FAILURE_RATIO:
            failures.append(f"{checks['volume_split_exceptions']} bars where ask_vol + bid_vol != volume")

        checks["num_trades_exceeds_volume"] = int((df["num_trades"] > df["volume"]).sum())
        if checks["num_trades_exceeds_volume"]:
            failures.append(f"{checks['num_trades_exceeds_volume']} bars where num_trades > volume")

        lot = (df["volume"] / df["num_trades"].replace(0, pd.NA)).dropna()
        checks["lots_per_trade_mean"] = round(float(lot.mean()), 4) if len(lot) else None
        checks["lots_per_trade_median"] = round(float(lot.median()), 4) if len(lot) else None
    else:
        checks["aggregated_records"] = int((df["num_trades"] > 1).sum())
        checks["side_distribution"] = df["side"].value_counts().to_dict()
        checks["max_records_per_row"] = int(df["num_trades"].max())

    checks["volume_total"] = int(df["volume"].sum())
    checks["first_ts_ms"] = int(ts.iloc[0])
    checks["last_ts_ms"] = int(ts.iloc[-1])

    return checks, failures


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------

def file_fingerprint(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_meta(path: Path) -> dict:
    """Read the metadata sidecar of a data file, when there is one."""
    candidates = [
        path.with_name(path.stem + "_meta.csv"),
        path.with_suffix(".csv_meta.csv"),
    ]
    for c in candidates:
        if c.exists():
            df = pd.read_csv(c)
            return dict(zip(df["key"], df["value"]))
    return {}


def symbol_of(path: Path, meta: dict) -> str:
    if meta.get("symbol"):
        return str(meta["symbol"])
    stem = path.stem
    for suffix in ("_bars_ticks", "_bars", "_ticks"):
        if stem.endswith(suffix):
            return stem[: -len(suffix)]
    return stem


def build_sessions(df: pd.DataFrame, symbol: str, edge_days: set) -> pd.DataFrame:
    """One row per trading day, with the kind of session."""
    g = df.groupby("trading_day")
    out = pd.DataFrame({
        "symbol": symbol,
        "trading_day": g.size().index,
        "open_ts": g["ts_ms"].min().values,
        "close_ts": g["ts_ms"].max().values,
        "bars": g.size().values,
        "volume": g["volume"].sum().values,
    })
    out["span_minutes"] = (out["close_ts"] - out["open_ts"]) // 60000 + 1

    median_span = int(out["span_minutes"].median())
    kinds = []
    for day in out["trading_day"]:
        if str(day) in edge_days:
            kinds.append("partial")
        elif out.loc[out["trading_day"] == day, "span_minutes"].iloc[0] < median_span - SHORT_SESSION_TOLERANCE_MINUTES:
            cal = cme_calendar.calendar(day.year).get(day, None)
            kinds.append("early_close" if cal == "early_close" else "gap")
        else:
            kinds.append("regular")
    out["kind"] = kinds

    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the MDTGrep Parquet data lake.")
    parser.add_argument("--source", type=Path, default=Path("market_data"))
    parser.add_argument("--out", type=Path, default=Path("data/parquet"))
    parser.add_argument("--reports", type=Path, default=Path("data/reports"))
    parser.add_argument("--kind", choices=["auto", "bars", "ticks"], default="auto")
    parser.add_argument("--force", action="store_true", help="reprocess files that have not changed")
    parser.add_argument("--max-rows-per-file", type=int, default=5_000_000)
    parser.add_argument("--max-rows-per-group", type=int, default=500_000)
    args = parser.parse_args()

    if not args.source.exists():
        sys.exit(f"source directory not found: {args.source}")

    args.out.mkdir(parents=True, exist_ok=True)
    args.reports.mkdir(parents=True, exist_ok=True)

    state_path = args.out / "_state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {}

    files = sorted(p for p in args.source.rglob("*.csv") if not p.name.endswith("_meta.csv"))
    if not files:
        sys.exit(f"no CSV files found under {args.source}")

    # One series per file stem (the symbol plus the suffix of the study
    # input).  When the same series exists more than once - for example after
    # a re-export - the newest one wins, so the lake never holds duplicates.
    candidates: dict[str, tuple[Path, str, float]] = {}

    for path in files:
        meta = read_meta(path)
        exported = meta.get("exported_at_utc")
        stamp = path.stat().st_mtime
        if exported:
            try:
                stamp = datetime.strptime(str(exported), "%Y-%m-%d %H:%M:%S").replace(
                    tzinfo=timezone.utc).timestamp()
            except ValueError:
                pass
        series = path.stem
        if series not in candidates or stamp > candidates[series][2]:
            candidates[series] = (path, str(exported or ""), stamp)

    selected = sorted(candidates.values(), key=lambda t: t[0].as_posix())
    superseded = len(files) - len(selected)
    if superseded:
        print(f"  {superseded} superseded export(s) skipped, the newest of each series is used")

    sessions_all: list[pd.DataFrame] = []
    gaps_all: list[dict] = []
    exports_all: list[dict] = []
    processed = 0
    skipped = 0
    failed = 0

    for path, _exported_at, _stamp in selected:
        rel = str(path.relative_to(args.source))

        fingerprint = file_fingerprint(path)
        if not args.force and state.get(rel, {}).get("fingerprint") == fingerprint:
            skipped += 1
            print(f"  skip  {rel} (unchanged)")
            continue

        meta = read_meta(path)
        header = pd.read_csv(path, nrows=0)
        kind = args.kind
        if kind == "auto":
            kind = "ticks" if "price" in header.columns and "open" not in header.columns else "bars"

        print(f"  read  {rel}  ({kind})")

        df = pd.read_csv(path)
        df = normalise(df, kind)

        symbol = symbol_of(path, meta)

        checks, failures = validate(df, kind)
        checks["symbol"] = symbol
        checks["kind"] = kind
        checks["source_file"] = rel
        checks["meta"] = meta
        checks["ok"] = not failures
        checks["failures"] = failures

        report = {
            "source_file": rel,
            "symbol": symbol,
            "kind": kind,
            "fingerprint": fingerprint,
            "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "checks": checks,
        }
        (args.reports / (path.stem + ".json")).write_text(json.dumps(report, indent=2, default=str))

        if failures:
            failed += 1
            for f in failures:
                print(f"        FAIL {f}")

        # Parquet
        target = args.out / ("trades" if kind == "ticks" else "bars_1m")
        table = pa.Table.from_pandas(df, schema=BAR_SCHEMA if kind == "bars" else TICK_SCHEMA,
                                     preserve_index=False)
        partitioning = ds.partitioning(
            pa.schema([("symbol", pa.string()), ("year", pa.int32())]), flavor="hive")
        table = table.append_column("symbol", pa.array([symbol] * len(table), pa.string()))
        table = table.append_column("year", pa.array(
            [int(pd.Timestamp(t).year) for t in table.column("trading_day").to_pylist()], pa.int32()))

        ds.write_dataset(
            table,
            base_dir=str(target),
            format="parquet",
            file_options=ds.ParquetFileFormat().make_write_options(compression="zstd"),
            partitioning=partitioning,
            existing_data_behavior="overwrite_or_ignore",
            max_rows_per_file=args.max_rows_per_file,
            max_rows_per_group=args.max_rows_per_group,
        )

        # Session and gap tables
        sessions_all.append(build_sessions(df, symbol, set(checks.get("edge_days", []))))

        median_span = checks.get("median_session_minutes", 0) or 0
        for g in checks.get("gaps", []):
            gaps_all.append({
                "symbol": symbol,
                "trading_day": g["trading_day"],
                "missing_minutes": g["missing_minutes"],
                "kind": "gap",
            })

        exports_all.append({
            "file": rel,
            "symbol": symbol,
            "kind": kind,
            "rows": checks["rows"],
            "first_ts_ms": checks["first_ts_ms"],
            "last_ts_ms": checks["last_ts_ms"],
            "first_trading_day": checks["first_trading_day"],
            "last_trading_day": checks["last_trading_day"],
            "acsil_interface_version": meta.get("acsil_interface_version"),
            "timestamp_basis": meta.get("timestamp_basis"),
            "timestamps_are_utc": meta.get("timestamps_are_utc"),
            "bar_period_type": meta.get("bar_period_type"),
            "is_range_bars": meta.get("is_range_bars"),
            "exported_at_utc": meta.get("exported_at_utc"),
            "ok": checks["ok"],
        })

        state[rel] = {"fingerprint": fingerprint, "symbol": symbol, "kind": kind}
        processed += 1

    state_path.write_text(json.dumps(state, indent=2))

    # Write the derived tables as CSV for the DuckDB loader.
    tables_dir = args.out.parent / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)
    if sessions_all:
        pd.concat(sessions_all).drop_duplicates().sort_values(["symbol", "trading_day"]) \
          .to_csv(tables_dir / "sessions.csv", index=False)
    if gaps_all:
        pd.DataFrame(gaps_all).sort_values(["symbol", "trading_day"]) \
          .to_csv(tables_dir / "data_gaps.csv", index=False)
    if exports_all:
        pd.DataFrame(exports_all).sort_values(["symbol", "file"]) \
          .to_csv(tables_dir / "exports.csv", index=False)

    print()
    print(f"processed {processed}, skipped {skipped}, with failures {failed}")
    print(f"parquet   : {args.out}")
    print(f"reports   : {args.reports}")
    print(f"tables    : {tables_dir}")
    if failed:
        print()
        print("files with validation failures:")
        for p in sorted(args.reports.glob("*.json")):
            r = json.loads(p.read_text())
            if not r["checks"]["ok"]:
                print(f"  {r['source_file']}: {'; '.join(r['checks']['failures'])}")
        sys.exit(1)


if __name__ == "__main__":
    main()

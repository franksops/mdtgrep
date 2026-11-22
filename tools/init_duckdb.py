#!/usr/bin/env python3
"""
Create the MDTGrep DuckDB catalogue and the views over the Parquet lake.

The catalogue holds the metadata that cannot be derived from the rows
themselves - the contract specifications, the session table, the data gaps,
the roll table and the export provenance.  The bulk data stays in Parquet and
is queried in place, so nothing is ever copied into the database.

Usage
-----
    python tools/init_duckdb.py --parquet data/parquet --tables data/tables \\
        --db data/market.duckdb
    python tools/init_duckdb.py --rebuild          # drop and recreate
    python tools/init_duckdb.py --seed tools/instruments.csv
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
from pathlib import Path

try:
    import duckdb
except ImportError:  # pragma: no cover
    sys.exit("duckdb is required: pip install duckdb")

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cme_calendar  # noqa: E402


class ViewSkipped(Exception):
    """Raised when a view has no data to read yet."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS instruments (
    instrument        VARCHAR,     -- ESH17
    symbol            VARCHAR,     -- ESH17_FUT_CME
    exchange          VARCHAR,     -- CME
    asset_class       VARCHAR,     -- FUT
    expiry_date       DATE,
    first_notice_date DATE,
    multiplier        DOUBLE,
    tick_size         DOUBLE,
    currency          VARCHAR
);

CREATE TABLE IF NOT EXISTS sessions (
    symbol         VARCHAR,
    trading_day    DATE,
    open_ts        BIGINT,
    close_ts       BIGINT,
    bars           BIGINT,
    volume         BIGINT,
    span_minutes   INTEGER,
    kind           VARCHAR        -- regular | early_close | partial
);

CREATE TABLE IF NOT EXISTS data_gaps (
    symbol          VARCHAR,
    trading_day     DATE,
    missing_minutes INTEGER,
    kind            VARCHAR
);

CREATE TABLE IF NOT EXISTS rolls (
    instrument_from VARCHAR,
    instrument_to   VARCHAR,
    roll_date       DATE,
    ratio           DOUBLE,
    difference      DOUBLE
);

CREATE TABLE IF NOT EXISTS exports (
    file                    VARCHAR,
    symbol                  VARCHAR,
    kind                    VARCHAR,
    rows                    BIGINT,
    first_ts_ms             BIGINT,
    last_ts_ms              BIGINT,
    first_trading_day       DATE,
    last_trading_day        DATE,
    acsil_interface_version VARCHAR,
    timestamp_basis         VARCHAR,
    timestamps_are_utc      VARCHAR,
    bar_period_type         VARCHAR,
    is_range_bars           VARCHAR,
    exported_at_utc         VARCHAR,
    ok                      BOOLEAN
);

CREATE TABLE IF NOT EXISTS trading_calendar (
    calendar_date DATE,
    weekday       VARCHAR,
    kind          VARCHAR,       -- holiday | early_close
    early_close_et VARCHAR
);

CREATE TABLE IF NOT EXISTS file_reports (
    source_file VARCHAR,
    symbol      VARCHAR,
    kind        VARCHAR,
    ok          BOOLEAN,
    failures    VARCHAR,
    checked_at  TIMESTAMP,
    checks      JSON
);
"""


def seed_instruments(con, path: Path) -> int:
    """Load the contract specifications from a CSV."""
    if not path.exists():
        print(f"  instruments file not found: {path} (the table stays empty)")
        return 0

    rows = list(csv.DictReader(path.open()))
    if not rows:
        return 0

    con.executemany(
        "INSERT INTO instruments VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(r.get("instrument"), r.get("symbol"), r.get("exchange"), r.get("asset_class"),
          r.get("expiry_date") or None, r.get("first_notice_date") or None,
          float(r["multiplier"]) if r.get("multiplier") else None,
          float(r["tick_size"]) if r.get("tick_size") else None,
          r.get("currency")) for r in rows],
    )
    print(f"  instruments: {len(rows)} rows")
    return len(rows)


def build_calendar(con, start_year: int, end_year: int) -> int:
    rows = []
    for year in range(start_year, end_year + 1):
        for d, kind in cme_calendar.calendar(year).items():
            rows.append((d, d.strftime("%a"), kind, "13:00:00" if kind == "early_close" else None))

    con.executemany("INSERT INTO trading_calendar VALUES (?, ?, ?, ?)", rows)
    print(f"  trading_calendar: {len(rows)} rows ({start_year}-{end_year})")
    return len(rows)


def load_csv(con, table: str, path: Path) -> int:
    if not path.exists():
        print(f"  {table}: no file at {path}")
        return 0

    con.execute(f"DELETE FROM {table}")
    count = con.execute(
        f"INSERT INTO {table} SELECT * FROM read_csv_auto('{path.as_posix()}', header=true)"
    ).fetchone()[0]
    print(f"  {table}: {count} rows")
    return count


def create_views(con, parquet_root: Path, db_path: Path) -> None:
    """Views over the Parquet lake.  The paths are absolute so the database
    can be opened from any working directory."""
    root = parquet_root.resolve().as_posix()
    db_dir = db_path.resolve().parent.as_posix()

    def build_view(name: str, select: str, dataset: str) -> None:
        """Create a view over a dataset that may not have any files yet."""
        con.execute(f"DROP VIEW IF EXISTS {name}")

        if not any(Path(root, dataset).rglob("*.parquet")):
            con.commit()
            print(f"  view {name} skipped (no data yet)")
            return

        try:
            con.execute(f"CREATE VIEW {name} AS {select}")
            con.commit()
            print(f"  view {name}")
        except duckdb.IOException:
            # A dataset with no files yet is not an error - there is simply
            # nothing to read from it.
            print(f"  view {name} skipped (no data yet)")
            raise ViewSkipped()

    build_view("bars_1m", f"""
        SELECT * FROM read_parquet('{root}/bars_1m/**/*.parquet', hive_partitioning=1)
    """, "bars_1m")

    build_view("trades", f"""
        SELECT * FROM read_parquet('{root}/trades/**/*.parquet', hive_partitioning=1)
    """, "trades")

    build_view("bars_1m_by_day", f"""
        SELECT symbol, trading_day,
               min(ts_ms) AS open_ts, max(ts_ms) AS close_ts,
               count(*) AS bars, sum(volume) AS volume,
               arg_max(close, ts_ms) AS close,
               max(high) AS high, min(low) AS low
        FROM read_parquet('{root}/bars_1m/**/*.parquet', hive_partitioning=1)
        GROUP BY symbol, trading_day
    """, "bars_1m")

    build_view("trades_by_day", f"""
        SELECT symbol, trading_day,
               count(*) AS trades, sum(volume) AS volume,
               sum(delta) AS delta, min(ts_ms) AS first_ts, max(ts_ms) AS last_ts
        FROM read_parquet('{root}/trades/**/*.parquet', hive_partitioning=1)
        GROUP BY symbol, trading_day
    """, "trades")

    con.commit()

    con.execute("CREATE TABLE IF NOT EXISTS settings (key VARCHAR, value VARCHAR)")
    con.execute("DELETE FROM settings")
    con.executemany("INSERT INTO settings VALUES (?, ?)", [
        ("parquet_root", root),
        ("database", db_dir),
        ("created_at", dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")),
    ])

    # Everything has to be committed, an open transaction is rolled back when
    # the connection is closed and that would drop the views as well.
    con.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Create the MDTGrep DuckDB catalogue.")
    parser.add_argument("--parquet", type=Path, default=Path("data/parquet"))
    parser.add_argument("--tables", type=Path, default=Path("data/tables"))
    parser.add_argument("--reports", type=Path, default=Path("data/reports"))
    parser.add_argument("--db", type=Path, default=Path("data/market.duckdb"))
    parser.add_argument("--instruments", type=Path, default=Path("tools/instruments.csv"))
    parser.add_argument("--calendar-from", type=int, default=2005)
    parser.add_argument("--calendar-to", type=int, default=2040)
    parser.add_argument("--rebuild", action="store_true", help="delete the database first")
    args = parser.parse_args()

    if args.rebuild and args.db.exists():
        args.db.unlink()
        print(f"removed {args.db}")

    args.db.parent.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect(str(args.db))
    con.execute(SCHEMA)

    print("loading the catalogue:")
    seed_instruments(con, args.instruments)
    build_calendar(con, args.calendar_from, args.calendar_to)
    load_csv(con, "sessions", args.tables / "sessions.csv")
    load_csv(con, "data_gaps", args.tables / "data_gaps.csv")
    load_csv(con, "exports", args.tables / "exports.csv")

    if args.reports.exists():
        import json
        rows = []
        for p in sorted(args.reports.glob("*.json")):
            r = json.loads(p.read_text())
            c = r.get("checks", {})
            rows.append((r["source_file"], r.get("symbol"), r.get("kind"), c.get("ok", False),
                         "; ".join(c.get("failures", [])), r.get("checked_at"),
                         json.dumps(c, default=str)))
        if rows:
            con.execute("DELETE FROM file_reports")
            con.executemany("INSERT INTO file_reports VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
            print(f"  file_reports: {len(rows)} rows")

    create_views(con, args.parquet, args.db)
    con.close()

    print()
    print(f"database: {args.db}")
    print()
    print("try:")
    print(f"  duckdb {args.db}")
    print("  SELECT symbol, trading_day, bars, volume FROM bars_1m_by_day LIMIT 5;")
    print("  SELECT * FROM data_gaps;")


if __name__ == "__main__":
    main()

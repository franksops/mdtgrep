#!/usr/bin/env python3
"""
Fetch a Databento trade session and compare it with an MDTGrep export.

The comparison needs the *same contract* over the *same session*, so this tool
takes the trading day and works out the session window the way the exchange
defines it: a CME session opens at 18:00 Eastern on the previous calendar day
and closes at 17:00 Eastern on the trading day.

The API key is read from the environment::

    export DATABENTO_API_KEY=db-XXXXXXXX

Examples
--------
    # fetch one session and compare it with the MDTGrep export of that session
    python tools/fetch_databento.py --symbol ESH6 --session-date 2026-11-18 \\
        --compare market_data/bars_1m/ESH6_bars_ticks.csv

    # fetch only
    python tools/fetch_databento.py --symbol ESH6 --session-date 2026-11-18 \\
        --out /tmp/esh6_trades.csv

Notes
-----
*   The Databento ``trades`` schema is the right comparison: one message per
    trade, with the aggressor side.
*   Compare a **recent** session.  Before 2017-05-21 Databento's CME data comes
    from the legacy aggregated FIX protocol, where the aggressor side is often
    missing, so a side comparison on old data measures the vendor's legacy
    coverage rather than yours.
*   Databento re-normalises the CME history retroactively, so record the
    dataset version with the result.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    import pandas as pd
except ImportError:  # pragma: no cover
    sys.exit("pandas is required: pip install pandas")

EASTERN = ZoneInfo("America/New_York")

# The CME equity index session: 18:00 Eastern on the previous calendar day
# until 17:00 Eastern on the trading day.
SESSION_OPEN_ET = (18, 0)
SESSION_CLOSE_ET = (17, 0)


def session_window(session_date: str) -> tuple[datetime, datetime]:
    """The UTC window of one CME trading day."""
    day = datetime.strptime(session_date, "%Y-%m-%d").date()

    open_et = datetime.combine(day - timedelta(days=1), datetime.min.time(),
                                EASTERN).replace(hour=SESSION_OPEN_ET[0],
                                                 minute=SESSION_OPEN_ET[1])
    close_et = datetime.combine(day, datetime.min.time(), EASTERN).replace(
        hour=SESSION_CLOSE_ET[0], minute=SESSION_CLOSE_ET[1])

    return open_et.astimezone(timezone.utc), close_et.astimezone(timezone.utc)


def list_es_contracts(client, dataset: str, session_date: str) -> None:
    """Print the ES contracts that exist for a session, with their expiries.

    Used when the symbol given on the command line does not resolve - the
    exchange changed its symbol convention for some products, so the year
    digit can differ from the one expected.
    """
    day = datetime.strptime(session_date, "%Y-%m-%d").date()

    try:
        store = client.timeseries.get_range(
            dataset=dataset,
            start=day,
            end=day + timedelta(days=1),
            schema="definition",
        )
        defs = store.to_df()
    except Exception as e:  # pragma: no cover
        print(f"could not read the definitions: {e}")
        return

    if defs.empty:
        print("no definitions returned")
        return

    mask = defs.get("raw_symbol", pd.Series(dtype=str)).astype(str).str.startswith("ES")
    rows = defs[mask]

    print(f"\n  ES contracts listed for {day}:")
    cols = [c for c in ("raw_symbol", "expiration_date", "contract_multiplier",
                        "min_price_increment", "underlying") if c in rows.columns]
    for _, r in rows.sort_values("expiration_date").iterrows():
        exp = r.get("expiration_date", "")
        print(f"    {r['raw_symbol']:<8} expires {exp}   multiplier {r.get('contract_multiplier','')}"
              f"   tick {r.get('min_price_increment','')}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch a Databento trade session.")
    parser.add_argument("--symbol", required=True, help="the raw contract symbol, e.g. ESZ6")
    parser.add_argument("--session-date", required=True, help="the trading day, YYYY-MM-DD")
    parser.add_argument("--dataset", default="GLBX.MDP3")
    parser.add_argument("--schema", default="trades")
    parser.add_argument("--out", type=Path, default=None, help="output CSV")
    parser.add_argument("--compare", type=Path, default=None,
                        help="an MDTGrep trade export to compare against")
    args = parser.parse_args()

    key = os.environ.get("DATABENTO_API_KEY")
    if not key:
        sys.exit("DATABENTO_API_KEY is not set - export it before running this tool")

    start, end = session_window(args.session_date)

    print(f"session {args.session_date}  {args.symbol}")
    print(f"  window  {start.isoformat()} -> {end.isoformat()}")

    try:
        import databento
    except ImportError:  # pragma: no cover
        sys.exit("the databento package is required: pip install databento")

    client = databento.Historical(key)

    store = client.timeseries.get_range(
        dataset=args.dataset,
        start=start,
        end=end,
        symbols=[args.symbol],
        schema=args.schema,
    )

    df = store.to_df()

    if df.empty:
        print()
        print(f"no data for '{args.symbol}' on {args.session_date}.")
        print("The symbol may be wrong - the exchange changed its year digit for some contracts.")
        list_es_contracts(client, args.dataset, args.session_date)
        sys.exit(2)

    # The DBN price is an integer scaled by 1e-9.
    if "price" in df.columns and pd.api.types.is_integer_dtype(df["price"]):
        df["price"] = df["price"] / 1e9

    out = args.out or Path(f"{args.symbol}_{args.session_date}_databento.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)

    print(f"  trades  {len(df):,}")
    if "size" in df.columns:
        print(f"  volume  {df['size'].sum():,.0f}")
    if "price" in df.columns:
        print(f"  prices  {df['price'].min():.2f} .. {df['price'].max():.2f}")
    if "side" in df.columns:
        print(f"  sides   {df['side'].value_counts().to_dict()}")
    print(f"  written to {out}")

    if args.compare:
        print()
        print("comparing:")
        import subprocess
        rc = subprocess.call([
            sys.executable,
            str(Path(__file__).with_name("verify_vs_databento.py")),
            "--sierra", str(args.compare),
            "--databento", str(out),
        ])
        sys.exit(rc)


if __name__ == "__main__":
    main()

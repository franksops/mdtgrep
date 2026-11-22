# MDTGrep — product brief

## Purpose

Give a trading research and automation stack a clean, validated market data feed out of Sierra
Chart: bars and trade records, in true UTC, keyed by the exchange trading day, ready to backtest
on and to train an execution bot against.

The consumer is the SC-Assist pipeline — a strategy research, backtesting and execution system
that needs the same data at every stage, so that a strategy researched on historical bars behaves
identically when it is traded live.

## Scope

Two modes, one dataset:

**Bars — back adjusted.** For price based strategies, where a continuous series matters more than
the individual contract.

```
Chart Settings → Bar Period → 1 minute

unix_ts_ms, trading_day, open, high, low, close,
volume, ask_vol, bid_vol, num_trades, open_interest
```

**Trades — raw.** For volume, delta and execution research, where the traded prices have to be
the real ones.

```
Chart Settings → Bar Period → Number of Trades Per Bar → 1

unix_ts_ms, trading_day, price, volume, ask_vol, bid_vol,
delta, num_trades, side, bid, ask
```

## Deliverables

| | |
|---|---|
| `MDTGrep.cpp` | The ACSIL study: exports the chart and its trade records with the exchange trading day, in UTC, with a metadata sidecar per file |
| `tools/build_parquet.py` | The ingest: schema normalisation, validation, session and gap derivation, partitioned Parquet |
| `tools/init_duckdb.py` | The catalogue: instruments, sessions, gaps, rolls, exports, calendar, and the views over the lake |
| `tools/validate_export.py` | Structural validation of any export |
| `tools/verify_vs_databento.py` | Trade level comparison against another vendor |
| `tools/cme_calendar.py` | The CME equity index holiday and early close calendar |
| `docs/SPEC.md` | The technical specification of the data lake |

## Principles

1. **Raw and adjusted stay separate.** The trade file always carries raw traded prices; the bar
   file follows the chart's back adjustment. A continuous series is derived at load time.
2. **The session key is computed.** The CME session opens at 18:00 and closes at 17:00 Eastern, so
   the evening bars belong to the next trading day — and that rule moves with daylight saving time.
3. **Nothing is filled in.** A missing minute stays missing and is reported. A backtest that
   trades through a hole in the feed is worse than one that skips it.
4. **Provenance is part of the data.** The chart settings that produced a file are recorded with
   it, so a collection of dozens of contracts can be audited after the fact.

## Status

**Shipped.**

*   The study exports bars and trade records with the exchange trading day, in UTC, with a
    metadata sidecar per file.
*   The ingest validates every file against the market structure and writes partitioned Parquet;
    it is idempotent and never holds duplicates.
*   The catalogue serves the lake through DuckDB: sessions, gaps, instruments, the trading
    calendar and the roll spreads.
*   The reference dataset — `ESZ16`, `ESH17`, `ESM17`, 262,552 bars, September 2016 to June 2017 —
    is verified end to end: every bar reproduced exactly, correct session structure across both
    daylight saving transitions, no unexplained gaps.
*   One gap in the feed is known and reported: ESM17 is missing the first 71 minutes of the
    2017-06-04 session.

**Open.**

*   The trade level export has not yet been compared against Databento. The comparison tool is in
    place; the measurement is the next step.
*   Tick level history is only available where the data service provides it. The `num_trades`
    column reports the granularity per row.

## Roadmap

*   Incremental collection, so a long history can be built session by session instead of in one
    pass.
*   A continuous-series builder driven by the `rolls` table, so a price based strategy can be
    researched on a continuous series without exporting adjusted charts.
*   More instruments in the contract catalogue.

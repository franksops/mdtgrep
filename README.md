# MDTGrep (Market Data Tick Grep)

MDTGrep is an Advanced Custom Study Interface and Language (ACSIL) DLL for Sierra Chart.

It extracts the data of the chart it is added to into flat CSV files for Pandas/Polars, a database
loader, or the SC-Assist pipeline. Both **chart bars** and the **underlying intraday trade records
(ticks)** can be exported, with timestamps in true UTC so the output joins cleanly with Databento
and other sources.

---

## Quick start — the verified recipe

### 1. Build the study

1.  Copy `MDTGrep.cpp` to `C:\SierraChart\ACS_Source\`.
2.  Sierra Chart → `Analysis` → `Build Custom Studies DLL` → check `MDTGrep.cpp` → **Build Remote**.
3.  **Restart Sierra Chart**, then remove and re-add the study to the chart.

> Always use **Build Remote**. See [Build and the interface version](#build-and-the-interface-version).

### 2. Chart settings (per symbol)

`Chart` → `Chart Settings`:

| Setting | Where | Value |
|---------|-------|-------|
| Bar Period | `Intraday Chart Bar Period` | `Days-Mins-Secs-Milliseconds Per Bar` = **1 minute** |
| Time Zone | `Advanced Settings` | **UTC** |
| Session Times | `Session Times` | Start **00:00:00**, End **23:59:59** |
| Date range | `Use Date Range` | From = contract open **−1 day**, To = contract close **+1 day** |

The bar period is **1 minute for both exports**. The tick export reads the intraday data files
directly, so the bar period only controls how those records are iterated — it does not change the
output. Do **not** use `Range Per Bar` here: that produces price-movement bars, not time bars.

### 3. Study settings

`Studies` → `MDT Grep (CSV Exporter)` → `Study Settings`:

| # | Input | Value |
|---|-------|-------|
| 0 | Trigger Export on Full Recalc | Yes |
| 1 | Export File Suffix | `_bars` |
| 2 | Export Mode | `Bars` first, then `Ticks` |
| 3 | Output Directory | `C:\SierraChart\SCAssistData\` |
| 4 | Timestamp Basis | **UTC** |
| 5 | Max Bars For Tick Export | `1400` (one session) for the first tick run |

### 4. Export

`Edit` → `Recalculate` on the chart. The log confirms what happened:

```
MDTGrep: ACSIL interface 2969 - exported 90619 bars to C:\SierraChart\SCAssistData\ESZ16_FUT_CME_bars.csv (chart local time)
MDTGrep: ACSIL interface 2969 - exported 240318 ticks to C:\SierraChart\SCAssistData\ESZ16_FUT_CME_bars_ticks.csv (chart local time)
```

### 5. Verify

```bash
python tools/validate_export.py ESZ16_FUT_CME_bars.csv
python tools/verify_vs_databento.py --sierra ESZ16_FUT_CME_bars_ticks.csv --databento esz16.parquet
```

---

## Build and the interface version

The `SCDLLName` macro exports `scdll_DLLVersion()`, which returns the ACSIL interface version the
DLL was compiled against. Sierra Chart compares it against the interface version of the running
build and **silently refuses to run a study whose DLL does not match**:

> The study ... needs to be recompiled for this version of Sierra Chart.

That is why a study built against a stale `ACS_Source\sierrachart.h` compiles with **zero errors**
and still never runs. The rules:

*   The interface version tracks the Sierra Chart build that introduced the change, so it has to
    match the running build. Sierra Chart **2972 and higher** finalized the internal double
    precision number project, which is why every custom study has to be recompiled for that
    version. A build made against an older header reports the older version (`SC_DLL_VERSION` in
    `sierrachart.h`) and is rejected.
*   **Build Remote** always compiles against the header of the running Sierra Chart build. A local
    build uses whatever `ACS_Source\sierrachart.h` happens to be.
*   MDTGrep logs the interface version it was built with on every export. **If that line is missing
    from the log, the study function never ran** — the DLL was not loaded.
*   A freshly built DLL is only picked up after Sierra Chart is restarted (or after the study is
    removed and re-added), and the export has to be triggered with `Edit → Recalculate`.

MDTGrep also emits a `#pragma message` at compile time when it is built against an interface older
than `2969`, so a stale local build is visible in the build log.

---

## Study inputs

| # | Input | Default | Description |
|---|-------|---------|-------------|
| 0 | Trigger Export on Full Recalc | Yes | Export on every full recalculation of the chart (chart load, bar period change, date range change). |
| 1 | Export File Suffix | `_bars` | Suffix of the output file, e.g. `_bars`, `_1min`, `_5min`. |
| 2 | Export Mode | Bars | `Bars` = chart bars, `Ticks` = underlying intraday trade records, `Both`. |
| 3 | Output Directory | `C:\SierraChart\SCAssistData\` | Directory the CSV files are written to. Created if it does not exist. |
| 4 | Timestamp Basis | Chart Local (as-is) | `Chart Local` keeps the chart/exchange local time, `UTC` converts it. |
| 5 | Max Bars For Tick Export | 0 (all) | Limits how many chart bars are read for the tick export. |

Changing any of these settings re-runs the export immediately — an input change does not cause a
full recalculation, and the study watches for setting changes itself.

---

## Generated files

Output directory (input #3), default `C:\SierraChart\SCAssistData\`:

| Mode | File | Example |
|------|------|---------|
| Bars | `<SYMBOL><suffix>.csv` | `ESM17_FUT_CME_bars.csv` |
| Ticks | `<SYMBOL><suffix>_ticks.csv` | `ESM17_FUT_CME_bars_ticks.csv` |

### Bars

```
unix_ts_ms,trading_day,open,high,low,close,volume,ask_vol,bid_vol,num_trades,open_interest
1795278600000,2026-11-23,5321.00,5321.75,5321.00,5321.50,154,82,72,98,0
```

*   `unix_ts_ms` — Unix epoch milliseconds, start of the chart bar.
*   `trading_day` — the **exchange trading day** (`YYYY-MM-DD`) the bar belongs to. The CME session
    opens at 18:00 and closes at 17:00 Eastern, so the evening bars carry the *next* day's label.
    This is the session key for grouping, joining and gap detection, and it is computed with the
    daylight saving rules so it stays correct over the whole history.
*   `open, high, low, close` — prices. These follow the **back adjustment settings of the chart**.
*   `volume, ask_vol, bid_vol, num_trades` — integers. `ask_vol`/`bid_vol` are the traded-at-ask /
    traded-at-bid volumes, i.e. the buy/sell split.
*   `open_interest` — only valid for daily and higher timeframe charts. Sierra Chart returns the
    *trade count* in that array for intraday charts, so MDTGrep writes `0` for intraday charts
    instead of duplicating `num_trades`.

### Ticks

```
unix_ts_ms,trading_day,price,volume,ask_vol,bid_vol,delta,num_trades,side,bid,ask
1795278602150,2026-11-23,5321.25,2,2,0,2,1,B,5321.00,5321.25
1795278602300,2026-11-23,5321.00,5,0,5,-5,2,S,5321.00,5321.25
```

*   `unix_ts_ms` — Unix epoch milliseconds of the trade.
*   `trading_day` — the exchange trading day of the trade, same rule as the bar export.
*   `price` — traded price (raw, not back adjusted).
*   `volume, ask_vol, bid_vol` — trade volume and its buy/sell split.
*   `delta` — `ask_vol - bid_vol`.
*   `num_trades` — number of trades in the record. **Values greater than 1 mean the record
    aggregates several trades**, i.e. the intraday storage time unit is not 1 Tick for that data.
*   `side` — `B` (buy), `S` (sell) or `N` (unknown/mixed). Derived with the same rule the
    exchanges use: a trade at or above the ask is a buy, a trade at or below the bid is a sell.
    When the record does not carry the quotes the side falls back to the buy/sell volume split.
*   `bid, ask` — the bid and ask prices of the moment of the trade. Only present when the data
    service supplied the quotes with the trade, otherwise `0`.

### Metadata sidecar

Every export also writes `<file>_meta.csv` next to the data file:

```
key,value
symbol,ESH17_FUT_CME
export_mode,bars
timestamp_basis,Chart Local
chart_time_zone_is_utc,1
timestamps_are_utc,1
acsil_interface_version,2969
bar_period_type,Days-Mins-Secs-Milliseconds
bar_period_parameter1,60
is_range_bars,0
rows,87162
first_ts_ms,1481476800000
last_ts_ms,1489593600000
first_trading_day,2016-12-12
last_trading_day,2017-03-15
exported_at_utc,2026-11-22 15:25:33
```

It records the settings that produced the file. The chart time zone and the session times are not
visible in the data itself, so they have to be captured when the file is written — without this a
collection of 40+ contracts cannot be audited after the fact. `is_range_bars` is 1 when the chart
was set to a price-movement bar period, which produces bars that are not time based.

`timestamps_are_utc` is the row the load pipeline should assert on: it is 1 when the timestamps in
the file are true UTC instants, which is the case when `Timestamp Basis` is `UTC` or when the
chart time zone is UTC. The study logs a warning when it is 0.

---

## Getting tick data

The tick export reads the intraday data files (`.scid`) of the chart, one record at a time. It
needs:

1.  An **intraday chart**. Daily charts have no intraday records.
2.  `Global Settings` → `Data/Trade Service Settings` → `Common Settings` → **Intraday Data Storage
    Time Unit** = **1 Tick**.
3.  The intraday data **re-downloaded** after that setting was changed:
    `Edit` → `Delete and Download Data` on an intraday chart per symbol. The setting only affects
    data collected or downloaded *afterwards*; existing `.scid` files keep their old granularity.
    (The built-in **Numbers Bars** study can switch the setting to 1 Tick automatically with its
    *"Require 1 Tick Intraday Data Storage Time"* input.)

> **Reading every tick of a long history makes the Sierra Chart user interface freeze for a while.**
> Sierra documents this limitation of `sc.ReadIntradayFileRecordForBarIndexAndSubIndex()`. Use
> `Max Bars For Tick Export`, or export day by day, until the pipeline is proven.

### Reading the `num_trades` column

This single column tells you whether the storage granularity is right:

| `num_trades` | Meaning | Action |
|--------------|---------|--------|
| `1` on ~every row | 1 Tick storage, one record per trade | ✅ This is the trade level feed — compare it against Databento. |
| ~160 (or any value > 1) | the records are batches of trades | Set the storage unit to 1 Tick, re-download the intraday data, export again. |

A quick visual check without exporting: open the **Time and Sales** window on the chart. One line
per trade with millisecond timestamps means 1 Tick storage; one aggregated line per bucket means
the storage is still coarse.

---

## Timestamps

Sierra Chart uses two different time bases internally and MDTGrep converts both into the basis you
select, so the bar file and the tick file always agree:

| Source | Time base in Sierra Chart | Conversion used |
|--------|---------------------------|-----------------|
| `sc.BaseDateTimeIn[]` (chart bars) | already adjusted to the **time zone of the chart** | chart time zone → UTC |
| Intraday `.scid` records (ticks) | **UTC/GMT** (raw storage format) | UTC → chart time zone |

`Timestamp Basis` selects the result:

*   `Chart Local (as-is)` — the exchange/chart local time.
*   `UTC` — required for a direct comparison with Databento, whose timestamps are UTC.

Set `Timestamp Basis` to **UTC** when comparing against Databento.

---

## Chart settings that matter, and why

### The chart time zone and the session times

The chart **Session Times** are wall clock times **in the time zone of the chart**. The CME equity
index session opens at 18:00 and closes at 17:00 Eastern, which is **23:00 → 22:00 UTC in winter
and 22:00 → 21:00 UTC in summer**.

A fixed UTC session can therefore only be correct for one of the two daylight saving halves, and
when Sierra Chart shifts the session times for a time zone change it uses the offset that is in
effect *at that moment* — so a chart that is right for the summer data ends up an hour off for that
period. The bars of the first hour of every affected session are then labelled an hour late or
dropped.

The **24 hour session** (`00:00:00` – `23:59:59`) avoids this entirely: nothing is ever outside
the session, so no bar can be dropped or shifted in any daylight saving period. Sierra documents
this as the way to include all 24 hours of data.

The alternative is to leave the chart on Eastern (or the exchange time zone) with the session times
at **18:00:00 – 17:59:59** and let the study do the DST aware conversion with
`Timestamp Basis = UTC`. Eastern and Central share the US daylight saving rules, so the session
follows the exchange all year.

### The first session of the chart

With a 24 hour session the first session day starts at 00:00, but a futures contract's intraday
data starts at the evening session open. If the chart's first day is the day the data starts on,
that partial session can end up not charted at all — typically the opening hour, which contains the
highest volume burst of the file.

**Start the chart date range one day before the contract opens.** The incomplete session day then
falls on a day with no data and nothing is lost. Add a day after the contract closes for the same
reason at the other end.

---

## Pitfalls

| Symptom | Cause | Fix |
|---------|-------|-----|
| Build reports no errors, no CSV appears | DLL built against an old `sierrachart.h`, rejected by Sierra Chart at runtime | Build **Remote**, restart Sierra Chart, re-add the study. Look for the `MDTGrep: ACSIL interface ...` log line. |
| Study settings show only 2 inputs | The old DLL is still loaded | Rebuild + restart + re-add the study. The new one has 6 inputs. |
| Timestamps are 4–5 h behind UTC | The chart time zone is Eastern and `Timestamp Basis` is `Chart Local` | Set `Timestamp Basis` to `UTC`, or set the chart time zone to UTC. |
| First hour of every summer session missing or shifted an hour | Fixed UTC session times vs. the exchange daylight saving change | Use the 24 hour session (recommended) or Eastern + `Timestamp Basis = UTC`. |
| First session of the file is missing | The chart's first day is the day the data starts on | Extend the date range one day earlier. |
| Bars have ragged, unaligned durations and ~160 trades each | The bar period is `Range Per Bar` (a price movement bar) | Use `Days-Mins-Secs-Milliseconds Per Bar` = 1 minute. |
| Tick file rows contain many trades | Intraday storage is coarser than 1 Tick | Set `Intraday Data Storage Time Unit` = 1 Tick **and re-download** the intraday data. |
| Tick export is very slow / Sierra freezes | Every tick of a long history is being read | Use `Max Bars For Tick Export`, or export day by day. |
| `open_interest` equals `num_trades` | Sierra returns the trade count in that array for intraday charts | Expected. MDTGrep writes `0` for intraday charts. |

---

## Fidelity compared to Databento

Databento `trades` and an MDTGrep tick export come from the same exchange feed when using Sierra's
Teton service (CME MDP 3.0), so prices and sizes should line up trade for trade. What remains
different:

| Aspect | Databento | MDTGrep / Sierra Chart | Impact on a backtest |
|--------|-----------|------------------------|-----------------------|
| Timestamp | UTC, nanoseconds | UTC, milliseconds (Sierra resolution) | Sub-millisecond ordering is lost. The export keeps the file order, so the sequence is still correct. |
| Price | raw traded price | raw traded price | none (the **bar** file is chart back adjusted, the tick file is not) |
| Size | integer | integer | none |
| Side | exchange reported (`B`/`S`/`N`) | derived from the quotes of the trade, otherwise from the buy/sell volume split | Mid-tick trades can be `N` in Sierra and `B`/`S` in Databento. |
| Cancels / corrections | `action` = `C` / `X` | not provided by the data service | Databento can contain fewer trades than Sierra for the same session. |
| Book depth (MBP-1 / MBO) | up to 10 price levels | not available | Depth based strategies cannot be reproduced from this export. |
| Aggregation | one message per trade | one record per trade, but only when the intraday storage time unit is 1 Tick | Check the `num_trades` column: values `> 1` are aggregated records. |

### Acceptance criteria for a tick export

| Metric | Target |
|--------|--------|
| Price match (exact) | ≥ 99.5% |
| Volume match | ≥ 99% |
| Timestamps matched within ±1 ms | ≥ 99.9% |
| Unmatched Sierra trades | < 0.5% |
| Side agreement (`B`/`S`) | ≥ 95% |

---

## The data lake

The exports are the immutable landing zone; everything else is derived from them.

```bash
# 1  ingest: CSV -> validated Parquet (idempotent, one run per changed file)
python tools/build_parquet.py --source market_data --out data/parquet --reports data/reports

# 2  the catalogue: sessions, gaps, instruments, calendar and the views
python tools/init_duckdb.py --parquet data/parquet --tables data/tables \
    --reports data/reports --db data/market.duckdb
```

Then query it:

```sql
-- duckdb data/market.duckdb
SELECT symbol, trading_day, bars, volume, close FROM bars_1m_by_day
WHERE symbol = 'ESZ16_FUT_CME' AND trading_day BETWEEN '2016-12-01' AND '2016-12-31';

SELECT * FROM data_gaps;
```

The ingest validates every file and writes a JSON report; it exits non-zero when a rule fails.
The rules, the schema, the calendar model and the backtest caveats are in
**[docs/SPEC.md](docs/SPEC.md)**.

## Tools

All tools are plain Python with `pandas`, `pyarrow` and `duckdb`
(`pip install pandas pyarrow duckdb`).

### `tools/build_parquet.py` — the ingest

```bash
python tools/build_parquet.py --source market_data --out data/parquet --reports data/reports
```

Reads every export, normalises the schema (deriving `trading_day` for exports made before the
column existed), validates the invariants, derives the session and gap tables and writes
partitioned Parquet (`symbol=/year=`). Idempotent: a file is fingerprinted and skipped when it
has not changed. When the same series exists more than once the newest export wins.

### `tools/init_duckdb.py` — the catalogue

```bash
python tools/init_duckdb.py --parquet data/parquet --tables data/tables \
    --reports data/reports --db data/market.duckdb
```

Creates the catalogue tables (`instruments`, `sessions`, `data_gaps`, `rolls`, `exports`,
`trading_calendar`, `file_reports`) and the views over the Parquet lake (`bars_1m`, `trades`,
`bars_1m_by_day`, `trades_by_day`). The bulk data is never copied into the database.

### `tools/cme_calendar.py` — the CME holiday rules

The holiday and early close rules of the CME equity index complex, derived from the exported
data. Used by the ingest to tell a scheduled early close apart from a real data gap.

```bash
python tools/cme_calendar.py 2017
```

### `tools/validate_export.py` — structural checks on any export

```bash
python tools/validate_export.py ESZ16_FUT_CME_bars.csv
python tools/validate_export.py ESZ16_FUT_CME_bars_ticks.csv --kind ticks
```

Checks the timestamp monotonicity, duplicates and spacing, the session structure and the time base,
the OHLC integrity and the price tick grid, `ask_vol + bid_vol == volume`, the trade counts, and
whether `open_interest` is a duplicate of `num_trades`.

### `tools/verify_vs_databento.py` — tick level comparison

```bash
python tools/verify_vs_databento.py \
    --sierra  ESZ16_FUT_CME_bars_ticks.csv \
    --databento esz16.parquet \
    --price-tol 0.00001
```

Reports the trade count difference, the timestamp alignment within a tolerance, the price and size
agreement, the side agreement with a confusion matrix, and examples of unmatched trades on both
sides. A file produced by the Databento CLI works directly
(`databento.trades.save_to_csv(...)`).

### `tools/fix_prev_version_data.py` — repair older exports

Exports made before the time base fix store the chart wall clock as if it was UTC (4–5 hours
behind true UTC) and duplicate `num_trades` into `open_interest`:

```bash
python tools/fix_prev_version_data.py market_data/*.csv --source-tz America/New_York
```

Converts the timestamps to real UTC (daylight saving aware), drops the duplicated column and
writes the counts as integers.

---

## Long horizon collection

Ten, twenty or thirty years of ES, measured against the exports in `market_data/`:

| Horizon | 1 minute bars | Tick records | CSV landing zone | Parquet |
|---------|--------------|--------------|------------------|---------|
| 10 years | 3.4M | ~900M | 63 GB | ~9 GB |
| 30 years | 10.4M | ~2.7B | 190 GB | ~27 GB |

Bars are trivial. **Ticks are the expensive part**, on three axes at once: the size of Sierra
Chart's `.scid` files at 1 Tick storage, the time it takes to read them back out, and the disk
space of the CSV landing zone. The realistic plan is therefore:

*   **1 minute bars for the full history** (30 years is ~30 MB of Parquet).
*   **Tick records for the recent window** that the strategy research actually needs.

### Storage

Use **partitioned Parquet on disk and query it with DuckDB** rather than importing into a row
store. The backtest then reads the Parquet files directly, with column pruning and row group
pruning, and no ETL copy of the data exists:

```sql
SELECT ts_ms, open, high, low, close
FROM read_parquet('market_data/bars_1m/symbol=ESH17/year=2017/*.parquet')
WHERE trading_day BETWEEN '2017-01-01' AND '2017-03-13';
```

DuckDB streams 2.7B rows without loading them into memory. SQLite is fine for the **catalog**
(metadata) but painful for the tick rows; Postgres/Timescale only if the bot needs to write
concurrently with the backtest.

### Tables

```sql
bars_1m(ts_ms BIGINT, trading_day DATE, instrument, symbol,
         open, high, low, close DOUBLE,
         volume, ask_vol, bid_vol, num_trades INT)

trades(ts_ms BIGINT, trading_day DATE, instrument, symbol,
       price DOUBLE, size INT, side VARCHAR, bid, ask DOUBLE, delta INT)

instruments(instrument, symbol, exchange, asset_class, expiry_date,
            first_notice_date, multiplier, tick_size, currency)
sessions(trading_day, symbol, open_ts, close_ts, bars, volume, is_early_close)
data_gaps(symbol, trading_day, start_ts, end_ts, minutes)
rolls(instrument_from, instrument_to, ratio, difference, roll_date)
```

*   `data_gaps` — the pipeline finds the gaps automatically. One is already known: ESM17 is missing
    the first 71 minutes of the 2017-06-04 session. A backtest that silently trades through a hole
    is worse than one that skips it.
*   `rolls` — the calendar spreads between the quarterly contracts are measurable from the data
    (ESZ16 → ESH17 was +5.50 points, ESH17 → ESM17 +3.20, constant to within a point), so any roll
    rule can be validated rather than assumed.

Keep **raw and adjusted** separate: the tick data is raw, the bar data follows the chart's back
adjustment. A continuous price series is generated at load time from the raw contracts through
the `rolls` table, never by exporting adjusted charts.

### Collection order

**Start with the most recent ten years, then walk backwards.** The order does not affect the
result — the ETL is per contract — but the order in which you collect does affect how much work
you might have to redo:

*   Tick level historical data is most likely to be available for the recent past. If the older
    years only exist as minute buckets, you want to find that out on one decade rather than
    after thirty.
*   If the schema has to change, you find out while the collection is young.
*   The recent data is the easiest to validate, because that is the range a comparison against
    another vendor is most likely to cover.

Never edit the exported CSVs in place. They are the reproducible landing zone; the ETL reads them,
validates them and writes Parquet.

---

## Validation status

The bar exports for `ESZ16`, `ESH17` and `ESM17` (3 contracts, 262,552 bars, Sept 2016 – Jun 2017)
have been verified end to end:

*   Every bar of the original export is present, with **zero value differences** in `open`, `high`,
    `low`, `close`, `volume`, `ask_vol`, `bid_vol` and `num_trades`.
*   Timestamps are true UTC and the session structure is correct across the November 2016 and
    March 2017 daylight saving transitions.
*   0 OHLC violations, 100% of prices on the 0.25 tick, `ask_vol + bid_vol == volume` on every bar
    but one (27 of 1,173 contracts unclassified in a single minute).
*   The gaps are all market structure: the CME daily index halt (16:15–16:30 ET), the maintenance
    break (17:00–18:00 ET), the holiday early closes (12:59 and 13:14 ET) and the weekends.
*   Session closes track the front month contract to within ~1 point on average against an
    independent market data source; the deviations are settlement-versus-close differences on
    volatile days.

**Known source data gap:** the intraday data of `ESM17` is missing the first 71 minutes of the
2017-06-04 session (18:00–19:10 ET). This is a gap in Sierra's data, not in the export — it is
present in every export of that contract. Treat that window as no-data rather than as a flat market.

---

## Troubleshooting

**The study was recompiled, the build reported no errors, but no CSV appears.**

See [Build and the interface version](#build-and-the-interface-version).

**Other causes of "no output":**

| Symptom | Cause |
|---------|-------|
| `MDTGrep: waiting for the historical data download to complete...` | The export starts once the download finishes; the log confirms when it ran. |
| `MDTGrep: no chart data loaded, nothing to export.` | No data on the chart (wrong symbol, no history, or the download failed). |
| `MDTGrep: no intraday trade records were found.` | Tick export on a daily chart, or the Intraday Data Storage Time Unit is not 1 Tick. |
| `MDTGrep: unable to create the output directory: ...` | The output directory could not be created (permissions/path). |
| File is written but the data looks stale | The export only runs on a full recalculation — use `Edit → Recalculate` on the chart after changing the chart settings. |

---

## Repository layout

```
MDTGrep.cpp                      the ACSIL study
README.md                        this document
PLAN.md                          requirements and roadmap
docs/SPEC.md                     the technical specification of the data lake
tools/build_parquet.py           ingest: CSV -> validated Parquet
tools/init_duckdb.py             the DuckDB catalogue and the views over the lake
tools/validate_export.py         structural validation of an export
tools/verify_vs_databento.py     tick level comparison against Databento
tools/fix_prev_version_data.py   repair of exports made with the older study
tools/cme_calendar.py            the CME equity index holiday calendar
tools/instruments.csv            the contract specifications
market_data/bars_1m/             the reviewed exports (the current schema)
market_data/legacy_bars/         exports made before the trading_day column
data/                            the derived lake (Parquet, catalogue, reports) - not committed
defunct/                         earlier versions of the exporter, kept for reference
```

## Future enhancements

*   **Parquet export.** A few years of tick data is 30+ GB in CSV. Parquet is columnar and
    compresses several times better, which matters for the load times of the backtest and training
    pipelines.
*   **Incremental / append mode** so a long history can be built up session by session instead of
    re-exporting everything.

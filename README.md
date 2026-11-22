# MDTGrep

**Market data extraction for Sierra Chart, built for backtesting and algorithmic trading.**

MDTGrep turns a Sierra Chart into a data feed you can actually backtest on. It exports the bars
*and* the underlying trade records of any chart to CSV in true UTC, stamps every row with the
exchange trading day, and ships a pipeline that validates the result, stores it as Parquet and
serves it through DuckDB.

---

## What you get

| | |
|---|---|
| **Bars** | OHLC, volume, the buy/sell volume split and the trade count of every minute of the chart |
| **Trades** | The individual trade records behind the bars: price, size, aggressor side, delta and the quotes of the moment |
| **True UTC timestamps** | Every row is a UTC instant, so the data joins cleanly with Databento, crypto or equity feeds |
| **Exchange trading day** | The session key on every row, correct across daylight saving transitions |
| **Provenance** | A metadata sidecar per export recording the settings that produced it |
| **Validation** | Every file is checked against the market structure before it enters the lake |
| **A queryable lake** | Partitioned Parquet with a DuckDB catalogue: sessions, gaps, instruments, calendar and roll spreads |

Three design decisions shape everything else:

1. **Raw and adjusted stay separate.** The trade file always contains raw traded prices. The bar
   file follows the chart's back adjustment. A continuous series is derived at load time, never
   baked into the source.
2. **The session key is computed, not assumed.** The CME session opens at 18:00 and closes at 17:00
   Eastern, so the evening bars belong to the *next* trading day — and that rule shifts with
   daylight saving time.
3. **Nothing is filled in.** A missing minute stays missing and is reported. Inventing a bar for a
   hole in the feed is how a backtest ends up trading on data that never existed.

---

## Install

1.  Copy `MDTGrep.cpp` to `C:\SierraChart\ACS_Source\`.
2.  In Sierra Chart: `Analysis` → `Build Custom Studies DLL` → check `MDTGrep.cpp` → **Build Remote**.
3.  Restart Sierra Chart, then add the study to a chart.

**Build Remote** compiles against the header of the Sierra Chart build you are running. A local
build uses whatever `ACS_Source\sierrachart.h` happens to be, and Sierra Chart rejects a DLL whose
ACSIL interface version does not match its own — silently, and without a compile error. MDTGrep
reports the interface version it was built with on every export and warns at compile time when the
header is too old.

---

## Quick start

### 1. Chart settings

`Chart` → `Chart Settings`:

| Setting | Where | Value |
|---------|-------|-------|
| Bar Period | `Intraday Chart Bar Period` | `Days-Mins-Secs-Milliseconds Per Bar` = **1 minute** |
| Time Zone | `Advanced Settings` | **UTC** |
| Session Times | `Session Times` | Start **00:00:00**, End **23:59:59** |
| Date range | `Use Date Range` | From = contract open **−1 day**, To = contract close **+1 day** |

The 24 hour session is deliberate: session times are wall clock times *in the chart's time zone*,
and a fixed UTC session cannot follow the exchange across a daylight saving change. Nothing is
ever outside a 24 hour session, so no bar can be dropped or shifted in either half of the year.

The date range is a day wider on each side so the first and the last session of the contract are
charted completely — a futures contract's data starts in the evening, not at midnight.

The bar period is **1 minute for both exports**. The trade export reads the intraday data files
directly, so the bar period controls only how those records are iterated.

### 2. Study settings

`Studies` → `MDT Grep (CSV Exporter)` → `Study Settings`:

| # | Input | Value |
|---|-------|-------|
| 0 | Trigger Export on Full Recalc | Yes |
| 1 | Export File Suffix | `_bars` |
| 2 | Export Mode | `Bars` first, then `Ticks` |
| 3 | Output Directory | `C:\SierraChart\SCAssistData\` |
| 4 | Timestamp Basis | **UTC** |
| 5 | Max Bars For Tick Export | `1400` (one session) for the first trade run |

### 3. Export

`Edit` → `Recalculate` on the chart. The log confirms the result:

```
MDTGrep: ACSIL interface 2969 - exported 90619 bars to C:\SierraChart\SCAssistData\ESZ16_FUT_CME_bars.csv (UTC)
```

### 4. Validate and load

```bash
python tools/validate_export.py market_data/bars_1m/ESZ16_FUT_CME_bars.csv
python tools/build_parquet.py --source market_data --out data/parquet --reports data/reports
python tools/init_duckdb.py --parquet data/parquet --tables data/tables --db data/market.duckdb
```

### 5. Query

```sql
-- duckdb data/market.duckdb
SELECT symbol, trading_day, bars, volume, close
FROM bars_1m_by_day
WHERE symbol = 'ESZ16_FUT_CME' AND trading_day BETWEEN '2016-12-01' AND '2016-12-31';

SELECT * FROM data_gaps;
```

---

## Output

### Bars — `<SYMBOL><suffix>.csv`

```
unix_ts_ms,trading_day,open,high,low,close,volume,ask_vol,bid_vol,num_trades,open_interest
1795278600000,2026-11-23,5321.00,5321.75,5321.00,5321.50,154,82,72,98,0
```

| Column | |
|---|---|
| `unix_ts_ms` | UTC epoch milliseconds, start of the bar |
| `trading_day` | The exchange trading day (`YYYY-MM-DD`). The CME session opens at 18:00 Eastern, so the evening bars carry the next day's label |
| `open` `high` `low` `close` | Prices, following the chart's back adjustment settings |
| `volume` | Contracts traded |
| `ask_vol` | Contracts bought at the ask (aggressive buys) |
| `bid_vol` | Contracts sold at the bid (aggressive sells) |
| `num_trades` | Individual trades in the bar |
| `open_interest` | Always `0` for intraday charts — Sierra Chart only provides open interest for daily and higher timeframes, and returns the trade count there instead |

### Trades — `<SYMBOL><suffix>_ticks.csv`

```
unix_ts_ms,trading_day,price,volume,ask_vol,bid_vol,delta,num_trades,side,bid,ask
1795278602150,2026-11-23,5321.25,2,2,0,2,1,B,5321.00,5321.25
```

| Column | |
|---|---|
| `unix_ts_ms` | UTC epoch milliseconds of the trade |
| `trading_day` | The exchange trading day of the trade |
| `price` | The traded price, raw |
| `volume` | The traded size |
| `ask_vol` `bid_vol` | The buy and sell aggressor volume |
| `delta` | `ask_vol - bid_vol` |
| `num_trades` | Trades in the record — `1` on a 1 Tick record, more when the record aggregates |
| `side` | `B`, `S` or `N`. A trade at or above the ask is a buy, at or below the bid a sell; the exchange rule when the record carries the quotes, otherwise the volume split |
| `bid` `ask` | The quotes at the moment of the trade, `0` when the feed did not supply them |

### Metadata sidecar — `<file>_meta.csv`

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

The chart's time zone and session times are invisible in the data itself, so the sidecar records
them when the file is written — that is what makes a collection of dozens of contracts auditable
after the fact. `timestamps_are_utc` is the assertion the pipeline makes, and `is_range_bars`
flags a chart that was set to a price-movement bar period, which does not produce time based bars.

---

## The data lake

The exports are the immutable landing zone; everything else is derived from them.

```
Sierra Chart ──▶ CSV landing zone ──▶ validated Parquet ──▶ DuckDB
                  (never edited)        (partitioned)        (queried in place)
```

```bash
# ingest: CSV -> validated Parquet, idempotent
python tools/build_parquet.py --source market_data --out data/parquet --reports data/reports

# catalogue: sessions, gaps, instruments, calendar, and the views over the lake
python tools/init_duckdb.py --parquet data/parquet --tables data/tables \
    --reports data/reports --db data/market.duckdb
```

**The ingest** reads every export, normalises the schema (deriving `trading_day` for exports made
before the column existed), validates the invariants, derives the session and gap tables and
writes partitioned Parquet (`symbol=/year=`, zstd, 500k row groups). It is idempotent — a source
file is fingerprinted and skipped when it has not changed — and when the same series exists more
than once, the newest export wins.

**The catalogue** holds what the rows cannot say:

| Table | |
|---|---|
| `instruments` | Contract specifications: expiry, first notice, multiplier, tick size, currency |
| `sessions` | One row per symbol and trading day: open, close, bars, volume, session kind |
| `data_gaps` | The holes in the feed, with the minutes missing |
| `rolls` | The calendar spread between two contracts, for building a continuous series |
| `exports` | Provenance: every file, its settings, its row count and its export time |
| `trading_calendar` | The CME holiday and early close calendar |
| `file_reports` | The validation result of every file |

| View | |
|---|---|
| `bars_1m` / `trades` | Every row |
| `bars_1m_by_day` / `trades_by_day` | One row per symbol and trading day |

The bulk data is never copied into the database — DuckDB reads the Parquet files directly, with
column and row group pruning.

---

## Data quality

The ingest enforces the market structure, and every file gets a JSON report in `data/reports/`.
The build exits non-zero when a rule fails.

| Rule | |
|---|---|
| Timestamps strictly increasing, no duplicates | enforced |
| No trading day on a weekend | enforced |
| Every session opens at 18:00 Eastern | enforced |
| Day to day gaps of 1, 3 (weekend) or 4 (holiday) | enforced |
| `high >= max(open, close)`, `low <= min(open, close)`, `high >= low` | enforced |
| `ask_vol + bid_vol == volume` | enforced above 0.1% of rows |
| `num_trades <= volume` | enforced |
| A session shorter than the median by more than 60 minutes | a gap, unless the CME calendar explains it as an early close |
| A weekday with no session at all | reported |

**The calendar** (`tools/cme_calendar.py`) implements the CME equity index holiday rules, derived
from the data itself: New Year's Day, Good Friday and Christmas are full closures; every other
federal holiday and the eves of Christmas and New Year are early closes at 12:00 CT (13:00
Eastern). A session the calendar cannot explain is reported as a gap rather than accepted.

**Verified on the reference dataset** — `ESZ16`, `ESH17` and `ESM17`, 3 contracts, 262,552 bars,
September 2016 to June 2017:

*   Every bar reproduced exactly, with zero value differences across every column.
*   Timestamps in true UTC, session structure correct across the November 2016 and March 2017
    daylight saving transitions.
*   0 OHLC violations, 100% of prices on the 0.25 tick, and `ask_vol + bid_vol == volume` on
    every bar but one.
*   Every gap accounted for: the CME daily index halt, the maintenance break, the holiday early
    closes and the weekends.
*   Session closes tracking the front month contract to within ~1 point on average against an
    independent market data source.

**One known gap in the feed:** ESM17 is missing the first 71 minutes of the 2017-06-04 session
(18:00–19:10 ET). It is a gap in Sierra Chart's data and is present in every export of that
contract. Treat that window as no-data, not as a flat market.

---

## Compared with Databento

With Sierra's Teton service (CME MDP 3.0) and Databento's `trades` schema, both are the same
exchange feed, and prices and sizes should line up trade for trade. What differs:

| | Databento | MDTGrep | Impact |
|---|---|---|---|
| Timestamp | UTC, nanoseconds | UTC, milliseconds | Sub-millisecond ordering is lost; the file order preserves the sequence |
| Price | raw traded price | raw traded price | none — the **bar** file is chart back adjusted, the trade file is not |
| Size | integer | integer | none |
| Side | exchange reported | derived from the quotes, otherwise the volume split | Mid-tick trades can be `N` here and `B`/`S` there |
| Cancels / corrections | not in the `trades` schema (`action` is always `T`); they live in `mbo` | not provided | A strategy that reacts to cancellations needs Databento's MBO schema, which this pipeline does not provide |
| Book depth (MBP-1 / MBO) | up to 10 price levels | not available | Depth based strategies cannot be reproduced from this feed |
| Aggregation | one message per trade | one record per trade, when the storage unit is 1 Tick | `num_trades` reports it per row |

`tools/fetch_databento.py` fetches the same session from Databento and runs the comparison in one
command. It derives the CME session window from the trading day, so the range covers the session
from the previous evening's open at 18:00 Eastern to the close at 17:00 Eastern, following
daylight saving time:

```bash
export DATABENTO_API_KEY=db-XXXXXXXX

python tools/fetch_databento.py --symbol ESH6 --session-date 2026-11-18 \
    --compare market_data/bars_1m/ESH6_bars_ticks.csv
```

`tools/verify_vs_databento.py` measures the difference rather than assuming it, and works with any
Databento file:

```bash
python tools/verify_vs_databento.py \
    --sierra  market_data/bars_1m/ESZ16_FUT_CME_bars_ticks.csv \
    --databento esz16.parquet
```

It reports the trade count difference, the timestamp alignment within a tolerance, the price and
size agreement, the side agreement with a confusion matrix and examples of unmatched trades on
both sides. Databento's side is named after the passive side of the book (`A` = sell aggressor,
`B` = buy aggressor, `N` = unspecified) and is translated to the MDTGrep convention before the
comparison.

**Compare a recent period.** Databento's CME data before 2017-05-21 comes from the legacy
aggregated FIX protocol, where the aggressor side is frequently missing, and the historical
dataset is normalised retroactively by the vendor. A current session gives a meaningful side
comparison; an old one mostly measures the vendor's legacy coverage.

Acceptance criteria for a trade level export:

| Metric | Target |
|---|---|
| Price match (exact) | ≥ 99.5% |
| Volume match | ≥ 99% |
| Timestamps matched within ±1 ms | ≥ 99.9% |
| Unmatched trades | < 0.5% |
| Side agreement (`B`/`S`) | ≥ 95% |

---

## Collecting a long history

| Horizon | 1 minute bars | Trade records | CSV landing zone | Parquet |
|---|---|---|---|---|
| 10 years | 3.4M | ~900M | 63 GB | ~9 GB |
| 30 years | 10.4M | ~2.7B | 190 GB | ~27 GB |

Bars are cheap. Trade records are the expensive part — in Sierra Chart's data files, in the export
time and in the landing zone. The practical split is **1 minute bars for the full history** and
**trade records for the window the strategy research actually needs**.

Collect the most recent decade first and walk backwards: tick level history is most likely to
exist for the recent past, a schema change is cheapest to make early, and the recent data is the
easiest to validate against another vendor.

Never edit the exported CSVs. They are the reproducible landing zone.

---

## Configuration reference

### Study inputs

| # | Input | Default | |
|---|---|---|---|
| 0 | Trigger Export on Full Recalc | Yes | Export on every full recalculation of the chart |
| 1 | Export File Suffix | `_bars` | Suffix of the output file |
| 2 | Export Mode | Bars | `Bars`, `Ticks` or `Both` |
| 3 | Output Directory | `C:\SierraChart\SCAssistData\` | Created if it does not exist |
| 4 | Timestamp Basis | Chart Local (as-is) | `UTC` for a direct comparison with Databento |
| 5 | Max Bars For Tick Export | 0 (all) | Limits how many chart bars the trade export reads |

A change to any of them re-runs the export immediately.

### Trade level data

The trade export reads the intraday data files of the chart, one record at a time. It needs an
intraday chart and `Global Settings` → `Data/Trade Service Settings` → `Common Settings` →
**Intraday Data Storage Time Unit** = **1 Tick**, followed by `Edit` → `Delete and Download Data`
on an intraday chart per symbol — the setting only affects data collected or downloaded
afterwards.

Reading every trade of a long history makes the Sierra Chart user interface freeze; `Max Bars For
Tick Export` bounds it.

The `num_trades` column reports the storage granularity of the data itself:

| `num_trades` | |
|---|---|
| `1` on nearly every row | one record per trade — trade level data |
| more than 1 | the records aggregate trades — the storage unit is coarser for that data |

The **Time and Sales** window shows the same thing without an export: one line per trade means 1
Tick storage.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| The build reports no errors and no CSV appears | The DLL was built against an outdated `sierrachart.h` and Sierra Chart rejected it at runtime | Build **Remote**, restart Sierra Chart, re-add the study. The `MDTGrep: ACSIL interface ...` log line is the confirmation that it ran |
| The study settings show only two inputs | The previous DLL is still loaded | Rebuild, restart, re-add the study — it has six inputs |
| Timestamps are 4–5 hours behind UTC | The chart is on Eastern and `Timestamp Basis` is `Chart Local` | Set `Timestamp Basis` to `UTC`, or set the chart time zone to UTC |
| The first hour of the summer sessions is missing or shifted | Fixed UTC session times against an exchange daylight saving change | Use the 24 hour session, or Eastern with `Timestamp Basis = UTC` |
| The first session of the file is missing | The chart's first day is the day the data starts on | Extend the date range one day earlier |
| Bars have ragged durations and ~160 trades each | The bar period is `Range Per Bar`, a price movement bar | Use `Days-Mins-Secs-Milliseconds Per Bar` = 1 minute |
| Trade rows contain many trades | The intraday storage is coarser than 1 Tick | Set the storage unit to 1 Tick and re-download the intraday data |
| The export is very slow | Every trade of a long history is being read | Use `Max Bars For Tick Export`, or export day by day |

Log messages that explain an empty or partial result:

| Message | Meaning |
|---|---|
| `MDTGrep: waiting for the historical data download to complete...` | The export starts once the download finishes |
| `MDTGrep: no chart data loaded, nothing to export.` | No data on the chart |
| `MDTGrep: no intraday trade records were found.` | A daily chart, or the storage unit is not 1 Tick |
| `MDTGrep: unable to create the output directory: ...` | The directory could not be created |
| `MDTGrep: the timestamps of ... are NOT true UTC.` | The chart time zone is not UTC and `Timestamp Basis` is `Chart Local` |

---

## Reference

| | |
|---|---|
| [`docs/SPEC.md`](docs/SPEC.md) | The technical specification: data model, time model, calendar model, storage layout, pipeline contracts, validation rules and the backtest caveats |
| [`PLAN.md`](PLAN.md) | Requirements and roadmap |
| [`market_data/`](market_data/) | The exports, with the naming conventions and the known gap |
| [`tools/`](tools/) | The pipeline and the verification tools — see [tools/README.md](tools/README.md) |

| Tool | |
|---|---|
| `build_parquet.py` | The ingest: CSV → validated Parquet |
| `init_duckdb.py` | The catalogue and the views over the lake |
| `validate_export.py` | Structural validation of an export |
| `fetch_databento.py` | Fetch a Databento session and compare it |
| `verify_vs_databento.py` | Trade level comparison against another vendor |
| `fix_prev_version_data.py` | Repair of exports made with the older study |
| `cme_calendar.py` | The CME equity index holiday calendar |

**Roadmap**

*   Incremental collection, so a long history can be built session by session.
*   More instruments in the contract catalogue.
*   A continuous-series builder driven by the `rolls` table, for price based strategies.

# MDTGrep data lake — technical specification

Version 1.0 · applies to the schema written by `MDTGrep.cpp` and the pipeline in `tools/`.

---

## 1. Purpose and scope

MDTGrep turns the data of a Sierra Chart chart into an analytical dataset for
backtesting and algorithmic strategy research.

The scope is deliberately narrow:

*   **In scope** — OHLCV bars with a buy/sell volume split, and the underlying intraday
    trade records, for the CME equity index complex and other instruments Sierra Chart can
    serve.
*   **Out of scope** — order book depth, trade cancellations and corrections. Sierra Chart's
    data service does not provide them, so a Databento MBP-1/MBO strategy cannot be
    reproduced from this pipeline.
*   **Out of scope** — the exchange's own settlement prices. The pipeline produces traded
    prices only.

---

## 2. Source: the ACSIL study

`MDTGrep.cpp` is an ACSIL study that is added to a chart. It writes two artefacts per export:

| Artefact | Content |
|----------|---------|
| `<SYMBOL><suffix>.csv` | the data |
| `<SYMBOL><suffix>.csv_meta.csv` | the metadata sidecar (`key,value` rows) |

The sidecar records the settings that produced the file, because the chart time zone and the
session times are **not visible in the data itself**:

```
symbol, bar_period_type, bar_period_parameter1, is_range_bars,
export_mode, timestamp_basis, chart_time_zone_is_utc, timestamps_are_utc,
acsil_interface_version, rows, first_ts_ms, last_ts_ms,
first_trading_day, last_trading_day, exported_at_utc
```

`timestamps_are_utc` is the assertion the pipeline makes: it is 1 when the timestamps in the file
are true UTC instants. `is_range_bars` is 1 when the chart used a price-movement bar period,
which does **not** produce time based bars.

The study writes the data with the timestamp basis the user selected:

| `timestamp_basis` | Bars | Ticks |
|---|---|---|
| `Chart Local` | the chart time zone, as-is | the record time converted to the chart time zone |
| `UTC` | the chart time zone converted to UTC | the record time, which is already UTC |

With the chart on UTC both settings give the same result — true UTC.

---

## 3. Data model

### 3.1 Bars — `bars_1m`

```
unix_ts_ms      int64    epoch milliseconds, UTC, start of the bar
trading_day     date     the exchange trading day the bar belongs to
open            double
high            double
low             double
close           double
volume          int64    contracts traded in the bar
ask_vol         int64    contracts bought at the ask (aggressive buys)
bid_vol         int64    contracts sold at the bid (aggressive sells)
num_trades      int64    individual trades in the bar
open_interest   int64    always 0 for intraday (see 3.4)
```

### 3.2 Trades — `trades`

```
unix_ts_ms      int64    epoch milliseconds, UTC, time of the trade
trading_day     date     the exchange trading day the trade belongs to
price           double   the traded price
volume          int64    the traded size
ask_vol         int64    the buy aggressor volume
bid_vol         int64    the sell aggressor volume
delta           int64    ask_vol - bid_vol
num_trades      int64    trades in the record (1 for a 1 Tick record)
side            string   B, S or N
bid             double   bid price at the moment of the trade, 0 when not supplied
ask             double   ask price at the moment of the trade, 0 when not supplied
```

### 3.3 Semantics that are easy to get wrong

*   **Back adjustment** — the *bar* file follows the chart's back adjustment settings, so the
    prices of a deferred contract can be shifted. The *trade* file always contains the raw
    traded price. A continuous series must be built at load time from the raw contracts
    (section 8), never by exporting adjusted charts.
*   **The buy/sell split** — `ask_vol` and `bid_vol` are Sierra Chart's classification of the
    trades, not the exchange reported aggressor side. A trade that happened between the bid
    and the ask is classified by the uptick/downtick rule. The `side` column uses the exchange
    rule (a trade at or above the ask is a buy) when the record carries the quotes, and falls
    back to the volume split otherwise.
*   **The bar timestamp** is the *start* of the bar. The session open bar is labelled with the
    session open, so a bar at 18:00 Eastern covers 18:00–18:00:59.
*   **A trade is not a bar** — a 1 minute bar aggregates every trade of that minute. The
    intrabar path is unknown, which is the main limit for backtesting (section 10).

### 3.4 `open_interest`

Sierra Chart only provides open interest for daily and higher timeframe charts. For intraday
charts the array returns the **trade count**, so MDTGrep writes `0` instead. The column is kept
because the study is generic and a daily chart export would carry the real value.

---

## 4. Time model

### 4.1 Two clocks

| Clock | Where it comes from |
|-------|--------------------|
| UTC instant | the Unix epoch milliseconds of the row |
| Exchange trading day | the CME session label of the row |

### 4.2 The trading day rule

The CME equity index session opens at **18:00** and closes at **17:00 Eastern**, so the
evening bars carry the *next* day's label:

```
trading_day = eastern_date + 1 day   if eastern_hour >= 18
            = eastern_date             otherwise
```

The conversion to Eastern uses the daylight saving rules of the chart, so the label is correct
across the transitions — a session that opens at 18:00 EST is labelled with the next day, and a
session that opens at 18:00 EDT with the day after that.

The rule is implemented twice and must agree:

*   `MDTGrep.cpp` — `GetTradingDayInfo()`, so every new export carries the column.
*   `tools/build_parquet.py` — `derive_trading_day()`, so exports made before the column
    existed can be brought up to date.

### 4.3 Verified behaviour

For the three contracts in `market_data/bars_1m`:

| | trading days | first trading day | CME contract start | weekend days | session opens |
|---|---|---|---|---|---|
| ESZ16 | 68 | 2016-09-12 | 2016-09-12 | none | all 18:00 ET |
| ESH17 | 66 | 2016-12-12 | 2016-12-12 | none | all 18:00 ET |
| ESM17 | 67 | 2017-03-13 | 2017-03-13 | none | all 18:00 ET (one session at 19:11, the known gap) |

---

## 5. Calendar model

`tools/cme_calendar.py` implements the CME equity index holiday rules:

*   **Full closures** — New Year's Day, Good Friday, Christmas.
*   **Early closes at 12:00 CT (13:00 Eastern)** — Martin Luther King Jr. Day, Presidents Day,
    Memorial Day, Juneteenth, Independence Day, Labor Day, Thanksgiving, the Friday after
    Thanksgiving, Christmas Eve, New Year's Eve and July 3rd. When the nominal date falls on a
    weekend the early close moves to the preceding business day.

These rules were **derived from the exported data**, not assumed: every full closure and every
early close in the three sample contracts matches the rules, and the sessions that the rules
cannot explain are reported as gaps.

---

## 6. Storage

### 6.1 Parquet lake

```
data/parquet/
    bars_1m/symbol=<SYMBOL>/year=<YYYY>/part-*.parquet
    trades/symbol=<SYMBOL>/year=<YYYY>/part-*.parquet
    _state.json                     the ingest state
data/tables/                        the derived catalogue tables (CSV)
data/reports/                       one validation report per source file
data/market.duckdb                 the catalogue database
```

| Setting | Value | Why |
|---------|--------|-----|
| Partitioning | `symbol`, `year` (of the timestamp, UTC) | a query for one contract and one year reads one directory |
| Row groups | max 500,000 rows | parallel reads without reading the whole file |
| File size | max 5,000,000 rows | keeps the file count manageable |
| Compression | zstd | the price columns are highly repetitive |
| Encoding | dictionary for `side`, plain for the numbers | |

The bulk data is never loaded into the database — DuckDB reads the Parquet files directly, with
column pruning and row group pruning.

### 6.2 Why Parquet and not a row store

| Horizon | 1 minute bars | Trades | CSV | Parquet |
|---------|--------------|--------|-----|---------|
| 10 years | 3.4M | ~900M | 63 GB | ~9 GB |
| 30 years | 10.4M | ~2.7B | 190 GB | ~27 GB |

DuckDB streams 2.7B rows from Parquet without loading them into memory. The same data in SQLite
would be painful to query and would have to be imported first, which doubles the storage.

### 6.3 DuckDB catalogue

```sql
bars_1m(trading_day, ts_ms, open, high, low, close, volume, ask_vol, bid_vol, num_trades, open_interest)
trades(trading_day, ts_ms, price, volume, ask_vol, bid_vol, delta, num_trades, side, bid, ask)

instruments(instrument, symbol, exchange, asset_class, expiry_date,
            first_notice_date, multiplier, tick_size, currency)
sessions(symbol, trading_day, open_ts, close_ts, bars, volume, span_minutes, kind)
data_gaps(symbol, trading_day, missing_minutes, kind)
rolls(instrument_from, instrument_to, roll_date, ratio, difference)
exports(file, symbol, kind, rows, first_ts_ms, last_ts_ms, first_trading_day, last_trading_day,
        acsil_interface_version, timestamp_basis, timestamps_are_utc,
        bar_period_type, is_range_bars, exported_at_utc, ok)
trading_calendar(calendar_date, weekday, kind, early_close_et)
file_reports(source_file, symbol, kind, ok, failures, checked_at, checks)
```

Views over the lake:

| View | Content |
|------|---------|
| `bars_1m` | every bar |
| `trades` | every trade record |
| `bars_1m_by_day` | one row per symbol and trading day |
| `trades_by_day` | one row per symbol and trading day |

`sessions.kind` is `regular`, `early_close`, `partial` (the first or last session of a file, cut
by the chart date range) or `gap`.

---

## 7. Pipeline

```
Sierra Chart
    │  MDTGrep writes <symbol><suffix>.csv + <symbol><suffix>.csv_meta.csv
    ▼
CSV landing zone            immutable, never edited in place
    │  tools/build_parquet.py
    │    1  read the CSV and the sidecar
    │    2  normalise the schema (derive trading_day when it is missing)
    │    3  validate the invariants
    │    4  derive the session and gap tables
    │    5  write partitioned Parquet
    ▼
Parquet lake
    │  tools/init_duckdb.py
    │    1  create the catalogue tables
    │    2  load the derived tables
    │    3  generate the trading calendar
    │    4  create the views over the lake
    ▼
DuckDB  →  backtests and feature generation
```

### 7.1 Ingest rules

*   **Idempotent.** Every source file is fingerprinted with SHA-256. A file that has not changed
    since the last run is skipped unless `--force` is given.
*   **One series per export.** The series id is the file stem (`<SYMBOL><suffix>`). When the
    same series exists more than once — after a re-export — the newest one wins, so the lake
    never holds duplicates.
*   **The sidecar is authoritative** for the symbol, the export mode and the settings.
*   **Nothing is edited in place.** The CSV is the landing zone; the Parquet is derived.

### 7.2 Validation rules and gates

| Rule | Severity |
|------|----------|
| the timestamps are strictly increasing | fail |
| no duplicate timestamps | fail |
| no trading day on a weekend | fail |
| every session opens at 18:00 Eastern | fail |
| the day to day gaps are 1, 3 (weekend) or 4 (holiday) | fail |
| `high >= max(open, close)` and `low <= min(open, close)` and `high >= low` | fail |
| `ask_vol + bid_vol == volume` | fail above 0.1% of the rows |
| `num_trades <= volume` | fail |
| a session shorter than the median by more than 60 minutes | fail, unless the CME calendar
explains it as an early close |
| a weekday with no session at all | reported |

Every file gets a JSON report in `data/reports/`, and the build exits non-zero when a rule
fails.

---

## 8. Derived data

### 8.1 The continuous series

The quarterly contracts are separate instruments and their prices are not continuous — the
calendar spread between them is real and changes over time. For a price based strategy the
series has to be adjusted, and that adjustment is a **derived** artefact:

*   The raw contracts are the source of truth and are never modified.
*   The `rolls` table holds the transition between two contracts with the ratio and the
    difference that makes the series continuous.
*   The adjustment can be validated against the data: the measured spreads of the sample are
    **ESZ16 → ESH17 = +5.50 points** and **ESH17 → ESM17 = +3.20 points**, constant to within a
    point over the whole overlap, which is the signature of a real calendar spread rather than
    an artefact.
*   The `instruments` table holds the expiry dates, so the roll schedule can be derived rather
    than hard coded.

### 8.2 What is not derived

The pipeline does not resample, interpolate or fill. A missing minute stays missing and is
reported in `data_gaps`. Filling a gap invents data that a backtest would then trade on.

---

## 9. The exchange contract table

`tools/instruments.csv` holds the contract specifications and is seeded into the `instruments`
table. One row per contract:

```csv
instrument,symbol,exchange,asset_class,expiry_date,first_notice_date,multiplier,tick_size,currency
ESZ16,ESZ16_FUT_CME,CME,FUT,2016-12-16,2016-11-17,50,0.25,USD
```

---

## 10. Backtest caveats

These follow from the data model and have to be respected by any strategy built on it.

1. **The intrabar path is unknown.** A 1 minute bar gives the high, the low and the close, but
   not the order. A stop that is hit inside a minute is only known to have been hit, not when.
   Use a conservative fill: a stop fills at the stop price, not at the bar's close.
2. **The buy/sell split is derived, not exchange reported.** Sierra Chart classifies a trade
   between the bid and the ask by the uptick/downtick rule. A strategy that depends on the
   exchange's aggressor side will not match Databento exactly on mid-tick trades.
3. **Milliseconds, not nanoseconds.** Sierra Chart's timestamps are millisecond resolution. Two
   trades in the same millisecond cannot be separated by time — the file order is the only
   sequence information.
4. **The data has gaps.** A gap is not a flat market. Join `data_gaps` and skip those sessions
   or handle them explicitly.
5. **Trade for trade equality with another vendor is a goal, not a guarantee.** Compare with
   `tools/verify_vs_databento.py` and accept the measured difference rather than assuming it.

---

## 11. Scalability

| Stage | Limit | Note |
|-------|-------|------|
| Sierra Chart export | the binding constraint | reading every tick of a long history freezes the user interface; use `Max Bars For Tick Export` |
| CSV landing zone | 190 GB for 30 years of trades | keep it on a fast disk, it is the reproducible source |
| Parquet lake | ~27 GB for 30 years of trades | the working copy |
| DuckDB | streams the Parquet files | a full scan of 2.7B rows is minutes, not hours |

The realistic plan is 1 minute bars for the whole history and trades for the window the
strategy research actually needs.

---

## 12. Operations

```bash
# 1  ingest, idempotent
python tools/build_parquet.py --source market_data --out data/parquet --reports data/reports

# 2  catalogue
python tools/init_duckdb.py --parquet data/parquet --tables data/tables \
    --reports data/reports --db data/market.duckdb

# 3  check a single export
python tools/validate_export.py market_data/bars_1m/ESZ16_FUT_CME_bars.csv

# 4  compare a tick export against another vendor
python tools/verify_vs_databento.py \
    --sierra market_data/bars_1m/ESZ16_FUT_CME_bars_ticks.csv \
    --databento esz16.parquet
```

Then:

```sql
-- duckdb data/market.duckdb
SELECT symbol, trading_day, bars, volume, close FROM bars_1m_by_day
WHERE symbol = 'ESZ16_FUT_CME' AND trading_day BETWEEN '2016-12-01' AND '2016-12-31';

SELECT * FROM data_gaps;
```

---

## 13. Known limitations

*   **No depth, no cancels, no corrections** — the data service does not provide them.
*   **The tick granularity depends on the vendor.** Sierra Chart's *Intraday Data Storage Time
    Unit* controls how the data is written, and historical data from the vendor is often
    coarser than the unit suggests. The `num_trades` column of the trade file reports it: `1` on
    every row means one record per trade, anything else means the record aggregates several.
*   **The session edges of a file are cut by the chart date range.** The first and the last
    trading day of an export are `partial` sessions.
*   **One known gap in the sample**: ESM17 is missing the first 71 minutes of the 2017-06-04
    session. It is a gap in Sierra Chart's data and is present in every export of that contract.

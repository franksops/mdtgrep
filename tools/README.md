# The tools

The pipeline and the verification tools. Python 3.10+ with:

```bash
pip install pandas pyarrow duckdb
```

`databento` is only needed for `fetch_databento.py`.

---

## The pipeline

### `build_parquet.py` — the ingest

```bash
python tools/build_parquet.py --source market_data --out data/parquet --reports data/reports
```

| | |
|---|---|
| Reads | every `*.csv` under `--source` that is not a `_meta.csv` sidecar |
| Normalises | the schema, deriving `trading_day` for exports made before the column existed |
| Validates | the invariants in `docs/SPEC.md` — the build exits non-zero when a rule fails |
| Derives | the session table and the gap table, using the CME calendar to tell an early close from a real gap |
| Writes | partitioned Parquet: `bars_1m/symbol=…/year=…` and `trades/symbol=…/year=…` |
| Reports | one JSON per source file in `--reports` |

Idempotent: every source file is fingerprinted with SHA-256 and skipped when it has not changed
since the last run. Use `--force` to reprocess everything. When the same series exists more than
once — after a re-export — the newest one wins, so the lake never holds duplicates.

Useful flags: `--kind bars|ticks|auto`, `--max-rows-per-file`, `--max-rows-per-group`.

### `init_duckdb.py` — the catalogue

```bash
python tools/init_duckdb.py --parquet data/parquet --tables data/tables \
    --reports data/reports --db data/market.duckdb
```

Creates the catalogue tables (`instruments`, `sessions`, `data_gaps`, `rolls`, `exports`,
`trading_calendar`, `file_reports`), loads the derived tables and creates the views over the
Parquet lake (`bars_1m`, `trades`, `bars_1m_by_day`, `trades_by_day`). The bulk data is never
copied into the database.

The contract specifications are seeded from `tools/instruments.csv`; the trading calendar is
generated from `cme_calendar.py`.

---

## Verification

### `validate_export.py` — structural checks on any export

```bash
python tools/validate_export.py market_data/bars_1m/ESZ16_FUT_CME_bars.csv
python tools/validate_export.py market_data/bars_1m/ESZ16_FUT_CME_bars_ticks.csv --kind ticks
```

Checks the timestamp monotonicity, duplicates and spacing, the session structure and the time
base, the trading day column, the OHLC integrity and the price tick grid,
`ask_vol + bid_vol == volume`, the trade counts and whether `open_interest` is a duplicate of
`num_trades`.

### `fetch_databento.py` — fetch a Databento session and compare it

```bash
export DATABENTO_API_KEY=db-XXXXXXXX

python tools/fetch_databento.py --symbol ESH6 --session-date 2026-11-18 \
    --compare market_data/bars_1m/ESH6_bars_ticks.csv
```

Downloads the `trades` schema for one CME session — the window runs from the previous evening's
open at 18:00 Eastern to the close at 17:00 Eastern, following daylight saving time — and hands it
to the comparison tool.

### `verify_vs_databento.py` — trade level comparison

```bash
python tools/verify_vs_databento.py \
    --sierra  market_data/bars_1m/ESZ16_FUT_CME_bars_ticks.csv \
    --databento esz16.parquet \
    --price-tol 0.00001
```

Reports the trade count difference, the timestamp alignment within a tolerance, the price and
size agreement, the side agreement with a confusion matrix and examples of unmatched trades on
both sides.

Databento names the side after the passive side of the book (`A` = sell aggressor, `B` = buy
aggressor, `N` = unspecified) and MDTGrep after the aggressor (`S`, `B`, `N`). The side is
translated before the comparison and the confusion matrix is reported in the MDTGrep convention.

---

## Utilities

### `cme_calendar.py` — the CME holiday calendar

```bash
python tools/cme_calendar.py 2017
```

The holiday and early close rules of the CME equity index complex, derived from the exported data:
New Year's Day, Good Friday and Christmas are full closures; every other federal holiday and the
eves of Christmas and New Year are early closes at 12:00 CT (13:00 Eastern). Used by the ingest to
tell a scheduled early close apart from a real data gap.

### `fix_prev_version_data.py` — repair of exports made with the older study

```bash
python tools/fix_prev_version_data.py market_data/legacy_bars/*.csv --source-tz America/New_York
```

Exports made before the time base fix store the chart wall clock as if it was UTC — 4 to 5 hours
behind true UTC — and duplicate `num_trades` into `open_interest`. This converts the timestamps to
real UTC (daylight saving aware), drops the duplicated column and writes the counts as integers.

### `instruments.csv` — the contract specifications

```csv
instrument,symbol,exchange,asset_class,expiry_date,first_notice_date,multiplier,tick_size,currency
ESZ16,ESZ16_FUT_CME,CME,FUT,2016-12-16,2016-11-17,50,0.25,USD
```

Seeded into the `instruments` table and used to build the roll schedule.

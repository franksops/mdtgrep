# market_data

Exported CSV files from MDTGrep.

| File | Content |
|------|---------|
| `<SYMBOL><suffix>.csv` | Bar export — `unix_ts_ms,trading_day,open,high,low,close,volume,ask_vol,bid_vol,num_trades,open_interest` |
| `<SYMBOL><suffix>_ticks.csv` | Tick export — `unix_ts_ms,trading_day,price,volume,ask_vol,bid_vol,delta,num_trades,side,bid,ask` |
| `<file>_meta.csv` | Metadata sidecar — the settings that produced the file (symbol, export mode, timestamp basis, bar period, row count, first/last trading day, export time) |

The `<suffix>` is the study's *Export File Suffix* input, `_bars` by default, so the default names
are `ESZ16_FUT_CME_bars.csv` and `ESZ16_FUT_CME_bars_ticks.csv`.

## Conventions

*   Timestamps are **Unix epoch milliseconds in UTC**. The bar timestamp is the start of the bar,
    the tick timestamp is the time of the trade.
*   `trading_day` is the **exchange trading day** (`YYYY-MM-DD`). The CME session opens at 18:00
    and closes at 17:00 Eastern, so the evening bars carry the next day's label. It is the session
    key for grouping, joining and gap detection.
*   Prices are **raw traded prices**, not back adjusted. (The *bar* file follows the chart's back
    adjustment settings, the *tick* file does not.)
*   `volume`, `ask_vol`, `bid_vol` and `num_trades` are integers.
*   `open_interest` is `0` for intraday exports — Sierra Chart only provides real open interest
    for daily and higher timeframe charts.

## Before using a file

```bash
python tools/validate_export.py market_data/ESZ16_FUT_CME_bars.csv
```

and for a tick file, compare it against Databento:

```bash
python tools/verify_vs_databento.py \
    --sierra market_data/ESZ16_FUT_CME_bars_ticks.csv \
    --databento esz16.parquet
```

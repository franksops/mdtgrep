# market_data

Exported CSV files from MDTGrep.

| File | Content |
|------|---------|
| `<SYMBOL><suffix>.csv` | Bar export — `unix_ts_ms,open,high,low,close,volume,ask_vol,bid_vol,num_trades,open_interest` |
| `<SYMBOL><suffix>_ticks.csv` | Tick export — `unix_ts_ms,price,volume,ask_vol,bid_vol,delta,num_trades,side,bid,ask` |

The `<suffix>` is the study's *Export File Suffix* input, `_bars` by default, so the default names
are `ESZ16_FUT_CME_bars.csv` and `ESZ16_FUT_CME_bars_ticks.csv`.

## Conventions

*   Timestamps are **Unix epoch milliseconds in UTC**. The bar timestamp is the start of the bar,
    the tick timestamp is the time of the trade.
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

## Known gap

The intraday data of `ESM17` is missing the first 71 minutes of the 2017-06-04 session
(18:00–19:10 ET). This is a gap in Sierra Chart's data, not in the export — it is present in every
export of that contract. Treat that window as no-data rather than as a flat market.

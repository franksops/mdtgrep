# legacy_bars

The same three contracts as [`bars_1m`](../bars_1m), exported before the `trading_day` column
existed. They are kept as the reference for the migration and for the regression check between the
old and the current export.

| | `legacy_bars` | `bars_1m` |
|---|---|---|
| Schema | `unix_ts_ms,open,high,low,close,volume,ask_vol,bid_vol,num_trades,open_interest` | adds `trading_day` after `unix_ts_ms` |
| Timestamps | the chart wall clock stored as if it was UTC — **4 to 5 hours behind true UTC** | true UTC |
| `open_interest` | a duplicate of `num_trades` | `0` for intraday charts |
| Counts | `969.00000` | `969` |

Both sets hold the same values — verified bar for bar, with zero differences in every column.

The ingest handles them directly: `trading_day` is derived from the timestamps and the counts are
cast, so a legacy file can be loaded as-is. To convert them in place instead:

```bash
python tools/fix_prev_version_data.py market_data/legacy_bars/*.csv --source-tz America/New_York
```

When both sets are present, the ingest uses the newer export of each series — the export time in
the metadata sidecar decides, not the file modification time.

See the [README](../README.md) for the current setup and `docs/SPEC.md` for the data model.

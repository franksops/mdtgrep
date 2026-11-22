# Superseded versions

These are the earlier exporters, kept for reference. **Do not use them** — they have known
defects that the current `MDTGrep.cpp` fixes.

| File | What it was | What it got wrong |
|---|---|---|
| `ExportBarsToCSV_v1.cpp` | The first exporter: OHLCV bars to `<SYMBOL>_bars.csv` | No volume split, no trade count, no timestamps |
| `ExportBarsToCSV_v2.cpp` | Added a Unix timestamp and a bar period in the file name | No volume split, no trade count |
| `ExportBarsToCSV_v3.cpp` | Added the volume split, the trade count and open interest | `open_interest` duplicates `num_trades` on intraday charts, and the timestamp has no time zone |
| `ExportBarsToCSV_v4.cpp` | Added a tick export alongside the bar export | The tick export reads the intraday records without `sc.MaintainAdditionalChartDataArrays`, and it does not set the ACSIL interface version |

The current `MDTGrep.cpp` supersedes all four. It exports bars and trade records, puts both on
the same time base in true UTC, stamps every row with the exchange trading day, writes a metadata
sidecar with the settings that produced the file, and reports the ACSIL interface version so a
stale build is visible instead of silently rejected by Sierra Chart.

See the [README](../README.md) for the current setup and `docs/SPEC.md` for the data model.

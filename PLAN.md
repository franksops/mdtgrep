## REQUIREMENT

https://www.sierrachart.com/index.php?page=doc/AdvancedCustomStudyInterfaceAndLanguage.php

https://github.com/power-shift/sierrachart-acsil-docs

ACSIL study, add to chart, uses chart settings (bar period, range of time, or specific contract name) to download tick data for future analysis, strategy backtesting, algo-bot training.
 
(Time) Back-adjusted - Best for price-based strategies — eliminates roll gaps, levels are continuous
 
    Chart Settings → Bar Period
    1 minute / 60 secondS

    unix_ts, open, high, low, close, volume, ask_vol, bid_vol, num_trades, open_interest

(Tick)No adjustment- Best for volume/delta/profile work — actual traded prices preserved

    Chart Settings → Bar Period
    Number ofTrades Per Bar / 1

    unix_ts_ms, price, volume, ask_vol, bid_vol, delta, num_trades

## STATUS

**Implemented** — `MDTGrep.cpp`, see README.md for the full instructions.

*   Bar export — chart bars with a Unix epoch millisecond timestamp, the buy/sell volume split and
    the trade count.
*   Tick export — the underlying intraday trade records with price, volume, aggressor volume,
    delta, trade count, side and the bid/ask of the moment.
*   Timestamps in true UTC. Sierra Chart keeps chart bars in the chart time zone and intraday file
    records in UTC; both are converted so the two files always agree.
*   `open_interest` is only valid for daily and higher timeframe charts. Sierra returns the trade
    count in that array for intraday charts, so intraday exports write `0`.
*   Chart settings that produce a complete, DST correct export are documented in the README
    (1 minute bars, chart time zone UTC, 24 hour session, date range one day wider on each side).

**Verified** — the bar exports of `ESZ16`, `ESH17` and `ESM17` (262,552 bars, Sept 2016 – Jun 2017)
were checked bar for bar against the original export: zero value differences, zero OHLC violations,
the session structure and the market holidays/halt/maintenance breaks correct across both daylight
saving transitions, and the session closes tracking the front month contract to within ~1 point.

**Open**

*   Tick export vs Databento — the trade level export still has to be compared against a Databento
    `trades` file to establish the real match rate (`tools/verify_vs_databento.py`).
*   Storage granularity — the export is only trade level when the intraday data storage time unit
    is 1 Tick and the data has been re-downloaded after that setting was changed. The `num_trades`
    column of the tick file reports it.
*   Known source data gap — the intraday data of `ESM17` is missing the first 71 minutes of the
    2017-06-04 session. This is a gap in Sierra's data and is present in every export of that
    contract.

## FUTURE ENHANCEMENTS

* **Parquet Export**: Eventually transition from CSV to Apache Parquet format. Given that a few years of tick data can amount to 30+ GB, Parquet's columnar storage and efficient compression will significantly reduce file sizes and improve read performance for subsequent analysis and backtesting.
* **Incremental export**: Append session by session instead of re-exporting the whole history.

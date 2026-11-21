// ExportBarsToCSV.cpp
// ACSIL study — exports full bar data to CSV for backtesting and bot training.
// Data source agnostic — works with Teton, Rithmic, CQG, etc.
// Symbol, bar period, and date range set in Chart Settings.
//
// Output: <SierraChart>/Data/<SYMBOL>_<SecondsPerBar>s.csv
// e.g. @CL_60s.csv, @ES_60s.csv, @NQ_300s.csv
// Rewrites on every full recalculation.
//
// Schema:
//   unix_ts      — Unix epoch seconds (int64), UTC
//   open         — float
//   high         — float
//   low          — float
//   close        — float
//   volume       — total volume (int)
//   ask_vol      — volume traded at ask / buy aggressor (int)
//   bid_vol      — volume traded at bid / sell aggressor (int)
//   num_trades   — number of individual trades (int)
//   open_interest — open interest (int)
//
// Notes:
//   delta        = ask_vol - bid_vol (derive in Python, no need to store)
//   open_interest requires your data service to provide it (Teton does)
//   ask_vol/bid_vol require per-trade data — not available on daily bars

#include "SierraChartStudyInterfaceACSIL.h"

SCSFExport scsf_ExportBarsToCSV(SCStudyInterfaceRef sc)
{
    if (sc.SetDefaults)
    {
        sc.GraphName        = "Export Bars to CSV (Full)";
        sc.StudyDescription = "Exports full OHLCV + bid/ask vol + trades + OI per bar. UTC Unix timestamps.";
        sc.AutoLoop         = 0;
        sc.UpdateAlways     = 0;
        return;
    }

    if (!sc.IsFullRecalculation)
        return;

    if (sc.ArraySize == 0)
    {
        sc.AddMessageToLog("ExportBarsToCSV: No bars loaded.", 1);
        return;
    }

    // Filename: <Symbol>_<SecondsPerBar>s.csv  e.g. @CL_60s.csv
    SCString FilePath;
    FilePath.Format("%s%s_%ds.csv",
        sc.DataFilesFolder.GetChars(),
        sc.Symbol.GetChars(),
        sc.SecondsPerBar);

    FILE* f = fopen(FilePath.GetChars(), "w");
    if (!f)
    {
        SCString Err;
        Err.Format("ExportBarsToCSV: Cannot open file: %s", FilePath.GetChars());
        sc.AddMessageToLog(Err.GetChars(), 1);
        return;
    }

    fprintf(f, "unix_ts,open,high,low,close,volume,ask_vol,bid_vol,num_trades,open_interest\n");

    for (int i = 0; i < sc.ArraySize; i++)
    {
        int64_t ts = (int64_t)sc.BaseDateTimeIn[i].GetAsUnixTimeStamp();

        fprintf(f, "%lld,%.4f,%.4f,%.4f,%.4f,%.0f,%.0f,%.0f,%.0f,%.0f\n",
            ts,
            (double)sc.Open[i],
            (double)sc.High[i],
            (double)sc.Low[i],
            (double)sc.Close[i],
            (double)sc.Volume[i],
            (double)sc.AskVolume[i],
            (double)sc.BidVolume[i],
            (double)sc.NumberOfTrades[i],
            (double)sc.OpenInterest[i]);
    }

    fclose(f);

    SCString Msg;
    Msg.Format("ExportBarsToCSV: Wrote %d bars -> %s", sc.ArraySize, FilePath.GetChars());
    sc.AddMessageToLog(Msg.GetChars(), 0);
}

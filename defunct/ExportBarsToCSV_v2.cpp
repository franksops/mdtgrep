// ExportBarsToCSV.cpp
// ACSIL study — exports chart OHLCV bars to CSV for backtesting.
// Data source agnostic — reads whatever Sierra Chart has loaded (Teton, Rithmic, etc.)
// Symbol and date range are set in Chart Settings as normal.
// Output: <SierraChart>/Data/<SYMBOL>_<SecondsPerBar>s.csv  e.g. @CL_60s.csv
// Rewrites on every full recalculation (chart load, date range change, etc.)
//
// CSV schema (optimized for Parquet conversion):
//   unix_ts  — Unix epoch seconds (int64), no string parsing overhead
//   open, high, low, close — float (4 decimal places)
//   volume   — integer

#include "SierraChartStudyInterfaceACSIL.h"

SCSFExport scsf_ExportBarsToCSV(SCStudyInterfaceRef sc)
{
    if (sc.SetDefaults)
    {
        sc.GraphName        = "Export Bars to CSV";
        sc.StudyDescription = "Exports chart OHLCV bars to CSV. Unix timestamps for fast Parquet conversion.";
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

    fprintf(f, "unix_ts,open,high,low,close,volume\n");

    for (int i = 0; i < sc.ArraySize; i++)
    {
        // GetAsUnixTimeStamp() — seconds since Jan 1, 1970 UTC
        int64_t ts = (int64_t)sc.BaseDateTimeIn[i].GetAsUnixTimeStamp();

        fprintf(f, "%lld,%.4f,%.4f,%.4f,%.4f,%.0f\n",
            ts,
            (double)sc.Open[i],
            (double)sc.High[i],
            (double)sc.Low[i],
            (double)sc.Close[i],
            (double)sc.Volume[i]);
    }

    fclose(f);

    SCString Msg;
    Msg.Format("ExportBarsToCSV: Wrote %d bars -> %s", sc.ArraySize, FilePath.GetChars());
    sc.AddMessageToLog(Msg.GetChars(), 0);
}

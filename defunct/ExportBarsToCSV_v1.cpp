// ExportBarsToCSV.cpp
// ACSIL study — exports chart bars to CSV for backtesting.
// Symbol and date range come from chart settings (no inputs needed).
// Output: <SierraChart>/Data/<SYMBOL>_bars.csv
// Rewrites the file on every full recalculation (chart load, date range change, etc.)

#include "SierraChartStudyInterfaceACSIL.h"

SCSFExport scsf_ExportBarsToCSV(SCStudyInterfaceRef sc)
{
    if (sc.SetDefaults)
    {
        sc.GraphName        = "Export Bars to CSV";
        sc.StudyDescription = "Exports chart OHLCV bars to CSV for backtesting.";
        sc.AutoLoop         = 0;  // Manual loop — we handle iteration ourselves
        sc.UpdateAlways     = 0;
        return;
    }

    // Only write on full recalculation (chart load, settings change, date range change)
    if (!sc.IsFullRecalculation)
        return;

    if (sc.ArraySize == 0)
    {
        sc.AddMessageToLog("ExportBarsToCSV: No bars loaded.", 1);
        return;
    }

    // Build output path: <DataFilesFolder>\<Symbol>_bars.csv
    SCString FilePath;
    FilePath.Format("%s%s_bars.csv", sc.DataFilesFolder.GetChars(), sc.Symbol.GetChars());

    FILE* f = fopen(FilePath.GetChars(), "w");
    if (!f)
    {
        SCString Err;
        Err.Format("ExportBarsToCSV: Cannot open file for writing: %s", FilePath.GetChars());
        sc.AddMessageToLog(Err.GetChars(), 1);
        return;
    }

    // CSV header
    fprintf(f, "DateTime,Open,High,Low,Close,Volume\n");

    // Write all loaded bars
    for (int i = 0; i < sc.ArraySize; i++)
    {
        SCDateTime dt = sc.BaseDateTimeIn[i];

        int Year, Month, Day, Hour, Minute, Second;
        dt.GetDateTimeYMDHMS(Year, Month, Day, Hour, Minute, Second);

        fprintf(f, "%04d-%02d-%02d %02d:%02d:%02d,%.4f,%.4f,%.4f,%.4f,%.0f\n",
            Year, Month, Day, Hour, Minute, Second,
            (double)sc.Open[i],
            (double)sc.High[i],
            (double)sc.Low[i],
            (double)sc.Close[i],
            (double)sc.Volume[i]);
    }

    fclose(f);

    SCString Msg;
    Msg.Format("ExportBarsToCSV: Wrote %d bars to %s", sc.ArraySize, FilePath.GetChars());
    sc.AddMessageToLog(Msg.GetChars(), 0);
}

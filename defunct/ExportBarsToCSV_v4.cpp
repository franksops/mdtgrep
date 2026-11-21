#include "sierrachart.h"

SCDLLName("Export Bars and Ticks")

// ExportBarsAndTicks.cpp
// Exports full bar data + raw tick data to CSV for backtesting / bot training.
// Data source agnostic — works with Teton, Rithmic, CQG, etc.
//
// SETUP:
//   Chart Settings → Symbol     : @CL, @ES, @NQ, etc.
//   Chart Settings → Bar Period : 60 seconds (or any period)
//   Chart Settings → Days       : however far back you want
//   Study Input    → Adjusted   : Yes = label as back-adjusted, No = label as raw
//
// NOTE: Back-adjustment is controlled by Sierra Chart chart/symbol settings.
//       This toggle only labels the output filename accordingly.
//
// REQUIREMENTS for tick data:
//   Global Settings → Data/Trade Service → Common Settings
//   → Intraday Data Storage Time Unit = 1 Tick
//   Without this, tick sub-records per bar will not exist.
//
// OUTPUTS (written to SierraChart/Data/):
//   Bars : @CL_60s_adj.csv  or  @CL_60s_raw.csv
//   Ticks: @CL_ticks_adj.csv or @CL_ticks_raw.csv
//
// BAR SCHEMA:
//   unix_ts, open, high, low, close, volume, ask_vol, bid_vol, num_trades, open_interest
//
// TICK SCHEMA:
//   unix_ts_ms, price, volume, ask_vol, bid_vol, side
//   side: 1 = ask/buy aggressor, -1 = bid/sell aggressor, 0 = unknown

// SCDateTime is OLE date: days since Dec 30, 1899. GetAsDouble() exposes the raw value.
// 25569.0 = days between OLE epoch and Unix epoch (Jan 1, 1970)
static int64_t ToUnixSeconds(SCDateTime dt)
{
    double d = dt.GetAsDouble();
    return (int64_t)((d - 25569.0) * 86400.0);
}

static int64_t ToUnixMilliseconds(SCDateTime dt)
{
    double d = dt.GetAsDouble();
    return (int64_t)((d - 25569.0) * 86400.0 * 1000.0);
}

SCSFExport scsf_ExportBarsAndTicks(SCStudyInterfaceRef sc)
{
    if (sc.SetDefaults)
    {
        sc.GraphName        = "Export Bars and Ticks";
        sc.StudyDescription = "Exports full OHLCV bar data and raw tick data to CSV.";
        sc.AutoLoop         = 0;
        sc.UpdateAlways     = 0;

        sc.Input[0].Name = "Back-Adjusted Prices (label only)";
        sc.Input[0].SetYesNo(1);
        return;
    }

    if (!sc.IsFullRecalculation)
        return;

    if (sc.ArraySize == 0)
    {
        sc.AddMessageToLog("ExportBarsAndTicks: No bars loaded.", 1);
        return;
    }

    const char* AdjLabel = sc.Input[0].GetYesNo() ? "adj" : "raw";

    // -------------------------------------------------------------------------
    // BAR FILE
    // -------------------------------------------------------------------------
    SCString BarFilePath;
    BarFilePath.Format("%s%s_%ds_%s.csv",
        sc.DataFilesFolder(),
        sc.Symbol.GetChars(),
        sc.SecondsPerBar,
        AdjLabel);

    FILE* fb = fopen(BarFilePath.GetChars(), "w");
    if (!fb)
    {
        SCString Err;
        Err.Format("ExportBarsAndTicks: Cannot open bar file: %s", BarFilePath.GetChars());
        sc.AddMessageToLog(Err.GetChars(), 1);
        return;
    }

    fprintf(fb, "unix_ts,open,high,low,close,volume,ask_vol,bid_vol,num_trades,open_interest\n");

    for (int i = 0; i < sc.ArraySize; i++)
    {
        int64_t ts = ToUnixSeconds(sc.BaseDateTimeIn[i]);

        fprintf(fb, "%lld,%.4f,%.4f,%.4f,%.4f,%.0f,%.0f,%.0f,%.0f,%.0f\n",
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

    fclose(fb);

    SCString BarMsg;
    BarMsg.Format("ExportBarsAndTicks: Wrote %d bars -> %s", sc.ArraySize, BarFilePath.GetChars());
    sc.AddMessageToLog(BarMsg.GetChars(), 0);

    // -------------------------------------------------------------------------
    // TICK FILE
    // Requires: Global Settings → Intraday Data Storage Time Unit = 1 Tick
    // -------------------------------------------------------------------------
    SCString TickFilePath;
    TickFilePath.Format("%s%s_ticks_%s.csv",
        sc.DataFilesFolder(),
        sc.Symbol.GetChars(),
        AdjLabel);

    FILE* ft = fopen(TickFilePath.GetChars(), "w");
    if (!ft)
    {
        SCString Err;
        Err.Format("ExportBarsAndTicks: Cannot open tick file: %s", TickFilePath.GetChars());
        sc.AddMessageToLog(Err.GetChars(), 1);
        return;
    }

    fprintf(ft, "unix_ts_ms,price,volume,ask_vol,bid_vol,side\n");

    int64_t TickCount = 0;

    for (int i = 0; i < sc.ArraySize; i++)
    {
        s_IntradayRecord Record;
        int SubIndex = 0;

        while (true)
        {
            IntradayFileLockActionEnum LockAction = (SubIndex == 0)
                ? IFLA_LOCK_READ_HOLD
                : IFLA_NO_CHANGE;

            int Result = sc.ReadIntradayFileRecordForBarIndexAndSubIndex(i, SubIndex, Record, LockAction);

            if (Result == 0)
            {
                // No more records — release lock
                sc.ReadIntradayFileRecordForBarIndexAndSubIndex(i, SubIndex, Record, IFLA_RELEASE_AFTER_READ);
                break;
            }

            int64_t ts_ms = ToUnixMilliseconds(Record.DateTime);

            int side = 0;
            if (Record.AskVolume > 0 && Record.BidVolume == 0)      side =  1;
            else if (Record.BidVolume > 0 && Record.AskVolume == 0) side = -1;

            fprintf(ft, "%lld,%.4f,%u,%u,%u,%d\n",
                ts_ms,
                (double)Record.Close,
                Record.TotalVolume,
                Record.AskVolume,
                Record.BidVolume,
                side);

            SubIndex++;
            TickCount++;
        }
    }

    fclose(ft);

    SCString TickMsg;
    TickMsg.Format("ExportBarsAndTicks: Wrote %lld ticks -> %s", TickCount, TickFilePath.GetChars());
    sc.AddMessageToLog(TickMsg.GetChars(), 0);
}

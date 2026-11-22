/*
	MDTGrep.cpp
	============

	ACSIL export study for Sierra Chart.

	Exports the data of the chart it is added to into CSV files that are
	consumed by the SC-Assist data pipeline.

	Output files (output directory is an input, default
	C:\SierraChart\SCAssistData\):

		Bars : <SYMBOL><suffix>.csv         e.g. ESM6_bars.csv
		Ticks: <SYMBOL><suffix>_ticks.csv   e.g. ESM6_bars_ticks.csv

	Bars schema:
		unix_ts_ms,open,high,low,close,volume,ask_vol,bid_vol,num_trades,open_interest

	Ticks schema:
		unix_ts_ms,price,volume,ask_vol,bid_vol,delta,num_trades

	Timestamps are Unix epoch milliseconds.  By default they are the raw
	Sierra Chart date/time values, i.e. the time zone of the chart / data
	(exchange local time) and NOT UTC.  The "Timestamp Basis" input can be
	set to UTC to convert them.
*/

#include "sierrachart.h"

#include <cmath>
#include <cstdint>
#include <fstream>
#include <iomanip>
#include <string>

SCDLLName("MDTGrep")

// ---------------------------------------------------------------------------
// ACSIL interface version guard.
//
// The SCDLLName macro (see scstructures.h) exports scdll_DLLVersion() which
// returns SC_DLL_VERSION - the ACSIL interface version this file was compiled
// against.  Sierra Chart compares that value against the interface version of
// the running build and refuses to run a study whose DLL does not match:
//
//    "The study ... needs to be recompiled for this version of Sierra Chart"
//
// That is exactly why a study that is built against a stale
// ACS_Source\sierrachart.h compiles with zero errors and still never runs:
// the DLL is silently rejected by Sierra Chart and no CSV is ever written.
//
// Interface version 2217 is Sierra Chart 2972 and higher (the internal double
// precision number project).  Analysis >> Build Custom Studies DLL >>
// "Build Remote" always compiles against the header of the running Sierra
// Chart build.  The interface version that was compiled in is written to the
// log with every export so it can be verified.
// ---------------------------------------------------------------------------
#if !defined(SC_DLL_VERSION)
	#error "MDTGrep.cpp requires a current sierrachart.h (ACSIL) header."
#endif

#if SC_DLL_VERSION < 2217
	#pragma message("MDTGrep: WARNING - ACSIL interface version is older than 2217 (Sierra Chart 2972+). Build with 'Build Remote' or update ACS_Source\\sierrachart.h, otherwise Sierra Chart will not run this DLL.")
#endif

#define OUTPUT_DIR_DEFAULT "C:\\SierraChart\\SCAssistData\\"

// Export Mode (input 2)
#define MDTGREP_MODE_BARS  0
#define MDTGREP_MODE_TICKS 1
#define MDTGREP_MODE_BOTH  2

// Timestamp Basis (input 4)
#define MDTGREP_TS_LOCAL 0
#define MDTGREP_TS_UTC   1

// 1970-01-01 in milliseconds since the Sierra Chart base date of 1899-12-30
// (25569 days * 86400000 ms).
static const int64_t MDTGREP_UNIX_EPOCH_MS = 2209161600000LL;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// Convert a Sierra Chart date/time into a Unix epoch millisecond timestamp.
static int64_t ToUnixMs(const SCDateTime& DateTime)
{
	return DateTime.GetMillisecondsSinceBaseDate() - MDTGREP_UNIX_EPOCH_MS;
}

// The two data sources used by this study have DIFFERENT time bases:
//
//   sc.BaseDateTimeIn[]  the chart bar Date-Times are already adjusted to the
//                        time zone of the chart (Sierra Chart documentation:
//                        "The Date-Time values of sc.BaseDateTimeIn[] are
//                        already adjusted to the Sierra Chart Time Zone
//                        setting").
//
//   Intraday file records the Date-Times of the records read with
//                        sc.ReadIntradayFileRecordForBarIndexAndSubIndex() are
//                        read straight out of the .scid data file and are
//                        therefore in UTC/GMT (Sierra Chart documentation:
//                        "All dates and times within Sierra Chart are obtained
//                        and stored in UTC/GMT time... This also applies to
//                        historical data").
//
// Both are converted to the requested basis below so that the bar file and
// the tick file always use exactly the same time base.

// Chart bar Date-Time (time zone of the chart) -> Unix epoch milliseconds.
static int64_t BarTimeToUnixMs(SCStudyInterfaceRef sc, const SCDateTime& ChartDateTime, bool bUtc)
{
	if (!bUtc)
		return ToUnixMs(ChartDateTime);

	const SCDateTimeMS UtcDateTime = sc.ConvertDateTimeFromChartTimeZone(ChartDateTime, TIMEZONE_UTC);

	return ToUnixMs(UtcDateTime);
}

// Intraday file record Date-Time (UTC/GMT) -> Unix epoch milliseconds.
static int64_t FileTimeToUnixMs(SCStudyInterfaceRef sc, const SCDateTime& FileDateTime, bool bUtc)
{
	if (bUtc)
		return ToUnixMs(FileDateTime);

	const SCDateTimeMS ChartDateTime = sc.ConvertDateTimeToChartTimeZone(FileDateTime, TIMEZONE_UTC);

	return ToUnixMs(ChartDateTime);
}

static bool DirectoryExists(const char* Path)
{
	const DWORD Attributes = GetFileAttributesA(Path);

	return (Attributes != INVALID_FILE_ATTRIBUTES) && ((Attributes & FILE_ATTRIBUTE_DIRECTORY) != 0);
}

static bool EnsureDirectory(const SCString& Path)
{
	if (DirectoryExists(Path.GetChars()))
		return true;

	if (CreateDirectoryA(Path.GetChars(), NULL))
		return true;

	// The directory may have been created by something else in the meantime.
	return DirectoryExists(Path.GetChars());
}

// Replace the characters that are not allowed in a Windows file name.
static SCString MakeFileNameSafe(const SCString& In)
{
	std::string Out;

	const char* pChars = In.GetChars();

	for (int i = 0; i < In.GetLength(); ++i)
	{
		const char c = pChars[i];

		switch (c)
		{
			case '/':
			case '\\':
			case ':':
			case '*':
			case '?':
			case '"':
			case '<':
			case '>':
			case '|':
				Out += '_';
				break;

			default:
				Out += c;
				break;
		}
	}

	return SCString(Out.c_str());
}

// sc.OpenInterest[] is only valid for daily and higher timeframe charts.
// For intraday charts Sierra Chart returns the number of trades in that array
// instead, so the intraday charts are detected here to avoid writing the
// trade count twice.
static bool IsIntradayChart(SCStudyInterfaceRef sc)
{
	if (sc.ArraySize < 2)
		return false;

	const double SpanInDays = sc.BaseDateTimeIn[1].GetAsDouble() - sc.BaseDateTimeIn[0].GetAsDouble();

	return SpanInDays < 1.0;
}

// ---------------------------------------------------------------------------
// Export of the chart bars
// ---------------------------------------------------------------------------

static bool ExportBars(SCStudyInterfaceRef sc, const SCString& FilePath, bool bUtc, bool bIntradayChart, int& BarsWritten)
{
	std::ofstream OutFile(FilePath.GetChars(), std::ios::out | std::ios::trunc);

	if (!OutFile.is_open())
		return false;

	OutFile << "unix_ts_ms,open,high,low,close,volume,ask_vol,bid_vol,num_trades,open_interest\n";

	// Prices keep their fractional part, the counts are written as integers.
	OutFile << std::fixed << std::setprecision(5);

	for (int i = 0; i < sc.ArraySize; ++i)
	{
		const int64_t UnixTS_ms = BarTimeToUnixMs(sc, sc.BaseDateTimeIn[i], bUtc);

		const int64_t OpenInterest = bIntradayChart
			? 0
			: (int64_t)std::llround((double)sc.OpenInterest[i]);

		OutFile << UnixTS_ms << ","
			<< (double)sc.Open[i] << ","
			<< (double)sc.High[i] << ","
			<< (double)sc.Low[i] << ","
			<< (double)sc.Close[i] << ","
			<< (int64_t)std::llround((double)sc.Volume[i]) << ","
			<< (int64_t)std::llround((double)sc.AskVolume[i]) << ","
			<< (int64_t)std::llround((double)sc.BidVolume[i]) << ","
			<< (int64_t)std::llround((double)sc.NumberOfTrades[i]) << ","
			<< OpenInterest << "\n";
	}

	OutFile.close();

	BarsWritten = sc.ArraySize;

	return true;
}

// ---------------------------------------------------------------------------
// Export of the underlying intraday trade records
// ---------------------------------------------------------------------------

static bool ExportTicks(SCStudyInterfaceRef sc, const SCString& FilePath, bool bUtc, int MaxBars, int64_t& TicksWritten)
{
	std::ofstream OutFile(FilePath.GetChars(), std::ios::out | std::ios::trunc);

	if (!OutFile.is_open())
		return false;

	OutFile << "unix_ts_ms,price,volume,ask_vol,bid_vol,delta,num_trades,side,bid,ask\n";

	OutFile << std::fixed << std::setprecision(5);

	const int LastBar = (MaxBars > 0 && MaxBars < sc.ArraySize) ? MaxBars : sc.ArraySize;

	TicksWritten = 0;

	for (int i = 0; i < LastBar; ++i)
	{
		s_IntradayRecord Record;
		int SubIndex = 0;
		bool bFirstRecord = true;

		while (true)
		{
			// Lock protocol: the first record of a bar is read with
			// IFLA_LOCK_READ_HOLD, the following ones with IFLA_NO_CHANGE and
			// the lock is released after the last record of the bar.
			const IntradayFileLockActionEnum LockAction = bFirstRecord ? IFLA_LOCK_READ_HOLD : IFLA_NO_CHANGE;

			bFirstRecord = false;

			const int ReadSuccess = sc.ReadIntradayFileRecordForBarIndexAndSubIndex(i, SubIndex, Record, LockAction);

			if (ReadSuccess == 0)
				break;

			++SubIndex;

			// Market depth only updates do not contain a trade.
			if (Record.IsBidAskUpdateOnly())
				continue;

			const int64_t UnixTS_ms = FileTimeToUnixMs(sc, Record.DateTime, bUtc);
			const int64_t Delta = (int64_t)Record.AskVolume - (int64_t)Record.BidVolume;

			// The bid and ask prices are only present when the record carries
			// the quotes of the moment of the trade.  In that case the High
			// field of the record holds the ask price and the Low field holds
			// the bid price.  GetBidPrice()/GetAskPrice() return 0 when the
			// quotes are not part of the record.
			const float Price = Record.GetClose();
			const float Bid = Record.GetBidPrice();
			const float Ask = Record.GetAskPrice();

			// Side follows the rule the exchanges use to report the side of a
			// trade, so it can be compared against the side of the Databento
			// trade messages:
			//   B  the trade was a buy  (traded at the ask)
			//   S  the trade was a sell (traded at the bid)
			//   N  the side is unknown / mixed
			// When the record does not contain the quotes the side is taken
			// from the buy/sell volume split of the record.
			char Side = 'N';

			if (Bid > 0.0f && Ask > 0.0f)
			{
				if (Price >= Ask)
					Side = 'B';
				else if (Price <= Bid)
					Side = 'S';
			}
			else if (Record.AskVolume > 0 && Record.BidVolume == 0)
				Side = 'B';
			else if (Record.BidVolume > 0 && Record.AskVolume == 0)
				Side = 'S';

			OutFile << UnixTS_ms << ","
				<< (double)Price << ","
				<< (int64_t)Record.TotalVolume << ","
				<< (int64_t)Record.AskVolume << ","
				<< (int64_t)Record.BidVolume << ","
				<< Delta << ","
				<< (int64_t)Record.NumTrades << ","
				<< Side << ","
				<< (double)Bid << ","
				<< (double)Ask << "\n";

			++TicksWritten;
		}

		// Release the intraday file lock.
		s_IntradayRecord IgnoreRecord;
		sc.ReadIntradayFileRecordForBarIndexAndSubIndex(-1, -1, IgnoreRecord, IFLA_RELEASE_AFTER_READ);
	}

	OutFile.close();

	return true;
}

// ---------------------------------------------------------------------------
// Study function
// ---------------------------------------------------------------------------

SCSFExport scsf_MDTGrep(SCStudyInterfaceRef sc)
{
	// Configuration
	if (sc.SetDefaults)
	{
		sc.GraphName = "MDT Grep (CSV Exporter)";
		sc.StudyDescription = "Exports chart data to CSV (unix_ts_ms, open, high, low, close...)\n"
			"Export Mode: Bars = chart bars, Ticks = underlying intraday trade records, Both.\n"
			"Exports on chart recalculation.";

		sc.AutoLoop = 0; // Manual loop to control export logic efficiently
		sc.GraphRegion = 0;

		// Required by sc.ReadIntradayFileRecordForBarIndexAndSubIndex() which
		// is used for the tick export.
		sc.MaintainAdditionalChartDataArrays = 1;

		// Input to control export behavior
		sc.Input[0].Name = "Trigger Export on Full Recalc";
		sc.Input[0].SetYesNo(1);

		// Input to control the filename suffix
		sc.Input[1].Name = "Export File Suffix (e.g. _bars, _1min)";
		sc.Input[1].SetString("_bars");

		// Input to control what is written to the CSV
		sc.Input[2].Name = "Export Mode";
		sc.Input[2].SetCustomInputStrings("Bars (chart bars);Ticks (underlying trades);Both");
		sc.Input[2].SetCustomInputIndex(MDTGREP_MODE_BARS);

		// Input to control the output directory
		sc.Input[3].Name = "Output Directory";
		sc.Input[3].SetString(OUTPUT_DIR_DEFAULT);

		// Input to control the time zone of the timestamps
		sc.Input[4].Name = "Timestamp Basis";
		sc.Input[4].SetCustomInputStrings("Chart Local (as-is);UTC");
		sc.Input[4].SetCustomInputIndex(MDTGREP_TS_LOCAL);

		// Input to limit how many bars are read for the tick export. Reading
		// every tick of a long history freezes the Sierra Chart user
		// interface, so a limit can be used for large exports.
		sc.Input[5].Name = "Max Bars For Tick Export (0 = all)";
		sc.Input[5].SetInt(0);
		sc.Input[5].SetIntLimits(0, 1000000);

		return;
	}

	// ------------------------------------------------------------------
	// Settings
	// ------------------------------------------------------------------
	const int ExportMode = (int)sc.Input[2].GetIndex();
	const bool bUtc = ((int)sc.Input[4].GetIndex() == MDTGREP_TS_UTC);
	const int MaxTickBars = sc.Input[5].GetInt();

	SCString OutputDir = sc.Input[3].GetString();
	if (OutputDir.IsEmpty())
		OutputDir = OUTPUT_DIR_DEFAULT;

	if (OutputDir.GetLength() > 0 && OutputDir.GetChars()[OutputDir.GetLength() - 1] != '\\')
		OutputDir += "\\";

	SCString Suffix = sc.Input[1].GetString();
	if (Suffix.IsEmpty())
		Suffix = "_bars";

	// ------------------------------------------------------------------
	// Reset the "export done" flag every time a full recalculation is
	// triggered (e.g. Edit -> Recalculate on the chart) and every time a
	// setting is changed.  Changing a study input does not cause a full
	// recalculation, so without this the export would never run again after
	// a setting was changed.
	// ------------------------------------------------------------------
	int& ExportDone = sc.GetPersistentInt(0);
	SCString& LastSettings = sc.GetPersistentSCString(1);

	SCString Settings;
	Settings.Format("%d|%d|%d|%s|%s", ExportMode, bUtc ? 1 : 0, MaxTickBars, Suffix.GetChars(), OutputDir.GetChars());

	if (sc.IsFullRecalculation || LastSettings != Settings)
	{
		ExportDone = 0;
		LastSettings = Settings;
	}

	if (ExportDone == 1)
		return; // Already exported this session

	if (!sc.Input[0].GetYesNo())
		return;

	// Wait until downloading historical data is complete, otherwise only the
	// data that is loaded so far would be exported.
	if (sc.DownloadingHistoricalData)
	{
		int& WaitingLogged = sc.GetPersistentInt(2);

		if (WaitingLogged == 0)
		{
			WaitingLogged = 1;
			sc.AddMessageToLog("MDTGrep: waiting for the historical data download to complete before exporting.", 1);
		}

		return;
	}

	sc.GetPersistentInt(2) = 0;

	// Execute export if we have data
	if (sc.ArraySize == 0)
	{
		sc.AddMessageToLog("MDTGrep: no chart data loaded, nothing to export.", 1);
		return;
	}

	// ------------------------------------------------------------------
	// Output directory
	// ------------------------------------------------------------------
	if (!EnsureDirectory(OutputDir))
	{
		SCString Msg;
		Msg.Format("MDTGrep: unable to create the output directory: %s", OutputDir.GetChars());
		sc.AddMessageToLog(Msg, 1);
		return;
	}

	const SCString SafeSymbol = MakeFileNameSafe(sc.Symbol);
	const SCString SafeSuffix = MakeFileNameSafe(Suffix);
	const bool bIntradayChart = IsIntradayChart(sc);

	SCString Msg;

	if (ExportMode == MDTGREP_MODE_BARS || ExportMode == MDTGREP_MODE_BOTH)
	{
		// Determine the export filename: {SYMBOL}{_suffix}.csv in the output
		// directory. e.g. ESM6_bars.csv or ESM6_1min.csv
		SCString BarFilePath;
		BarFilePath.Format("%s%s%s.csv", OutputDir.GetChars(), SafeSymbol.GetChars(), SafeSuffix.GetChars());

		int BarsWritten = 0;

		if (!ExportBars(sc, BarFilePath, bUtc, bIntradayChart, BarsWritten))
		{
			Msg.Format("MDTGrep: failed to open file for writing: %s", BarFilePath.GetChars());
			sc.AddMessageToLog(Msg, 1);
			return;
		}

		Msg.Format("MDTGrep: ACSIL interface %d - exported %d bars to %s (%s)", SC_DLL_VERSION, BarsWritten, BarFilePath.GetChars(), bUtc ? "UTC" : "chart local time");
		sc.AddMessageToLog(Msg, 0);

		sc.AddMessageToLog("MDTGrep: bar prices follow the back adjustment settings of the chart. The tick file contains the raw traded prices, which is what a Databento comparison needs.", 1);

		if (bIntradayChart)
		{
			sc.AddMessageToLog("MDTGrep: intraday chart detected, the open_interest column is written as 0 because Sierra Chart only provides open interest for daily and higher timeframe charts.", 1);
		}
	}

	if (ExportMode == MDTGREP_MODE_TICKS || ExportMode == MDTGREP_MODE_BOTH)
	{
		SCString TickFilePath;
		TickFilePath.Format("%s%s%s_ticks.csv", OutputDir.GetChars(), SafeSymbol.GetChars(), SafeSuffix.GetChars());

		int64_t TicksWritten = 0;

		if (!ExportTicks(sc, TickFilePath, bUtc, MaxTickBars, TicksWritten))
		{
			Msg.Format("MDTGrep: failed to open file for writing: %s", TickFilePath.GetChars());
			sc.AddMessageToLog(Msg, 1);
			return;
		}

		Msg.Format("MDTGrep: ACSIL interface %d - exported %lld ticks to %s (%s)", SC_DLL_VERSION, (long long)TicksWritten, TickFilePath.GetChars(), bUtc ? "UTC" : "chart local time");
		sc.AddMessageToLog(Msg, 0);

		if (TicksWritten == 0)
		{
			sc.AddMessageToLog("MDTGrep: no intraday trade records were found. The tick export requires an intraday chart with intraday data files (.scid) and Global Settings >> Data/Trade Service Settings >> Intraday Data Storage Time Unit set to 1 Tick or 1 Second.", 1);
		}
	}

	// Mark as done so we don't re-export on every tick
	ExportDone = 1;
}

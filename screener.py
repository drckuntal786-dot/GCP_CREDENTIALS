import time
import schedule
import pandas as pd
import pandas_ta as ta
import numpy as np
import yfinance as yf
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials

# ==========================================
# CONFIGURATION & CONSTANTS
# ==========================================
# 1. Replace with your Google Spreadsheet ID
SPREADSHEET_ID = "1LSHDayXuQ43C8FNdn9bnxw-lFPkSTOrBjov1gme0t2g"

# 2. Path to your downloaded service account credentials file
CREDENTIALS_FILE = "credentials.json"

# Sectoral Indices to monitor
SECTOR_INDICES = {
    "NIFTY BANK": "^NSEBANK",
    "NIFTY IT": "^CNXIT",
    "NIFTY AUTO": "^CNXAUTO",
    "NIFTY FMCG": "^CNXFMCG",
    "NIFTY METAL": "^CNXMETAL",
    "NIFTY REALTY": "^CNXREALTY",
    "NIFTY PHARMA": "^CNXPHARMA",
    "NIFTY ENERGY": "^CNXENERGY",
    "NIFTY INFRA": "^CNXINFRA",
    "NIFTY PSU BANK": "^CNXPSUBANK"
}

# Stock constituents categorized by Index
INDEX_CONSTITUENTS = {
    "NIFTY50": ["RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "ICICIBANK.NS", "INFY.NS", "BHARTIARTL.NS", "ITC.NS", "SBIN.NS", "LTIM.NS", "AXISBANK.NS"],
    "NIFTY NEXT50": ["BEL.NS", "HAL.NS", "TATASTEEL.NS", "TRENT.NS", "PFC.NS", "REC.NS", "DLF.NS", "IOC.NS", "GAIL.NS", "BANKBARODA.NS"],
    "BANK NIFTY": ["HDFCBANK.NS", "ICICIBANK.NS", "SBIN.NS", "AXISBANK.NS", "KOTAKBANK.NS", "INDUSINDBK.NS", "BANKBARODA.NS", "PNB.NS", "AUBANK.NS", "FEDERALBNK.NS"],
    "NIFTY MIDCAP150": ["PERSISTENT.NS", "POLYCAB.NS", "COFORGE.NS", "MPHASIS.NS", "FEDERALBNK.NS", "ASHOKLEY.NS", "MAXHEALTH.NS", "ASTRAL.NS", "BALKRISIND.NS", "IDFCFIRSTB.NS"]
}

# ==========================================
# HELPER FUNCTIONS
# ==========================================

def get_gspread_client():
    """Authenticates and returns the Google Sheet worksheet."""
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=scopes)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SPREADSHEET_ID).sheet1
    return sheet

def flatten_yf_df(df):
    """Flattens MultiIndex columns returned by newer yfinance versions."""
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df

def get_sector_crackdowns():
    """Identifies sectors that cracked down by >= 2% today."""
    cracked_sectors = []
    print("--- Step 1: Checking Sector Performance ---")
    for name, ticker in SECTOR_INDICES.items():
        try:
            data = yf.download(ticker, period="5d", progress=False)
            data = flatten_yf_df(data)
            if len(data) >= 2:
                prev_close = float(data['Close'].iloc[-2])
                curr_close = float(data['Close'].iloc[-1])
                day_change = ((curr_close - prev_close) / prev_close) * 100
                
                if day_change <= -2.0:
                    cracked_sectors.append(name)
                    print(f"[CRACKED] {name}: {day_change:.2f}%")
                else:
                    print(f"[OK] {name}: {day_change:.2f}%")
        except Exception as e:
            print(f"Error fetching sector {name}: {e}")
    return cracked_sectors

def compute_vwap_intraday(ticker):
    """Fetches 5-minute intraday data to calculate true Intraday VWAP."""
    try:
        df_intraday = yf.download(ticker, period="1d", interval="5m", progress=False)
        df_intraday = flatten_yf_df(df_intraday)
        if df_intraday.empty:
            return 0.0
        
        tp = (df_intraday['High'] + df_intraday['Low'] + df_intraday['Close']) / 3
        vwap = (tp * df_intraday['Volume']).sum() / df_intraday['Volume'].sum()
        return float(vwap)
    except Exception:
        return 0.0

def analyze_stock(ticker):
    """Fetches stock data and evaluates weekly, previous day, and daily drawdown conditions."""
    try:
        df_daily = yf.download(ticker, period="1mo", interval="1d", progress=False)
        df_daily = flatten_yf_df(df_daily)
        
        if len(df_daily) < 10:
            return None

        # Price metrics
        curr_price = float(df_daily['Close'].iloc[-1])
        prev_close = float(df_daily['Close'].iloc[-2])
        prev_prev_close = float(df_daily['Close'].iloc[-3])
        
        day_change = ((curr_price - prev_close) / prev_close) * 100
        prev_day_change = ((prev_close - prev_prev_close) / prev_prev_close) * 100
        
        weekly_close_5d_ago = float(df_daily['Close'].iloc[-6]) if len(df_daily) >= 6 else float(df_daily['Close'].iloc[0])
        weekly_change = ((curr_price - weekly_close_5d_ago) / weekly_close_5d_ago) * 100

        # Filter Condition: Bearish Weekly < -7%, Previous Day < -5%, Daily < -3%
        is_bearish_pass = (weekly_change < -7.0) and (prev_day_change < -5.0) and (day_change < -3.0)

        # Technical Indicators
        df_daily['20_EMA'] = ta.ema(df_daily['Close'], length=20)
        df_daily['9_EMA'] = ta.ema(df_daily['Close'], length=9)
        df_daily['21_EMA'] = ta.ema(df_daily['Close'], length=21)
        
        st = ta.supertrend(df_daily['High'], df_daily['Low'], df_daily['Close'], length=10, multiplier=3)
        
        # Determine correct column keys dynamically from pandas_ta response
        st_col = [c for c in st.columns if c.startswith("SUPERT_")][0]
        st_dir_col = [c for c in st.columns if c.startswith("SUPERTd_")][0]

        df_daily['Supertrend'] = st[st_col]
        df_daily['ST_Direction'] = st[st_dir_col]

        # Fetch Intraday VWAP
        vwap = compute_vwap_intraday(ticker)

        # Signal Metrics
        ema_9 = float(df_daily['9_EMA'].iloc[-1])
        ema_21 = float(df_daily['21_EMA'].iloc[-1])
        ema_20 = float(df_daily['20_EMA'].iloc[-1])
        supertrend = float(df_daily['Supertrend'].iloc[-1])
        st_direction = int(df_daily['ST_Direction'].iloc[-1])

        short_signal = (
            (curr_price < ema_9) and 
            (ema_9 < ema_21) and 
            (curr_price < vwap if vwap > 0 else True) and 
            (st_direction == -1)
        )

        return {
            "Ticker": ticker.replace(".NS", ""),
            "Current Price": round(curr_price, 2),
            "Day Return (%)": round(day_change, 2),
            "Prev Day Return (%)": round(prev_day_change, 2),
            "Weekly Return (%)": round(weekly_change, 2),
            "VWAP": round(vwap, 2),
            "20 EMA": round(ema_20, 2),
            "9 EMA": round(ema_9, 2),
            "21 EMA": round(ema_21, 2),
            "Supertrend": round(supertrend, 2),
            "Bearish Criteria Met": is_bearish_pass,
            "Short Trade Trigger": "SELL CONFIRMED" if short_signal else "WAIT"
        }
    except Exception as e:
        print(f"Error analyzing {ticker}: {e}")
        return None

def update_google_sheet(df_results, run_timestamp):
    """Appends screener results along with Date and Time into Google Sheets."""
    try:
        sheet = get_gspread_client()
        
        # Check if Sheet is completely empty (Add headers if empty)
        existing_records = sheet.get_all_values()
        if not existing_records:
            headers = ["Run Date", "Run Time", "Rank", "Ticker", "Index / Category", 
                       "Current Price", "Day Return (%)", "Prev Day Return (%)", 
                       "Weekly Return (%)", "VWAP", "20 EMA", "Supertrend", "Short Trade Trigger"]
            sheet.append_row(headers)

        # Prepare rows to append
        rows_to_append = []
        date_str = run_timestamp.strftime("%Y-%m-%d")
        time_str = run_timestamp.strftime("%H:%M:%S")

        for _, row in df_results.iterrows():
            row_data = [
                date_str,
                time_str,
                int(row['Rank']),
                str(row['Ticker']),
                str(row['Index / Category']),
                float(row['Current Price']),
                float(row['Day Return (%)']),
                float(row['Prev Day Return (%)']),
                float(row['Weekly Return (%)']),
                float(row['VWAP']),
                float(row['20 EMA']),
                float(row['Supertrend']),
                str(row['Short Trade Trigger'])
            ]
            rows_to_append.append(row_data)

        if rows_to_append:
            sheet.append_rows(rows_to_append)
            print(f"[SUCCESS] Successfully written {len(rows_to_append)} rows to Google Sheet.")
    except Exception as e:
        print(f"[ERROR] Failed to update Google Sheet: {e}")

# ==========================================
# MAIN ROUTINE
# ==========================================

def run_screener():
    now = datetime.now()
    print(f"\n==================================================")
    print(f" Running Screener Task at: {now.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"==================================================")
    
    # Step 1: Detect cracked sectors
    get_sector_crackdowns()
    
    # Step 2: Screen Index Members
    results = []
    print("\n--- Step 2 & 3: Screening Index Members ---")
    
    for index_name, ticker_list in INDEX_CONSTITUENTS.items():
        for ticker in set(ticker_list):
            stock_data = analyze_stock(ticker)
            if stock_data and stock_data['Bearish Criteria Met']:
                stock_data['Index / Category'] = index_name
                results.append(stock_data)

    # Step 3: Process and Export Results
    if results:
        df_results = pd.DataFrame(results)
        df_results = df_results.sort_values(by="Day Return (%)", ascending=True)
        df_results['Rank'] = range(1, len(df_results) + 1)
        
        cols = ['Rank', 'Ticker', 'Index / Category', 'Current Price', 'Day Return (%)', 
                'Prev Day Return (%)', 'Weekly Return (%)', 'VWAP', '20 EMA', 'Supertrend', 'Short Trade Trigger']
        
        df_final = df_results[cols]
        print("\n================ FINAL BEARISH BREAKOUT RESULTS ================")
        print(df_final.to_string(index=False))
        
        # Step 4: Write to Google Sheet
        update_google_sheet(df_final, now)
    else:
        print("\nNo stocks met all strict drawdown conditions (Weekly < -7%, Prev Day < -5%, Day < -3%).")
        # Write "No Signals" entry to Sheet for historical logs
        sheet = get_gspread_client()
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H:%M:%S")
        sheet.append_row([date_str, time_str, 0, "N/A", "N/A", 0, 0, 0, 0, 0, 0, 0, "NO SIGNALS MET"])

# ==========================================
# SCHEDULER EXECUTION LOOP
# ==========================================

if __name__ == "__main__":
    print("Screener Scheduler Started...")
    print("Scheduled Runs: 09:15 AM and 05:30 PM Daily.")
    
    # Schedule the jobs
    schedule.every().day.at("09:15").do(run_screener)
    schedule.every().day.at("17:30").do(run_screener)
    
    # Infinite loop to keep the script running
    while True:
        schedule.run_pending()
        time.sleep(30)

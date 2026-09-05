import os
import sys
import time
import datetime
import pandas as pd
import numpy as np
import yfinance as yf
import gspread
from google.oauth2.service_account import Credentials

# ==========================================
# 1. Environment & Google Sheets Config
# ==========================================
raw_id = (
    os.environ.get("SPREADSHEET_ID", "")
    or os.environ.get("SPREADSHEET_ID_SECRET", "")
    or "1LSHDayXuQ43C8FNdn9bnxw-lFPkSTOrBjov1gme0t2g"
)
SPREADSHEET_ID = raw_id.strip().strip('"').strip("'")

if "spreadsheets/d/" in SPREADSHEET_ID:
    SPREADSHEET_ID = SPREADSHEET_ID.split("spreadsheets/d/")[1].split("/")[0]

if not SPREADSHEET_ID:
    print("❌ ERROR: SPREADSHEET_ID environment variable is missing!")
    sys.exit(1)

SERVICE_ACCOUNT_FILE = "service_account.json"
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

def get_spreadsheet():
    if not os.path.exists(SERVICE_ACCOUNT_FILE):
        print(f"❌ ERROR: {SERVICE_ACCOUNT_FILE} not found!")
        sys.exit(1)
        
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    client = gspread.authorize(creds)
    return client.open_by_key(SPREADSHEET_ID)

def get_sheet(sheet_name: str):
    spreadsheet = get_spreadsheet()
    try:
        worksheet = spreadsheet.worksheet(sheet_name)
    except gspread.exceptions.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=sheet_name, rows="300", cols="30")
    return worksheet


# ==========================================
# 2. Indicator & Calculation Helpers
# ==========================================
def calculate_rsi(series: pd.Series, period: int = 14) -> float:
    if len(series) < period + 1:
        return 50.0
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs = avg_gain / (avg_loss + 1e-9)
    rsi = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1])

def calculate_atr(df: pd.DataFrame, period: int = 14) -> float:
    if len(df) < period + 1:
        return float(df['Close'].iloc[-1] * 0.02) if not df.empty else 10.0
    
    high = df['High']
    low = df['Low']
    close = df['Close'].shift(1)
    
    tr = pd.concat([
        high - low,
        (high - close).abs(),
        (low - close).abs()
    ], axis=1).max(axis=1)
    
    atr = tr.rolling(window=period).mean()
    return float(atr.iloc[-1])

def get_atm_strike(spot_price: float) -> int:
    if spot_price >= 5000:
        step = 100
    elif spot_price >= 1000:
        step = 50
    elif spot_price >= 250:
        step = 20
    else:
        step = 10
    return int(round(spot_price / step) * step)


# ==========================================
# 3. Short Trade Technical Analysis Engine
# ==========================================
def analyze_short_stock(symbol: str, default_price: float = None, default_change: float = None):
    clean_symbol = symbol.replace(".NS", "").strip()
    ticker = f"{clean_symbol}.NS"
    
    try:
        df_daily = yf.download(ticker, period="30d", interval="1d", progress=False)
        
        if len(df_daily) >= 15:
            if isinstance(df_daily.columns, pd.MultiIndex):
                df_daily.columns = df_daily.columns.get_level_values(0)

            latest_day = df_daily.iloc[-1]
            prev_day = df_daily.iloc[-2]

            close_price = float(latest_day['Close'])
            prev_close = float(prev_day['Close'])
            price_change_pct = ((close_price - prev_close) / prev_close) * 100.0
            rsi = calculate_rsi(df_daily['Close'])
            atr = calculate_atr(df_daily)
        else:
            close_price = float(default_price) if default_price is not None else 0.0
            price_change_pct = float(default_change) if default_change is not None else 0.0
            rsi = 40.0
            atr = close_price * 0.025

    except Exception as e:
        print(f"⚠️ Live indicator fetch warning for {symbol}: {e}")
        close_price = float(default_price) if default_price is not None else 0.0
        price_change_pct = float(default_change) if default_change is not None else 0.0
        rsi = 40.0
        atr = close_price * 0.025

    target_price = round(close_price - (1.2 * atr), 2)
    stop_loss = round(close_price + (0.8 * atr), 2)
    atm_strike = get_atm_strike(close_price)

    return {
        "Symbol": clean_symbol,
        "Close": round(close_price, 2),
        "Price_Change_%": round(price_change_pct, 2),
        "RSI": round(rsi, 2),
        "Suggested_Option": f"BUY {atm_strike} PE",
        "Target_Price": target_price,
        "Stop_Loss": stop_loss,
        "Signal": "SELL CONFIRMED"
    }


# ==========================================
# 4. Pipeline Core Tasks
# ==========================================
def process_short_execution():
    timestamp_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{timestamp_str}] 📥 Fetching data from Sheet1...")

    spreadsheet = get_spreadsheet()
    
    try:
        source_sheet = spreadsheet.worksheet("Sheet1")
    except gspread.exceptions.WorksheetNotFound:
        source_sheet = spreadsheet.get_worksheet(0)

    records = source_sheet.get_all_records()
    if not records:
        print("⚠️ No records found in source sheet.")
        return

    confirmed_short_trades = []

    for row in records:
        ticker = str(row.get("Ticker", "") or row.get("Stock", "") or row.get("Symbol", "")).strip()
        trigger_status = str(row.get("Short Trade Trigger", "") or row.get("Trigger", "") or row.get("Status", "")).strip().upper()
        
        current_price = row.get("Current Price", None)
        day_return = row.get("Day Return (%)", None)

        if not ticker:
            continue

        if trigger_status == "SELL CONFIRMED":
            print(f"🎯 'SELL CONFIRMED' detected for: {ticker}")
            analysis = analyze_short_stock(ticker, default_price=current_price, default_change=day_return)
            
            if analysis:
                confirmed_short_trades.append([
                    timestamp_str,
                    analysis['Symbol'],
                    "SHORT / STBT",
                    analysis['Close'],
                    f"{analysis['Price_Change_%']}%",
                    analysis['RSI'],
                    analysis['Suggested_Option'],
                    analysis['Target_Price'],
                    analysis['Stop_Loss'],
                    analysis['Signal']
                ])

    exec_sheet = get_sheet("Execution")
    
    headers = [
        "Execution-Timestamp", "Symbol", "Trade Type", "Close Price", 
        "% Change", "RSI (14)", "Suggested Option", 
        "Target Price", "Stop Loss", "Execution Status"
    ]

    exec_sheet.clear()
    exec_sheet.append_row(headers)

    if confirmed_short_trades:
        exec_sheet.append_rows(confirmed_short_trades)
        print(f"✓ Execution Sheet updated successfully with {len(confirmed_short_trades)} 'SELL CONFIRMED' trade(s)!\n")
    else:
        print("ℹ️ No 'SELL CONFIRMED' trade triggers met during this run.\n")


def run_nightly_reset():
    print("🌙 Running Nightly Reset...")
    sheet = get_sheet("Execution")
    timestamp_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    
    sheet.clear()
    sheet.append_row(["Status", "Last Reset Time", "Message"])
    sheet.append_row(["CLEARED", timestamp_str, "Execution sheet reset for next trading session."])
    print("✓ Nightly reset completed!\n")


# ==========================================
# 5. Main Execution Entrypoint
# ==========================================
if __name__ == "__main__":
    run_mode = os.environ.get("RUN_MODE", "").strip().upper()
    print(f"🚀 Main script triggered with RUN_MODE: [{run_mode or 'DEFAULT_SCHEDULER'}]")

    if run_mode == "EXECUTION":
        process_short_execution()
        sys.exit(0)
    elif run_mode == "NIGHTLY_RESET":
        run_nightly_reset()
        sys.exit(0)
    else:
        # Fallback to single execution for automated actions/CI pipelines
        process_short_execution()
        sys.exit(0)

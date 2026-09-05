import os
import sys
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
# 2. Indicator Calculation Helpers
# ==========================================
def calculate_rsi(series: pd.Series, period: int = 14) -> float:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()
    rs = avg_gain / (avg_loss + 1e-9)
    rsi = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1])

def calculate_atr(df: pd.DataFrame, period: int = 14) -> float:
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
# 3. Short Trade Analysis Engine
# ==========================================
def analyze_short_stock(symbol: str):
    try:
        ticker = f"{symbol}.NS" if not symbol.endswith(".NS") else symbol
        df_daily = yf.download(ticker, period="1y", interval="1d", progress=False)
        
        if len(df_daily) < 50:
            return None

        if isinstance(df_daily.columns, pd.MultiIndex):
            df_daily.columns = df_daily.columns.get_level_values(0)

        latest_day = df_daily.iloc[-1]
        prev_day = df_daily.iloc[-2]

        close_price = float(latest_day['Close'])
        prev_close = float(prev_day['Close'])
        
        price_change_pct = ((close_price - prev_close) / prev_close) * 100.0
        rsi = calculate_rsi(df_daily['Close'])
        atr = calculate_atr(df_daily)

        # STBT / Short Targets & Stop Loss
        target_price = round(close_price - (1.2 * atr), 2)
        stop_loss = round(close_price + (0.8 * atr), 2)
        atm_strike = get_atm_strike(close_price)

        return {
            "Symbol": symbol.replace(".NS", ""),
            "Close": round(close_price, 2),
            "Price_Change_%": round(price_change_pct, 2),
            "RSI": round(rsi, 2),
            "Suggested_Option": f"BUY {atm_strike} PE",
            "Target_Price": target_price,
            "Stop_Loss": stop_loss,
            "Signal": "SELL CONFIRMED"
        }
    except Exception as e:
        print(f"Skipping {symbol}: {e}")
        return None


# ==========================================
# 4. Pipeline Execution Tasks
# ==========================================
def detect_run_phase() -> str:
    env_mode = os.environ.get("RUN_MODE", "").strip().upper()
    if env_mode in ["PRE_MARKET", "EXECUTION", "NIGHTLY_RESET"]:
        return env_mode

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    hour, minute = now_utc.hour, now_utc.minute

    if hour == 3 and 30 <= minute <= 50:
        return "PRE_MARKET"
    elif hour == 4 and minute <= 30:
        return "EXECUTION"
    elif hour == 22 and minute <= 30:
        return "NIGHTLY_RESET"
    
    return "EXECUTION"

def run_pre_market_scan():
    print("🔍 Executing Pre-Market Scan...")
    sheet = get_sheet("PreMarket_Scan")
    timestamp_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    
    sheet.clear()
    sheet.append_row(["Timestamp", "Phase", "Status"])
    sheet.append_row([timestamp_str, "Pre-Market Scan", "READY"])
    print("✓ Pre-Market Scan complete!")

def run_short_execution():
    """Sheet 1 से Short स्टॉक्स को पढ़ना और SELL CONFIRMED होने पर Execution शीट में लिखना"""
    print(f"[{datetime.datetime.now()}] Reading Sheet 1 for Short Stocks...")
    
    spreadsheet = get_spreadsheet()
    try:
        source_sheet = spreadsheet.get_worksheet(0) # First sheet (Sheet 1)
    except Exception as e:
        print(f"❌ Error accessing Sheet 1: {e}")
        return

    records = source_sheet.get_all_records()
    if not records:
        print("No data found in Sheet 1.")
        return

    confirmed_short_trades = []
    timestamp_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    for row in records:
        # Sheet 1 की रो चेक करें (Stock - Short और Trigger Status)
        stock_symbol = str(row.get("Stock", "") or row.get("Symbol", "")).strip()
        signal_type = str(row.get("Type", "") or row.get("Signal", "")).strip().upper()
        trigger_status = str(row.get("Trigger", "") or row.get("Status", "")).strip().upper()

        # कंडीशन: Short स्टॉक्स जिनका ट्रिगर SELL CONFIRMED है
        if stock_symbol and (signal_type == "SHORT" or "SHORT" in signal_type or trigger_status == "SELL CONFIRMED"):
            analysis = analyze_short_stock(stock_symbol)
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

    # Result को Execution शीट में भेजें
    exec_sheet = get_sheet("Execution")
    
    headers = [
        "Timestamp", "Symbol", "Trade Type", "Close Price", 
        "% Change", "RSI (14)", "Suggested Option", 
        "Target Price", "Stop Loss", "Execution Status"
    ]

    exec_sheet.clear()
    exec_sheet.append_row(headers)
    
    if confirmed_short_trades:
        exec_sheet.append_rows(confirmed_short_trades)
        print(f"✓ Execution Sheet updated with {len(confirmed_short_trades)} confirmed SHORT trades!")
    else:
        print("No confirmed SELL triggers matched.")

def run_nightly_reset():
    print("🌙 Running Nightly Reset...")
    sheet = get_sheet("Execution")
    timestamp_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    
    sheet.clear()
    sheet.append_row(["Status", "Last Reset Time", "Message"])
    sheet.append_row(["CLEARED", timestamp_str, "Execution sheet reset for next trading session."])
    print("✓ Nightly reset completed!")


# ==========================================
# 5. Controller Main Entrypoint
# ==========================================
if __name__ == "__main__":
    phase = detect_run_phase()
    print(f"🚀 Execution started for phase: [{phase}]")

    if phase == "PRE_MARKET":
        run_pre_market_scan()
    elif phase == "NIGHTLY_RESET":
        run_nightly_reset()
    else:
        run_short_execution()

    sys.exit(0)

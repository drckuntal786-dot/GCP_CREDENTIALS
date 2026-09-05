import os
import sys
import time
import datetime
import json
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
    # Priority 1: Check for JSON string stored directly in env variable
    gcp_secret_env = os.environ.get("GCP_CREDENTIALS", "").strip()
    
    if gcp_secret_env:
        try:
            cred_info = json.loads(gcp_secret_env)
            creds = Credentials.from_service_account_info(cred_info, scopes=SCOPES)
        except Exception as e:
            print(f"⚠️ Failed parsing GCP_CREDENTIALS environment secret: {e}")
            creds = None
    else:
        creds = None

    # Priority 2: Fall back to local service_account.json file
    if creds is None:
        if not os.path.exists(SERVICE_ACCOUNT_FILE):
            print(f"❌ ERROR: Credentials file {SERVICE_ACCOUNT_FILE} not found and GCP_CREDENTIALS env var is missing/invalid!")
            sys.exit(1)
        
        try:
            creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
        except Exception as e:
            print(f"❌ ERROR: Failed loading {SERVICE_ACCOUNT_FILE}: {e}")
            sys.exit(1)

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
    
    val = rsi.iloc[-1]
    return float(val) if not np.isnan(val) else 50.0

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
    val = atr.iloc[-1]
    return float(val) if not np.isnan(val) else float(df['Close'].iloc[-1] * 0.02)

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

# Comprehensive F&O Universe with valid Yahoo Finance symbols
FNO_TICKERS = [
    # --- NIFTY 50 & BANK NIFTY ---
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "BHARTIARTL", "ITC",
    "SBIN", "LT", "BAJFINANCE", "HINDUNILVR", "AXISBANK", "KOTAKBANK",
    "MARUTI", "SUNPHARMA", "TATASTEEL", "NTPC", "POWERGRID", "PERSISTENT",
    "ULTRACEMCO", "TITAN", "ONGC", "ADANIENT", "ADANIPORTS", "JSWSTEEL",
    "COALINDIA", "BAJAJ-AUTO", "M&M", "GRASIM", "TECHM", "HCLTECH",
    "DRREDDY", "HEROMOTOCO", "EICHERMOT", "DIVISLAB", "CIPLA", "APOLLOHOSP",
    "HDFCLIFE", "SBILIFE", "BPCL", "TATACONSUM", "BRITANNIA", "ASIANPAINT",
    "HINDALCO", "INDUSINDBK", "BEL", "VBL", "SHRIRAMFIN", "TRENT", "LTTS",
    "BANKBARODA", "PNB", "CANBK", "AUBANK", "IDFCFIRSTB", "FEDERALBNK",

    # --- NIFTY NEXT 50 & LIQUID STOCKS ---
    "ABB", "ADANIGREEN", "ADANIPOWER", "AMBUJACEM", "ATGL", "BAJAJHLDNG",
    "BANKINDIA", "BOSCHLTD", "CGPOWER", "CHOLAFIN", "COLPAL", "DLF",
    "GAIL", "GODREJCP", "HAVELLS", "ICICIGI", "ICICIPRULI", "IOC",
    "IRFC", "JINDALSTEL", "JIOFIN", "LODHA", "MAXHEALTH", "NAUKRI",
    "NHPC", "NMDC", "OIL", "PAYTM", "PFC", "PIDILITIND",
    "POLYCAB", "RECLTD", "SBICARD", "SIEMENS", "SRF", "TATAELXSI",
    "TATAPOWER", "TORNTPHARM", "TIINDIA", "UNITDSPR", "ETWEEN",

    # --- LIQUID MIDCAPS ---
    "AUROPHARMA", "BALKRISIND", "BANDHANBNK", "BERGEPAINT", "BHARATFORG",
    "BIOCON", "BSOFT", "CANFINHOME", "CHAMBLFERT", "COFORGE",
    "CONCOR", "COROMANDEL", "CROMPTON", "CUMMINSIND", "DABUR",
    "DALBHARAT", "DEEPAKNTR", "ESCORTS", "EXIDEIND", "GLENMARK",
    "GMRAIRPORT", "GODREJPROP", "GRANULES", "GUJGASLTD", "HAL",
    "HDFCAMC", "IEX", "IGL", "INDHOTEL", "INDIAMART",
    "INDIGO", "INDUSTOWER", "IPCALAB", "JKCEMENT",
    "JUBLFOOD", "KALYANKJIL", "KEI", "LALPATHLAB", "LICHSGFIN",
    "LUPIN", "MFSL", "MGL", "MOTHERSON", "MPHASIS",
    "MRF", "MUTHOOTFIN", "NATIONALUM", "NAVINFLUOR", "OBEROIRLTY",
    "OFSS", "PAGEIND", "PETRONET", "PIIND", "PNBHOUSING",
    "RAMCOCEM", "SAIL", "SJVN", "SYNGENE", "TATACOMM",
    "TATACHEM", "TVSMOTOR", "UPL", "VOLTAS", "ZEEL"
]

# De-duplicate ticker entries while retaining structure
FNO_TICKERS = list(dict.fromkeys(FNO_TICKERS))

def fetch_fno_top_losers(top_n: int = 15) -> pd.DataFrame:
    """Fetch live data for F&O universe and return top losers sorted by price change %."""
    print("🔍 Fetching market performance for F&O universe...")
    yf_symbols = [f"{symbol}.NS" for symbol in FNO_TICKERS]
    
    try:
        # Download 5 days of data to accurately evaluate day change
        data = yf.download(yf_symbols, period="5d", interval="1d", progress=False)
        if isinstance(data.columns, pd.MultiIndex):
            close_prices = data['Close']
        else:
            close_prices = data
            
        results = []
        for symbol in FNO_TICKERS:
            ticker = f"{symbol}.NS"
            if ticker in close_prices.columns:
                series = close_prices[ticker].dropna()
                if len(series) >= 2:
                    current_p = float(series.iloc[-1])
                    prev_p = float(series.iloc[-2])
                    day_ret = ((current_p - prev_p) / prev_p) * 100.0
                    results.append({
                        "Ticker": symbol,
                        "Current Price": round(current_p, 2),
                        "Day Return (%)": round(day_ret, 2),
                        "Short Trade Trigger": "SELL CONFIRMED" if day_ret < 0 else "WATCH"
                    })
                    
        df = pd.DataFrame(results)
        if not df.empty:
            df = df.sort_values(by="Day Return (%)", ascending=True).head(top_n)
        return df

    except Exception as e:
        print(f"❌ Error fetching F&O market data: {e}")
        return pd.DataFrame()


def update_sheet1_fno_losers():
    """Download market losers and overwrite Sheet1 in Google Sheets."""
    print("⚡ Updating Sheet1 with top F&O losers...")
    df_losers = fetch_fno_top_losers(top_n=15)
    
    if df_losers.empty:
        print("⚠️ No data gathered for Sheet1 update.")
        return

    source_sheet = get_sheet("Sheet1")
    source_sheet.clear()  # Wipes stale content
    
    # Structure data payload for Google Sheets batch update
    headers = ["Ticker", "Current Price", "Day Return (%)", "Short Trade Trigger"]
    data_matrix = [headers] + df_losers.values.tolist()
    
    source_sheet.update(range_name="A1", values=data_matrix)
    print(f"✅ Sheet1 successfully updated with top {len(df_losers)} losing F&O stocks!")


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

    if not confirmed_short_trades:
        print("ℹ️ No 'SELL CONFIRMED' signals found to post.")
        return

    exec_sheet = get_sheet("Execution")
    
    headers = [
        "Execution-Timestamp", "Symbol", "Trade Type", "Close Price", 
        "% Change", "RSI (14)", "Suggested Option", 
        "Target Price", "Stop Loss", "Execution Status"
    ]
    
    # Initialize sheet header if sheet is completely fresh
    existing_records = exec_sheet.get_all_values()
    if not existing_records:
        exec_sheet.append_row(headers)

    exec_sheet.append_rows(confirmed_short_trades)
    print(f"🚀 Successfully appended {len(confirmed_short_trades)} records to Execution sheet!")


# ==========================================
# 5. Pipeline Entry Point
# ==========================================
def main():
    print("=== STARTING AUTOMATED PIPELINE ===")
    
    # Step 1: Update Sheet1 with live F&O losers
    update_sheet1_fno_losers()
    
    # Brief delay to allow sheet state synchronization
    time.sleep(2)
    
    # Step 2: Read Sheet1 and execute short trade pipeline
    process_short_execution()
    
    print("=== PIPELINE EXECUTION COMPLETE ===")

if __name__ == "__main__":
    main()

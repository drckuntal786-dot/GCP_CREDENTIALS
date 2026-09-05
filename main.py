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
    or "1yfFGrDViitvuqPhSyDstFebsntpo7pZ8b0s4f-oYH4E"
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

def get_sheet(sheet_name: str = "BTST_STBT_Signals"):
    if not os.path.exists(SERVICE_ACCOUNT_FILE):
        print(f"❌ ERROR: {SERVICE_ACCOUNT_FILE} not found!")
        sys.exit(1)
        
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(SPREADSHEET_ID)
    
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

def calculate_cpr(prev_high: float, prev_low: float, prev_close: float):
    pivot = (prev_high + prev_low + prev_close) / 3.0
    bc = (prev_high + prev_low) / 2.0
    tc = (pivot - bc) + pivot
    cpr_high = max(pivot, bc, tc)
    cpr_low = min(pivot, bc, tc)
    return pivot, cpr_high, cpr_low

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
# 3. Liquid Nifty F&O Universe
# ==========================================
NIFTY_FNO_UNIVERSE = [
    "RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS", "ICICIBANK.NS", "SBIN.NS",
    "BHARTIARTL.NS", "AXISBANK.NS", "KOTAKBANK.NS", "LT.NS", "TATAMOTORS.NS",
    "TATASTEEL.NS", "NTPC.NS", "POWERGRID.NS", "HCLTECH.NS", "MARUTI.NS",
    "SUNPHARMA.NS", "TITAN.NS", "ULTRACEMCO.NS", "BAJFINANCE.NS", "BAJAJFINSV.NS",
    "ASIANPAINT.NS", "NESTLEIND.NS", "JSWSTEEL.NS", "GRASIM.NS", "HEROMOTOCO.NS",
    "EICHERMOT.NS", "COALINDIA.NS", "BPCL.NS", "ONGC.NS", "HINDALCO.NS",
    "ADANIENT.NS", "ADANIPORTS.NS", "TECHM.NS", "WIPRO.NS", "DIVISLAB.NS",
    "DRREDDY.NS", "CIPLA.NS", "APOLLOHOSP.NS", "TATACONSUM.NS", "BRITANNIA.NS"
]


# ==========================================
# 4. Multi-Factor Stock Screener
# ==========================================
def analyze_stock(ticker: str, nifty_change_pct: float):
    try:
        # Fetch Daily Data (1 year)
        df_daily = yf.download(ticker, period="1y", interval="1d", progress=False)
        if len(df_daily) < 50:
            return None
            
        # Fetch Intraday 15M Data for ORB
        df_15m = yf.download(ticker, period="5d", interval="15m", progress=False)
        
        # Flatten MultiIndex headers if returned by yfinance
        if isinstance(df_daily.columns, pd.MultiIndex):
            df_daily.columns = df_daily.columns.get_level_values(0)
        if len(df_15m) > 0 and isinstance(df_15m.columns, pd.MultiIndex):
            df_15m.columns = df_15m.columns.get_level_values(0)

        latest_day = df_daily.iloc[-1]
        prev_day = df_daily.iloc[-2]
        
        close_price = float(latest_day['Close'])
        open_price = float(latest_day['Open'])
        high_price = float(latest_day['High'])
        low_price = float(latest_day['Low'])
        volume = float(latest_day['Volume'])
        
        prev_close = float(prev_day['Close'])
        prev_open = float(prev_day['Open'])
        prev_high = float(prev_day['High'])
        prev_low = float(prev_day['Low'])
        
        # 1. Price Momentum & Relative Strength vs Nifty 50
        price_change_pct = ((close_price - prev_close) / prev_close) * 100.0
        relative_strength = price_change_pct - nifty_change_pct

        # 2. Moving Average Alignment (20 & 50 EMA)
        ema20 = float(df_daily['Close'].ewm(span=20, adjust=False).mean().iloc[-1])
        ema50 = float(df_daily['Close'].ewm(span=50, adjust=False).mean().iloc[-1])
        bullish_ema = (close_price > ema20) and (ema20 > ema50)
        bearish_ema = (close_price < ema20) and (ema20 < ema50)

        # 3. Volume Surge vs 10-day Average
        vol_10d_avg = float(df_daily['Volume'].iloc[-11:-1].mean())
        vol_surge_ratio = volume / vol_10d_avg if vol_10d_avg > 0 else 0
        volume_check = vol_surge_ratio >= 1.8

        # 4. RSI Bounds
        rsi = calculate_rsi(df_daily['Close'])
        bullish_rsi = (60.0 <= rsi <= 75.0)
        bearish_rsi = (25.0 <= rsi <= 40.0)

        # 5. CPR Status
        _, cpr_high, cpr_low = calculate_cpr(prev_high, prev_low, prev_close)
        above_cpr_high = close_price > cpr_high
        below_cpr_low = close_price < cpr_low

        # 6. 52-Week High Proximity
        high_52w = float(df_daily['High'].max())
        pct_52w_high = (high_price / high_52w) * 100.0

        # 7. Day Range Close Position
        day_range = high_price - low_price
        close_pos_pct = ((close_price - low_price) / day_range) * 100.0 if day_range > 0 else 50.0
        closing_near_high = close_pos_pct >= 80.0
        closing_near_low = close_pos_pct <= 20.0

        # 8. 15-Min ORB Breakout Check
        orb_breakout_bull = False
        orb_breakout_bear = False
        if len(df_15m) >= 2:
            first_15m_high = float(df_15m['High'].iloc[0])
            first_15m_low = float(df_15m['Low'].iloc[0])
            orb_breakout_bull = close_price > first_15m_high
            orb_breakout_bear = close_price < first_15m_low

        # 9. ATR Target & Stop Loss
        atr = calculate_atr(df_daily)
        btst_target = round(close_price + (1.2 * atr), 2)
        btst_sl = round(close_price - (0.8 * atr), 2)
        stbt_target = round(close_price - (1.2 * atr), 2)
        stbt_sl = round(close_price + (0.8 * atr), 2)

        # ATM Option Strike Selection
        clean_symbol = ticker.replace(".NS", "")
        atm_strike = get_atm_strike(close_price)

        # Composite Ranking Scores (0-100 Scale)
        bullish_score = 0
        if bullish_rsi: bullish_score += 20
        if volume_check: bullish_score += 20
        if bullish_ema: bullish_score += 15
        if relative_strength > 1.0: bullish_score += 15
        if above_cpr_high: bullish_score += 10
        if orb_breakout_bull: bullish_score += 10
        if closing_near_high: bullish_score += 10

        bearish_score = 0
        if bearish_rsi: bearish_score += 20
        if volume_check: bearish_score += 20
        if bearish_ema: bearish_score += 15
        if relative_strength < -1.0: bearish_score += 15
        if below_cpr_low: bearish_score += 10
        if orb_breakout_bear: bearish_score += 10
        if closing_near_low: bearish_score += 10

        return {
            "Symbol": clean_symbol,
            "Close": round(close_price, 2),
            "Price_Change_%": round(price_change_pct, 2),
            "Rel_Strength": round(relative_strength, 2),
            "RSI": round(rsi, 2),
            "Vol_Surge_x": round(vol_surge_ratio, 2),
            "Close_Pos_%": round(close_pos_pct, 1),
            "CPR_Status": "ABOVE CPR HIGH" if above_cpr_high else ("BELOW CPR LOW" if below_cpr_low else "INSIDE CPR"),
            "EMA_Trend": "BULLISH (20>50)" if bullish_ema else ("BEARISH (20<50)" if bearish_ema else "NEUTRAL"),
            "%_52W_High": round(pct_52w_high, 1),
            "ATM_Strike": atm_strike,
            "Bull_Target": btst_target,
            "Bull_SL": btst_sl,
            "Bear_Target": stbt_target,
            "Bear_SL": stbt_sl,
            "Bullish_Score": bullish_score,
            "Bearish_Score": bearish_score
        }
    except Exception as e:
        print(f"Skipping {ticker}: {e}")
        return None


# ==========================================
# 5. Pipeline Tasks & Schedule Routines
# ==========================================
def detect_run_phase() -> str:
    """Detect run phase from ENV variable or system UTC hour/minute."""
    env_mode = os.environ.get("RUN_MODE", "").strip().upper()
    if env_mode in ["PRE_MARKET", "EXECUTION", "NIGHTLY_RESET"]:
        return env_mode

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    hour, minute = now_utc.hour, now_utc.minute

    # Schedule match (cron 38 3 * * 1-5 -> ~03:38 UTC)
    if hour == 3 and 30 <= minute <= 45:
        return "PRE_MARKET"
    # Schedule match (cron 0 4 * * 1-5 -> ~04:00 UTC)
    elif hour == 4 and minute <= 15:
        return "EXECUTION"
    # Schedule match (cron 0 22 * * 0-4 -> ~22:00 UTC)
    elif hour == 22 and minute <= 15:
        return "NIGHTLY_RESET"
    
    # Default fallback for workflow_dispatch manual runs
    return "EXECUTION"

def run_pre_market_scan():
    """Phase 1: Pre-Market Scan (9:08 AM IST / 03:38 AM UTC)"""
    print("🔍 Executing Pre-Market Scan...")
    sheet = get_sheet("PreMarket_Scan")
    timestamp_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Optional light check / index gap check
    try:
        nifty = yf.Ticker("^NSEI").history(period="2d")
        last_close = nifty['Close'].iloc[-1]
    except Exception as e:
        last_close = 0.0

    sheet.clear()
    headers = ["Timestamp", "Phase", "Status", "Nifty Prev Close"]
    sheet.append_row(headers)
    sheet.append_row([timestamp_str, "Pre-Market Scan", "COMPLETED", round(last_close, 2)])
    print("✓ Pre-Market Scan complete!")

def run_execution_screener():
    """Phase 2: Full Strategy Execution Run (9:30 AM IST / 04:00 AM UTC)"""
    print(f"[{datetime.datetime.now()}] Fetching Nifty 50 Index Performance...")
    try:
        nifty = yf.Ticker("^NSEI").history(period="5d")
        nifty_close = nifty['Close'].iloc[-1]
        nifty_prev = nifty['Close'].iloc[-2]
        nifty_change_pct = ((nifty_close - nifty_prev) / nifty_prev) * 100.0
    except Exception:
        nifty_change_pct = 0.0

    print(f"Index (Nifty 50) Day Change: {nifty_change_pct:.2f}%")
    sheet = get_sheet("BTST_STBT_Signals")
    
    results = []
    for ticker in NIFTY_FNO_UNIVERSE:
        data = analyze_stock(ticker, nifty_change_pct)
        if data:
            results.append(data)

    df_results = pd.DataFrame(results)
    if df_results.empty:
        print("No data fetched.")
        return

    # Filter and rank Top 5 Bullish (BTST) & Top 5 Bearish (STBT)
    top_bullish = df_results.sort_values(by="Bullish_Score", ascending=False).head(5)
    top_bearish = df_results.sort_values(by="Bearish_Score", ascending=False).head(5)

    timestamp_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted_rows = []

    # Format Bullish Entries
    for rank, (_, row) in enumerate(top_bullish.iterrows(), 1):
        action_label = "HIGH CONVICTION BUY" if row['Bullish_Score'] >= 80 else "STRONG BUY (BTST)"
        formatted_rows.append([
            "BTST (Bullish)", rank, timestamp_str, row['Symbol'], row['Close'],
            f"{row['Price_Change_%']}%", f"+{row['Rel_Strength']}%", row['RSI'],
            f"{row['Vol_Surge_x']}x", f"{row['Close_Pos_%']}%", row['CPR_Status'],
            row['EMA_Trend'], f"{row['%_52W_High']}%", f"BUY {row['ATM_Strike']} CE",
            row['Bull_Target'], row['Bull_SL'], f"{row['Bullish_Score']}/100", action_label
        ])

    # Format Bearish Entries
    for rank, (_, row) in enumerate(top_bearish.iterrows(), 1):
        action_label = "HIGH CONVICTION SELL" if row['Bearish_Score'] >= 80 else "STRONG SELL (STBT)"
        formatted_rows.append([
            "STBT (Bearish)", rank, timestamp_str, row['Symbol'], row['Close'],
            f"{row['Price_Change_%']}%", f"{row['Rel_Strength']}%", row['RSI'],
            f"{row['Vol_Surge_x']}x", f"{row['Close_Pos_%']}%", row['CPR_Status'],
            row['EMA_Trend'], f"{row['%_52W_High']}%", f"BUY {row['ATM_Strike']} PE",
            row['Bear_Target'], row['Bear_SL'], f"{row['Bearish_Score']}/100", action_label
        ])

    headers = [
        "Signal Type", "Rank", "Timestamp", "Ticker", "Close Price", "% Change",
        "Rel Strength (vs Nifty)", "RSI (14)", "Volume Surge", "Close Range %",
        "CPR Status", "EMA Trend (20/50)", "% of 52W High", "Suggested Option",
        "Target Price (ATR)", "Stop Loss (ATR)", "Quality Score", "Final Action"
    ]

    sheet.clear()
    sheet.append_row(headers)
    sheet.append_rows(formatted_rows)
    print(f"✓ Google Sheet successfully updated with {len(formatted_rows)} ranked trades!")

def run_nightly_reset():
    """Phase 3: Nightly Reset / Preparation Run (3:30 AM IST / 10:00 PM UTC)"""
    print("🌙 Running Nightly Reset & Preparation...")
    sheet = get_sheet("BTST_STBT_Signals")
    
    # Mark old signals as expired / archive state
    timestamp_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    reset_header = ["Status", "Last Reset Time", "Message"]
    reset_row = ["CLEARED", timestamp_str, "Sheet cleared in preparation for next trading day."]
    
    sheet.clear()
    sheet.append_row(reset_header)
    sheet.append_row(reset_row)
    print("✓ Nightly reset completed successfully!")


# ==========================================
# 6. Main Controller Entrypoint
# ==========================================
if __name__ == "__main__":
    phase = detect_run_phase()
    print(f"🚀 Execution started for phase: [{phase}]")

    if phase == "PRE_MARKET":
        run_pre_market_scan()
    elif phase == "NIGHTLY_RESET":
        run_nightly_reset()
    else:
        # Default to full execution run
        run_execution_screener()

    sys.exit(0)

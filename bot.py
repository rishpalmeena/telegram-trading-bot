import os
import time
import requests
import pandas as pd
from threading import Thread
from flask import Flask
from datetime import datetime
import pytz

from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator, MACD
from ta.volatility import AverageTrueRange

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

SCAN_INTERVAL = 900
COOLDOWN_SECONDS = 900
MAX_AUTO_RISK_PERCENT = 1.5
TOP_ALERT_LIMIT = 5
MIN_CONFIDENCE = 90

WATCHLIST = [
    "BTC","ETH","BNB","SOL","XRP","ADA","DOGE","AVAX",
    "LINK","DOT","TRX","TON","LTC","SHIB","PEPE",
    "ARB","OP","APT","SUI","NEAR","ATOM","HBAR",
    "FIL","INJ","ICP","SEI","TIA","UNI","AAVE",
    "RUNE","STX","POL","RENDER","FET","GRT","LDO",
    "ENS","SNX","CRV","CAKE","COMP","DYDX","JUP",
    "ENA","W","STRK","ZRO","NOT","BONK","FLOKI"
]

SYMBOL_MAP = {
    "MATIC": ["POL", "MATIC"],
    "POL": ["POL", "MATIC"],
    "RNDR": ["RENDER", "RNDR"],
    "RENDER": ["RENDER", "RNDR"],
    "FET": ["FET"],
    "ASI": ["FET"]
}

ALERTS_ON = True
LAST_ALERT_TIME = {}

web_app = Flask(__name__)

@web_app.route("/")
def home():
    return "Advanced scanner bot is running"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    web_app.run(host="0.0.0.0", port=port)

def get_india_time():
    india = pytz.timezone("Asia/Kolkata")
    return datetime.now(india).strftime("%Y-%m-%d %H:%M:%S IST")

def possible_symbols(symbol):
    return SYMBOL_MAP.get(symbol.upper(), [symbol.upper()])

def get_binance_data(symbol, interval="15m"):

    url = "https://api.binance.com/api/v3/klines"

    params = {
        "symbol": symbol.upper() + "USDT",
        "interval": interval,
        "limit": 150
    }

    response = requests.get(
        url,
        params=params,
        timeout=15
    )

    if response.status_code != 200:
        raise Exception("Binance API error")

    data = response.json()

    if not isinstance(data, list):
        raise Exception("Binance invalid data")

    df = pd.DataFrame(data, columns=[
        "time","open","high","low","close","volume",
        "close_time","qav","trades","tbbav","tbqav","ignore"
    ])

    for col in ["open","high","low","close","volume"]:
        df[col] = df[col].astype(float)

    return df, "Binance", symbol.upper()


def get_okx_data(symbol, interval="15m"):

    okx_map = {
        "15m": "15m",
        "1h": "1H"
    }

    url = "https://www.okx.com/api/v5/market/candles"

    params = {
        "instId": symbol.upper() + "-USDT",
        "bar": okx_map.get(interval, "15m"),
        "limit": "150"
    }

    response = requests.get(
        url,
        params=params,
        timeout=15
    )

    if response.status_code != 200:
        raise Exception("OKX API error")

    data = response.json()

    if "data" not in data:
        raise Exception("OKX invalid data")

    candles = data["data"]

    if not candles:
        raise Exception("OKX empty data")

    candles.reverse()

    df = pd.DataFrame(candles, columns=[
        "time","open","high","low","close",
        "volume","volCcy","volCcyQuote","confirm"
    ])

    for col in ["open","high","low","close","volume"]:
        df[col] = df[col].astype(float)

    return df, "OKX", symbol.upper()


def get_data(symbol, interval="15m"):

    for sym in possible_symbols(symbol):

        try:
            return get_binance_data(sym, interval)

        except Exception as e1:

            print(f"Binance failed {sym}: {e1}")

            try:
                return get_okx_data(sym, interval)

            except Exception as e2:

                print(f"OKX failed {sym}: {e2}")

                continue

    raise Exception("No live exchange data found")

    df = pd.DataFrame(data, columns=[
        "time","open","high","low","close","volume",
        "close_time","qav","trades","tbbav","tbqav","ignore"
    ])

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)

    return df, "Binance", symbol.upper()

def get_okx_data(symbol, interval="15m"):
    okx_interval = interval

    url = "https://www.okx.com/api/v5/market/candles"
    params = {
        "instId": symbol.upper() + "-USDT",
        "bar": okx_interval,
        "limit": "150"
    }

    data = requests.get(url, params=params, timeout=10).json()

    if "data" not in data or not data["data"]:
        raise Exception("OKX data not found")

    candles = data["data"]
    candles.reverse()

    df = pd.DataFrame(candles, columns=[
        "time","open","high","low","close",
        "volume","volCcy","volCcyQuote","confirm"
    ])

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)

    return df, "OKX", symbol.upper()

def get_data(symbol, interval="15m"):
    for sym in possible_symbols(symbol):
        try:
            return get_binance_data(sym, interval)
        except Exception:
            try:
                return get_okx_data(sym, interval)
            except Exception:
                continue

    raise Exception("No live exchange data found")

def add_indicators(df):
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    df["ema20"] = EMAIndicator(close, window=20).ema_indicator()
    df["ema50"] = EMAIndicator(close, window=50).ema_indicator()
    df["ema200"] = EMAIndicator(close, window=200 if len(df) >= 200 else 100).ema_indicator()

    df["rsi"] = RSIIndicator(close, window=14).rsi()

    macd = MACD(close)
    df["macd"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()
    df["macd_hist"] = macd.macd_diff()

    df["atr"] = AverageTrueRange(high, low, close, window=14).average_true_range()
    df["volume_avg"] = volume.rolling(20).mean()

    return df

def analyze_coin(coin):
    scan_time = get_india_time()

    df15, source, used_symbol = get_data(coin, "15m")
    df1h, _, _ = get_data(coin, "1h")

    df15 = add_indicators(df15)
    df1h = add_indicators(df1h)

    last = df15.iloc[-1]
    prev = df15.iloc[-2]
    htf = df1h.iloc[-1]

    price = float(last["close"])
    ema20 = float(last["ema20"])
    ema50 = float(last["ema50"])
    rsi = float(last["rsi"])
    atr = float(last["atr"])
    volume = float(last["volume"])
    volume_avg = float(last["volume_avg"])
    macd_hist = float(last["macd_hist"])

    htf_ema20 = float(htf["ema20"])
    htf_ema50 = float(htf["ema50"])

    confidence = 0
    reasons = []

    buy_trend = ema20 > ema50
    sell_trend = ema20 < ema50

    buy_htf = htf_ema20 > htf_ema50
    sell_htf = htf_ema20 < htf_ema50

    buy_rsi = 55 <= rsi <= 72
    sell_rsi = 28 <= rsi <= 45

    buy_macd = macd_hist > 0
    sell_macd = macd_hist < 0

    volume_ok = volume > volume_avg

    if buy_trend:
        confidence += 20
        reasons.append("15m trend bullish")
    if sell_trend:
        confidence += 20
        reasons.append("15m trend bearish")

    if buy_htf:
        confidence += 25
        reasons.append("1h trend bullish")
    if sell_htf:
        confidence += 25
        reasons.append("1h trend bearish")

    if buy_rsi or sell_rsi:
        confidence += 20
        reasons.append("RSI momentum valid")

    if buy_macd or sell_macd:
        confidence += 20
        reasons.append("MACD confirmation")

    if volume_ok:
        confidence += 15
        reasons.append("Volume above average")

    if buy_trend and buy_htf and buy_rsi and buy_macd and volume_ok:
        signal = "BUY 🟢"
        entry = price
        sl = price - (atr * 1.5)
        t1 = price + (atr * 2)
        t2 = price + (atr * 3)

    elif sell_trend and sell_htf and sell_rsi and sell_macd and volume_ok:
        signal = "SELL 🔴"
        entry = price
        sl = price + (atr * 1.5)
        t1 = price - (atr * 2)
        t2 = price - (atr * 3)

    else:
        return {
            "coin": coin.upper(),
            "used_symbol": used_symbol,
            "source": source,
            "scan_time": scan_time,
            "signal": "NO TRADE",
            "current_price": price,
            "rsi": rsi,
            "confidence": confidence,
            "reason": "Advanced filters match nahi hue.",
            "checks": ", ".join(reasons) if reasons else "No strong confirmation"
        }

    risk_percent = abs(((entry - sl) / entry) * 100)

    if risk_percent <= 1.5:
        risk_level = "Low Risk ✅"
    elif risk_percent <= 3:
        risk_level = "Medium Risk ⚠️"
    else:
        risk_level = "High Risk 🔴"

    return {
        "coin": coin.upper(),
        "used_symbol": used_symbol,
        "source": source,
        "scan_time": scan_time,
        "signal": signal,
        "current_price": price,
        "entry": entry,
        "sl": sl,
        "t1": t1,
        "t2": t2,
        "rsi": rsi,
        "risk_percent": risk_percent,
        "risk_level": risk_level,
        "confidence": confidence,
        "checks": ", ".join(reasons)
    }

def format_signal_alert(result):
    return f"""
🚨 Advanced Fresh Trading Signal

Scan Time: {result['scan_time']}

Coin: {result['coin']}USDT
Data Source: {result['source']}
Used Symbol: {result['used_symbol']}USDT

Signal: {result['signal']}
Confidence Score: {result['confidence']}%

Current Price: {result['current_price']:.8f}

Entry: {result['entry']:.8f}
Stop Loss: {result['sl']:.8f}

Target 1: {result['t1']:.8f}
Target 2: {result['t2']:.8f}

RSI: {result['rsi']:.2f}

Risk: {result['risk_percent']:.2f}%
Risk Level: {result['risk_level']}

Confirmations:
{result['checks']}

Note:
Ye fresh live scan ke baad signal hai.
🙏Trad Your Risk, lekin advanced filters fake signals kam karte hain.
"""

def format_no_trade(result):
    return f"""
📊 Advanced Fresh Analysis

Scan Time: {result['scan_time']}

Coin: {result['coin']}USDT
Data Source: {result['source']}
Used Symbol: {result['used_symbol']}USDT

Current Price: {result['current_price']:.8f}
RSI: {result['rsi']:.2f}
Confidence Score: {result['confidence']}%

Signal: NO TRADE ❌
Reason: {result['reason']}

Checks:
{result['checks']}

Note:
Ye result fresh live scan ke baad hai.
"""

def send_telegram(message):
    if not CHAT_ID:
        print("CHAT_ID missing")
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    data = {
        "chat_id": CHAT_ID,
        "text": message
    }

    requests.post(url, data=data, timeout=10)

def scanner_loop():
    global ALERTS_ON

    while True:
        try:
            if ALERTS_ON:
                now = time.time()
                valid_signals = []

                for coin in WATCHLIST:
                    try:
                        result = analyze_coin(coin)

                        if result["signal"] == "NO TRADE":
                            print(f"{coin}: No trade after fresh scan")
                            continue

                        if result["risk_percent"] > MAX_AUTO_RISK_PERCENT:
                            print(f"{coin}: Risk not low")
                            continue

                        if result["confidence"] < MIN_CONFIDENCE:
                            print(f"{coin}: Confidence low")
                            continue

                        last_time = LAST_ALERT_TIME.get(coin, 0)

                        if now - last_time < COOLDOWN_SECONDS:
                            print(f"{coin}: Cooldown active")
                            continue

                        valid_signals.append(result)

                    except Exception as e:
                        print(f"{coin} scan error:", e)

                valid_signals = sorted(
                    valid_signals,
                    key=lambda x: (-x["confidence"], x["risk_percent"])
                )

                top_signals = valid_signals[:TOP_ALERT_LIMIT]

                for signal in top_signals:
                    send_telegram(format_signal_alert(signal))
                    LAST_ALERT_TIME[signal["coin"]] = now
                    print(f"Top advanced alert sent for {signal['coin']}")

            time.sleep(SCAN_INTERVAL)

        except Exception as e:
            print("Scanner loop error:", e)
            time.sleep(60)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Bot ready hai 🚀\n\n"
        "Commands:\n"
        "/analyze btc\n"
        "/startalerts\n"
        "/stopalerts\n"
        "/status\n"
        "/watchlist"
    )

async def analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Example:\n/analyze btc")
        return

    coin = context.args[0].upper()

    try:
        result = analyze_coin(coin)

        if result["signal"] == "NO TRADE":
            await update.message.reply_text(format_no_trade(result))
        else:
            await update.message.reply_text(format_signal_alert(result))

    except Exception:
        await update.message.reply_text(
            f"❌ {coin} ka fresh data Binance/OKX dono par nahi mila."
        )

async def startalerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ALERTS_ON
    ALERTS_ON = True
    await update.message.reply_text("✅ Auto alerts ON ho gaye.")

async def stopalerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ALERTS_ON
    ALERTS_ON = False
    await update.message.reply_text("🛑 Auto alerts OFF ho gaye.")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_text = "ON ✅" if ALERTS_ON else "OFF 🛑"

    await update.message.reply_text(
        f"Auto alerts: {status_text}\n"
        f"Scan interval: 15 minutes\n"
        f"Top alerts per scan: {TOP_ALERT_LIMIT}\n"
        f"Low Risk only: max {MAX_AUTO_RISK_PERCENT}%\n"
        f"Minimum confidence: {MIN_CONFIDENCE}%\n"
        f"Timezone: Asia/Kolkata IST"
    )

async def watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Watchlist:\n\n" + ", ".join(WATCHLIST)
    )

Thread(target=run_web).start()
Thread(target=scanner_loop).start()

app = ApplicationBuilder().token(BOT_TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("analyze", analyze))
app.add_handler(CommandHandler("startalerts", startalerts))
app.add_handler(CommandHandler("stopalerts", stopalerts))
app.add_handler(CommandHandler("status", status))
app.add_handler(CommandHandler("watchlist", watchlist))

app.run_polling()

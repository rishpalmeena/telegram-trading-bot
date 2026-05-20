import os
import time
import requests
import pandas as pd
from threading import Thread
from flask import Flask
from datetime import datetime
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator
from ta.volatility import AverageTrueRange
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

SCAN_INTERVAL = 900
COOLDOWN_SECONDS = 900
MAX_AUTO_RISK_PERCENT = 1.5

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
    return "Auto scanner bot is running"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    web_app.run(host="0.0.0.0", port=port)

def possible_symbols(symbol):
    return SYMBOL_MAP.get(symbol.upper(), [symbol.upper()])

def get_binance_data(symbol):
    url = "https://api.binance.com/api/v3/klines"

    params = {
        "symbol": symbol.upper() + "USDT",
        "interval": "15m",
        "limit": 120
    }

    data = requests.get(
        url,
        params=params,
        timeout=10
    ).json()

    if not isinstance(data, list):
        raise Exception("Binance data not found")

    df = pd.DataFrame(data, columns=[
        "time","open","high","low","close","volume",
        "close_time","qav","trades","tbbav","tbqav","ignore"
    ])

    df["open"] = df["open"].astype(float)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)
    df["close"] = df["close"].astype(float)

    return df, "Binance", symbol.upper()

def get_okx_data(symbol):
    url = "https://www.okx.com/api/v5/market/candles"

    params = {
        "instId": symbol.upper() + "-USDT",
        "bar": "15m",
        "limit": "120"
    }

    data = requests.get(
        url,
        params=params,
        timeout=10
    ).json()

    if "data" not in data or not data["data"]:
        raise Exception("OKX data not found")

    candles = data["data"]

    candles.reverse()

    df = pd.DataFrame(candles, columns=[
        "time","open","high","low","close",
        "volume","volCcy","volCcyQuote","confirm"
    ])

    df["open"] = df["open"].astype(float)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)
    df["close"] = df["close"].astype(float)

    return df, "OKX", symbol.upper()

def get_data(symbol):
    for sym in possible_symbols(symbol):

        try:
            return get_binance_data(sym)

        except Exception:

            try:
                return get_okx_data(sym)

            except Exception:
                continue

    raise Exception("No live exchange data found")

def analyze_coin(coin):

    scan_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    df, source, used_symbol = get_data(coin)

    close = df["close"]
    high = df["high"]
    low = df["low"]

    df["ema20"] = EMAIndicator(
        close,
        window=20
    ).ema_indicator()

    df["ema50"] = EMAIndicator(
        close,
        window=50
    ).ema_indicator()

    df["rsi"] = RSIIndicator(
        close,
        window=14
    ).rsi()

    df["atr"] = AverageTrueRange(
        high,
        low,
        close,
        window=14
    ).average_true_range()

    last = df.iloc[-1]

    price = float(last["close"])
    ema20 = float(last["ema20"])
    ema50 = float(last["ema50"])
    rsi = float(last["rsi"])
    atr = float(last["atr"])

    if ema20 > ema50 and rsi > 55:

        signal = "BUY 🟢"

        entry = price

        sl = price - (atr * 1.5)

        t1 = price + (atr * 2)

        t2 = price + (atr * 3)

    elif ema20 < ema50 and rsi < 45:

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
            "reason": "Clear BUY/SELL condition match nahi hui."
        }

    risk_percent = abs(
        ((entry - sl) / entry) * 100
    )

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
        "risk_level": risk_level
    }

def format_signal_alert(result):

    return f"""
🚨 Fresh Trading Signal

Scan Time: {result['scan_time']}

Coin: {result['coin']}USDT

Data Source: {result['source']}

Used Symbol: {result['used_symbol']}USDT

Signal: {result['signal']}

Current Price: {result['current_price']:.8f}

Entry: {result['entry']:.8f}

Stop Loss: {result['sl']:.8f}

Target 1: {result['t1']:.8f}

Target 2: {result['t2']:.8f}

RSI: {result['rsi']:.2f}

Risk: {result['risk_percent']:.2f}%

Risk Level: {result['risk_level']}

Note:
Ye fresh live scan ke baad signal hai.

"""

def format_no_trade(result):

    return f"""
📊 Fresh Analysis

Scan Time: {result['scan_time']}

Coin: {result['coin']}USDT

Data Source: {result['source']}

Used Symbol: {result['used_symbol']}USDT

Current Price: {result['current_price']:.8f}

RSI: {result['rsi']:.2f}

Signal: NO TRADE ❌

Reason: {result['reason']}

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

    requests.post(
        url,
        data=data,
        timeout=10
    )

def scanner_loop():

    global ALERTS_ON

    while True:

        try:

            if ALERTS_ON:

                now = time.time()

                for coin in WATCHLIST:

                    try:

                        result = analyze_coin(coin)

                        if result["signal"] == "NO TRADE":

                            print(
                                f"{coin}: No trade after fresh scan"
                            )

                            continue

                        risk_percent = result["risk_percent"]

                        if risk_percent > MAX_AUTO_RISK_PERCENT:

                            print(
                                f"{coin}: Signal found but risk not low"
                            )

                            continue

                        last_time = LAST_ALERT_TIME.get(
                            coin,
                            0
                        )

                        if now - last_time < COOLDOWN_SECONDS:

                            print(
                                f"{coin}: Cooldown active"
                            )

                            continue

                        send_telegram(
                            format_signal_alert(result)
                        )

                        LAST_ALERT_TIME[coin] = now

                    except Exception as e:

                        print(f"{coin} scan error:", e)

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

        await update.message.reply_text(
            "Example:\n/analyze btc"
        )

        return

    coin = context.args[0].upper()

    try:

        result = analyze_coin(coin)

        if result["signal"] == "NO TRADE":

            await update.message.reply_text(
                format_no_trade(result)
            )

        else:

            await update.message.reply_text(
                format_signal_alert(result)
            )

    except Exception:

        await update.message.reply_text(
            f"❌ {coin} ka fresh data Binance/OKX dono par nahi mila."
        )

async def startalerts(update: Update, context: ContextTypes.DEFAULT_TYPE):

    global ALERTS_ON

    ALERTS_ON = True

    await update.message.reply_text(
        "✅ Auto alerts ON ho gaye."
    )

async def stopalerts(update: Update, context: ContextTypes.DEFAULT_TYPE):

    global ALERTS_ON

    ALERTS_ON = False

    await update.message.reply_text(
        "🛑 Auto alerts OFF ho gaye."
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):

    status_text = "ON ✅" if ALERTS_ON else "OFF 🛑"

    await update.message.reply_text(
        f"Auto alerts: {status_text}\n"
        f"Scan interval: 15 minutes\n"
        f"Low Risk signals only"
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

import os
import json
import time
import asyncio
import csv
from collections import deque
from datetime import datetime
from threading import Thread

import pandas as pd
import pytz
import websockets
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
TIMEFRAME = "1m"

COOLDOWN_SECONDS = 900
MIN_CONFIDENCE = 75
MAX_RISK_PERCENT = 1.5
RISK_REWARD = 2.0

ALERTS_ON = True

CANDLES = {s: deque(maxlen=250) for s in SYMBOLS}
LAST_ALERT = {}
OPEN_SIGNALS = {}

LOG_FILE = "signal_log.csv"

web = Flask(__name__)

@web.route("/")
def home():
    return "WebSocket Candlestick Bot Running"

def run_web():
    web.run(host="0.0.0.0", port=10000)

def india_time():
    tz = pytz.timezone("Asia/Kolkata")
    return datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S IST")

def ema(series, length):
    return series.ewm(span=length, adjust=False).mean()

def atr(df, length=14):
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return tr.rolling(length).mean()

def log_signal(row):
    file_exists = os.path.exists(LOG_FILE)

    with open(LOG_FILE, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=row.keys())
        if not file_exists:
            writer.writeheader()
        writer.writerow(row)

def candle_parts(c):
    body = abs(c["close"] - c["open"])
    candle_range = max(c["high"] - c["low"], 0.00000001)
    upper_wick = c["high"] - max(c["open"], c["close"])
    lower_wick = min(c["open"], c["close"]) - c["low"]
    bullish = c["close"] > c["open"]
    bearish = c["close"] < c["open"]
    return body, candle_range, upper_wick, lower_wick, bullish, bearish

def detect_candlestick_pattern(df):
    if len(df) < 5:
        return None, None, 0

    c1 = df.iloc[-1].to_dict()
    c2 = df.iloc[-2].to_dict()
    c3 = df.iloc[-3].to_dict()

    b1, r1, uw1, lw1, bull1, bear1 = candle_parts(c1)
    b2, r2, uw2, lw2, bull2, bear2 = candle_parts(c2)
    b3, r3, uw3, lw3, bull3, bear3 = candle_parts(c3)

    # Doji
    if b1 / r1 <= 0.12:
        return "Doji", "Market indecision, trade avoid/confirmation wait", 5

    # Hammer
    if lw1 >= b1 * 2.2 and uw1 <= b1 * 0.7 and bull1:
        return "Hammer", "Bullish rejection from lower level", 20

    # Shooting Star
    if uw1 >= b1 * 2.2 and lw1 <= b1 * 0.7 and bear1:
        return "Shooting Star", "Bearish rejection from upper level", 20

    # Bullish Engulfing
    if bear2 and bull1 and c1["close"] > c2["open"] and c1["open"] < c2["close"]:
        return "Bullish Engulfing", "Strong buyer takeover", 25

    # Bearish Engulfing
    if bull2 and bear1 and c1["open"] > c2["close"] and c1["close"] < c2["open"]:
        return "Bearish Engulfing", "Strong seller takeover", 25

    # Morning Star
    if bear3 and b2 / r2 <= 0.35 and bull1 and c1["close"] > ((c3["open"] + c3["close"]) / 2):
        return "Morning Star", "Potential bullish reversal", 25

    # Evening Star
    if bull3 and b2 / r2 <= 0.35 and bear1 and c1["close"] < ((c3["open"] + c3["close"]) / 2):
        return "Evening Star", "Potential bearish reversal", 25

    # Piercing Pattern
    if bear2 and bull1 and c1["open"] < c2["close"] and c1["close"] > ((c2["open"] + c2["close"]) / 2):
        return "Piercing Pattern", "Bullish recovery after sell pressure", 20

    # Dark Cloud Cover
    if bull2 and bear1 and c1["open"] > c2["close"] and c1["close"] < ((c2["open"] + c2["close"]) / 2):
        return "Dark Cloud Cover", "Bearish rejection after buying pressure", 20

    # Marubozu Bullish
    if bull1 and uw1 / r1 <= 0.08 and lw1 / r1 <= 0.08 and b1 / r1 >= 0.8:
        return "Bullish Marubozu", "Strong bullish candle", 20

    # Marubozu Bearish
    if bear1 and uw1 / r1 <= 0.08 and lw1 / r1 <= 0.08 and b1 / r1 >= 0.8:
        return "Bearish Marubozu", "Strong bearish candle", 20

    return None, None, 0

def analyze_symbol(symbol):
    data = list(CANDLES[symbol])

    if len(data) < 80:
        return None

    df = pd.DataFrame(data)

    df["ema20"] = ema(df["close"], 20)
    df["ema50"] = ema(df["close"], 50)
    df["atr"] = atr(df, 14)
    df["vol_avg"] = df["volume"].rolling(20).mean()

    last = df.iloc[-1]
    prev_zone = df.iloc[-31:-2]

    price = float(last["close"])
    high = float(last["high"])
    low = float(last["low"])
    open_price = float(last["open"])
    volume = float(last["volume"])
    vol_avg = float(last["vol_avg"])
    atr_val = float(last["atr"])

    if pd.isna(atr_val) or pd.isna(vol_avg):
        return None

    swing_high = float(prev_zone["high"].max())
    swing_low = float(prev_zone["low"].min())

    body = abs(price - open_price)
    candle_range = max(high - low, 0.00000001)
    body_ratio = body / candle_range
    close_position = (price - low) / candle_range

    trend_buy = last["ema20"] > last["ema50"]
    trend_sell = last["ema20"] < last["ema50"]

    volume_spike = volume > vol_avg * 1.4

    pattern, pattern_meaning, pattern_score = detect_candlestick_pattern(df)

    confidence = 0
    reasons = []

    signal = None
    entry = price
    sl = None
    target = None

    bullish_patterns = [
        "Hammer",
        "Bullish Engulfing",
        "Morning Star",
        "Piercing Pattern",
        "Bullish Marubozu"
    ]

    bearish_patterns = [
        "Shooting Star",
        "Bearish Engulfing",
        "Evening Star",
        "Dark Cloud Cover",
        "Bearish Marubozu"
    ]

    # BUY setup
    if price > swing_high and trend_buy and pattern in bullish_patterns:
        confidence += 30
        reasons.append("Breakout above resistance")

        confidence += pattern_score
        reasons.append(f"Candlestick: {pattern}")

        if volume_spike:
            confidence += 20
            reasons.append("Volume spike confirmed")

        if body_ratio > 0.45:
            confidence += 10
            reasons.append("Strong candle body")

        if close_position > 0.65:
            confidence += 10
            reasons.append("Close near candle high")

        if atr_val / price < 0.025:
            confidence += 10
            reasons.append("Controlled volatility")

        if confidence >= MIN_CONFIDENCE:
            signal = "BUY 🟢"
            sl = min(swing_high, price - atr_val * 1.2)
            risk = price - sl
            target = price + risk * RISK_REWARD

    # SELL setup
    elif price < swing_low and trend_sell and pattern in bearish_patterns:
        confidence += 30
        reasons.append("Breakdown below support")

        confidence += pattern_score
        reasons.append(f"Candlestick: {pattern}")

        if volume_spike:
            confidence += 20
            reasons.append("Volume spike confirmed")

        if body_ratio > 0.45:
            confidence += 10
            reasons.append("Strong candle body")

        if close_position < 0.35:
            confidence += 10
            reasons.append("Close near candle low")

        if atr_val / price < 0.025:
            confidence += 10
            reasons.append("Controlled volatility")

        if confidence >= MIN_CONFIDENCE:
            signal = "SELL 🔴"
            sl = max(swing_low, price + atr_val * 1.2)
            risk = sl - price
            target = price - risk * RISK_REWARD

    if not signal:
        return None

    risk_percent = abs((entry - sl) / entry) * 100

    if risk_percent > MAX_RISK_PERCENT:
        return None

    return {
        "symbol": symbol,
        "signal": signal,
        "time": india_time(),
        "current_price": entry,
        "entry": entry,
        "sl": sl,
        "target": target,
        "risk_percent": risk_percent,
        "confidence": confidence,
        "pattern": pattern,
        "pattern_meaning": pattern_meaning,
        "reasons": ", ".join(reasons)
    }

def format_alert(result):
    return f"""
🚨 Real-Time Candlestick Signal

Time: {result['time']}
Coin: {result['symbol']}

Signal: {result['signal']}
Confidence: {result['confidence']}%

Candlestick Pattern: {result['pattern']}
Meaning: {result['pattern_meaning']}

Current Price: {result['current_price']:.8f}
Entry: {result['entry']:.8f}

Stop Loss: {result['sl']:.8f}
Target: {result['target']:.8f}

Risk: {result['risk_percent']:.2f}%

Confirmations:
{result['reasons']}

Note:
Ye live candle close hone ke baad Japanese candlestick + market structure filter se generate hua hai.
Guarantee nahi hoti. Paper/demo testing zaroor karein.
"""

def log_result(row):
    log_signal(row)

async def websocket_loop(app):
    streams = "/".join([f"{s.lower()}@kline_{TIMEFRAME}" for s in SYMBOLS])
    url = f"wss://stream.binance.com:9443/stream?streams={streams}"

    while True:
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                print("WebSocket connected")

                async for message in ws:
                    payload = json.loads(message)
                    k = payload["data"]["k"]

                    # Only closed candle
                    if not k["x"]:
                        continue

                    symbol = k["s"]

                    candle = {
                        "time": k["t"],
                        "open": float(k["o"]),
                        "high": float(k["h"]),
                        "low": float(k["l"]),
                        "close": float(k["c"]),
                        "volume": float(k["v"])
                    }

                    CANDLES[symbol].append(candle)

                    if not ALERTS_ON:
                        continue

                    result = analyze_symbol(symbol)

                    if not result:
                        continue

                    now = time.time()
                    last_time = LAST_ALERT.get(symbol, 0)

                    if now - last_time < COOLDOWN_SECONDS:
                        continue

                    await app.bot.send_message(
                        chat_id=CHAT_ID,
                        text=format_alert(result)
                    )

                    LAST_ALERT[symbol] = now

                    log_result({
                        "time": result["time"],
                        "symbol": result["symbol"],
                        "signal": result["signal"],
                        "pattern": result["pattern"],
                        "entry": result["entry"],
                        "sl": result["sl"],
                        "target": result["target"],
                        "risk_percent": result["risk_percent"],
                        "confidence": result["confidence"]
                    })

        except Exception as e:
            print("WebSocket error:", e)
            await asyncio.sleep(10)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Candlestick WebSocket bot ready 🚀\n\n"
        "Commands:\n"
        "/status\n"
        "/startalerts\n"
        "/stopalerts\n"
        "/symbols\n"
        "/myid"
    )
    async def analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if len(context.args) == 0:
        await update.message.reply_text(
            "Usage:\n/analyze btc"
        )
        return

    coin = context.args[0].upper()

    if not coin.endswith("USDT"):
        coin += "USDT"

    if coin not in CANDLES or len(CANDLES[coin]) < 50:
        await update.message.reply_text(
            f"{coin} data not ready yet."
        )
        return

    result = analyze_symbol(coin)

    if not result:
        await update.message.reply_text(
            f"{coin} currently no high probability setup found."
        )
        return

    await update.message.reply_text(
        format_alert(result)
    )
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_text = "ON ✅" if ALERTS_ON else "OFF 🛑"

    await update.message.reply_text(
        f"Alerts: {status_text}\n"
        f"Symbols: {', '.join(SYMBOLS)}\n"
        f"Timeframe: {TIMEFRAME}\n"
        f"Min Confidence: {MIN_CONFIDENCE}%\n"
        f"Max Risk: {MAX_RISK_PERCENT}%\n"
        f"Cooldown: 15 minutes\n"
        f"Timezone: IST"
    )

async def startalerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ALERTS_ON
    ALERTS_ON = True
    await update.message.reply_text("✅ Alerts ON")

async def stopalerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ALERTS_ON
    ALERTS_ON = False
    await update.message.reply_text("🛑 Alerts OFF")

async def symbols(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Symbols:\n" + ", ".join(SYMBOLS))

async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Your chat id: {update.effective_chat.id}")

app = ApplicationBuilder().token(BOT_TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("status", status))
app.add_handler(CommandHandler("startalerts", startalerts))
app.add_handler(CommandHandler("stopalerts", stopalerts))
app.add_handler(CommandHandler("symbols", symbols))
app.add_handler(CommandHandler("myid", myid))
app.add_handler(CommandHandler("analyze", analyze))
async def run_bot():
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    asyncio.create_task(websocket_loop(app))

    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    Thread(target=run_web).start()
    asyncio.run(run_bot())

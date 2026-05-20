import os
import json
import time
import asyncio
import csv
from collections import deque
from datetime import datetime

import pandas as pd
import pytz
import websockets
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

TIMEFRAME = "1m"
COOLDOWN_SECONDS = 900
MIN_CONFIDENCE = 75
RISK_REWARD = 2.0

ALERTS_ON = True

CANDLES = {s: deque(maxlen=250) for s in SYMBOLS}
LAST_ALERT = {}
OPEN_SIGNALS = {}

LOG_FILE = "signal_log.csv"


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


def analyze_symbol(symbol):
    data = list(CANDLES[symbol])

    if len(data) < 70:
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

    confidence = 0
    reasons = []

    signal = None
    entry = price
    sl = None
    target = None

    # BUY breakout
    if price > swing_high and trend_buy:
        confidence += 30
        reasons.append("Breakout above resistance")

        if volume_spike:
            confidence += 25
            reasons.append("Volume spike confirmed")

        if body_ratio > 0.45:
            confidence += 15
            reasons.append("Strong candle body")

        if close_position > 0.65:
            confidence += 15
            reasons.append("Close near candle high")

        if atr_val / price < 0.025:
            confidence += 15
            reasons.append("Controlled volatility")

        if confidence >= MIN_CONFIDENCE:
            signal = "BUY 🟢"
            sl = min(swing_high, price - atr_val * 1.2)
            risk = price - sl
            target = price + risk * RISK_REWARD

    # SELL breakdown
    elif price < swing_low and trend_sell:
        confidence += 30
        reasons.append("Breakdown below support")

        if volume_spike:
            confidence += 25
            reasons.append("Volume spike confirmed")

        if body_ratio > 0.45:
            confidence += 15
            reasons.append("Strong candle body")

        if close_position < 0.35:
            confidence += 15
            reasons.append("Close near candle low")

        if atr_val / price < 0.025:
            confidence += 15
            reasons.append("Controlled volatility")

        if confidence >= MIN_CONFIDENCE:
            signal = "SELL 🔴"
            sl = max(swing_low, price + atr_val * 1.2)
            risk = sl - price
            target = price - risk * RISK_REWARD

    if not signal:
        return None

    risk_percent = abs((entry - sl) / entry) * 100

    if risk_percent > 1.5:
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
        "reasons": ", ".join(reasons)
    }


def format_alert(result):
    return f"""
🚨 Real-Time WebSocket Signal

Time: {result['time']}
Coin: {result['symbol']}

Signal: {result['signal']}
Confidence: {result['confidence']}%

Current Price: {result['current_price']:.8f}
Entry: {result['entry']:.8f}

Stop Loss: {result['sl']:.8f}
Target: {result['target']:.8f}

Risk: {result['risk_percent']:.2f}%

Confirmations:
{result['reasons']}

Note:
Ye signal live candle close hone ke baad generate hua hai.
Guarantee nahi hoti. Paper/demo testing zaroor karein.
"""


def check_open_signal(symbol, candle, app):
    if symbol not in OPEN_SIGNALS:
        return

    sig = OPEN_SIGNALS[symbol]

    high = candle["high"]
    low = candle["low"]

    result = None

    if sig["side"] == "BUY":
        if low <= sig["sl"]:
            result = "SL HIT ❌"
        elif high >= sig["target"]:
            result = "TARGET HIT ✅"

    if sig["side"] == "SELL":
        if high >= sig["sl"]:
            result = "SL HIT ❌"
        elif low <= sig["target"]:
            result = "TARGET HIT ✅"

    if result:
        msg = f"""
📌 Signal Result

Coin: {symbol}
Result: {result}

Entry: {sig['entry']:.8f}
SL: {sig['sl']:.8f}
Target: {sig['target']:.8f}

Time: {india_time()}
"""
        asyncio.create_task(app.bot.send_message(chat_id=CHAT_ID, text=msg))
        del OPEN_SIGNALS[symbol]


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

                    check_open_signal(symbol, candle, app)

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

                    side = "BUY" if "BUY" in result["signal"] else "SELL"

                    OPEN_SIGNALS[symbol] = {
                        "side": side,
                        "entry": result["entry"],
                        "sl": result["sl"],
                        "target": result["target"]
                    }

                    LAST_ALERT[symbol] = now

                    log_signal({
                        "time": result["time"],
                        "symbol": result["symbol"],
                        "signal": result["signal"],
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
        "Realtime bot ready 🚀\n\n"
        "Commands:\n"
        "/status\n"
        "/startalerts\n"
        "/stopalerts\n"
        "/symbols\n"
        "/myid"
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_text = "ON ✅" if ALERTS_ON else "OFF 🛑"

    await update.message.reply_text(
        f"Alerts: {status_text}\n"
        f"Symbols: {', '.join(SYMBOLS)}\n"
        f"Timeframe: {TIMEFRAME}\n"
        f"Min Confidence: {MIN_CONFIDENCE}%\n"
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


async def post_init(app):
    app.create_task(websocket_loop(app))


app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("status", status))
app.add_handler(CommandHandler("startalerts", startalerts))
app.add_handler(CommandHandler("stopalerts", stopalerts))
app.add_handler(CommandHandler("symbols", symbols))
app.add_handler(CommandHandler("myid", myid))

app.run_polling()

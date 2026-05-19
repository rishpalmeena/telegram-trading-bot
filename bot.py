import os
import requests
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator
from ta.volatility import AverageTrueRange
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN")

def get_data(symbol):
    url = "https://api.binance.com/api/v3/klines"
    params = {
        "symbol": symbol.upper() + "USDT",
        "interval": "15m",
        "limit": 100
    }

    data = requests.get(url, params=params).json()

    df = pd.DataFrame(data, columns=[
        "time", "open", "high", "low", "close", "volume",
        "close_time", "qav", "trades", "tbbav", "tbqav", "ignore"
    ])

    df["open"] = df["open"].astype(float)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)
    df["close"] = df["close"].astype(float)

    return df

def analyze_coin(coin):
    df = get_data(coin)

    close = df["close"]
    high = df["high"]
    low = df["low"]

    df["ema20"] = EMAIndicator(close, window=20).ema_indicator()
    df["ema50"] = EMAIndicator(close, window=50).ema_indicator()
    df["rsi"] = RSIIndicator(close, window=14).rsi()
    df["atr"] = AverageTrueRange(high, low, close, window=14).average_true_range()

    last = df.iloc[-1]

    price = last["close"]
    ema20 = last["ema20"]
    ema50 = last["ema50"]
    rsi = last["rsi"]
    atr = last["atr"]

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
        return f"""
⚠️ {coin.upper()} me abhi clear signal nahi hai.

Price: {price:.4f}
RSI: {rsi:.2f}
"""

    return f"""
📊 {coin.upper()}USDT Analysis

Signal: {signal}

Entry: {entry:.4f}
Stop Loss: {sl:.4f}

Target 1: {t1:.4f}
Target 2: {t2:.4f}

RSI: {rsi:.2f}

⚠️ Risk:
Har trade me sirf 1-2% risk karein.
"""

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Bot ready hai 🚀\n\nUse:\n/analyze btc\n/analyze eth"
    )

async def analyze(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Example:\n/analyze btc")
        return

    coin = context.args[0].upper()

    try:
        result = analyze_coin(coin)
        await update.message.reply_text(result)
    except:
        await update.message.reply_text(
            "Error: Coin name galat hai ya data nahi mil raha.\nExample: /analyze btc"
        )

app = ApplicationBuilder().token(BOT_TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("analyze", analyze))

app.run_polling()

import os
import requests
from flask import Flask, request

BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

app = Flask(__name__)

def send_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {
        "chat_id": CHAT_ID,
        "text": message
    }
    requests.post(url, data=data)

@app.route("/")
def home():
    return "TradingView Telegram Bot is running"

@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json

    symbol = data.get("symbol", "Unknown")
    signal = data.get("signal", "Signal")
    entry = data.get("entry", "N/A")
    sl = data.get("sl", "N/A")
    target = data.get("target", "N/A")
    timeframe = data.get("timeframe", "N/A")

    message = f"""
🚨 Trading Alert

Market: {symbol}
Signal: {signal}
Timeframe: {timeframe}

Entry: {entry}
Stop Loss: {sl}
Target: {target}

⚠️ Risk: 1-2% capital only
"""

    send_telegram(message)

    return {"status": "success"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

import time
import numpy as np
import pandas as pd
import requests
from binance.client import Client
from ta.momentum import RSIIndicator

# ================== НАСТРОЙКИ ==================
API_KEY = "41bJFweA1m3Mp9UOTXMr82kQeFCSGu2AtYweii1Rn9CacNTeHor3tPzZfOa1Ty7q"
API_SECRET = "JTNiSXaKeBvcl3oFDN8GgP6rR2KgCfIhW8f1ByEAU4EsD2Ijid1dAn8b9wBotHS6"

SYMBOL = "AAVEUSDT"
LEVERAGE = 20

MARGIN_USDT = 20        # маржа на сделку
TAKE_PROFIT_USDT = 6    # скальпинг TP
STOP_LOSS_USDT = 3

INTERVAL = Client.KLINE_INTERVAL_5MINUTE
CANDLES = 100

# ================== TELEGRAM ==================
TOKEN = "8554034676:AAEIEPOwkWYFz9_dpDla2jfu-t5EDRpSygE"
CHAT_ID = "5540625088"   # ОБЯЗАТЕЛЬНО вставь свой chat_id

def tg_notify(text):
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": CHAT_ID, "text": text}, timeout=5)
    except:
        pass

# ================== BINANCE ==================
client = Client(
    API_KEY,
    API_SECRET,
    testnet=True,
    requests_params={'timeout': 30}
)

client.futures_change_leverage(symbol=SYMBOL, leverage=LEVERAGE)

# ================== UTILS ==================
def get_precision(symbol):
    info = client.futures_exchange_info()
    for s in info["symbols"]:
        if s["symbol"] == symbol:
            step = float(s["filters"][2]["stepSize"])
            return int(round(-np.log10(step), 0))
    return 3

PRECISION = get_precision(SYMBOL)

def get_price():
    return float(client.futures_symbol_ticker(symbol=SYMBOL)["price"])

def get_qty(price):
    qty = (MARGIN_USDT * LEVERAGE) / price
    return round(qty, PRECISION)

# ================== DATA ==================
def get_klines(retries=5):
    for _ in range(retries):
        try:
            klines = client.futures_klines(
                symbol=SYMBOL,
                interval=INTERVAL,
                limit=CANDLES
            )
            df = pd.DataFrame(klines, columns=[
                "time","open","high","low","close","volume",
                "ct","qav","trades","tb","tq","ignore"
            ])
            df["close"] = df["close"].astype(float)
            df["volume"] = df["volume"].astype(float)
            return df
        except Exception as e:
            print("Ошибка свечей:", e)
            time.sleep(5)
    return None

# ================== STRATEGY ==================
def check_scalping_signal(df):
    df["EMA8"] = df["close"].ewm(span=8).mean()
    df["EMA21"] = df["close"].ewm(span=21).mean()
    df["RSI"] = RSIIndicator(df["close"], 5).rsi()
    df["VOL_MA"] = df["volume"].rolling(20).mean()

    last = df.iloc[-1]

    print(
        f"EMA8={last['EMA8']:.2f} | EMA21={last['EMA21']:.2f} | "
        f"RSI={last['RSI']:.2f} | VOL={last['volume']:.0f}"
        f" | VOL_MA={last['VOL_MA']:.0f}"
        f" | price={last['close']:.2f}"
    )

    if (
        last["EMA8"] > last["EMA21"] and
        last["RSI"] < 20 and
        last["volume"] > last["VOL_MA"]
    ):
        return "LONG"

    if (
        last["EMA8"] < last["EMA21"] and
        last["RSI"] > 80 and
        last["volume"] > last["VOL_MA"]
    ):
        return "SHORT"

    return None

# ================== MAIN ==================
tg_notify("🤖 Скальпинг-бот запущен (EMA + RSI + Volume)")

while True:
    df = get_klines()
    if df is None:
        continue

    signal = check_scalping_signal(df)

    if signal is None:
        time.sleep(10)
        continue

    side = "BUY" if signal == "LONG" else "SELL"
    exit_side = "SELL" if signal == "LONG" else "BUY"

    price = get_price()
    qty = get_qty(price)

    # ===== ВХОД =====
    client.futures_create_order(
        symbol=SYMBOL,
        side=side,
        type="MARKET",
        quantity=qty
    )

    entry_price = price
    tg_notify(f"🚀 {signal} ВХОД\nЦена: {entry_price}\nQty: {qty}")

    # ===== TP / SL =====
    while True:
        price = get_price()

        pnl = (
            (price - entry_price) * qty
            if signal == "LONG"
            else (entry_price - price) * qty
        )

        if pnl >= TAKE_PROFIT_USDT or pnl <= -STOP_LOSS_USDT:
            client.futures_create_order(
                symbol=SYMBOL,
                side=exit_side,
                type="MARKET",
                quantity=qty
            )

            result = "✅ TP" if pnl > 0 else "🛑 SL"
            tg_notify(
                f"{result} {signal}\nPnL: {pnl:.2f} USDT\nЦена: {price}"
            )
            break

        time.sleep(1)

    time.sleep(30)

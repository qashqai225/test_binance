import time
import requests
import threading
import pandas as pd
import numpy as np
from binance.client import Client

# ================== НАСТРОЙКИ ==================

API_KEY = "41bJFweA1m3Mp9UOTXMr82kQeFCSGu2AtYweii1Rn9CacNTeHor3tPzZfOa1Ty7q"
API_SECRET = "JTNiSXaKeBvcl3oFDN8GgP6rR2KgCfIhW8f1ByEAU4EsD2Ijid1dAn8b9wBotHS6"

client = Client(API_KEY, API_SECRET)
client.FUTURES_URL = "https://testnet.binancefuture.com/fapi"

SYMBOLS = [
    "AAVEUSDT",
    "LTCUSDT",
    "HBARUSDC",
    "INJUSDC",
    "ADAUSDC"
]

INTERVAL = Client.KLINE_INTERVAL_5MINUTE
CANDLES = 100
LEVERAGE = 20
MAX_MARGIN = 15
RISK_PER_TRADE = 10

# ===== Telegram =====
TG_TOKEN = "8554034676:AAEIEPOwkWYFz9_dpDla2jfu-t5EDRpSygE"
CHAT_ID = "5540625088"

BOT_ON = True
stats = {"trades": 0, "tp": 0, "sl": 0}


# ================== TELEGRAM ==================

def tg(msg):
    try:
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        requests.post(url, json={"chat_id": CHAT_ID, "text": msg})
    except:
        pass

def telegram_listener():
    global BOT_ON
    offset = 0
    while True:
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates?offset={offset}"
            ).json()
            for u in r["result"]:
                offset = u["update_id"] + 1
                cmd = u["message"]["text"]

                if cmd == "/start":
                    BOT_ON = True
                    tg("✅ Бот зап")
                elif cmd == "/stop":
                    BOT_ON = False
                    tg("⛔ Бот остановлен")
                elif cmd == "/status":
                    tg(f"📊 Статус: {'ON' if BOT_ON else 'OFF'}")
                elif cmd == "/stats":
                    tg(str(stats))
                elif cmd == "/positions":
                    show_positions()
        except:
            pass
        time.sleep(2)

# ================== DATA ==================

def get_klines(symbol):
    kl = client.futures_klines(symbol=symbol, interval=INTERVAL, limit=CANDLES)
    df = pd.DataFrame(kl, columns=[
        "time","o","h","l","c","v","x","q","n","t","T","i"
    ])
    df = df.astype(float)
    return df

# ================== INDICATORS ==================

def indicators(df):
    df["ema9"] = df["c"].ewm(span=9).mean()
    df["ema21"] = df["c"].ewm(span=21).mean()

    delta = df["c"].diff()
    gain = delta.clip(lower=0).rolling(7).mean()
    loss = -delta.clip(upper=0).rolling(7).mean()
    rs = gain / loss
    df["rsi"] = 100 - (100 / (1 + rs))

    df["atr"] = (df["h"] - df["l"]).rolling(7).mean()
    df["vol_ma"] = df["v"].rolling(20).mean()
    return df

# ================== POSITIONS ==================

def open_positions():
    pos = client.futures_position_information()
    active = {}
    for p in pos:
        amt = float(p["positionAmt"])
        if amt != 0:
            active[p["symbol"]] = p
    return active

def show_positions():
    pos = open_positions()
    if not pos:
        tg("📭 Открытых позиций нет")
        return
    msg = "📌 ОТКРЫТЫЕ ПОЗИЦИИ:\n\n/start /stop /status /stats /positions "

    for s,p in pos.items():
        msg += (
            f"{s}\n"
            f"Количество монет: {p['positionAmt']}\n"
            f"Точка входа: {p['entryPrice']}\n"
            f"Прибыль: {p['unRealizedProfit']} USDT\n\n"
            
        )
    tg(msg)

# ================== TRADING ==================
def round_qty(symbol, qty):
    info = client.futures_exchange_info()
    for s in info["symbols"]:
        if s["symbol"] == symbol:
            for f in s["filters"]:
                if f["filterType"] == "LOT_SIZE":
                    step = float(f["stepSize"])
                    precision = int(round(-np.log10(step), 0))
                    return round(qty, precision)
    return qty
def trade(symbol):
    global stats

    if symbol in open_positions():
        return

    df = indicators(get_klines(symbol))
    last = df.iloc[-1]

    long_signal = (
        last.ema9 > last.ema21 and
        last.rsi > 50 and
        last.v > last.vol_ma
    )

    short_signal = (
        last.ema9 < last.ema21 and
        last.rsi < 50 and
        last.v > last.vol_ma
    )

    if not (long_signal or short_signal):
        return

    price = last.c
    atr = last.atr
    side = "BUY" if long_signal else "SELL"

    raw_qty = (RISK_PER_TRADE * LEVERAGE) / price
    qty = round_qty(symbol, raw_qty)
    
    try:
        client.futures_change_leverage(symbol=symbol, leverage=LEVERAGE)

        client.futures_create_order(
            symbol=symbol,
            side=side,
            type="MARKET",
            quantity=qty
        )

        stats["trades"] += 1

        tp = price + atr*2 if side=="BUY" else price - atr*2
        sl = price - atr if side=="BUY" else price + atr

        tg(
            f"🚀 ВХОД {symbol}\n"
            f"{side}\n"
            f"Цена: {price}\n"
            f"TP: {round(tp,2)}\n"
            f"SL: {round(sl,2)}"
        )

    except Exception as e:
        tg(f"❌ Ошибка {symbol}: {e}")

# ================== MAIN ==================

threading.Thread(target=telegram_listener, daemon=True).start()
tg("🤖 Бот запущен\n\n/start /stop /status /stats /positions")

while True:
    if BOT_ON:
        for s in SYMBOLS:
            try:
                trade(s)
                time.sleep(1)
            except:
                pass
    time.sleep(10)

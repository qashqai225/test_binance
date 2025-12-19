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

SYMBOLS = ["AAVEUSDT", "LTCUSDT", "HBARUSDC", "INJUSDT", "ADAUSDC"]
INTERVAL = Client.KLINE_INTERVAL_5MINUTE
CANDLES = 100
LEVERAGE = 20
RISK_PER_TRADE = 10
STOP_LOSS_PERC = 0.0833  # 8.33% от точки входа

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
            r = requests.get(f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates?offset={offset}").json()
            for u in r["result"]:
                offset = u["update_id"] + 1
                cmd = u["message"]["text"]
                if cmd == "/start":
                    BOT_ON = True
                    tg("✅ Бот запущен")
                elif cmd == "/stop":
                    BOT_ON = False
                    tg("⛔ Бот остановлен")
                elif cmd == "/status":
                    tg(f"📊 Статус: {'ON' if BOT_ON else 'OFF'}")
                elif cmd == "/stats":
                    tg(str(stats))
                elif cmd == "/positions":
                    show_positions()
                elif cmd.startswith("/close_all"):
                    close_all_positions()
                elif cmd.startswith("/close_"):
                    symbol = cmd.split("_")[1]
                    close_position(symbol)
        except:
            pass
        time.sleep(2)

# ================== DATA ==================
def get_klines(symbol):
    kl = client.futures_klines(symbol=symbol, interval=INTERVAL, limit=CANDLES)
    df = pd.DataFrame(kl, columns=["time","o","h","l","c","v","x","q","n","t","T","i"])
    df = df.astype(float)
    return df

def get_mark_price(symbol):
    try:
        return float(client.futures_mark_price(symbol=symbol)['markPrice'])
    except:
        return None

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
    msg = "📌 ОТКРЫТЫЕ ПОЗИЦИИ:\n\n/start /stop /status /stats /positions /close_all\n\n"
    for s, p in pos.items():
        msg += (
            f"{s}\n"
            f"Количество монет: {p['positionAmt']}\n"
            f"Точка входа: {p['entryPrice']}\n"
            f"Прибыль: {p['unRealizedProfit']} USDT\n"
            f"Закрыть: /close_{s}\n\n"
        )
    tg(msg)

def close_position(symbol):
    try:
        pos = open_positions()
        if symbol not in pos:
            tg(f"📭 Позиция {symbol} не найдена")
            return
        amt = float(pos[symbol]["positionAmt"])
        side = "SELL" if amt > 0 else "BUY"
        qty = round_qty(symbol, abs(amt))
        client.futures_create_order(
            symbol=symbol,
            side=side,
            type="MARKET",
            quantity=qty,
            reduceOnly=True
        )
        tg(f"✂️ Закрыта позиция {symbol} ({side})")
        cancel_open_orders(symbol)
    except Exception as e:
        tg(f"❌ Ошибка закрытия {symbol}: {e}")

def close_all_positions():
    pos = open_positions()
    for symbol in pos.keys():
        close_position(symbol)

# ================== HELPERS ==================
def get_tick_size(symbol):
    info = client.futures_exchange_info()
    for s in info["symbols"]:
        if s["symbol"] == symbol:
            for f in s["filters"]:
                if f["filterType"] == "PRICE_FILTER":
                    return float(f["tickSize"])
    return 0.0001

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

def round_price(symbol, price):
    tick = get_tick_size(symbol)
    precision = int(round(-np.log10(tick), 0))
    return round(price, precision)

def cancel_open_orders(symbol):
    try:
        orders = client.futures_get_open_orders(symbol=symbol)
        for o in orders:
            client.futures_cancel_order(symbol=symbol, orderId=o['orderId'])
        if orders:
            tg(f"🛑 Отменены все открытые ордера {symbol}")
    except Exception as e:
        tg(f"❌ Ошибка отмены ордеров {symbol}: {e}")

# ================== TRADING ЛИМИТНЫЕ ==================
def trade(symbol):
    global stats
    if symbol in open_positions():
        return

    df = indicators(get_klines(symbol))
    last = df.iloc[-1]
    long_signal = (last.ema9 > last.ema21 and last.rsi > 50 and last.v > last.vol_ma)
    short_signal = (last.ema9 < last.ema21 and last.rsi < 50 and last.v > last.vol_ma)
    if not (long_signal or short_signal):
        return

    cancel_open_orders(symbol)

    side = "BUY" if long_signal else "SELL"
    mark_price = get_mark_price(symbol)
    if not mark_price:
        return
    raw_qty = (RISK_PER_TRADE * LEVERAGE) / mark_price
    qty = round_qty(symbol, raw_qty)
    tick = get_tick_size(symbol)

    # Лимитный вход близко к markPrice
    entry_price = round_price(symbol, mark_price * (1 + 0.001) if side=="BUY" else mark_price * (1 - 0.001))

    # Stop Loss 8.33% от точки входа
    sl_price = round_price(symbol, entry_price * (1 - STOP_LOSS_PERC) if side=="BUY" else entry_price * (1 + STOP_LOSS_PERC))
    tp_price = round_price(symbol, entry_price + 3*(entry_price - sl_price) if side=="BUY" else entry_price - 3*(sl_price - entry_price))

    try:
        client.futures_change_leverage(symbol=symbol, leverage=LEVERAGE)

        # Лимитный вход
        client.futures_create_order(
            symbol=symbol,
            side=side,
            type="LIMIT",
            timeInForce="GTC",
            quantity=qty,
            price=entry_price
        )

        # TP
        client.futures_create_order(
            symbol=symbol,
            side="SELL" if side=="BUY" else "BUY",
            type="TAKE_PROFIT_MARKET",
            stopPrice=tp_price,
            closePosition=True
        )

        # SL
        client.futures_create_order(
            symbol=symbol,
            side="SELL" if side=="BUY" else "BUY",
            type="STOP_MARKET",
            stopPrice=sl_price,
            closePosition=True
        )

        stats["trades"] += 1
        tg(f"🚀 ЛИМИТ ВХОД {symbol}\n{side}\nЦена: {entry_price}\nTP: {tp_price}\nSL: {sl_price}")

    except Exception as e:
        tg(f"❌ Ошибка лимитного входа {symbol}: {e}")

# ================== CHECK TP/SL ==================
def check_tp_sl():
    while True:
        if BOT_ON:
            positions = open_positions()
            for symbol, pos in positions.items():
                try:
                    amt = float(pos["positionAmt"])
                    entry = float(pos["entryPrice"])
                    side = "BUY" if amt > 0 else "SELL"
                    df = get_klines(symbol)
                    price = df["c"].iloc[-1]
                    sl_price = entry * (1 - STOP_LOSS_PERC) if side=="BUY" else entry * (1 + STOP_LOSS_PERC)
                    tp_price = entry + 3*(entry - sl_price) if side=="BUY" else entry - 3*(sl_price - entry)
                    if (side=="BUY" and (price >= tp_price or price <= sl_price)) or \
                       (side=="SELL" and (price <= tp_price or price >= sl_price)):
                        close_position(symbol)
                        stats["tp" if (price>=tp_price if side=="BUY" else price<=tp_price) else "sl"] += 1
                        tg(f"✂️ ЗАКРЫТИЕ {symbol}\n{side} по {'TP' if (price>=tp_price if side=='BUY' else price<=tp_price) else 'SL'}\nЦена: {price}")
                except Exception as e:
                    tg(f"❌ Ошибка check_tp_sl {symbol}: {e}")
        time.sleep(5)

# ================== MAIN ==================
threading.Thread(target=telegram_listener, daemon=True).start()
threading.Thread(target=check_tp_sl, daemon=True).start()
tg("🤖 Бот запущен\n/start /stop /status /stats /positions /close_all /close_SYMBOL")

while True:
    if BOT_ON:
        for s in SYMBOLS:
            try:
                trade(s)
                time.sleep(1)
            except Exception as e:
                tg(f"❌ Ошибка trade {s}: {e}")
    time.sleep(10)

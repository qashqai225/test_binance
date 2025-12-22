import time
import requests
import threading
import pandas as pd
from binance.client import Client
from math import floor
import json


# ================== CONFIG ==================
API_KEY = "41bJFweA1m3Mp9UOTXMr82kQeFCSGu2AtYweii1Rn9CacNTeHor3tPzZfOa1Ty7q"
API_SECRET = "JTNiSXaKeBvcl3oFDN8GgP6rR2KgCfIhW8f1ByEAU4EsD2Ijid1dAn8b9wBotHS6"

TG_TOKEN = "8554034676:AAEIEPOwkWYFz9_dpDla2jfu-t5EDRpSygE"
CHAT_ID = "5540625088"

SYMBOLS = ["AAVEUSDT","LTCUSDT","INJUSDT","XRPUSDT","ADAUSDT","HBARUSDT"]
INTERVAL = Client.KLINE_INTERVAL_5MINUTE

LEVERAGE = 20
RISK_PER_TRADE = 10
TP_ROI = 0.15
SL_ROI = 0.20   # 🔴 STOP LOSS 20% ROI

BOT_ON = True

client = Client(API_KEY, API_SECRET)
client.FUTURES_URL = "https://testnet.binancefuture.com/fapi"
client.timestamp_offset = client.get_server_time()["serverTime"] - int(time.time() * 1000)


# ================== EXCHANGE INFO ==================
exchange_info = client.futures_exchange_info()

def get_filters(symbol):
    for s in exchange_info["symbols"]:
        if s["symbol"] == symbol:
            lot = next(f for f in s["filters"] if f["filterType"] == "LOT_SIZE")
            price = next(f for f in s["filters"] if f["filterType"] == "PRICE_FILTER")
            return float(lot["stepSize"]), float(lot["minQty"]), float(price["tickSize"])
    return 0.001, 0.001, 0.01

def step_precision(step):
    return max(0, len(str(step).split('.')[-1].rstrip('0')))

def fmt_qty(symbol, qty):
    step, min_qty, _ = get_filters(symbol)
    precision = step_precision(step)
    q = floor(qty / step) * step
    q = round(q, precision)
    return q if q >= min_qty else 0

def fmt_price(symbol, price):
    _, _, tick = get_filters(symbol)
    precision = step_precision(tick)
    p = floor(price / tick) * tick
    return round(p, precision)


# ================== STATE ==================
positions = {}
stats = {
    "trades": 0,
    "pnl": 0.0
}


# ================== TELEGRAM ==================
def tg(msg, buttons=True):
    payload = {
        "chat_id": CHAT_ID,
        "text": msg,
        "parse_mode": "HTML"
    }

    if buttons:
        payload["reply_markup"] = {
            "inline_keyboard": [
                [
                    {"text": "▶️ Старт", "callback_data": "start"},
                    {"text": "⏹ Стоп", "callback_data": "stop"}
                ],
                [
                    {"text": "📜 Відкриті позиції", "callback_data": "positions"},
                    {"text": "📊 Статистика", "callback_data": "stats"}
                ],
                [
                    {"text": "🗑️ Закрити всі позиції", "callback_data": "close_all"}
                ]
            ]
        }

    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json=payload
        )
    except:
        pass


def handle_callback(data):
    global BOT_ON

    if data == "start":
        BOT_ON = True
        tg("🤖 BOT Запущен\n\n Гарного профіту")

    elif data == "stop":
        BOT_ON = False
        tg("🔴 BOT Зупинений")

    elif data == "positions":
        show_positions()

    elif data == "stats":
        show_stats()

    elif data == "close_all":
        for s in list(positions.keys()):
            close_position(s, manual=True)
        tg("❌ Всі позиції закриті")


def telegram_listener():
    offset = 0
    while True:
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates?offset={offset}"
            ).json()

            for u in r.get("result", []):
                offset = u["update_id"] + 1

                if "callback_query" in u:
                    handle_callback(u["callback_query"]["data"])

                elif "message" in u:
                    text = u["message"]["text"]
                    if text.startswith("/close"):
                        sym = text.split()[1].upper()
                        close_position(sym, manual=True)
        except:
            pass

        time.sleep(1)


# ================== MARKET DATA ==================
def get_klines(symbol):
    df = pd.DataFrame(
        client.futures_klines(symbol=symbol, interval=INTERVAL, limit=100),
        columns=["t","o","h","l","c","v","x","q","n","T","Q","i"]
    )
    return df.astype(float)


# ================== INDICATORS ==================
def indicators(df):
    df["ema9"] = df["c"].ewm(span=9).mean()
    df["ema21"] = df["c"].ewm(span=21).mean()
    delta = df["c"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = -delta.clip(upper=0).rolling(14).mean()
    df["rsi"] = 100 - (100 / (1 + gain / loss))
    df["vol_ma"] = df["v"].rolling(20).mean()
    return df


# ================== TRADE ==================
def trade(symbol):
    if symbol in positions:
        return

    df = indicators(get_klines(symbol))
    last = df.iloc[-1]

    long = last.ema9 > last.ema21 and last.rsi > 50 and last.v > last.vol_ma
    short = last.ema9 < last.ema21 and last.rsi < 50 and last.v > last.vol_ma
    if not (long or short):
        return

    side = "BUY" if long else "SELL"
    price = fmt_price(symbol, float(client.futures_symbol_ticker(symbol=symbol)["price"]))

    qty = fmt_qty(symbol, (RISK_PER_TRADE * LEVERAGE) / price)
    if qty == 0:
        return

    client.futures_change_leverage(symbol=symbol, leverage=LEVERAGE)
    client.futures_create_order(symbol=symbol, side=side, type="MARKET", quantity=qty)

    tp_price = price * (1 + TP_ROI / LEVERAGE) if side == "BUY" else price * (1 - TP_ROI / LEVERAGE)
    sl_price = price * (1 - SL_ROI / LEVERAGE) if side == "BUY" else price * (1 + SL_ROI / LEVERAGE)

    tp_price = fmt_price(symbol, tp_price)
    sl_price = fmt_price(symbol, sl_price)

    positions[symbol] = {
        "side": side,
        "entry": price,
        "qty": qty,
        "tp": tp_price,
        "sl": sl_price
    }

    tg(
        f"🚀 <b>Вхід в позицію</b> {side}\n<i>{symbol}</i>\n"
        f"<i>Entry:</i> <b>{price}</b>\n"
        f"<i>TP:</i> <b>{tp_price}</b>\n"
        f"<i>SL:</i> <b>{sl_price}</b>"
    )


# ================== CLOSE ==================
def close_position(symbol, manual=False, sl=False):
    if symbol not in positions:
        return

    p = positions[symbol]
    price = fmt_price(symbol, float(client.futures_symbol_ticker(symbol=symbol)["price"]))

    pnl = (price - p["entry"]) * p["qty"]
    if p["side"] == "SELL":
        pnl = -pnl

    stats["trades"] += 1
    stats["pnl"] += pnl

    client.futures_create_order(
        symbol=symbol,
        side="SELL" if p["side"] == "BUY" else "BUY",
        type="MARKET",
        quantity=p["qty"]
    )

    if manual:
        title = "🗑️ Закрито вручну"
    elif sl:
        title = "🛑 STOP LOSS"
    else:
        title = "✅ TAKE PROFIT 💸"

    tg(
        f"{title} {symbol}\n"
        f"<i>PnL:</i> <b>{pnl:.2f}</b> USDT"
    )

    positions.pop(symbol)


# ================== MANAGER ==================
def manage_positions():
    while True:
        for s, p in list(positions.items()):
            price = fmt_price(s, float(client.futures_symbol_ticker(symbol=s)["price"]))

            if p["side"] == "BUY":
                if price >= p["tp"]:
                    close_position(s)
                elif price <= p["sl"]:
                    close_position(s, sl=True)

            else:
                if price <= p["tp"]:
                    close_position(s)
                elif price >= p["sl"]:
                    close_position(s, sl=True)

        time.sleep(1)


# ================== UI ==================
def show_positions():
    if not positions:
        tg("📭 <b>Немає відкритих позицій</b>")
        return

    msg = "📜 ВІДКРИТІ ПОЗИЦІЇ\n\n"
    for s,p in positions.items():
        price = fmt_price(s, float(client.futures_symbol_ticker(symbol=s)["price"]))
        pnl = (price - p["entry"]) * p["qty"]
        if p["side"] == "SELL":
            pnl = -pnl

        msg += (
            f"<b>{s}</b> | <i>{p['side']}</i>\n"
            f"Entry: <b>{p['entry']}</b>\n"
            f"TP: <b>{p['tp']}</b>\n"
            f"SL: <b>{p['sl']}</b>\n"
            f"PnL: <b>{pnl:.2f}</b> USDT\n\n"
        )
    tg(msg)


def show_stats():
    tg(
        f"📊 СТАТИСТИКА\n"
        f"Trades: {stats['trades']}\n"
        f"Total PnL: <b>{stats['pnl']:.2f}</b> USDT"
    )


# ================== MAIN ==================
threading.Thread(target=telegram_listener, daemon=True).start()
threading.Thread(target=manage_positions, daemon=True).start()

tg("🤖 BOT ЗАПУЩЕН 🚀")

while True:
    if BOT_ON:
        for s in SYMBOLS:
            trade(s)
            time.sleep(1)
    time.sleep(5)

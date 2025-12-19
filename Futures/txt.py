import time
import requests
import threading
import pandas as pd
import numpy as np
from binance.client import Client

# ================== CONFIG ==================
API_KEY = "API_KEY"
API_SECRET = "API_SECRET"

TG_TOKEN = "TG_TOKEN"
CHAT_ID = "CHAT_ID"

SYMBOLS = ["AAVEUSDT", "LTCUSDT", "HBARUSDC", "INJUSDT", "ADAUSDC"]
INTERVAL = Client.KLINE_INTERVAL_5MINUTE
CANDLES = 100
LEVERAGE = 20
RISK_PER_TRADE = 10

BOT_ON = True

client = Client(API_KEY, API_SECRET)
client.FUTURES_URL = "https://testnet.binancefuture.com/fapi"

# ================== STATE ==================
stats = {
    "trades": 0,
    "tp1": 0,
    "tp2": 0,
    "tp3": 0,
    "sl": 0,
    "pnl_usdt": 0.0,
    "pnl_pct": 0.0
}

symbol_stats = {}
position_state = {}

# ================== TELEGRAM ==================
def tg(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg}
        )
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

            for u in r.get("result", []):
                offset = u["update_id"] + 1
                cmd = u["message"]["text"]

                if cmd == "/start":
                    BOT_ON = True
                    tg("🟢 Бот ЗАПУЩЕН")

                elif cmd == "/stop":
                    BOT_ON = False
                    tg("🔴 Бот ОСТАНОВЛЕН")

                elif cmd == "/status":
                    tg(
                        f"📡 СТАТУС\n"
                        f"BOT: {'ON' if BOT_ON else 'OFF'}\n"
                        f"Открытых позиций: {len(open_positions())}"
                    )

                elif cmd == "/stats":
                    send_stats()

                elif cmd == "/positions":
                    show_positions()

        except:
            pass
        time.sleep(2)

# ================== EXCHANGE FILTERS ==================
exchange_info = client.futures_exchange_info()

def get_filters(symbol):
    for s in exchange_info["symbols"]:
        if s["symbol"] == symbol:
            tick = float(next(f["tickSize"] for f in s["filters"] if f["filterType"] == "PRICE_FILTER"))
            step = float(next(f["stepSize"] for f in s["filters"] if f["filterType"] == "LOT_SIZE"))
            return tick, step
    return 0.01, 0.001

def price_fmt(symbol, price):
    tick, _ = get_filters(symbol)
    precision = min(10, int(-np.log10(tick)))
    return round(round(price / tick) * tick, precision)

def qty_fmt(symbol, qty):
    _, step = get_filters(symbol)
    precision = min(10, int(-np.log10(step)))
    return round(round(qty / step) * step, precision)

# ================== DATA ==================
def get_klines(symbol):
    df = pd.DataFrame(
        client.futures_klines(symbol=symbol, interval=INTERVAL, limit=CANDLES),
        columns=["t","o","h","l","c","v","x","q","n","T","Q","i"]
    )
    return df.astype(float)

def indicators(df):
    df["ema9"] = df["c"].ewm(span=9).mean()
    df["ema21"] = df["c"].ewm(span=21).mean()
    delta = df["c"].diff()
    gain = delta.clip(lower=0).rolling(7).mean()
    loss = -delta.clip(upper=0).rolling(7).mean()
    df["rsi"] = 100 - (100 / (1 + gain / loss))
    df["atr"] = (df["h"] - df["l"]).rolling(7).mean()
    df["vol_ma"] = df["v"].rolling(20).mean()
    return df

# ================== POSITIONS ==================
def open_positions():
    pos = client.futures_position_information()
    return {p["symbol"]: p for p in pos if float(p["positionAmt"]) != 0}

def show_positions():
    pos = open_positions()
    if not pos:
        tg("📭 Открытых позиций нет")
        return

    msg = "📌 ОТКРЫТЫЕ ПОЗИЦИИ\n\n"
    for s, p in pos.items():
        msg += (
            f"{s}\n"
            f"Qty: {p['positionAmt']}\n"
            f"Entry: {p['entryPrice']}\n"
            f"PNL: {p['unRealizedProfit']} USDT\n\n"
        )
    tg(msg)

# ================== STATS ==================
def init_symbol(symbol):
    if symbol not in symbol_stats:
        symbol_stats[symbol] = {
            "trades": 0,
            "tp1": 0,
            "tp2": 0,
            "tp3": 0,
            "sl": 0,
            "pnl_usdt": 0.0,
            "pnl_pct": 0.0
        }

def send_stats():
    msg = "📊 СТАТИСТИКА\n\n"
    for s, d in symbol_stats.items():
        msg += (
            f"📌 {s}\n"
            f"Trades: {d['trades']}\n"
            f"TP1/TP2/TP3: {d['tp1']} / {d['tp2']} / {d['tp3']}\n"
            f"SL: {d['sl']}\n"
            f"PNL: {d['pnl_usdt']:.10f} USDT ({d['pnl_pct']:.10f}%)\n\n"
        )

    msg += (
        f"💰 TOTAL\n"
        f"PNL: {stats['pnl_usdt']:.10f} USDT ({stats['pnl_pct']:.10f}%)"
    )
    tg(msg)

# ================== PNL ==================
def calc_pnl(entry, exit_price, qty, side):
    direction = 1 if side == "BUY" else -1
    pnl_usdt = (exit_price - entry) * qty * direction
    pnl_pct = (pnl_usdt / (entry * qty)) * 100
    return pnl_usdt, pnl_pct

# ================== TRADE ==================
def trade(symbol):
    if symbol in open_positions():
        return

    df = indicators(get_klines(symbol))
    last = df.iloc[-1]

    long = last.ema9 > last.ema21 and last.rsi > 50 and last.v > last.vol_ma
    short = last.ema9 < last.ema21 and last.rsi < 50 and last.v > last.vol_ma
    if not (long or short):
        return

    side = "BUY" if long else "SELL"
    price = last.c
    atr = last.atr

    qty = qty_fmt(symbol, (RISK_PER_TRADE * LEVERAGE) / price)

    client.futures_change_leverage(symbol=symbol, leverage=LEVERAGE)
    client.futures_create_order(symbol=symbol, side=side, type="MARKET", quantity=qty)

    init_symbol(symbol)

    position_state[symbol] = {
        "side": side,
        "entry": price,
        "qty": qty,
        "tp1": price_fmt(symbol, price + atr if side == "BUY" else price - atr),
        "tp2": price_fmt(symbol, price + atr*2 if side == "BUY" else price - atr*2),
        "tp3": price_fmt(symbol, price + atr*3 if side == "BUY" else price - atr*3),
        "sl": price_fmt(symbol, price - atr if side == "BUY" else price + atr),
        "hit_tp1": False,
        "hit_tp2": False
    }

    stats["trades"] += 1
    symbol_stats[symbol]["trades"] += 1

    tg(f"🚀 {symbol} {side}\nEntry: {price_fmt(symbol, price)}")

# ================== MANAGE POSITIONS ==================
def manage_positions():
    while True:
        for symbol, pos in open_positions().items():
            if symbol not in position_state:
                continue

            state = position_state[symbol]
            price = get_klines(symbol)["c"].iloc[-1]
            side = state["side"]
            amt = abs(float(pos["positionAmt"]))

            def close(percent, key):
                qty = qty_fmt(symbol, amt * percent)
                client.futures_create_order(
                    symbol=symbol,
                    side="SELL" if side == "BUY" else "BUY",
                    type="MARKET",
                    quantity=qty
                )

                pnl_u, pnl_p = calc_pnl(state["entry"], price, qty, side)
                stats["pnl_usdt"] += pnl_u
                stats["pnl_pct"] += pnl_p
                symbol_stats[symbol]["pnl_usdt"] += pnl_u
                symbol_stats[symbol]["pnl_pct"] += pnl_p
                stats[key] += 1
                symbol_stats[symbol][key] += 1

                tg(f"✂️ {symbol} {key.upper()} @ {price_fmt(symbol, price)}")

            if not state["hit_tp1"] and (price >= state["tp1"] if side == "BUY" else price <= state["tp1"]):
                close(0.33, "tp1")
                state["hit_tp1"] = True
                state["sl"] = state["entry"]

            elif state["hit_tp1"] and not state["hit_tp2"] and (price >= state["tp2"] if side == "BUY" else price <= state["tp2"]):
                close(0.5, "tp2")
                state["hit_tp2"] = True

            elif state["hit_tp2"] and (price >= state["tp3"] if side == "BUY" else price <= state["tp3"]):
                close(1.0, "tp3")
                position_state.pop(symbol, None)

            elif price <= state["sl"] if side == "BUY" else price >= state["sl"]:
                close(1.0, "sl")
                position_state.pop(symbol, None)

        time.sleep(3)

# ================== MAIN ==================
threading.Thread(target=telegram_listener, daemon=True).start()
threading.Thread(target=manage_positions, daemon=True).start()

tg("🤖 Бот запущен\n/start /stop /status /stats /positions")

while True:
    if BOT_ON:
        for s in SYMBOLS:
            trade(s)
            time.sleep(1)
    time.sleep(10)

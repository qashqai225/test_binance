import time
import requests
import threading
import pandas as pd
from binance.client import Client
from math import floor

# ================== CONFIG ==================
API_KEY = "41bJFweA1m3Mp9UOTXMr82kQeFCSGu2AtYweii1Rn9CacNTeHor3tPzZfOa1Ty7q"
API_SECRET = "JTNiSXaKeBvcl3oFDN8GgP6rR2KgCfIhW8f1ByEAU4EsD2Ijid1dAn8b9wBotHS6"

TG_TOKEN = "8554034676:AAEIEPOwkWYFz9_dpDla2jfu-t5EDRpSygE"
CHAT_ID = "5540625088"

SYMBOLS = ["AAVEUSDT","LTCUSDT","INJUSDT","XRPUSDT","ADAUSDT","HBARUSDT"]
INTERVAL = Client.KLINE_INTERVAL_5MINUTE

LEVERAGE = 20
RISK_PER_TRADE = 10
TP_ROI = 0.10   # +10% ROI

BOT_ON = True

client = Client(API_KEY, API_SECRET)
client.FUTURES_URL = "https://testnet.binancefuture.com/fapi"

# ================== EXCHANGE INFO ==================
exchange_info = client.futures_exchange_info()

def get_filters(symbol):
    """Возвращает шаг количества, минимальное количество и шаг цены для символа"""
    for s in exchange_info["symbols"]:
        if s["symbol"] == symbol:
            lot = next(f for f in s["filters"] if f["filterType"] == "LOT_SIZE")
            price = next(f for f in s["filters"] if f["filterType"] == "PRICE_FILTER")
            return float(lot["stepSize"]), float(lot["minQty"]), float(price["tickSize"])
    return 0.001, 0.001, 0.01

def fmt_qty(symbol, qty):
    """Форматирует количество под шаг и минимум"""
    step, min_qty, _ = get_filters(symbol)
    # округляем вниз до ближайшего шага
    q = floor(qty / step) * step
    # определяем precision по stepSize
    precision = max(0, -int(floor(round(step, 8).as_integer_ratio()[1]).bit_length()/3.3219))
    q = round(q, precision if precision > 0 else 8)
    return q if q >= min_qty else 0

def fmt_price(symbol, price):
    """Форматирует цену под шаг цены"""
    _, _, tick = get_filters(symbol)
    p = floor(price / tick) * tick
    # определяем precision по tickSize
    precision = max(0, -int(floor(round(tick, 8).as_integer_ratio()[1]).bit_length()/3.3219))
    p = round(p, precision if precision > 0 else 8)
    return p

# ================== STATE ==================
positions = {}

# ================== TELEGRAM ==================
def tg(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg}
        )
    except: pass

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
                text = u["message"]["text"]

                if text == "/start":
                    BOT_ON = True
                    tg("🟢 BOT ON")

                elif text == "/stop":
                    BOT_ON = False
                    tg("🔴 BOT OFF")

                elif text == "/positions":
                    show_positions()

                elif text.startswith("/close"):
                    sym = text.split()[1].upper()
                    close_position(sym, manual=True)
        except: pass
        time.sleep(2)

# ================== DATA ==================
def get_klines(symbol):
    df = pd.DataFrame(
        client.futures_klines(symbol=symbol, interval=INTERVAL, limit=100),
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
    price = float(client.futures_symbol_ticker(symbol=symbol)["price"])
    price = fmt_price(symbol, price)

    raw_qty = (RISK_PER_TRADE * LEVERAGE) / price
    qty = fmt_qty(symbol, raw_qty)
    if qty == 0:
        return

    client.futures_change_leverage(symbol=symbol, leverage=LEVERAGE)
    client.futures_create_order(
        symbol=symbol,
        side=side,
        type="MARKET",
        quantity=qty
    )

    # TP рассчитываем и сразу округляем по шагу цены
    tp_price = (
        price * (1 + TP_ROI / LEVERAGE)
        if side == "BUY"
        else price * (1 - TP_ROI / LEVERAGE)
    )
    tp_price = fmt_price(symbol, tp_price)  # округление по tickSize

    positions[symbol] = {
        "side": side,
        "entry": price,
        "qty": qty,
        "tp": tp_price
    }

    tg(
        f"🚀 ENTRY {symbol} {side}\n"
        f"Entry: {price}\n"
        f"Qty: {qty}\n"
        f"TP (ROI 10%): {tp_price}"
        f"\n\n/start /stop /positions "
    )

# ================== CLOSE ==================
def close_position(symbol, manual=False):
    if symbol not in positions:
        return

    pos_info = client.futures_position_information(symbol=symbol)
    amt = abs(float(pos_info[0]["positionAmt"]))
    qty = fmt_qty(symbol, amt)

    if qty == 0:
        positions.pop(symbol, None)
        return

    side = positions[symbol]["side"]

    client.futures_create_order(
        symbol=symbol,
        side="SELL" if side == "BUY" else "BUY",
        type="MARKET",
        quantity=qty
    )

    tg(f"{'✂️ MANUAL CLOSE\n\n/start /stop /positions ' if manual else '✅ TP HIT\n\n/start /stop /positions '} {symbol}")
    positions.pop(symbol, None)

# ================== MANAGER ==================
def manage_positions():
    while True:
        for symbol, p in list(positions.items()):
            price = float(client.futures_symbol_ticker(symbol=symbol)["price"])
            price = fmt_price(symbol, price)

            if p["side"] == "BUY" and price >= p["tp"]:
                close_position(symbol)
            elif p["side"] == "SELL" and price <= p["tp"]:
                close_position(symbol)
        time.sleep(1)

# ================== UI ==================
def show_positions():
    if not positions:
        tg("📭 No open positions")
        return

    msg = "📌 OPEN POSITIONS\n\n"
    for s,p in positions.items():
        msg += (
            f"{s} | {p['side']}\n"
            f"Entry: {p['entry']}\n"
            f"TP: {p['tp']}\n"
            f"Qty: {p['qty']}\n\n"
            f"\n/start /stop /positions "
        )
    tg(msg)

# ================== MAIN ==================
threading.Thread(target=telegram_listener, daemon=True).start()
threading.Thread(target=manage_positions, daemon=True).start()

tg("🤖 BOT STARTED\n/start /stop /positions /close SYMBOL")

while True:
    if BOT_ON:
        for s in SYMBOLS:
            trade(s)
            time.sleep(1)
    time.sleep(5)

import time
import requests
import threading
import pandas as pd
from binance.client import Client
from math import floor
from decimal import Decimal, ROUND_DOWN

# ================== CONFIG ==================
API_KEY = "41bJFweA1m3Mp9UOTXMr82kQeFCSGu2AtYweii1Rn9CacNTeHor3tPzZfOa1Ty7q"
API_SECRET = "JTNiSXaKeBvcl3oFDN8GgP6rR2KgCfIhW8f1ByEAU4EsD2Ijid1dAn8b9wBotHS6"

TG_TOKEN = "8554034676:AAEIEPOwkWYFz9_dpDla2jfu-t5EDRpSygE"
CHAT_ID = "5540625088"

SYMBOLS = ["AAVEUSDT","LTCUSDT","INJUSDT","XRPUSDT","ADAUSDT","HBARUSDT"]
INTERVAL = Client.KLINE_INTERVAL_5MINUTE
BOT_ON = True

LEVERAGE = 20
RISK_PER_TRADE = 10
TP_ROI = [0.05, 0.10, 0.15]  # TP1 / TP2 / TP3 в процентах
SL_ROI = 0.20                 # стоп-лосс в процентах
BE_TRIGGER = 0.005             # 0.5% прибыли для переноса SL в BE
TRAILING_STEP = 0.003           # шаг трейлинг стопа 0.3%

# ================== BINANCE CLIENT ==================
client = Client(API_KEY, API_SECRET)
client.FUTURES_URL = "https://testnet.binancefuture.com/fapi"
client.timestamp_offset = client.get_server_time()["serverTime"] - int(time.time() * 1000)

# ================== STATE ==================
positions = {}
stats = {"trades": 0, "pnl": 0.0, "win": 0, "loss": 0}

# ================== EXCHANGE INFO ==================
exchange_info = client.futures_exchange_info()

def get_filters(symbol):
    for s in exchange_info["symbols"]:
        if s["symbol"] == symbol:
            lot = next(f for f in s["filters"] if f["filterType"] == "LOT_SIZE")
            price = next(f for f in s["filters"] if f["filterType"] == "PRICE_FILTER")
            return float(lot["stepSize"]), float(lot["minQty"]), float(price["tickSize"])
    return 0.001, 0.001, 0.01

def fmt_qty(symbol, qty):
    step, min_qty, _ = get_filters(symbol)
    step = Decimal(str(step))
    qty = Decimal(str(qty))
    q = (qty // step) * step
    q = q.quantize(step, rounding=ROUND_DOWN)
    return float(q) if q >= Decimal(str(min_qty)) else 0

def fmt_price(symbol, price):
    _, _, tick = get_filters(symbol)
    tick = Decimal(str(tick))
    price = Decimal(str(price))
    p = (price // tick) * tick
    p = p.quantize(tick, rounding=ROUND_DOWN)
    return float(p)

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

# ================== TELEGRAM ==================
def tg(msg, buttons=True):
    payload = {"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}
    if buttons:
        payload["reply_markup"] = {
            "inline_keyboard": [
                [{"text": "▶️ Старт", "callback_data": "start"},
                 {"text": "⏹ Стоп", "callback_data": "stop"}],
                [{"text": "📜 Відкриті позиції", "callback_data": "positions"},
                 {"text": "📊 Статистика", "callback_data": "stats"}],
                [{"text": "🗑️ Закрити всі позиції", "callback_data": "close_all"}]
            ]
        }
    try:
        requests.post(f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage", json=payload)
    except: pass

def handle_callback(data):
    global BOT_ON
    if data == "start":
        BOT_ON = True
        tg("🤖 BOT Запущен\n\nГарного профіту")
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
            r = requests.get(f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates?offset={offset}").json()
            for u in r.get("result", []):
                offset = u["update_id"] + 1
                if "callback_query" in u:
                    handle_callback(u["callback_query"]["data"])
                elif "message" in u:
                    text = u["message"]["text"]
                    if text.startswith("/close"):
                        sym = text.split()[1].upper()
                        close_position(sym, manual=True)
        except: pass
        time.sleep(1)

# ================== UI ==================
def show_positions():
    if not positions:
        tg("📭 <b>Немає відкритих позицій</b>")
        return
    msg = "📜 ВІДКРИТІ ПОЗИЦІЇ\n\n"
    for s, p in positions.items():
        price = fmt_price(s, float(client.futures_symbol_ticker(symbol=s)["price"]))
        pnl = (price - p["entry"]) * p["qty"]
        if p["side"] == "SELL": pnl = -pnl
        msg += f"<b>{s}</b> | <i>{p['side']}</i>\nEntry: <b>{p['entry']}</b>\nPnL: <b>{pnl:.2f}</b> USDT\n\n"
    tg(msg)

def show_stats():
    tg(f"📊 СТАТИСТИКА\nTrades: {stats['trades']}\nWin: {stats['win']}\nLoss: {stats['loss']}\nTotal PnL: <b>{stats['pnl']:.2f}</b> USDT")

# ================== TRADE ==================
def trade(symbol):
    if symbol in positions: return
    df = indicators(get_klines(symbol))
    last = df.iloc[-1]
    long = last.ema9 > last.ema21 and last.rsi > 50 and last.v > last.vol_ma
    short = last.ema9 < last.ema21 and last.rsi < 50 and last.v > last.vol_ma
    if not (long or short): return
    side = "BUY" if long else "SELL"
    price = fmt_price(symbol, float(client.futures_symbol_ticker(symbol=symbol)["price"]))
    qty = fmt_qty(symbol, (RISK_PER_TRADE * LEVERAGE) / price)
    if qty == 0: return
    client.futures_change_leverage(symbol=symbol, leverage=LEVERAGE)
    client.futures_create_order(symbol=symbol, side=side, type="MARKET", quantity=qty)
    if side == "BUY":
        tp_prices = [fmt_price(symbol, price * (1 + tp / LEVERAGE)) for tp in TP_ROI]
        sl_price = fmt_price(symbol, price * (1 - SL_ROI / LEVERAGE))
    else:
        tp_prices = [fmt_price(symbol, price * (1 - tp / LEVERAGE)) for tp in TP_ROI]
        sl_price = fmt_price(symbol, price * (1 + SL_ROI / LEVERAGE))
    positions[symbol] = {"side": side, "entry": price, "qty": qty, "tp_prices": tp_prices, "sl": sl_price, "tp_closed": [False]*len(TP_ROI)}
    tg(f"🚀 <b>Вхід в позицію</b> {side}\n<i>{symbol}</i>\n<i>Entry:</i> <b>{price}</b>\n<i>TP1:</i> <b>{tp_prices[0]}</b>\n<i>TP2:</i> <b>{tp_prices[1]}</b>\n<i>TP3:</i> <b>{tp_prices[2]}</b>\n<i>SL:</i> <b>{sl_price}</b>")

# ================== CLOSE ==================
def close_position(symbol, qty=None, manual=False, sl=False):
    if symbol not in positions: return
    p = positions[symbol]
    price = fmt_price(symbol, float(client.futures_symbol_ticker(symbol=symbol)["price"]))
    close_qty = qty if qty else p["qty"]
    pnl = (price - p["entry"]) * close_qty
    if p["side"] == "SELL": pnl = -pnl
    stats["trades"] += 1
    stats["pnl"] += pnl
    if pnl > 0: stats["win"] += 1
    else: stats["loss"] += 1
    client.futures_create_order(symbol=symbol, side="SELL" if p["side"]=="BUY" else "BUY", type="MARKET", quantity=close_qty)
    if manual: title = "🗑️ Закрито вручну"
    elif sl: title = "🛑 STOP LOSS"
    else: title = "✅ TAKE PROFIT 💸"
    tg(f"{title} {symbol}\n<i>PnL:</i> <b>{pnl:.2f}</b> USDT\n<i>Статистика:</i> Trades={stats['trades']}, PnL={stats['pnl']:.2f}, Win={stats['win']}, Loss={stats['loss']}")
    if qty is None or qty >= p["qty"]: positions.pop(symbol)
    else: p["qty"] -= qty

# ================== MANAGER ==================
def manage_positions():
    while True:
        for s, p in list(positions.items()):
            price = fmt_price(s, float(client.futures_symbol_ticker(symbol=s)["price"]))
            side = p["side"]
            for i, tp_price in enumerate(p["tp_prices"]):
                if not p["tp_closed"][i] and ((side=="BUY" and price>=tp_price) or (side=="SELL" and price<=tp_price)):
                    tp_qty = fmt_qty(s, p["qty"] / (len(p["tp_prices"])-i))
                    close_position(s, qty=tp_qty)
                    p["tp_closed"][i] = True
            if side=="BUY" and price-p["entry"]>=p["entry"]*BE_TRIGGER and p["sl"]<p["entry"]:
                p["sl"] = p["entry"]
                tg(f"🛡️ Stop Loss перенесено в BE для {s}")
            elif side=="SELL" and p["entry"]-price>=p["entry"]*BE_TRIGGER and p["sl"]>p["entry"]:
                p["sl"] = p["entry"]
                tg(f"🛡️ Stop Loss перенесено в BE для {s}")
            if side=="BUY":
                new_sl = price - price*TRAILING_STEP
                if new_sl>p["sl"]: p["sl"]=fmt_price(s,new_sl)
            else:
                new_sl = price + price*TRAILING_STEP
                if new_sl<p["sl"]: p["sl"]=fmt_price(s,new_sl)
            if (side=="BUY" and price<=p["sl"]) or (side=="SELL" and price>=p["sl"]):
                close_position(s, sl=True)
        time.sleep(1)

# ================== MAIN ==================
threading.Thread(target=manage_positions, daemon=True).start()
threading.Thread(target=telegram_listener, daemon=True).start()
tg("🤖 Бот запущен 🚀")

while True:
    if BOT_ON:
        for s in SYMBOLS:
            trade(s)
            time.sleep(1)
    time.sleep(5)

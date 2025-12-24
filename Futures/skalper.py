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
TP_ROIS = [0.15, 0.25, 0.35]
TP_PORTIONS = [0.40, 0.40, 0.20]    
SL_ROI = 0.15   # 🔴 STOP LOSS 15% ROI
BE_ROI = 0.10
BE_OFFSET_ROI = 0.010
TRAILING_START_ROI = 0.10
TRAILING_OFFSET_ROI = 0.08

MIN_NOTIONAL = 5.0

BOT_ON = True

client = Client(API_KEY, API_SECRET, requests_params={"timeout": 30})
client.FUTURES_URL = "https://testnet.binancefuture.com/fapi"
client.timestamp_offset = client.get_server_time()["serverTime"] - int(time.time() * 1000)


# ================== EXCHANGE INFO ==================
exchange_info = client.futures_exchange_info()

def get_filters(symbol):
    for s in exchange_info["symbols"]:
        if s["symbol"] == symbol:
            lot = next(f for f in s["filters"] if f["filterType"] == "LOT_SIZE")
            price_filter = next(f for f in s["filters"] if f["filterType"] == "PRICE_FILTER")
            min_notional_filter = next((f for f in s["filters"] if f["filterType"] == "MIN_NOTIONAL"), None)
            min_notional = float(min_notional_filter["notional"]) if min_notional_filter else 5.0
            return float(lot["stepSize"]), float(lot["minQty"]), float(price_filter["tickSize"]), min_notional
    return 0.001, 0.001, 0.01, 5.0

def step_precision(step):
    return max(0, len(str(step).split('.')[-1].rstrip('0')))

def fmt_qty(symbol, qty):
    step, min_qty, _, _ = get_filters(symbol)
    precision = step_precision(step)
    q = floor(qty / step) * step
    q = round(q, precision)
    return q if q >= min_qty else 0

def fmt_price(symbol, price):
    _, _, tick, _ = get_filters(symbol)
    precision = step_precision(tick)
    p = floor(price / tick) * tick
    return round(p, precision)

def get_real_position_qty(symbol):
    positions_info = client.futures_position_information(symbol=symbol)
    for pos in positions_info:
        if pos["symbol"] == symbol:
            amt = float(pos["positionAmt"])
            return abs(amt)
    return 0.0


# ================== STATE ==================
positions = {}
stats = {
    "trades": 0,
    "pnl": 0.0,
    "win": 0,
    "loss": 0
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

    total_qty = fmt_qty(symbol, (RISK_PER_TRADE * LEVERAGE) / price)
    if total_qty == 0:
        return

    client.futures_change_leverage(symbol=symbol, leverage=LEVERAGE)
    client.futures_create_order(symbol=symbol, side=side, type="MARKET", quantity=total_qty)

    sl_roi = SL_ROI
    sl_price = price * (1 - sl_roi / LEVERAGE) if side == "BUY" else price * (1 + sl_roi / LEVERAGE)
    sl_price = fmt_price(symbol, sl_price)

    tp_levels = []
    remaining_qty = total_qty
    for i, (roi, portion) in enumerate(zip(TP_ROIS, TP_PORTIONS)):
        tp_price = price * (1 + roi / LEVERAGE) if side == "BUY" else price * (1 - roi / LEVERAGE)
        tp_price = fmt_price(symbol, tp_price)
        
        if i == len(TP_ROIS) - 1:
            partial_qty = remaining_qty
        else:
            partial_qty = fmt_qty(symbol, total_qty * portion)
            if partial_qty == 0:
                continue
            remaining_qty -= partial_qty
        
        if partial_qty > 0:
            tp_levels.append({
                "roi": roi,
                "price": tp_price,
                "qty": partial_qty
            })

    if not tp_levels:
        return

    positions[symbol] = {
        "side": side,
        "entry": price,
        "total_qty": total_qty,
        "remaining_qty": total_qty,
        "sl": sl_price,
        "tp_levels": tp_levels,
        "be_triggered": False,
        "trailing_triggered": False,
        "trailing_sl": sl_price
    }

    tp_msg = "\n".join([f"TP{i+1}: <b>{tp['price']}</b> (Qty: {tp['qty']})" for i, tp in enumerate(tp_levels)])
    tg(
        f"🚀 <b>Вхід в позицію</b> {side}\n<i>{symbol}</i>\n"
        f"<i>Entry:</i> <b>{price}</b>\n"
        f"{tp_msg}\n"
        f"<i>SL:</i> <b>{sl_price}</b>"
    )



# ================== CLOSE PARTIAL ==================
def close_partial(symbol, qty, price, is_tp=False, level_index=None):
    if symbol not in positions:
        return 0.0, False

    p = positions[symbol]

    real_qty = get_real_position_qty(symbol)
    if real_qty <= 0:
        # ❌ Позиции реально нет — чистим память
        positions.pop(symbol, None)
        return 0.0, True

    qty = fmt_qty(symbol, min(qty, real_qty))
    if qty == 0:
        return 0.0, False

    close_side = "SELL" if p["side"] == "BUY" else "BUY"

    try:
        client.futures_create_order(
            symbol=symbol,
            side=close_side,
            type="MARKET",
            quantity=qty,
            reduceOnly=True,
            positionSide="BOTH"   # 🔥 ОБЯЗАТЕЛЬНО
        )
    except Exception as e:
        print(f"ReduceOnly rejected {symbol}: {e}")
        return 0.0, False

    pnl = (price - p["entry"]) * qty if p["side"] == "BUY" else (p["entry"] - price) * qty

    p["remaining_qty"] -= qty

    if is_tp and level_index is not None:
        del p["tp_levels"][level_index]

    if p["remaining_qty"] <= 0:
        positions.pop(symbol, None)
        return pnl, True

    return pnl, False




# ================== CLOSE ==================
def close_position(symbol, manual=False, sl=False):
    if symbol not in positions:
        return

    p = positions[symbol]
    price = fmt_price(symbol, float(client.futures_symbol_ticker(symbol=symbol)["price"]))

    pnl, fully_closed = close_partial(symbol, p["remaining_qty"], price)

    if pnl == 0.0 and not fully_closed:
        # Не удалось закрыть — принудительно удаляем из памяти
        positions.pop(symbol, None)
        return

    stats["trades"] += 1
    stats["pnl"] += pnl

    if pnl > 0:
        stats["win"] += 1
    elif pnl < 0:
        stats["loss"] += 1

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


# ================== MANAGER ==================
def manage_positions():
    while True:
        for s in list(positions.keys()):  # Используем list(keys()) для безопасного перебора
            if s not in positions:
                continue

            p = positions[s]
            price = fmt_price(s, float(client.futures_symbol_ticker(symbol=s)["price"]))

            if p["side"] == "BUY":
                roi = (price / p["entry"] - 1) * LEVERAGE
            else:
                roi = (p["entry"] / price - 1) * LEVERAGE

            # SL Check
            if (p["side"] == "BUY" and price <= p["sl"]) or (p["side"] == "SELL" and price >= p["sl"]):
                close_position(s, sl=True)
                continue

            # Break Even
            if not p["be_triggered"] and roi >= BE_ROI:
                be_sl = p["entry"] * (1 + BE_OFFSET_ROI / LEVERAGE) if p["side"] == "BUY" else p["entry"] * (1 - BE_OFFSET_ROI / LEVERAGE)
                p["sl"] = fmt_price(s, be_sl)
                p["trailing_sl"] = p["sl"]
                p["be_triggered"] = True
                tg(f"🔄 BE triggered for {s} at {p['sl']}")

            # Trailing Stop
            if roi >= TRAILING_START_ROI:
                p["trailing_triggered"] = True

            if p["trailing_triggered"]:
                new_trailing_sl = price * (1 - TRAILING_OFFSET_ROI / LEVERAGE) if p["side"] == "BUY" else price * (1 + TRAILING_OFFSET_ROI / LEVERAGE)
                new_trailing_sl = fmt_price(s, new_trailing_sl)
                
                if (p["side"] == "BUY" and new_trailing_sl > p["trailing_sl"]) or (p["side"] == "SELL" and new_trailing_sl < p["trailing_sl"]):
                    p["trailing_sl"] = new_trailing_sl
                    p["sl"] = new_trailing_sl
                    tg(f"📈 Trailing SL updated for {s} to {p['sl']}")

            # TP Levels
            for i, tp in enumerate(p["tp_levels"][:]):
                hit_tp = (p["side"] == "BUY" and price >= tp["price"]) or (p["side"] == "SELL" and price <= tp["price"])
                if hit_tp:
                    pnl, _ = close_partial(s, tp["qty"], price, is_tp=True, level_index=i)
                    if pnl != 0.0:
                        stats["trades"] += 1
                        stats["pnl"] += pnl
                        if pnl > 0:
                            stats["win"] += 1
                        elif pnl < 0:
                            stats["loss"] += 1
                        tg(f"✅ TP{i+1} hit for {s}\n<i>PnL:</i> <b>{pnl:.2f}</b> USDT")
                    break

        time.sleep(1)


# ================== UI ==================
def show_positions():
    if not positions:
        tg("📭 <b>Немає відкритих позицій</b>")
        return

    msg = "📜 ВІДКРИТІ ПОЗИЦІЇ\n\n"
    for s, p in positions.items():
        price = fmt_price(s, float(client.futures_symbol_ticker(symbol=s)["price"]))
        pnl = (price - p["entry"]) * p["remaining_qty"] if p["side"] == "BUY" else (p["entry"] - price) * p["remaining_qty"]

        tp_msg = "\n".join([f"TP{i+1}: <b>{tp['price']}</b> (Qty: {tp['qty']})" for i, tp in enumerate(p["tp_levels"])])

        msg += (
            f"<b>{s}</b> | <i>{p['side']}</i>\n"
            f"Entry: <b>{p['entry']}</b>\n"
            f"{tp_msg}\n"
            f"SL: <b>{p['sl']}</b>\n"
            f"PnL: <b>{pnl:.2f}</b> USDT\n\n"
        )
    tg(msg)


def show_stats():
    win_rate = (stats['win'] / stats['trades'] * 100) if stats['trades'] > 0 else 0
    tg(
        f"📊 СТАТИСТИКА\n"
        f"Trades: {stats['trades']}\n"
        f"Win: {stats['win']} ({win_rate:.1f}%)\n"
        f"Loss: {stats['loss']}\n"
        f"Total PnL: <b>{stats['pnl']:.2f}</b> USDT"
    )


# ================== MAIN ==================
threading.Thread(target=telegram_listener, daemon=True).start()
threading.Thread(target=manage_positions, daemon=True).start()

tg("🤖🤖 Працівник працює 🚀")

while True:
    if BOT_ON:
        for s in SYMBOLS:
            trade(s)
            time.sleep(1)
    time.sleep(5)
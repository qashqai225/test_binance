# ================== IMPORTS ==================
import time
import requests
import threading
import pandas as pd
import json
import os
from binance.client import Client
from math import floor
from functools import wraps

# ================== CONFIG ==================
API_KEY = "41bJFweA1m3Mp9UOTXMr82kQeFCSGu2AtYweii1Rn9CacNTeHor3tPzZfOa1Ty7q"
API_SECRET = "JTNiSXaKeBvcl3oFDN8GgP6rR2KgCfIhW8f1ByEAU4EsD2Ijid1dAn8b9wBotHS6"
TG_TOKEN = "8554034676:AAEIEPOwkWYFz9_dpDla2jfu-t5EDRpSygE"
CHAT_ID = "5540625088"
SYMBOLS = ["AAVEUSDT","LTCUSDT","INJUSDT","XRPUSDT","ADAUSDT","HBARUSDT"]
ENTRY_INTERVAL = Client.KLINE_INTERVAL_5MINUTE
TREND_INTERVAL = Client.KLINE_INTERVAL_15MINUTE
LEVERAGE = 20
RISK_PER_TRADE = 10
SL_ATR_MULT = 1.2
BE_ATR_MULT = 1.0
BE_OFFSET = 0.1
TRAIL_ATR_MULT = 1.0
BOT_ON = True

STATS_FILE = "stats.json"
STATS_INTERVAL = 1800  # секунд (30 минут)

# ================== BINANCE ==================
client = Client(API_KEY, API_SECRET, requests_params={"timeout": 30})
client.FUTURES_URL = "https://testnet.binancefuture.com/fapi"
client.timestamp_offset = client.get_server_time()["serverTime"] - int(time.time() * 1000)
exchange_info = client.futures_exchange_info()

# ================== STATISTICS ==================
class Stats:
    def __init__(self):
        self.trades = []  # список завершённых сделок
        self.load()

    def load(self):
        if os.path.exists(STATS_FILE):
            try:
                with open(STATS_FILE, 'r') as f:
                    data = json.load(f)
                    self.trades = data.get("trades", [])
            except:
                self.trades = []

    def save(self):
        with open(STATS_FILE, 'w') as f:
            json.dump({"trades": self.trades}, f)

    def add_trade(self, symbol, side, entry, exit_price, qty):
        pnl = (exit_price - entry) * qty if side == "BUY" else (entry - exit_price) * qty
        pnl *= LEVERAGE  # учитываем плечо
        win = pnl > 0
        self.trades.append({
            "symbol": symbol,
            "side": side,
            "entry": entry,
            "exit": exit_price,
            "qty": qty,
            "pnl": round(pnl, 2),
            "win": win,
            "timestamp": int(time.time())
        })
        self.save()
        return pnl

    def get_summary(self):
        if not self.trades:
            return "📊 Статистика пуста\nНет завершённых сделок"
        
        total = len(self.trades)
        wins = sum(1 for t in self.trades if t["win"])
        losses = total - wins
        winrate = round(wins / total * 100, 2) if total > 0 else 0
        total_pnl = round(sum(t["pnl"] for t in self.trades), 2)
        
        return f"""📊 Статистика бота
Всего сделок: {total}
Побед: {wins} | Поражений: {losses}
Винрейт: {winrate}%
Общий PnL: {total_pnl:+.2f} USDT
Активных позиций: {len(positions)}"""

stats = Stats()

# ================== SAFE API ==================
def safe_api(retries=3, delay=1):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for _ in range(retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    print("API error:", e)
                    time.sleep(delay)
            return None
        return wrapper
    return decorator

# ================== FILTERS ==================
def get_filters(symbol):
    for s in exchange_info["symbols"]:
        if s["symbol"] == symbol:
            lot = next(f for f in s["filters"] if f["filterType"] == "LOT_SIZE")
            price = next(f for f in s["filters"] if f["filterType"] == "PRICE_FILTER")
            return float(lot["stepSize"]), float(lot["minQty"]), float(price["tickSize"])
    return 0.001, 0.001, 0.01

def precision(step):
    return max(0, len(str(step).split('.')[-1].rstrip('0')))

def fmt_qty(symbol, qty):
    step, min_qty, _ = get_filters(symbol)
    qty = floor(qty / step) * step
    return round(qty, precision(step)) if qty >= min_qty else 0

def fmt_price(symbol, price):
    _, _, tick = get_filters(symbol)
    price = floor(price / tick) * tick
    return round(price, precision(tick))

# ================== MARKET DATA ==================
@safe_api()
def get_klines(symbol, interval):
    df = pd.DataFrame(
        client.futures_klines(symbol=symbol, interval=interval, limit=200),
        columns=["t","o","h","l","c","v","x","q","n","T","Q","i"]
    )
    return df.astype(float)

# ================== INDICATORS ==================
def indicators(df):
    df["ema9"] = df["c"].ewm(span=9).mean()
    df["ema21"] = df["c"].ewm(span=21).mean()
    df["ema50"] = df["c"].ewm(span=50).mean()
    df["ema200"] = df["c"].ewm(span=200).mean()
    delta = df["c"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = -delta.clip(upper=0).rolling(14).mean()
    df["rsi"] = 100 - (100 / (1 + gain / loss))
    df["vol_ma"] = df["v"].rolling(20).mean()
    df["atr"] = (df["h"] - df["l"]).rolling(14).mean()
    return df

# ================== ENTRY LOGIC ==================
def impulse(r):
    body = abs(r.c - r.o)
    rng = r.h - r.l
    return rng > 0 and body / rng > 0.6

def pullback(r):
    return r.l <= r.ema9 <= r.h or r.l <= r.ema21 <= r.h

def improved_entry(df):
    r = df.iloc[-1]
    if r.atr < r.c * 0.0012:
        return None
    if r.ema9 > r.ema21 and pullback(r) and impulse(r) and 52 <= r.rsi <= 65 and r.v > r.vol_ma * 1.2:
        return "BUY"
    if r.ema9 < r.ema21 and pullback(r) and impulse(r) and 35 <= r.rsi <= 48 and r.v > r.vol_ma * 1.2:
        return "SELL"
    return None

# ================== MTF ==================
def mtf_trend(symbol):
    df = indicators(get_klines(symbol, TREND_INTERVAL))
    r = df.iloc[-1]
    if r.ema50 > r.ema200:
        return "BUY"
    if r.ema50 < r.ema200:
        return "SELL"
    return None

# ================== STATE ==================
positions = {}

# ================== TELEGRAM ==================
def tg(msg):
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": msg, "parse_mode":"HTML"}
        )
    except:
        pass

# ================== TRADE ==================
@safe_api()
def trade(symbol):
    if symbol in positions:
        return
    trend = mtf_trend(symbol)
    if not trend:
        return
    df = indicators(get_klines(symbol, ENTRY_INTERVAL))
    side = improved_entry(df)
    if side != trend:
        return
    price = fmt_price(symbol, float(client.futures_symbol_ticker(symbol=symbol)["price"]))
    qty = fmt_qty(symbol, (RISK_PER_TRADE * LEVERAGE) / price)
    if qty == 0:
        return
    atr = df.iloc[-1].atr
    client.futures_change_leverage(symbol=symbol, leverage=LEVERAGE)
    client.futures_create_order(symbol=symbol, side=side, type="MARKET", quantity=qty)
    sl = price - atr * SL_ATR_MULT if side == "BUY" else price + atr * SL_ATR_MULT
    sl = fmt_price(symbol, sl)
    positions[symbol] = {
        "side": side,
        "qty": qty,
        "entry": price,
        "atr": atr,
        "sl": sl,
        "be": False
    }
    tg(f"🚀 <b>{symbol}</b>\n{side} | Левередж x{LEVERAGE}\nEntry: {price}\nSL: {sl}\nРазмер: {qty}")

# ================== MANAGER ==================
def manager():
    last_stats_time = time.time()
    while True:
        current_time = time.time()
        
        # Периодический вывод статистики
        if current_time - last_stats_time >= STATS_INTERVAL:
            tg(stats.get_summary())
            last_stats_time = current_time

        for s in list(positions):
            p = positions[s]
            price = fmt_price(s, float(client.futures_symbol_ticker(symbol=s)["price"]))
            
            # BE
            if not p["be"]:
                move = abs(price - p["entry"])
                if move >= p["atr"] * BE_ATR_MULT:
                    p["sl"] = p["entry"] + BE_OFFSET if p["side"] == "BUY" else p["entry"] - BE_OFFSET
                    p["be"] = True
                    tg(f"🟡 <b>BE активирован</b> {s}\nSL перемещён в безубыток")

            # Trailing
            if p["be"]:
                new_sl = price - p["atr"] * TRAIL_ATR_MULT if p["side"] == "BUY" else price + p["atr"] * TRAIL_ATR_MULT
                if (p["side"] == "BUY" and new_sl > p["sl"]) or (p["side"] == "SELL" and new_sl < p["sl"]):
                    old_sl = p["sl"]
                    p["sl"] = fmt_price(s, new_sl)
                    tg(f"🔄 <b>Trailing SL</b> {s}\n{old_sl} → {p['sl']}")

            # Stop / Exit
            exit_triggered = (p["side"] == "BUY" and price <= p["sl"]) or (p["side"] == "SELL" and price >= p["sl"])
            if exit_triggered:
                close_side = "SELL" if p["side"] == "BUY" else "BUY"
                client.futures_create_order(
                    symbol=s, side=close_side, type="MARKET",
                    quantity=p["qty"], reduceOnly=True
                )
                pnl = stats.add_trade(s, p["side"], p["entry"], price, p["qty"])
                result_emoji = "🟢" if pnl > 0 else "🔴"
                positions.pop(s)
                tg(f"{result_emoji} <b>ВЫХОД {s}</b>\n"
                   f"Сделка: {p['side']}\n"
                   f"Entry: {p['entry']} → Exit: {price}\n"
                   f"PnL: {pnl:+.2f} USDT")
        
        time.sleep(1)

# ================== MAIN ==================
threading.Thread(target=manager, daemon=True).start()
tg("🤖 <b>Працівник працює</b>\nMTF + ATR Management + Статистика\n" + stats.get_summary())

while True:
    if BOT_ON:
        for s in SYMBOLS:
            trade(s)
            time.sleep(1)
    time.sleep(5)
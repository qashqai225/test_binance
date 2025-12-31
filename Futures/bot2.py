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
import logging

# ================== LOGGING ==================
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ================== CONFIG ==================
API_KEY = "41bJFweA1m3Mp9UOTXMr82kQeFCSGu2AtYweii1Rn9CacNTeHor3tPzZfOa1Ty7q"
API_SECRET = "JTNiSXaKeBvcl3oFDN8GgP6rR2KgCfIhW8f1ByEAU4EsD2Ijid1dAn8b9wBotHS6"
TG_TOKEN = "8554034676:AAEIEPOwkWYFz9_dpDla2jfu-t5EDRpSygE"
CHAT_ID = "5540625088"
SYMBOLS = ["AAVEUSDT","LTCUSDT","INJUSDT","XRPUSDT","ADAUSDT","HBARUSDT"]
ENTRY_INTERVAL = Client.KLINE_INTERVAL_5MINUTE
TREND_INTERVAL = Client.KLINE_INTERVAL_30MINUTE
LEVERAGE = 20
RISK_PER_TRADE = 10  # USDT риска на сделку (без учёта плеча)
SL_ATR_MULT = 1.5
BE_ATR_MULT = 2.5
BE_OFFSET = 0.5  # небольшое смещение для BE (в цене, не в %)
TRAIL_ATR_MULT = 0.5
BOT_ON = True

STATS_FILE = "stats.json"
STATS_INTERVAL = 1800  # секунд (30 минут)

# ================== BINANCE ==================
client = Client(API_KEY, API_SECRET, requests_params={"timeout": 30})
client.FUTURES_URL = "https://testnet.binancefuture.com/fapi"  # Тестнет
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
            except Exception as e:
                logging.error(f"Ошибка загрузки stats: {e}")
                self.trades = []

    def save(self):
        try:
            with open(STATS_FILE, 'w') as f:
                json.dump({"trades": self.trades}, f)
        except Exception as e:
            logging.error(f"Ошибка сохранения stats: {e}")

    def add_trade(self, symbol, side, entry, exit_price, qty):
        # Правильный расчёт PnL для USDT-M perpetual futures
        # PnL = qty * (exit - entry) для LONG, qty * (entry - exit) для SHORT
        # Уже умножено на плечо благодаря размеру позиции (notional * leverage)
        if side == "BUY":
            pnl = qty * (exit_price - entry)
        else:
            pnl = qty * (entry - exit_price)
        pnl = round(pnl, 2)
        win = pnl > 0
        self.trades.append({
            "symbol": symbol,
            "side": side,
            "entry": entry,
            "exit": exit_price,
            "qty": qty,
            "pnl": pnl,
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
        
        return (f"📊 Статистика бота\n"
                f"Всего сделок: {total}\n"
                f"Побед: {wins} | Поражений: {losses}\n"
                f"Винрейт: {winrate}%\n"
                f"Общий PnL: <b>{total_pnl:+.2f}</b> USDT\n"
                f"Активных позиций: {len(positions)}")

stats = Stats()

# ================== SAFE API ==================
def safe_api(retries=3, delay=2):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    logging.warning(f"API error в {func.__name__}: {e} (попытка {attempt+1}/{retries})")
                    time.sleep(delay)
            logging.error(f"Не удалось выполнить {func.__name__} после {retries} попыток")
            return None
        return wrapper
    return decorator

# ================== FILTERS ==================
def get_filters(symbol):
    for s in exchange_info["symbols"]:
        if s["symbol"] == symbol:
            lot = next(f for f in s["filters"] if f["filterType"] == "LOT_SIZE")
            price = next(f for f in s["filters"] if f["filterType"] == "PRICE_FILTER")
            min_notional = next((f for f in s["filters"] if f["filterType"] == "MIN_NOTIONAL"), {"notional": "5"})
            return (float(lot["stepSize"]), float(lot["minQty"]), float(price["tickSize"]),
                    float(min_notional.get("notional", 5)))
    return 0.001, 0.001, 0.01, 5.0

def precision(step):
    return max(0, len(str(step).split('.')[-1].rstrip('0')))

def fmt_qty(symbol, qty):
    step, min_qty, _, min_notional = get_filters(symbol)
    qty = floor(qty / step) * step
    qty = round(qty, precision(step))
    return qty if qty >= min_qty and qty * current_price(symbol) >= min_notional else 0

def fmt_price(symbol, price):
    _, _, tick, _ = get_filters(symbol)
    price = floor(price / tick) * tick
    return round(price, precision(tick))

def current_price(symbol):
    try:
        return float(client.futures_symbol_ticker(symbol=symbol)["price"])
    except:
        return 0.0

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
    df["atr"] = (df["h"] - df["l"]).rolling(14).mean()  # True Range упрощённо как H-L
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
    if r.atr < r.c * 0.0012:  # фильтр низкой волатильности
        return None
    if (r.ema9 > r.ema21 and pullback(r) and impulse(r) and
        52 <= r.rsi <= 65 and r.v > r.vol_ma * 1.2):
        return "BUY"
    if (r.ema9 < r.ema21 and pullback(r) and impulse(r) and
        35 <= r.rsi <= 48 and r.v > r.vol_ma * 1.2):
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
            json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}
        )
    except Exception as e:
        logging.error(f"Ошибка отправки в TG: {e}")

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
    if not side or side != trend:
        return

    price = fmt_price(symbol, current_price(symbol))
    if price == 0:
        return

    qty = fmt_qty(symbol, (RISK_PER_TRADE * LEVERAGE) / price)
    if qty == 0:
        return

    atr = df.iloc[-1].atr

    try:
        client.futures_change_leverage(symbol=symbol, leverage=LEVERAGE)
        client.futures_create_order(symbol=symbol, side=side, type="MARKET", quantity=qty)
    except Exception as e:
        logging.error(f"Ошибка открытия позиции {symbol}: {e}")
        tg(f"❌ Ошибка открытия {symbol}: {str(e)}")
        return

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

    tg(f"🚀 <b>{symbol}</b>\n"
       f"{side} | x{LEVERAGE}\n"
       f"Entry: <b>{price}</b>\n"
       f"SL: <b>{sl}</b>\n"
       f"Qty: {qty}")

    logging.info(f"Открыта позиция {symbol} {side} по {price}, qty={qty}")

# ================== MANAGER ==================
def manager():
    last_stats_time = time.time()
    while True:
        current_time = time.time()
        
        # Периодическая статистика
        if current_time - last_stats_time >= STATS_INTERVAL:
            tg(stats.get_summary())
            last_stats_time = current_time

        for s in list(positions.keys()):
            if s not in positions:
                continue
            p = positions[s]
            try:
                price = fmt_price(s, current_price(s))
            except:
                continue

            # Break Even
            if not p["be"]:
                move = abs(price - p["entry"])
                if move >= p["atr"] * BE_ATR_MULT:
                    new_sl = p["entry"] + BE_OFFSET if p["side"] == "BUY" else p["entry"] - BE_OFFSET
                    new_sl = fmt_price(s, new_sl)
                    p["sl"] = new_sl
                    p["be"] = True
                    tg(f"🟡 <b>BE активирован</b> {s}\nSL → <b>{new_sl}</b>")

            # Trailing Stop после BE
            if p["be"]:
                new_sl = price - p["atr"] * TRAIL_ATR_MULT if p["side"] == "BUY" else price + p["atr"] * TRAIL_ATR_MULT
                new_sl = fmt_price(s, new_sl)
                if (p["side"] == "BUY" and new_sl > p["sl"]) or (p["side"] == "SELL" and new_sl < p["sl"]):
                    old_sl = p["sl"]
                    p["sl"] = new_sl
                    tg(f"🔄 <b>Trailing SL</b> {s}\n{old_sl} → <b>{p['sl']}</b>")

            # Выход по SL
            exit_triggered = ((p["side"] == "BUY" and price <= p["sl"]) or
                              (p["side"] == "SELL" and price >= p["sl"]))
            if exit_triggered:
                close_side = "SELL" if p["side"] == "BUY" else "BUY"
                try:
                    client.futures_create_order(
                        symbol=s, side=close_side, type="MARKET",
                        quantity=p["qty"], reduceOnly=True
                    )
                except Exception as e:
                    logging.error(f"Ошибка закрытия {s}: {e}")
                    tg(f"❌ Ошибка закрытия {s}: {str(e)}")
                    continue

                pnl = stats.add_trade(s, p["side"], p["entry"], price, p["qty"])
                result_emoji = "🟢" if pnl > 0 else "🔴"
                positions.pop(s, None)
                tg(f"{result_emoji} <b>ВЫХОД {s}</b>\n"
                   f"{p['side']} | Entry: {p['entry']} → Exit: {price}\n"
                   f"PnL: <b>{pnl:+.2f}</b> USDT")

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
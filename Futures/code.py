import time
import numpy as np
import pandas as pd
from binance.client import Client
from ta.momentum import RSIIndicator
from ta.volatility import AverageTrueRange
import requests

# =====================
# BINANCE
# =====================
API_KEY = "41bJFweA1m3Mp9UOTXMr82kQeFCSGu2AtYweii1Rn9CacNTeHor3tPzZfOa1Ty7q"
API_SECRET = "JTNiSXaKeBvcl3oFDN8GgP6rR2KgCfIhW8f1ByEAU4EsD2Ijid1dAn8b9wBotHS6"
SYMBOL = "AAVEUSDT"
LEVERAGE = 20
MAX_MARGIN = 100
STEP_MARGIN = 20
TAKE_PROFIT_USDT = 10
INTERVAL = Client.KLINE_INTERVAL_5MINUTE
CANDLES = 100

client = Client(API_KEY, API_SECRET, testnet=True, requests_params={'timeout': 30})

# =====================
# TELEGRAM
# =====================
TOKEN = "t.me/GenTraiderbot"
CHAT_ID = "8554034676:AAEIEPOwkWYFz9_dpDla2jfu-t5EDRpSygE"

def tg_notify(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": CHAT_ID, "text": text})

# =====================
# UTILS
# =====================
def get_precision(symbol):
    info = client.futures_exchange_info()
    for s in info['symbols']:
        if s['symbol'] == symbol:
            step_size = float(s['filters'][2]['stepSize'])
            return int(round(-np.log10(step_size), 0))
    return 3

def get_qty(price, step_margin, leverage, precision):
    qty = (step_margin * leverage) / price
    return round(qty, precision)

def get_price():
    ticker = client.futures_symbol_ticker(symbol=SYMBOL)
    return float(ticker['price'])

# =====================
# INDICATORS
# =====================
def calculate_vwap(df):
    tp = (df["high"] + df["low"] + df["close"]) / 3
    return (tp * df["volume"]).cumsum() / df["volume"].cumsum()

def calculate_supertrend(df, period=10, multiplier=3):
    atr = AverageTrueRange(df["high"], df["low"], df["close"], period).average_true_range()
    hl2 = (df["high"] + df["low"]) / 2
    upperband = hl2 + multiplier * atr
    lowerband = hl2 - multiplier * atr
    trend = [True]
    for i in range(1, len(df)):
        if df["close"].iloc[i] > upperband.iloc[i - 1]:
            trend.append(True)
        elif df["close"].iloc[i] < lowerband.iloc[i - 1]:
            trend.append(False)
        else:
            trend.append(trend[i - 1])
    df["Supertrend"] = trend
    df["ATR"] = atr
    df["VWAP"] = calculate_vwap(df)
    df["RSI"] = RSIIndicator(df["close"], 14).rsi()
    return df

def get_klines(retries=3):
    for _ in range(retries):
        try:
            klines = client.futures_klines(symbol=SYMBOL, interval=INTERVAL, limit=CANDLES, recvWindow=60000)
            df = pd.DataFrame(klines, columns=["open_time","open","high","low","close","volume",
                                               "close_time","qav","num_trades","taker_base","taker_quote","ignore"])
            df["open"] = df["open"].astype(float)
            df["high"] = df["high"].astype(float)
            df["low"] = df["low"].astype(float)
            df["close"] = df["close"].astype(float)
            df["volume"] = df["volume"].astype(float)
            return df
        except Exception as e:
            print("Ошибка получения свечей:", e)
            time.sleep(5)
    raise Exception("Не удалось получить данные после 3 попыток")

# =====================
# CHECK SIGNAL
# =====================
def check_entry_signal(df):
    last = df.iloc[-1]
    long_conditions = [
        last["Supertrend"] == True,
        last["close"] >= last["VWAP"] * 0.999,
        48 < last["RSI"] < 62
    ]
    short_conditions = [
        last["Supertrend"] == False,
        last["close"] <= last["VWAP"] * 1.001,
        38 < last["RSI"] < 52
    ]
    print(f"ST={last['Supertrend']} | RSI={last['RSI']:.2f} | ΔVWAP={(last['close'] - last['VWAP']):.4f}")
    if all(long_conditions):
        return "LONG"
    if all(short_conditions):
        return "SHORT"
    return None

# =====================
# MAIN LOOP
# =====================
precision = get_precision(SYMBOL)

while True:
    df = get_klines()
    df = calculate_supertrend(df)

    print("Ожидание сигнала входа...")
    signal = None
    while signal is None:
        df = get_klines()
        df = calculate_supertrend(df)
        signal = check_entry_signal(df)
        time.sleep(60)  # проверка каждую минуту

    tg_notify(f"Сигнал {signal} найден!")

    side = "BUY" if signal=="LONG" else "SELL"
    exit_side = "SELL" if signal=="LONG" else "BUY"

    entry_prices = []
    position_qty = 0
    used_margin = 0

    # Постепенный вход
    while used_margin < MAX_MARGIN:
        price = get_price()
        qty = get_qty(price, STEP_MARGIN, LEVERAGE, precision)

        client.futures_create_order(
            symbol=SYMBOL,
            side=side,
            type="MARKET",
            quantity=qty
        )

        entry_prices.append(price)
        position_qty += qty
        used_margin += STEP_MARGIN
        print(f"{signal} вход по {price} | qty={qty} | Маржа {used_margin} USDT")
        tg_notify(f"{signal} вход по {price} | qty={qty} | Маржа {used_margin} USDT")
        time.sleep(2)

    avg_entry = sum(entry_prices)/len(entry_prices)
    atr_value = df["ATR"].iloc[-1]

    stop_loss_price = avg_entry - atr_value*1.2 if signal=="LONG" else avg_entry + atr_value*1.2

    # Ожидание TP / SL
    while True:
        price = get_price()
        if signal=="LONG":
            pnl = (price - avg_entry) * position_qty
            stop = price <= stop_loss_price
        else:
            pnl = (avg_entry - price) * position_qty
            stop = price >= stop_loss_price

        if pnl >= TAKE_PROFIT_USDT or stop:
            exit_price = price
            client.futures_create_order(
                symbol=SYMBOL,
                side=exit_side,
                type="MARKET",
                quantity=position_qty
            )
            msg_type = "TP" if pnl>=TAKE_PROFIT_USDT else "SL"
            print(f"{msg_type} {signal}: {pnl:.2f} USDT")
            tg_notify(f"{msg_type} {signal}: {pnl:.2f} USDT")
            break

        time.sleep(1)

    time.sleep(60)  # пауза перед новой проверкой

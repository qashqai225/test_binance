from core.client import client
from core.state import positions, stats
from config import LEVERAGE, RISK_PER_TRADE, TP_ROI
from telegram.bot import tg
from txt import indicators

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
    tp_price = fmt_price(symbol, tp_price)

    tp_pnl = abs(tp_price - price) * qty

    positions[symbol] = {
        "side": side,
        "entry": price,
        "qty": qty,
        "tp": tp_price
    }

    tg(
        f"🚀 ENTRY {symbol} {side}\n"
        f"Entry: {price}\n"
        f"TP: {tp_price}\n"
        f"TP PnL: {tp_pnl:.2f} USDT"
        f"\n/start /stop /positions /stats\n"
    )

# ================== CLOSE ==================
def close_position(symbol, manual=False):
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

    tg(
        f"{'✂️ MANUAL CLOSE' if manual else '✅ TP HIT'} {symbol}\n"
        f"PnL: {pnl:.2f} USDT"
        f"\n/start /stop /positions /stats\n"
    )

    positions.pop(symbol)


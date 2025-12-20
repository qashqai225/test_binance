from core.client import client
from core.state import positions, stats
from config import LEVERAGE, RISK_PER_TRADE, TP_ROI
from telegram.bot import tg

def open_position(symbol, side):
    if symbol in positions:
        return

    price = float(client.futures_symbol_ticker(symbol=symbol)["price"])
    qty = round((RISK_PER_TRADE * LEVERAGE) / price, 3)
    pln = qty  # если pln = количество (можно поменять формулу при необходимости)

    client.futures_change_leverage(symbol=symbol, leverage=LEVERAGE)
    client.futures_create_order(
        symbol=symbol,
        side=side,
        type="MARKET",
        quantity=qty
    )

    tp = price * (1 + TP_ROI / LEVERAGE) if side == "BUY" else price * (1 - TP_ROI / LEVERAGE)

    positions[symbol] = {
        "side": side,
        "entry": price,
        "pln": pln,
        "tp": tp
    }

    stats["trades"] += 1
    tg(f"🚀 ENTRY {symbol} {side}\nEntry: {price:.5f}\nTP: {tp:.5f}")


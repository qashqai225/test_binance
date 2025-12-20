import time
from core.client import client
from core.state import positions
from core.trader import close_position

def manager():
    while True:
        for s, p in list(positions.items()):
            price = float(client.futures_symbol_ticker(symbol=s)["price"])
            if p["side"] == "BUY" and price >= p["tp"]:
                close_position(s)
            elif p["side"] == "SELL" and price <= p["tp"]:
                close_position(s)
        time.sleep(1)

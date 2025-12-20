import threading
import time
import importlib

from config import SYMBOLS, STRATEGY, BOT_ON
from core.trader import open_position
from telegram.commands import listener
from core.manager import manager

strategy = importlib.import_module(f"strategies.{STRATEGY}")

threading.Thread(target=listener, daemon=True).start()
threading.Thread(target=manager, daemon=True).start()

while True:
    if BOT_ON:
        for s in SYMBOLS:
            signal = strategy.check_signal(s)
            if signal:
                open_position(s, signal)
            time.sleep(1)
    time.sleep(5)

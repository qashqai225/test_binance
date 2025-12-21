import time
import requests
from code import show_positions, show_stats
from config import TG_TOKEN
from core.state import positions
from core.trader import close_position
from core.state import stats
from telegram.bot import tg
import config

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

                elif text == "/stats":
                    show_stats() 

                elif text.startswith("/close"):
                    sym = text.split()[1].upper()
                    close_position(sym, manual=True)
        except:
            pass
        time.sleep(2)

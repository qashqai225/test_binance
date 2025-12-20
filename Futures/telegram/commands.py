import time
import requests
from code import show_positions
from config import TG_TOKEN
from core.state import positions
from core.trader import close_position
from core.state import stats
from telegram.bot import tg
import config

def listener():
    offset = 0
    while True:
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates?offset={offset}"
            ).json()

            for u in r.get("result", []):
                offset = u["update_id"] + 1
                text = u.get("message", {}).get("text")
                if not text:
                    continue
                cmd = text.strip().lower()

                if cmd == "/start":
                    BOT_ON = True
                    tg("✅ Бот запущен")
                elif cmd == "/stop":
                    BOT_ON = False
                    tg("⛔ Бот остановлен")
                elif cmd == "/status":
                    tg(f"📊 Статус: {'ON' if BOT_ON else 'OFF'}")
                elif cmd == "/stats":
                    tg(str(stats))
                elif cmd == "/positions":
                    show_positions()
        except:
            pass

        time.sleep(2)

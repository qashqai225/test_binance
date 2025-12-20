from binance.client import Client

API_KEY = "41bJFweA1m3Mp9UOTXMr82kQeFCSGu2AtYweii1Rn9CacNTeHor3tPzZfOa1Ty7q"
API_SECRET = "JTNiSXaKeBvcl3oFDN8GgP6rR2KgCfIhW8f1ByEAU4EsD2Ijid1dAn8b9wBotHS6"

TG_TOKEN = "8554034676:AAEIEPOwkWYFz9_dpDla2jfu-t5EDRpSygE"
CHAT_ID = "5540625088"
# ================== CONFIG ==================
SYMBOLS = ["AAVEUSDT","LTCUSDT","INJUSDT","XRPUSDT","ADAUSDT","HBARUSDT"]
INTERVAL = Client.KLINE_INTERVAL_5MINUTE
# ================== CONFIG ==================
LEVERAGE = 20
RISK_PER_TRADE = 10
TP_ROI = 0.10

BOT_ON = True
# Define your trading strategy here
STRATEGY = "ema_rsi_volume"

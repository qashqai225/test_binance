import pandas as pd
from core.client import client
from config import INTERVAL

def get_klines(symbol):
    df = pd.DataFrame(
        client.futures_klines(symbol=symbol, interval=INTERVAL, limit=100),
        columns=["t","o","h","l","c","v","x","q","n","T","Q","i"]
    )
    return df.astype(float)

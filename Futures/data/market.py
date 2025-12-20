import pandas as pd
from core.client import client
from config import INTERVAL

def get_klines(symbol, limit=100):
    df = pd.DataFrame(
        client.futures_klines(symbol=symbol, interval=INTERVAL, limit=limit),
        columns=["t","o","h","l","c","v","x","q","n","T","Q","i"]
    )
    return df.astype(float)

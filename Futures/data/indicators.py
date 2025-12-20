def add_ema(df, fast=9, slow=21):
    df["ema_fast"] = df["c"].ewm(span=fast).mean()
    df["ema_slow"] = df["c"].ewm(span=slow).mean()
    return df

def add_rsi(df, period=7):
    delta = df["c"].diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = -delta.clip(upper=0).rolling(period).mean()
    df["rsi"] = 100 - (100 / (1 + gain / loss))
    return df

def add_volume_ma(df, period=20):
    df["vol_ma"] = df["v"].rolling(period).mean()
    return df

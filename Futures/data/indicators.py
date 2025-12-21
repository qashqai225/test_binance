def indicators(df):
    df["ema9"] = df["c"].ewm(span=9).mean()
    df["ema21"] = df["c"].ewm(span=21).mean()
    delta = df["c"].diff()
    gain = delta.clip(lower=0).rolling(7).mean()
    loss = -delta.clip(upper=0).rolling(7).mean()
    df["rsi"] = 100 - (100 / (1 + gain / loss))
    df["vol_ma"] = df["v"].rolling(20).mean()
    return df

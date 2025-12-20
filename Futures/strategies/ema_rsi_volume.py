from data.market import get_klines
from data.indicators import add_ema, add_rsi, add_volume_ma

def check_signal(symbol):
    df = get_klines(symbol)
    df = add_ema(df)
    df = add_rsi(df)
    df = add_volume_ma(df)

    last = df.iloc[-1]

    long = (
        last.ema_fast > last.ema_slow and
        last.rsi > 50 and
        last.v > last.vol_ma
    )

    short = (
        last.ema_fast < last.ema_slow and
        last.rsi < 50 and
        last.v > last.vol_ma
    )

    if long:
        return "BUY"
    if short:
        return "SELL"
    return None

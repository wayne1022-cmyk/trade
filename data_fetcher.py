"""
data_fetcher.py - Twelve Data 報價模組（含技術指標）
"""

import logging
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

import config

logger = logging.getLogger(__name__)

TWELVEDATA_BASE_URL = "https://api.twelvedata.com/time_series"

# 技術指標參數
EMA_SHORT = 9
EMA_LONG = 21
RSI_PERIOD = 14
ATR_PERIOD = 14


def _calculate_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _calculate_rsi(series: pd.Series, period: int) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, adjust=False).mean()
    avg_loss = loss.ewm(com=period - 1, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _calculate_atr(df: pd.DataFrame, period: int) -> pd.Series:
    high_low = df["high"] - df["low"]
    high_close = (df["high"] - df["close"].shift()).abs()
    low_close = (df["low"] - df["close"].shift()).abs()
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return true_range.ewm(com=period - 1, adjust=False).mean()


def _calculate_kd(df: pd.DataFrame, n: int = 9, k_smooth: int = 3, d_smooth: int = 3) -> pd.DataFrame:
    low_n = df['low'].rolling(window=n).min()
    high_n = df['high'].rolling(window=n).max()
    rsv = (df['close'] - low_n) / (high_n - low_n) * 100
    df['%K'] = rsv.rolling(window=k_smooth).mean()
    df['%D'] = df['%K'].rolling(window=d_smooth).mean()
    return df


def _calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df[f"ema_{EMA_SHORT}"] = _calculate_ema(df["close"], EMA_SHORT)
    df[f"ema_{EMA_LONG}"] = _calculate_ema(df["close"], EMA_LONG)
    df["rsi"] = _calculate_rsi(df["close"], RSI_PERIOD)
    df["atr"] = _calculate_atr(df, ATR_PERIOD)
    df = _calculate_kd(df, n=9, k_smooth=3, d_smooth=3)
    return df.dropna()


def fetch_usdjpy_klines(lookback_hours: int = config.KLINE_LOOKBACK_HOURS) -> "pd.DataFrame | None":
    interval = config.KLINE_INTERVAL
    # 計算所需 K 線數量（回溯期 + 技術指標暖機）
    interval_minutes = 5  # 因為固定使用 5min
    required_bars = (lookback_hours * 60 // interval_minutes) + 50
    outputsize = min(required_bars, 5000)  # Twelve Data 免費版一次最多 5000 筆

    params = {
        "symbol": "USD/JPY",
        "interval": interval,
        "outputsize": outputsize,
        "apikey": config.TWELVEDATA_KEY,
        "format": "JSON",
    }

    logger.info("📡 開始從 Twelve Data 取得 USDJPY K 線（interval=%s, outputsize=%d）", interval, outputsize)

    try:
        resp = requests.get(TWELVEDATA_BASE_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        if data.get("status") == "error":
            logger.error("Twelve Data 錯誤：%s", data.get("message"))
            return None

        values = data.get("values", [])
        if not values:
            logger.warning("Twelve Data 回傳空資料")
            return None

        records = []
        for v in values:
            dt = datetime.strptime(v["datetime"], "%Y-%m-%d %H:%M:%S")
            dt = dt.replace(tzinfo=timezone.utc)
            records.append({
                "timestamp": dt,
                "open": float(v["open"]),
                "high": float(v["high"]),
                "low": float(v["low"]),
                "close": float(v["close"]),
                "volume": int(v.get("volume", 0)),
            })

        df = pd.DataFrame(records)
        df = df.sort_values("timestamp").reset_index(drop=True)

        cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
        df = df[df["timestamp"] >= cutoff].reset_index(drop=True)

        if df.empty:
            logger.warning("過濾後無資料")
            return None

        df = _calculate_indicators(df)
        if df.empty:
            logger.error("技術指標計算後資料為空")
            return None

        latest = df.iloc[-1]
        logger.info("✅ 成功取得 %d 筆資料", len(df))
        logger.info("📈 收盤：%.3f | EMA9：%.3f | EMA21：%.3f | RSI：%.1f | ATR：%.3f | %%K：%.1f | %%D：%.1f",
                    latest["close"], latest[f"ema_{EMA_SHORT}"], latest[f"ema_{EMA_LONG}"],
                    latest["rsi"], latest["atr"], latest["%K"], latest["%D"])
        return df

    except Exception as e:
        logger.error("取得 K 線失敗：%s", e, exc_info=True)
        return None


if __name__ == "__main__":
    config.validate_config()
    df = fetch_usdjpy_klines()
    if df is not None:
        print(df[["close", "ema_9", "ema_21", "rsi", "atr", "%K", "%D"]].tail())
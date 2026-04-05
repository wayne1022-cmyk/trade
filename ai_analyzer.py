import json
import logging
import time
from groq import Groq
import pandas as pd

import config
from data_fetcher import ATR_PERIOD, EMA_LONG, EMA_SHORT, RSI_PERIOD

logger = logging.getLogger(__name__)




def _build_prompt(df: pd.DataFrame, news: list) -> str:
    """
    將技術指標資料與新聞整合成結構化 Prompt。
    """
    # ── 技術指標摘要 ──────────────────────────────────────────────
    latest       = df.iloc[-1]
    open_price   = df["open"].iloc[0]
    latest_close = latest["close"]
    price_change = latest_close - open_price
    candle_count = len(df)

    ema_short = latest[f"ema_{EMA_SHORT}"]
    ema_long  = latest[f"ema_{EMA_LONG}"]
    ema_trend = "Bullish alignment (EMA9 > EMA21)" if ema_short > ema_long else "Bearish alignment (EMA9 < EMA21)"

    rsi = latest["rsi"]
    if rsi >= 70:
        rsi_signal = f"{rsi:.1f} - Overbought, watch for reversal"
    elif rsi <= 30:
        rsi_signal = f"{rsi:.1f} - Oversold, watch for bounce"
    else:
        rsi_signal = f"{rsi:.1f} - Neutral zone"

    atr = latest["atr"]

    # 獲取最新的 KD 值
    latest_k = df['%K'].iloc[-1] if '%K' in df else None
    latest_d = df['%D'].iloc[-1] if '%D' in df else None
    kd_signal = ""
    if latest_k and latest_d:
        if latest_k > 80 and latest_d > 80:
            kd_signal = "Overbought zone, possible pullback"
        elif latest_k < 20 and latest_d < 20:
            kd_signal = "Oversold zone, possible bounce"
        elif latest_k > latest_d:
            kd_signal = "Bullish crossover (%K > %D)"
        else:
            kd_signal = "Bearish crossover (%K < %D)"

    kline_summary = f"""
## USDJPY Technical Analysis ({candle_count} minute candles)
- Period open : {open_price:.3f}
- Latest close: {latest_close:.3f}
- Price change : {price_change:+.3f}

### EMA Trend
- EMA{EMA_SHORT} (short-term): {ema_short:.3f}
- EMA{EMA_LONG}  (long-term) : {ema_long:.3f}
- Trend : {ema_trend}

### RSI Momentum (period={RSI_PERIOD})
- RSI: {rsi_signal}

### ATR Volatility (period={ATR_PERIOD})
- ATR: {atr:.3f} (average pip movement per candle)

### Last 5 closing prices
- {[round(p, 3) for p in df['close'].tail(5).tolist()]}

### KD Indicator (Stochastic)
- %K: {latest_k:.1f} | %D: {latest_d:.1f}
- Signal: {kd_signal}

"""

    # ── 新聞摘要 ──────────────────────────────────────────────────
    if news:
        news_lines = []
        for i, article in enumerate(news[:8], 1):
            news_lines.append(
                f"[{i}] (relevance={article['score']}) {article['published_at']}\n"
                f"    Headline: {article['title']}\n"
                f"    Summary : {article['summary'][:200]}"
            )
        news_summary = "## Recent USDJPY Related News\n" + "\n".join(news_lines)
    else:
        news_summary = "## Recent USDJPY Related News\nNo relevant news available."

    # ── 完整 Prompt ───────────────────────────────────────────────
    prompt = f"""
You are a professional FX quantitative trading analyst specializing in USDJPY macroeconomic analysis.

Based on the data below, analyze the USDJPY directional bias from a macroeconomic perspective.
Pay close attention to the following core factors:
1. Federal Reserve (Fed) monetary policy and rate hike/cut expectations
2. Bank of Japan (BOJ) monetary policy, including YCC adjustments and rate hike timeline
3. US-Japan interest rate differential trends (widening or narrowing)
4. US inflation data (CPI/PCE) and employment figures (Non-Farm Payrolls)
5. Japan inflation and GDP data
6. Global risk sentiment (Risk-on / Risk-off) and JPY safe-haven demand
7. Technical signals from EMA9/EMA21 crossovers as trend direction filter and Use RSI and KD for overbought/oversold conditions.

---
{kline_summary}
---
{news_summary}
---

Output ONLY a valid JSON object with NO additional text, explanation, or markdown formatting:

{{
  "direction": "BUY or SELL or HOLD",
  "confidence": integer between 0 and 100,
  "stop_loss": float rounded to 3 decimal places,
  "take_profit": float rounded to 3 decimal places,
  "position_size": float between 0.1 and 1.0,
  "macro_analysis": "Macro and technical analysis summary in English, within 100 words",
  "key_factors": ["key factor 1", "key factor 2", "key factor 3"],
  "risk_level": "LOW or MEDIUM or HIGH"
}}

Stop loss / take profit rules:
- BUY  : stop_loss = latest_close - (ATR * 2), take_profit = latest_close + (ATR * 4)
- SELL : stop_loss = latest_close + (ATR * 2), take_profit = latest_close - (ATR * 4)
- HOLD : stop_loss = latest_close, take_profit = latest_close
- If risk_level is HIGH, position_size must not exceed 0.3

Current ATR value: {atr:.3f}
Current latest close: {latest_close:.3f}
"""
    return prompt


def _validate_signal(signal: dict, latest_close: float) -> bool:
    """
    驗證 Gemini 回傳的交易訊號是否符合格式與邏輯要求。
    """
    required_keys = [
        "direction", "confidence", "stop_loss", "take_profit",
        "position_size", "macro_analysis", "key_factors", "risk_level"
    ]

    for key in required_keys:
        if key not in signal:
            logger.error("❌ 訊號缺少必要欄位：%s", key)
            return False

    if signal["direction"] not in ["BUY", "SELL", "HOLD"]:
        logger.error("❌ 無效的交易方向：%s", signal["direction"])
        return False

    if not (0 <= int(signal["confidence"]) <= 100):
        logger.error("❌ 信心度超出範圍：%s", signal["confidence"])
        return False

    if not (0.1 <= float(signal["position_size"]) <= 1.0):
        logger.error("❌ 部位大小超出範圍：%s", signal["position_size"])
        return False

    direction = signal["direction"]
    sl = float(signal["stop_loss"])
    tp = float(signal["take_profit"])

    if direction == "BUY" and not (sl < latest_close < tp):
        logger.error("❌ BUY 停損停利邏輯錯誤：SL=%s Close=%s TP=%s", sl, latest_close, tp)
        return False

    if direction == "SELL" and not (tp < latest_close < sl):
        logger.error("❌ SELL 停損停利邏輯錯誤：TP=%s Close=%s SL=%s", tp, latest_close, sl)
        return False

    return True


import requests

def analyze_and_generate_signal(df, news, max_retries=3):
    prompt = _build_prompt(df, news)
    
    client = Groq(api_key=config.Groq_API_KEY) # 使用 config 裡的 API Key
    latest_close = df["close"].iloc[-1]

    for attempt in range(max_retries):
        try:
            chat_completion = client.chat.completions.create(
                messages=[{"role": "user", "content": prompt}],
                model="llama-3.3-70b-versatile", # 推薦的模型
                temperature=0.2,
                max_tokens=4096,
                response_format={"type": "json_object"} # 要求 JSON 格式輸出
            )
            raw = chat_completion.choices[0].message.content
            signal = json.loads(raw.strip())
            if _validate_signal(signal, latest_close):
                return signal
        except Exception as e:
            logger.warning(f"Groq API 失敗，重試中 ({attempt+1}/{max_retries}): {e}")
            time.sleep(3)
    return None

# ── 模組直接執行時進行自我測試 ───────────────────────────────
if __name__ == "__main__":
    config.validate_config()

    from data_fetcher import fetch_usdjpy_klines
    from news_fetcher import fetch_usdjpy_news

    logger.info("📡 取得測試用 K 線與技術指標資料...")
    df = fetch_usdjpy_klines()

    logger.info("📰 取得測試用新聞資料...")
    news = fetch_usdjpy_news(lookback_hours=72)

    if df is not None:
        signal = analyze_and_generate_signal(df, news or [])

        if signal:
            print("\n===== Gemini 交易訊號 =====")
            print(json.dumps(signal, ensure_ascii=False, indent=2))
    else:
        logger.error("❌ 無法取得資料，測試中止")
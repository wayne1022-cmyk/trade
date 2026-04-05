"""
news_fetcher.py - Finnhub 新聞模組
負責：
  1. 呼叫 Finnhub API 取得過去 N 小時外匯新聞
  2. 針對 USDJPY 相關關鍵字進行過濾與評分
  3. 回傳整理好的新聞列表
"""

import logging
from datetime import datetime, timedelta, timezone

import requests

import config

logger = logging.getLogger(__name__)

FINNHUB_BASE_URL = "https://finnhub.io/api/v1/news"

# ── USDJPY 相關關鍵字（用於過濾與優先排序）────────────────────
# 核心關鍵字：直接與 USDJPY 相關
KEYWORDS_HIGH = [
    "usdjpy", "usd/jpy", "dollar yen", "yen", "jpy",
    "bank of japan", "boj", "日銀", "日本銀行",
    "kuroda", "ueda", "kazuo ueda",
    "japan interest rate", "japan inflation", "japan gdp",
    "yield curve control", "ycc",
]

# 次要關鍵字：總經面，影響美元走向
KEYWORDS_MEDIUM = [
    "federal reserve", "fed", "fomc",
    "jerome powell", "powell",
    "interest rate", "rate hike", "rate cut",
    "inflation", "cpi", "pce",
    "nonfarm payroll", "unemployment", "jobs report",
    "us dollar", "usd", "dxy",
    "treasury yield", "bond yield",
    "tariff", "trade war", "risk off", "risk on",
]


def _score_article(title: str, summary: str) -> int:
    """
    根據標題與摘要中的關鍵字，對新聞進行相關性評分。

    Returns:
        score: 高關鍵字每個 +2，次要關鍵字每個 +1，0 分代表完全不相關
    """
    text = (title + " " + summary).lower()
    score = 0
    score += sum(2 for kw in KEYWORDS_HIGH   if kw.lower() in text)
    score += sum(1 for kw in KEYWORDS_MEDIUM if kw.lower() in text)
    return score


def fetch_usdjpy_news(lookback_hours: int = config.KLINE_LOOKBACK_HOURS) -> "list | None":
    """
    從 Finnhub 取得外匯新聞，並過濾出 USDJPY 相關文章。

    Args:
        lookback_hours: 回溯小時數，預設從 config 讀取（4 小時）

    Returns:
        過濾後的新聞列表（依相關性評分由高到低排序），每則包含：
        - title      : 標題
        - summary    : 摘要
        - source     : 來源
        - published_at: 發布時間（UTC ISO 格式字串）
        - url        : 原文連結
        - score      : 相關性評分
        若發生錯誤則回傳 None
    """
    logger.info("📰 開始從 Finnhub 取得過去 %d 小時 USDJPY 相關新聞...", lookback_hours)

    now        = datetime.now(timezone.utc)
    cutoff     = now - timedelta(hours=lookback_hours)
    now_ts     = int(now.timestamp())
    cutoff_ts  = int(cutoff.timestamp())

    params = {
        "category": "forex",
        "minId": 0,
        "token": config.FINNHUB_KEY,
    }

    try:
        response = requests.get(FINNHUB_BASE_URL, params=params, timeout=30)
        response.raise_for_status()

        articles = response.json()

        if not isinstance(articles, list):
            logger.error("❌ Finnhub 回傳格式非預期：%s", type(articles))
            return None

        logger.info("📥 Finnhub 共回傳 %d 則新聞，開始過濾...", len(articles))

        # ── 時間過濾 + 關鍵字評分 ─────────────────────────────────
        filtered = []
        for article in articles:
            pub_ts = article.get("datetime", 0)

            # 只保留回溯時間內的文章
            if not (cutoff_ts <= pub_ts <= now_ts):
                continue

            title   = article.get("headline", "")
            summary = article.get("summary",  "")
            score   = _score_article(title, summary)

            # 只保留有相關性的文章（score > 0）
            if score == 0:
                continue

            filtered.append({
                "title"       : title,
                "summary"     : summary,
                "source"      : article.get("source", ""),
                "published_at": datetime.fromtimestamp(pub_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                "url"         : article.get("url", ""),
                "score"       : score,
            })

        # 依相關性評分由高到低排序
        filtered.sort(key=lambda x: x["score"], reverse=True)

        if not filtered:
            logger.warning("⚠️ 過去 %d 小時內無 USDJPY 相關新聞（可能市場休市或新聞較少）", lookback_hours)
        else:
            logger.info("✅ 過濾後取得 %d 則 USDJPY 相關新聞", len(filtered))
            for i, article in enumerate(filtered[:3], 1):
                logger.info("  [%d] (score=%d) %s", i, article["score"], article["title"])

        return filtered

    except requests.exceptions.Timeout:
        logger.error("❌ Finnhub API 請求逾時（超過 30 秒）")
        return None
    except requests.exceptions.ConnectionError:
        logger.error("❌ 無法連線至 Finnhub，請檢查網路連線")
        return None
    except requests.exceptions.HTTPError as e:
        logger.error("❌ Finnhub HTTP 錯誤：%s", e)
        return None
    except Exception as e:
        logger.error("❌ 取得新聞時發生未預期錯誤：%s", e, exc_info=True)
        return None


# ── 模組直接執行時進行自我測試 ───────────────────────────────
if __name__ == "__main__":
    config.validate_config()

    # 今天休市，用 72 小時確保能抓到新聞
    news = fetch_usdjpy_news(lookback_hours=72)

    if news:
        print(f"\n===== USDJPY 相關新聞（共 {len(news)} 則）=====")
        for i, article in enumerate(news, 1):
            print(f"\n[{i}] score={article['score']} | {article['published_at']}")
            print(f"    標題：{article['title']}")
            print(f"    來源：{article['source']}")
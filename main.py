"""
main.py - 主程式整合模組
負責：
  1. 串接所有模組，完成完整自動交易流程
  2. 符合 Google Cloud Functions (GCF) 部署格式
  3. 同時支援本機直接執行
"""

import json
import logging

#import functions_framework

import config
from ai_analyzer import analyze_and_generate_signal
from data_fetcher import fetch_usdjpy_klines
from ig_trader import IGTrader
from trade_logger import save_trade_record
from news_fetcher import fetch_usdjpy_news

logger = logging.getLogger(__name__)


def run_trading_bot() -> dict:
    """
    執行完整交易流程：
    資料取得 → AI 分析 → 交易執行

    Returns:
        執行結果摘要 dict
    """
    logger.info("=" * 60)
    logger.info("🚀 USDJPY 自動交易機器人啟動")
    logger.info("=" * 60)

    result = {
        "status":    "started",
        "signal":    None,
        "order":     None,
        "error":     None,
    }

    # ── Step 1：取得 K 線資料 ─────────────────────────────────
    logger.info("【Step 1】取得 USDJPY K 線資料")
    df = fetch_usdjpy_klines()

    if df is None:
        msg = "K 線資料取得失敗，流程中止"
        logger.error("❌ %s", msg)
        result["status"] = "failed"
        result["error"]  = msg
        return result

    # ── Step 2：取得新聞資料 ──────────────────────────────────
    logger.info("【Step 2】取得 USDJPY 相關新聞")
    news = fetch_usdjpy_news()

    if news is None:
        logger.warning("⚠️ 新聞取得失敗，將以空新聞列表繼續執行")
        news = []

    # ── Step 3：AI 分析產生交易訊號 ───────────────────────────
    logger.info("【Step 3】Gemini AI 總經分析")
    signal = analyze_and_generate_signal(df, news)

    if signal is None:
        msg = "AI 訊號產生失敗，流程中止"
        logger.error("❌ %s", msg)
        result["status"] = "failed"
        result["error"]  = msg
        return result

    result["signal"] = signal
    logger.info("📊 AI 訊號：方向=%s | 信心度=%d%% | 風險=%s",
                signal["direction"], signal["confidence"], signal["risk_level"])

    # ── Step 4：IG 交易執行 ───────────────────────────────────
    logger.info("【Step 4】IG Trading 交易執行")
    trader = IGTrader()

    try:
        if not trader.login():
            msg = "IG 登入失敗，流程中止"
            logger.error("❌ %s", msg)
            result["status"] = "failed"
            result["error"]  = msg
            return result

        # 查詢帳戶餘額（確認帳戶正常）
        balance = trader.get_account_balance()
        if balance is None:
            logger.warning("⚠️ 無法取得帳戶餘額，仍繼續嘗試下單")

        # 執行下單
        order_result = trader.place_order(signal)

        if order_result is None:
            result["status"] = "order_failed"
            result["error"]  = "下單模組回傳 None"
            logger.error("❌ 下單模組異常")

        else:
            reason    = order_result.get("reason", "")
            executed  = order_result.get("executed", False)
            result["order"] = order_result

            if executed:
                result["status"] = "success"
                logger.info("✅ 交易執行成功")
            elif reason == "HOLD":
                result["status"] = "hold"
                logger.info("⏸️  本次決策為 HOLD，無交易執行")
            elif reason == "ALREADY_IN_POSITION":
                result["status"] = "skipped"
                logger.info("⏸️  已有持倉，跳過本次下單")
            elif reason == "LOW_CONFIDENCE":
                result["status"] = "skipped"
                logger.info("⏸️  信心度不足，跳過本次下單")
            elif reason.startswith("DAILY_RISK"):
                result["status"] = "risk_limit"
                result["error"]  = reason
                logger.error("🛑 每日風控觸發：%s", reason)
            elif reason.startswith("REJECTED"):
                result["status"] = "rejected"
                result["error"]  = reason
                logger.error("❌ 下單被 IG 拒絕：%s", reason)
            else:
                result["status"] = "order_failed"
                result["error"]  = reason
                logger.error("❌ 下單失敗：%s", reason)

    finally:
        # 無論成功或失敗，都確保登出
        trader.logout()

    # ── 流程結束 ──────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("🏁 交易流程結束，狀態：%s", result["status"])
    logger.info("=" * 60)

    # ── 儲存交易記錄至 GCS ────────────────────────────────────
    save_trade_record(
        signal       = result.get("signal"),
        order_result = result.get("order"),
        status       = result["status"],
    )

    return result


# ── GCF 入口點（Cloud Scheduler 透過 HTTP 觸發）────────────────
#@functions_framework.http
def gcf_entry_point(request):
    """
    Google Cloud Functions HTTP 觸發入口。
    Cloud Scheduler 每小時呼叫此函式。
    """
    try:
        config.validate_config()
        result = run_trading_bot()
        return (json.dumps(result, ensure_ascii=False), 200,
                {"Content-Type": "application/json"})

    except RuntimeError as e:
        # config 驗證失敗
        logger.error("❌ Config 驗證失敗：%s", e)
        return (json.dumps({"status": "config_error", "error": str(e)}),
                500, {"Content-Type": "application/json"})

    except Exception as e:
        logger.error("❌ 未預期錯誤：%s", e, exc_info=True)
        return (json.dumps({"status": "error", "error": str(e)}),
                500, {"Content-Type": "application/json"})


# ── 本機直接執行 ──────────────────────────────────────────────
if __name__ == "__main__":
    config.validate_config()
    result = run_trading_bot()

    print("\n===== 執行結果摘要 =====")
    print(json.dumps(result, ensure_ascii=False, indent=2))
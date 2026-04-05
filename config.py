"""
config.py - 系統設定中心
負責：
  1. 從 .env 讀取所有環境變數
  2. 宣告全域設定常數
  3. 啟動時驗證關鍵 API Key 是否存在
  4. 初始化全域 logging 設定
"""

import logging
import os
import sys

from dotenv import load_dotenv

# ── 載入 .env 檔案 ────────────────────────────────────────────
load_dotenv()


# ── Logging 初始化 ────────────────────────────────────────────
def _setup_logging() -> logging.Logger:
    """設定統一的 logging 格式，層級為 INFO。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),  # 輸出至終端（GCF 會自動捕捉 stdout）
        ],
    )
    return logging.getLogger(__name__)


logger = _setup_logging()


# ── API 金鑰讀取 ──────────────────────────────────────────────

ALPHA_VANTAGE_KEY: str = os.getenv("ALPHA_VANTAGE_KEY", "")
FINNHUB_KEY: str = os.getenv("FINNHUB_KEY", "")
GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")   # 大写       # 取代 GEMINI_API_KEY
IG_API_KEY: str = os.getenv("IG_API_KEY", "")
IG_IDENTIFIER: str = os.getenv("IG_IDENTIFIER", "")
IG_PASSWORD: str = os.getenv("IG_PASSWORD", "")
IG_API_URL: str = os.getenv("IG_API_URL", "https://demo-api.ig.com/gateway/deal")
IG_EPIC: str = os.getenv("IG_EPIC", "")
TWELVEDATA_KEY: str = os.getenv("TWELVEDATA_KEY", "")
IG_USERNAME: str = os.getenv("IG_USERNAME", "")


# ── 交易參數常數 ──────────────────────────────────────────────

TRADING_SYMBOL: str = "USDJPY"
KLINE_INTERVAL: str = "5min"          # Alpha Vantage 支援 5min, 15min, 30min, 60min
KLINE_LOOKBACK_HOURS: int = 4         # 回溯 4 小時 → 48 根 K 線（5min）
# 風控參數
MAX_DAILY_LOSS_PCT: float = 0.05   # 每日最大虧損比例（帳戶淨值 5%）
MAX_DAILY_TRADES: int = 6          # 每日最大交易次數

KD_N = 9
KD_K_SMOOTH = 3
KD_D_SMOOTH = 3
# ── 啟動驗證 ──────────────────────────────────────────────────

_REQUIRED_KEYS = {
    "TWELVEDATA_KEY": TWELVEDATA_KEY,
    "FINNHUB_KEY": FINNHUB_KEY,
    "Groq_API_KEY": Groq_API_KEY,
    "IG_API_KEY": IG_API_KEY,
    "IG_IDENTIFIER": IG_IDENTIFIER,
    "IG_PASSWORD": IG_PASSWORD,
    "IG_USERNAME": IG_USERNAME
    #"IG_EPIC": IG_EPIC,
}

def validate_config() -> None:
    """
    驗證所有必要的環境變數是否已設定。
    若有缺漏，記錄 ERROR 並終止程式（Raise RuntimeError）。
    此函式應在主程式進入點最早期呼叫。
    """
    missing_keys = [key for key, value in _REQUIRED_KEYS.items() if not value]

    if missing_keys:
        logger.error(
            "❌ 缺少以下必要的環境變數，請檢查 .env 檔案：%s",
            ", ".join(missing_keys),
        )
        raise RuntimeError(
            f"Config 驗證失敗，缺少環境變數：{', '.join(missing_keys)}"
        )

    logger.info("✅ Config 驗證通過，所有必要的 API Key 已載入。")
    logger.info("📌 交易標的：%s | K 線間隔：%s | 回溯：%d 小時",
                TRADING_SYMBOL, KLINE_INTERVAL, KLINE_LOOKBACK_HOURS)
    logger.info("📌 IG API 端點：%s", IG_API_URL)


# ── 模組直接執行時進行自我測試 ───────────────────────────────
if __name__ == "__main__":
    validate_config()

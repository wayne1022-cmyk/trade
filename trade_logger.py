"""
trade_logger.py - 交易記錄儲存模組 (Git 倉庫 TXT 版本)
負責：
  1. 將每次交易結果寫入倉庫根目錄的 LOG_FILE.txt
  2. 以 JSON 格式儲存，每筆記錄為一行
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import config

logger = logging.getLogger(__name__)

# 日誌檔案路徑：專案根目錄下的 LOG_FILE.txt
LOG_FILE = Path(__file__).parent / "LOG_FILE.txt"


def save_trade_record(signal: dict | None, order_result: dict | None, status: str) -> bool:
    """
    將交易記錄追加至 LOG_FILE.txt（一行 JSON）。
    """
    try:
        now = datetime.now(timezone.utc)
        timestamp = now.strftime("%Y-%m-%d %H:%M:%S UTC")

        record = {
            "timestamp": timestamp,
            "status": status,
            "signal": signal,
            "order_result": order_result,
        }

        # 追加寫入（檔案不存在會自動建立）
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        logger.info("💾 交易記錄已追加至 %s", LOG_FILE)
        return True

    except Exception as e:
        logger.error("❌ 交易記錄儲存失敗：%s", e, exc_info=True)
        return False


# 測試代碼不變...

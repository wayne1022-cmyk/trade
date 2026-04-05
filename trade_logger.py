"""
trade_logger.py - 交易記錄儲存模組 (本地 TXT 版本)
負責：
  1. 將每次交易結果寫入本地 TXT 檔案
  2. 以 JSON 格式儲存，每筆記錄為一行
  3. 檔案路徑：logs/trade_log.txt
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import config

logger = logging.getLogger(__name__)

# 本地日誌檔案路徑（相對於專案根目錄）
LOG_DIR = Path(__file__).parent / "logs"
LOG_FILE = LOG_DIR / "trade_log.txt"


def save_trade_record(signal: "dict | None", order_result: "dict | None", status: str) -> bool:
    """
    將交易記錄儲存至本地 TXT 檔案（每行一筆 JSON）。

    Args:
        signal      : AI 交易訊號
        order_result: IG 下單結果
        status      : 本次執行狀態

    Returns:
        True 若儲存成功，False 若失敗
    """
    try:
        # 確保日誌目錄存在
        LOG_DIR.mkdir(parents=True, exist_ok=True)

        now = datetime.now(timezone.utc)
        timestamp = now.strftime("%Y-%m-%d %H:%M:%S UTC")

        record = {
            "timestamp": timestamp,
            "status": status,
            "signal": signal,
            "order_result": order_result,
        }

        # 以追加模式寫入檔案（一行一筆 JSON）
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        logger.info("💾 交易記錄已儲存至 %s", LOG_FILE)
        return True

    except Exception as e:
        logger.error("❌ 交易記錄儲存失敗：%s", e, exc_info=True)
        return False


# ── 模組直接執行時進行自我測試 ───────────────────────────────
if __name__ == "__main__":
    config.validate_config()

    fake_record = {
        "direction": "BUY",
        "confidence": 75,
        "stop_loss": 159.500,
        "take_profit": 159.800,
        "position_size": 0.5,
        "macro_analysis": "Test record",
        "key_factors": ["test"],
        "risk_level": "MEDIUM",
    }

    success = save_trade_record(
        signal=fake_record,
        order_result={"executed": False, "reason": "TEST"},
        status="test",
    )

    if success:
        print("✅ 本地 TXT 儲存測試成功")
        print(f"記錄已寫入：{LOG_FILE}")
    else:
        print("❌ 本地 TXT 儲存測試失敗")
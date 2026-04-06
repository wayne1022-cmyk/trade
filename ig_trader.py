"""
ig_trader.py - IG Trading 交易執行模組（修正版：先平仓再开仓，固定 0.2 手，包含止损止盈）
"""

import logging
import time
import requests
import config

logger = logging.getLogger(__name__)

USDJPY_EPIC = "CS.D.USDJPY.MINI.IP"   # 迷你合约
TARGET_SIZE = 0.2                     # 固定仓位大小
MIN_DEAL_SIZE = 0.2
MAX_DEAL_SIZE = 0.2                   # 强制最大0.2，不允许更大
DEAL_SIZE_STEP = 0.01


class IGTrader:
    # ... （前面的 __init__, login, get_account_balance, get_daily_pnl, check_daily_risk 保持不变，略）

    # ==================== 持仓查询与平仓 ====================
    def get_open_positions(self) -> list | None:
        if not self.is_logged_in:
            return None
        try:
            url = f"{config.IG_API_URL}/positions"
            headers = {**dict(self.session.headers), "Version": "2"}
            resp = self.session.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
            return resp.json().get("positions", [])
        except Exception as e:
            logger.error("查詢持倉失敗：%s", e)
            return None

    def get_current_position(self):
        """返回 (方向, 手数) 若无持仓则 (None, 0)"""
        positions = self.get_open_positions()
        if not positions:
            return None, 0
        for pos in positions:
            epic = pos.get("market", {}).get("epic", "")
            if USDJPY_EPIC in epic:
                direction = pos.get("position", {}).get("direction")
                size = float(pos.get("position", {}).get("size", 0))
                return direction, size
        return None, 0

    def close_all_positions(self) -> bool:
        """平仓所有 USDJPY 持仓（使用 DELETE /positions/{epic}）"""
        url = f"{config.IG_API_URL}/positions/{USDJPY_EPIC}"
        headers = {**dict(self.session.headers), "Version": "1"}
        try:
            resp = self.session.delete(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                logger.info("✅ 平仓所有 USDJPY 持仓成功")
                return True
            else:
                logger.error("❌ 平仓失败：HTTP %d - %s", resp.status_code, resp.text)
                return False
        except Exception as e:
            logger.error("平仓异常：%s", e)
            return False

    # ==================== 开仓（带止损止盈） ====================
    def _open_position(self, direction: str, size: float, stop_loss: float = None, take_profit: float = None) -> dict:
        """开仓，确保 size 固定为 0.2，并添加 stop_loss / take_profit"""
        size = TARGET_SIZE  # 强制使用固定大小
        payload = {
            "epic": USDJPY_EPIC,
            "expiry": "-",
            "direction": direction,
            "size": f"{size:.2f}",
            "orderType": "MARKET",
            "timeInForce": "FILL_OR_KILL",
            "guaranteedStop": False,
            "forceOpen": True,
            "marketOrderPreference": "AVAILABLE",
            "currencyCode": "JPY",
            "accountId": self.account_id,
        }
        # 关键：添加止损止盈
        if stop_loss is not None:
            payload["stopLevel"] = round(stop_loss, 5)
            logger.info("设置止损价：%.5f", stop_loss)
        if take_profit is not None:
            payload["profitLevel"] = round(take_profit, 5)
            logger.info("设置止盈价：%.5f", take_profit)

        logger.info("📤 开仓 Payload: %s", payload)
        url = f"{config.IG_API_URL}/positions/otc"
        headers = {**dict(self.session.headers), "Version": "2"}
        try:
            resp = self.session.post(url, json=payload, headers=headers, timeout=30)
            if resp.status_code != 200:
                logger.error("HTTP %d: %s", resp.status_code, resp.text)
                return {"executed": False, "reason": f"HTTP_{resp.status_code}", "detail": resp.text}
            result = resp.json()
            deal_ref = result.get("dealReference")
            if not deal_ref:
                return {"executed": False, "reason": "NO_DEAL_REF", "detail": result}
            logger.info("✅ 下单请求送出，Deal Reference: %s", deal_ref)
            confirm = self._confirm_deal(deal_ref)
            if not confirm:
                return {"executed": False, "reason": "CONFIRM_FAILED"}
            status = confirm.get("dealStatus")
            reason = confirm.get("reason", "")
            if status == "ACCEPTED":
                logger.info("✅ 开仓成功！开仓价=%s", confirm.get("level"))
                return {"executed": True, "reason": "ACCEPTED", "detail": confirm}
            else:
                logger.error("❌ 开仓被拒绝：%s", reason)
                return {"executed": False, "reason": f"REJECTED:{reason}", "detail": confirm}
        except Exception as e:
            logger.error("开仓异常：%s", e)
            return {"executed": False, "reason": f"ERROR:{e}"}

    # ==================== 主要下单入口 ====================
    def place_order(self, signal: dict) -> dict | None:
        if not self.is_logged_in or not self.account_id:
            return {"executed": False, "reason": "NOT_LOGGED_IN"}

        direction = signal["direction"]
        confidence = signal.get("confidence", 0)
        stop_loss = signal.get("stop_loss")
        take_profit = signal.get("take_profit")

        if direction == "HOLD":
            logger.info("AI 建议 HOLD，不调整仓位")
            return {"executed": False, "reason": "HOLD", "detail": None}
        if confidence < 60:
            logger.warning("信心度过低（%d%%），不操作", confidence)
            return {"executed": False, "reason": "LOW_CONFIDENCE", "detail": None}

        can_trade, reason = self.check_daily_risk()
        if not can_trade:
            logger.error("每日风控触发：%s", reason)
            return {"executed": False, "reason": f"DAILY_RISK:{reason}", "detail": None}

        # 获取当前持仓
        curr_dir, curr_size = self.get_current_position()
        logger.info("当前持仓：方向=%s, 手数=%.2f", curr_dir if curr_dir else "无", curr_size)

        # 无持仓：直接开目标仓位
        if curr_dir is None:
            logger.info("无持仓，开仓 %.2f 手 %s", TARGET_SIZE, direction)
            return self._open_position(direction, TARGET_SIZE, stop_loss, take_profit)

        # 有持仓且方向相同：不操作
        if curr_dir == direction:
            logger.info("方向一致，不调整")
            return {"executed": False, "reason": "ALREADY_ALIGNED", "detail": None}

        # 有持仓且方向相反：先平仓，再开仓
        logger.info("方向相反（现有 %s，AI 建议 %s），先平仓再开新仓", curr_dir, direction)
        if not self.close_all_positions():
            logger.error("平仓失败，停止开新仓")
            return {"executed": False, "reason": "CLOSE_FAILED", "detail": None}

        # 平仓成功后，开新仓
        logger.info("平仓成功，开仓 %.2f 手 %s", TARGET_SIZE, direction)
        return self._open_position(direction, TARGET_SIZE, stop_loss, take_profit)

    def _confirm_deal(self, deal_reference: str, retries=3) -> dict | None:
        # ... 保持不变 ...
        for i in range(retries):
            try:
                time.sleep(1)
                url = f"{config.IG_API_URL}/confirms/{deal_reference}"
                headers = {**dict(self.session.headers), "Version": "1"}
                resp = self.session.get(url, headers=headers, timeout=30)
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                if i < retries - 1:
                    logger.warning("确认失败，重试 %d/3", i+2)
                else:
                    logger.error("确认失败：%s", e)
        return None

    def logout(self) -> None:
        # ... 保持不变 ...
        if not self.is_logged_in:
            return
        try:
            url = f"{config.IG_API_URL}/session"
            headers = {**dict(self.session.headers), "Version": "1"}
            self.session.delete(url, headers=headers, timeout=10)
            self.is_logged_in = False
            logger.info("👋 已登出")
        except Exception as e:
            logger.warning("登出错误：%s", e)


if __name__ == "__main__":
    config.validate_config()
    trader = IGTrader()
    if trader.login():
        trader.get_account_balance()
        fake_signal = {
            "direction": "BUY",
            "confidence": 75,
            "stop_loss": 159.00,
            "take_profit": 159.50,
            "risk_level": "MEDIUM",
        }
        result = trader.place_order(fake_signal)
        print("下单结果：", result)
        trader.logout()
    else:
        print("登录失败")

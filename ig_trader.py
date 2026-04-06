"""
ig_trader.py - 净头寸管理版（不平仓，只开仓）
"""

import logging
import time
import requests
import config

logger = logging.getLogger(__name__)

# 请根据实际持仓修改 EPIC（通过诊断确定）
USDJPY_EPIC = "CS.D.USDJPY.CFD.IP"   # 改为你实际使用的 EPIC
TARGET_NET_SIZE = 0.2                # 目标净头寸大小
MIN_DEAL_SIZE = 0.01
MAX_DEAL_SIZE = 1.0
DEAL_SIZE_STEP = 0.01


class IGTrader:
    # ... __init__, login, get_account_balance, get_daily_pnl, check_daily_risk 保持不变 ...

    def get_open_positions(self) -> list | None:
        # 同上，保持不变
        ...

    def get_net_position(self) -> float:
        """计算当前净头寸（多头手数 - 空头手数）"""
        positions = self.get_open_positions()
        net = 0.0
        for pos in positions:
            epic = pos.get("market", {}).get("epic", "")
            if USDJPY_EPIC in epic:   # 模糊匹配，避免后缀差异
                direction = pos.get("position", {}).get("direction")
                size = float(pos.get("position", {}).get("size", 0))
                if direction == "BUY":
                    net += size
                elif direction == "SELL":
                    net -= size
        logger.info("当前净头寸：%.3f", net)
        return net

    def _open_position(self, direction: str, size: float, stop_loss: float = None, take_profit: float = None) -> dict:
        # 确保 size 为步进倍数
        size = max(MIN_DEAL_SIZE, min(MAX_DEAL_SIZE, size))
        size = round(size / DEAL_SIZE_STEP) * DEAL_SIZE_STEP
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
        if stop_loss is not None:
            payload["stopLevel"] = round(stop_loss, 5)
        if take_profit is not None:
            payload["profitLevel"] = round(take_profit, 5)
        # ... 发送请求逻辑（与之前相同）...

    def place_order(self, signal: dict) -> dict | None:
        if not self.is_logged_in or not self.account_id:
            return {"executed": False, "reason": "NOT_LOGGED_IN"}

        direction = signal["direction"]
        confidence = signal.get("confidence", 0)
        stop_loss = signal.get("stop_loss")
        take_profit = signal.get("take_profit")

        if direction == "HOLD":
            return {"executed": False, "reason": "HOLD"}
        if confidence < 60:
            return {"executed": False, "reason": "LOW_CONFIDENCE"}

        can_trade, reason = self.check_daily_risk()
        if not can_trade:
            return {"executed": False, "reason": f"DAILY_RISK:{reason}"}

        # 计算需要调整的净头寸
        current_net = self.get_net_position()
        target_net = TARGET_NET_SIZE if direction == "BUY" else -TARGET_NET_SIZE
        delta = target_net - current_net   # 需要变化的净头寸

        if abs(delta) < 0.001:
            logger.info("净头寸已达目标 (%.2f)，无需操作", current_net)
            return {"executed": False, "reason": "ALREADY_ALIGNED"}

        # 确定开仓方向和大小
        if delta > 0:
            order_direction = "BUY"
            order_size = delta
        else:
            order_direction = "SELL"
            order_size = -delta

        # 限制单次开仓大小（避免过大）
        order_size = max(MIN_DEAL_SIZE, min(MAX_DEAL_SIZE, order_size))
        logger.info("目标净头寸：%.2f (%s)，当前净头寸：%.2f，需开 %.3f 手 %s",
                    target_net, direction, current_net, order_size, order_direction)
        return self._open_position(order_direction, order_size, stop_loss, take_profit)

    # ... _confirm_deal, logout 保持不变 ...

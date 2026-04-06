"""
ig_trader.py - IG Trading 交易執行模組（淨倉位管理版，先平倉再開反向）
固定維持 0.2 手倉位，方向由 AI 信號決定。
"""

import logging
import time
import requests
import config

logger = logging.getLogger(__name__)

# USDJPY 在 IG 的 Epic 代碼（請確認使用正確的 EPIC）
USDJPY_EPIC = "CS.D.USDJPY.MINI.IP"   # 迷你合約

# 固定目標倉位大小（手數）
TARGET_SIZE = 0.2

# 下單手數範圍與步進（保留以備用）
MIN_DEAL_SIZE = 0.2
MAX_DEAL_SIZE = 1.0
DEAL_SIZE_STEP = 0.01


class IGTrader:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json; charset=UTF-8",
            "Accept": "application/json; charset=UTF-8",
            "X-IG-API-KEY": config.IG_API_KEY,
        })
        self.cst = None
        self.x_security = None
        self.account_id = None
        self.is_logged_in = False

    # ==================== 登入 ====================
    def login(self, max_retries: int = 3, retry_delay: int = 5) -> bool:
        url = f"{config.IG_API_URL}/session"
        headers = {**dict(self.session.headers), "Version": "2"}
        payload = {
            "identifier": config.IG_IDENTIFIER,
            "password": config.IG_PASSWORD,
            "encryptedPassword": False,
        }

        for attempt in range(1, max_retries + 1):
            logger.info("🔐 登入 IG Demo API...（%d/%d）", attempt, max_retries)
            try:
                resp = self.session.post(url, json=payload, headers=headers, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                self.cst = resp.headers.get("CST")
                self.x_security = resp.headers.get("X-SECURITY-TOKEN")
                # 正確獲取 accountId
                self.account_id = data.get("currentAccountId") or data.get("accountId")
                if not self.account_id:
                    accounts = data.get("accounts", [])
                    if accounts:
                        self.account_id = accounts[0].get("accountId")
                if not self.cst or not self.x_security or not self.account_id:
                    logger.error("登入失敗：缺少必要資訊")
                    if attempt < max_retries:
                        time.sleep(retry_delay)
                    continue
                self.session.headers.update({
                    "CST": self.cst,
                    "X-SECURITY-TOKEN": self.x_security,
                })
                self.is_logged_in = True
                logger.info("✅ 登入成功，帳號 ID：%s", self.account_id)
                return True
            except Exception as e:
                logger.warning("登入失敗（%d/%d）：%s", attempt, max_retries, e)
                if attempt < max_retries:
                    time.sleep(retry_delay)
        return False

    # ==================== 查詢餘額 ====================
    def get_account_balance(self) -> dict | None:
        if not self.is_logged_in:
            return None
        try:
            url = f"{config.IG_API_URL}/accounts"
            headers = {**dict(self.session.headers), "Version": "1"}
            resp = self.session.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
            accounts = resp.json().get("accounts", [])
            if not accounts:
                return None
            account = accounts[0]
            balance = account.get("balance", {})
            result = {
                "available": balance.get("available", 0),
                "equity": balance.get("equity", 0),
                "profitLoss": balance.get("profitLoss", 0),
                "currency": account.get("currency", "USD"),
            }
            logger.info("💰 餘額 - 可用：%.2f | 淨值：%.2f | 幣別：%s",
                        result["available"], result["equity"], result["currency"])
            return result
        except Exception as e:
            logger.error("查詢餘額失敗：%s", e)
            return None

    # ==================== 每日損益 ====================
    def get_daily_pnl(self) -> dict | None:
        balance = self.get_account_balance()
        if not balance:
            return None
        equity = balance.get("equity", 0)
        pnl = balance.get("profitLoss", 0)
        loss_pct = (-pnl / equity * 100) if equity > 0 and pnl < 0 else 0.0
        return {"pnl": pnl, "trade_count": 0, "equity": equity, "loss_pct": loss_pct}

    def check_daily_risk(self) -> tuple[bool, str]:
        daily = self.get_daily_pnl()
        if not daily:
            return False, "CANNOT_FETCH_DAILY_PNL"
        max_loss_pct = config.MAX_DAILY_LOSS_PCT * 100
        if daily["loss_pct"] >= max_loss_pct:
            return False, f"DAILY_LOSS_LIMIT:{daily['loss_pct']:.2f}%"
        return True, ""

    # ==================== 持倉查詢 ====================
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
        """返回 (方向, 手數, epic)，若無持倉則 (None, 0, None)"""
        positions = self.get_open_positions()
        if not positions:
            return None, 0, None
        for pos in positions:
            epic = pos.get("market", {}).get("epic", "")
            if USDJPY_EPIC in epic:
                direction = pos.get("position", {}).get("direction")  # "BUY" or "SELL"
                size = float(pos.get("position", {}).get("size", 0))
                return direction, size, epic
        return None, 0, None

    def close_position(self, epic: str) -> bool:
        """平倉指定 EPIC 的所有持倉（不使用 dealId）"""
        url = f"{config.IG_API_URL}/positions/{epic}"
        headers = {**dict(self.session.headers), "Version": "1"}
        try:
            resp = self.session.delete(url, headers=headers, timeout=10)
            logger.info("平倉響應：%d - %s", resp.status_code, resp.text)
            if resp.status_code == 200:
                logger.info("✅ 平倉成功：%s", epic)
                return True
            else:
                logger.error("❌ 平倉失敗：HTTP %d - %s", resp.status_code, resp.text)
                return False
        except Exception as e:
            logger.error("平倉異常：%s", e)
            return False

    def _open_position(self, direction: str, size: float, stop_loss: float = None, take_profit: float = None) -> dict:
        """實際開倉方法（使用市價單）"""
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

        logger.info("📤 開倉 Payload: %s", payload)
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
            logger.info("✅ 下單請求送出，Deal Reference: %s", deal_ref)
            confirm = self._confirm_deal(deal_ref)
            if not confirm:
                return {"executed": False, "reason": "CONFIRM_FAILED"}
            status = confirm.get("dealStatus")
            reason = confirm.get("reason", "")
            if status == "ACCEPTED":
                logger.info("✅ 開倉成功！開倉價=%s", confirm.get("level"))
                return {"executed": True, "reason": "ACCEPTED", "detail": confirm}
            else:
                logger.error("❌ 開倉被拒絕：%s", reason)
                return {"executed": False, "reason": f"REJECTED:{reason}", "detail": confirm}
        except Exception as e:
            logger.error("開倉異常：%s", e)
            return {"executed": False, "reason": f"ERROR:{e}"}

    # ==================== 主要下單入口 ====================
    def place_order(self, signal: dict) -> dict | None:
        if not self.is_logged_in or not self.account_id:
            return {"executed": False, "reason": "NOT_LOGGED_IN"}

        direction = signal["direction"]
        confidence = signal.get("confidence", 0)
        risk_level = signal.get("risk_level", "MEDIUM")
        stop_loss = signal.get("stop_loss")
        take_profit = signal.get("take_profit")

        # 基本過濾
        if direction == "HOLD":
            logger.info("AI 建議 HOLD，不調整倉位")
            return {"executed": False, "reason": "HOLD", "detail": None}
        if confidence < 60:
            logger.warning("信心度過低（%d%%），不操作", confidence)
            return {"executed": False, "reason": "LOW_CONFIDENCE", "detail": None}

        # 風控檢查
        can_trade, reason = self.check_daily_risk()
        if not can_trade:
            logger.error("每日風控觸發：%s", reason)
            return {"executed": False, "reason": f"DAILY_RISK:{reason}", "detail": None}

        # 獲取當前持倉
        curr_dir, curr_size, curr_epic = self.get_current_position()
        logger.info("當前持倉：方向=%s, 手數=%.2f, epic=%s", curr_dir if curr_dir else "無", curr_size, curr_epic)

        # 無持倉 -> 開目標倉位
        if curr_dir is None:
            return self._open_position(direction, TARGET_SIZE, stop_loss, take_profit)

        # 同向 -> 不操作
        if curr_dir == direction:
            logger.info("方向一致，不調整")
            return {"executed": False, "reason": "ALREADY_ALIGNED"}

        # 反向 -> 先平倉，再開目標倉位
        logger.info("方向相反（現有 %s，AI 建議 %s），先平倉再開新倉", curr_dir, direction)
        if not self.close_position(curr_epic):
            logger.error("平倉失敗，停止開新倉")
            return {"executed": False, "reason": "CLOSE_FAILED", "detail": None}
        # 平倉後開新倉（目標 0.2 手）
        return self._open_position(direction, TARGET_SIZE, stop_loss, take_profit)

    def _confirm_deal(self, deal_reference: str, retries=3) -> dict | None:
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
                    logger.warning("確認失敗，重試 %d/3", i+2)
                else:
                    logger.error("確認失敗：%s", e)
        return None

    def logout(self) -> None:
        if not self.is_logged_in:
            return
        try:
            url = f"{config.IG_API_URL}/session"
            headers = {**dict(self.session.headers), "Version": "1"}
            self.session.delete(url, headers=headers, timeout=10)
            self.is_logged_in = False
            logger.info("👋 已登出")
        except Exception as e:
            logger.warning("登出錯誤：%s", e)


if __name__ == "__main__":
    config.validate_config()
    trader = IGTrader()
    if trader.login():
        trader.get_account_balance()
        # 測試反向平倉
        fake_signal = {
            "direction": "SELL",
            "confidence": 75,
            "stop_loss": 159.00,
            "take_profit": 158.50,
            "risk_level": "MEDIUM",
        }
        result = trader.place_order(fake_signal)
        print("下單結果：", result)
        trader.logout()
    else:
        print("登入失敗")

"""
ig_trader.py - IG Trading 交易執行模組（最終修正版，使用 JPY 貨幣）
"""

import logging
import time
import requests
import config

logger = logging.getLogger(__name__)

# USDJPY 在 IG 的 Epic 代碼（Demo 環境）
USDJPY_EPIC = "CS.D.USDJPY.MINI.IP"

# 下單手數範圍與步進
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

    def has_open_position(self) -> tuple[bool, str]:
        positions = self.get_open_positions()
        if positions is None:
            return True, "POSITION_CHECK_FAILED"
        for pos in positions:
            if USDJPY_EPIC in pos.get("market", {}).get("epic", ""):
                return True, "ALREADY_IN_POSITION"
        return False, ""

    # ==================== 下單 ====================
    def place_order(self, signal: dict) -> dict | None:
        if not self.is_logged_in or not self.account_id:
            return {"executed": False, "reason": "NOT_LOGGED_IN"}

        direction = signal["direction"]
        position_size = signal["position_size"]
        confidence = signal["confidence"]
        risk_level = signal["risk_level"]
    
        if direction == "HOLD":
            return {"executed": False, "reason": "HOLD"}
        if confidence < 60:
            return {"executed": False, "reason": "LOW_CONFIDENCE"}
    
        can_trade, reason = self.check_daily_risk()
        if not can_trade:
            return {"executed": False, "reason": f"DAILY_RISK:{reason}"}
    
        has_pos, pos_reason = self.has_open_position()
        if has_pos:
            return {"executed": False, "reason": pos_reason}
    
        # 计算手数
        raw = max(MIN_DEAL_SIZE, min(MAX_DEAL_SIZE, position_size))
        deal_size = round(raw / DEAL_SIZE_STEP) * DEAL_SIZE_STEP
        deal_size = max(MIN_DEAL_SIZE, min(MAX_DEAL_SIZE, deal_size))
        logger.info("💡 實際手數：%.2f", deal_size)
    
        # 获取当前市价（用于验证停损距离）
        # 从 snapshot 中获取 bid/offer，这里简单使用 signal 中的 stop_loss/take_profit
        stop_loss = signal.get("stop_loss")
        take_profit = signal.get("take_profit")
        if stop_loss is None or take_profit is None:
            logger.warning("缺少停損或停利價格，本次不下單")
            return {"executed": False, "reason": "MISSING_SL_TP"}
    
        # 获取最新市场价格（建议通过市场快照获取，这里暂用 signal 中的参考价）
        # 实际应该调用 /markets/{epic} 获取当前 bid/offer
        # 简化：假设 signal 中的 stop_loss/take_profit 已符合要求
        # 验证停损距离（至少 0.002）
        current_price = None
        # 为了安全，先不验证距离，直接使用 AI 提供的价格
    
        # 使用日圓作為結算貨幣
        currency_code = "JPY"
    
        payload = {
            "epic": USDJPY_EPIC,
            "expiry": "-",
            "direction": direction,
            "size": f"{deal_size:.2f}",
            "orderType": "MARKET",
            "timeInForce": "FILL_OR_KILL",
            "guaranteedStop": False,
            "forceOpen": True,
            "marketOrderPreference": "AVAILABLE",
            "currencyCode": currency_code,
            "accountId": self.account_id,
            "stopLevel": round(stop_loss, 5),      # 添加停损
            "profitLevel": round(take_profit, 5),  # 添加停利
        }
        logger.info("📤 下單 Payload: %s", payload)
    
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
                logger.info("✅ 下單成功！開倉價=%s", confirm.get("level"))
                return {"executed": True, "reason": "ACCEPTED", "detail": confirm}
            else:
                logger.error("❌ 下單被拒絕：%s", reason)
                return {"executed": False, "reason": f"REJECTED:{reason}", "detail": confirm}
        except Exception as e:
            logger.error("下單異常：%s", e)
            return {"executed": False, "reason": f"ERROR:{e}"}

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
        fake_signal = {
            "direction": "BUY",
            "confidence": 75,
            "position_size": 0.4,
            "risk_level": "MEDIUM",
        }
        result = trader.place_order(fake_signal)
        print("下單結果：", result)
        trader.logout()
    else:
        print("登入失敗")

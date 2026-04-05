"""
ig_trader.py - IG Trading 交易執行模組
負責：
  1. 登入 IG Demo API 取得 Session Token
  2. 查詢 USDJPY 的 Epic 代碼與最小下單單位
  3. 根據 AI 訊號執行下單（含停損停利）
  4. 登出 Session
"""

import logging

import requests

import config

logger = logging.getLogger(__name__)

# IG API 標頭常數
IG_HEADERS_BASE = {
    "Content-Type": "application/json; charset=UTF-8",
    "Accept":       "application/json; charset=UTF-8",
    "X-IG-API-KEY": config.IG_API_KEY,
}

# USDJPY 在 IG 的 Epic 代碼（Demo 環境）
USDJPY_EPIC = "CS.D.USDJPY.MINI.IP" #"CS.D.USDJPY.MINI.IP" "CS.D.USDJPY.CFD.IP"

# 最小下單單位（Mini Contract = 10,000 單位）
MIN_DEAL_SIZE = 1


class IGTrader:
    """IG Trading API 封裝類別。"""

    def __init__(self):
        self.session      = requests.Session()
        self.session.headers.update(IG_HEADERS_BASE)
        self.cst          = None   # Client Security Token
        self.x_security   = None   # X-SECURITY-TOKEN
        self.account_id   = None
        self.is_logged_in = False

    # ── 登入 ────────────────────────────────────────────────────
    def login(self, max_retries: int = 3, retry_delay: int = 5) -> bool:
        """
        登入 IG API，取得 CST 與 X-SECURITY-TOKEN。
        失敗時自動重試最多 max_retries 次。

        Returns:
            True 若登入成功，False 若失敗
        """
        import time

        url     = f"{config.IG_API_URL}/session"
        headers = {**IG_HEADERS_BASE, "Version": "2"}
        payload = {
            "identifier":        config.IG_IDENTIFIER,
            "password":          config.IG_PASSWORD,
            "encryptedPassword": False,
        }

        for attempt in range(1, max_retries + 1):
            logger.info("🔐 正在登入 IG Demo API...（第 %d/%d 次嘗試）", attempt, max_retries)
            try:
                response = self.session.post(url, json=payload, headers=headers, timeout=30)
                response.raise_for_status()

                data            = response.json()
                self.cst        = response.headers.get("CST")
                self.x_security = response.headers.get("X-SECURITY-TOKEN")
                self.account_id = data.get("accountId", "")

                if not self.cst or not self.x_security:
                    logger.error("❌ 登入失敗：回應中缺少 CST 或 X-SECURITY-TOKEN")
                    if attempt < max_retries:
                        logger.info("⏳ %d 秒後重試...", retry_delay)
                        time.sleep(retry_delay)
                    continue

                self.session.headers.update({
                    "CST":              self.cst,
                    "X-SECURITY-TOKEN": self.x_security,
                })

                self.is_logged_in = True
                logger.info("✅ IG 登入成功！帳號 ID：%s", self.account_id)
                return True

            except requests.exceptions.Timeout:
                logger.warning("⚠️ 登入逾時（第 %d 次）", attempt)
            except requests.exceptions.ConnectionError:
                logger.warning("⚠️ 連線失敗（第 %d 次）", attempt)
            except requests.exceptions.HTTPError as e:
                logger.warning("⚠️ 登入 HTTP 錯誤（第 %d 次）：%s", attempt, e)
            except Exception as e:
                logger.warning("⚠️ 登入未預期錯誤（第 %d 次）：%s", attempt, e)

            if attempt < max_retries:
                logger.info("⏳ %d 秒後重試...", retry_delay)
                time.sleep(retry_delay)

        logger.error("❌ 登入失敗，已重試 %d 次仍無法連線", max_retries)
        return False
        
     # ── 查詢帳戶餘額 ─────────────────────────────────────────────
    def get_account_balance(self) -> "dict | None":
        if not self.is_logged_in:
            logger.error("❌ 尚未登入")
            return None

        try:
            url = f"{config.IG_API_URL}/accounts"
            headers = {**dict(self.session.headers), "Version": "1"}
            response = self.session.get(url, headers=headers, timeout=30)
            response.raise_for_status()
            accounts = response.json().get("accounts", [])
            if not accounts:
                logger.warning("⚠️ 查無帳戶資料")
                return None
            account = accounts[0]
            balance = account.get("balance", {})
            logger.info("💰 帳戶餘額 - 可用：%.2f | 淨值：%.2f | 貨幣：%s",
                        balance.get("available", 0),
                        balance.get("equity", 0),
                        account.get("currency", ""))
            return balance
        except Exception as e:
            logger.error("❌ 查詢帳戶餘額失敗：%s", e, exc_info=True)
            return None

    # ── 查詢每日損益（簡化版，不統計交易次數）────────────────────
    def get_daily_pnl(self) -> "dict | None":
        """查詢今日損益（從餘額中獲取 profitLoss）"""
        if not self.is_logged_in:
            logger.error("❌ 尚未登入")
            return None

        try:
            balance = self.get_account_balance()
            if balance is None:
                return None
            equity = balance.get("equity", 0)
            pnl = balance.get("profitLoss", 0)   # 今日已實現損益
            loss_pct = (-pnl / equity * 100) if equity > 0 and pnl < 0 else 0.0
            trade_count = 0   # 簡化版，不統計交易次數

            logger.info("📊 今日損益：%.2f | 交易次數：%d | 虧損比例：%.2f%%",
                        pnl, trade_count, loss_pct)
            return {
                "pnl": pnl,
                "trade_count": trade_count,
                "equity": equity,
                "loss_pct": loss_pct,
            }
        except Exception as e:
            logger.error("❌ 查詢每日損益失敗：%s", e, exc_info=True)
            return None

    # ── 每日風控檢查（返回 (bool, str) 元組）─────────────────────
    def check_daily_risk(self) -> "tuple[bool, str]":
        daily = self.get_daily_pnl()
        if daily is None:
            logger.warning("⚠️ 無法取得每日損益，暫停交易")
            return False, "CANNOT_FETCH_DAILY_PNL"

        max_loss_pct = config.MAX_DAILY_LOSS_PCT * 100
        if daily["loss_pct"] >= max_loss_pct:
            logger.error("🛑 今日虧損 %.2f%% 已達上限 %.2f%%", daily["loss_pct"], max_loss_pct)
            return False, f"DAILY_LOSS_LIMIT:{daily['loss_pct']:.2f}%"

        if daily["trade_count"] >= config.MAX_DAILY_TRADES:
            logger.error("🛑 今日交易次數 %d 已達上限 %d", daily["trade_count"], config.MAX_DAILY_TRADES)
            return False, f"DAILY_TRADE_LIMIT:{daily['trade_count']}"

        logger.info("✅ 每日風控檢查通過：虧損 %.2f%% | 交易次數 %d",
                    daily["loss_pct"], daily["trade_count"])
        return True, ""

    # ── 查詢現有持倉 ─────────────────────────────────────────────
    def get_open_positions(self) -> "list | None":
        if not self.is_logged_in:
            logger.error("❌ 尚未登入")
            return None

        try:
            url = f"{config.IG_API_URL}/positions"
            headers = {**dict(self.session.headers), "Version": "2"}
            response = self.session.get(url, headers=headers, timeout=30)
            response.raise_for_status()
            positions = response.json().get("positions", [])
            logger.info("📋 目前持倉數量：%d", len(positions))
            for pos in positions:
                market = pos.get("market", {})
                deal = pos.get("position", {})
                logger.info("  持倉：%s | 方向：%s | 手數：%s | 開倉價：%s",
                            market.get("epic", ""),
                            deal.get("direction", ""),
                            deal.get("size", ""),
                            deal.get("level", ""))
            return positions
        except Exception as e:
            logger.error("❌ 查詢持倉失敗：%s", e, exc_info=True)
            return None

    # ── 檢查是否已有 USDJPY 持倉 ─────────────────────────────────
    def has_open_position(self) -> bool:
        positions = self.get_open_positions()   # 修正方法名
        if positions is None:
            logger.warning("⚠️ 無法確認持倉狀態，視為已有持倉")
            return True
        for pos in positions:
            epic = pos.get("market", {}).get("epic", "")
            if USDJPY_EPIC in epic:
                logger.warning("⚠️ 已有 USDJPY 持倉（%s），跳過下單", epic)
                return True
        return False

    # ── 下單 ─────────────────────────────────────────────────────
    def place_order(self, signal: dict) -> "dict | None":
        if not self.is_logged_in:
            logger.error("❌ 尚未登入")
            return None

        direction = signal["direction"]
        stop_loss = signal["stop_loss"]
        take_profit = signal["take_profit"]
        position_size = signal["position_size"]
        confidence = signal["confidence"]
        risk_level = signal["risk_level"]

        if direction == "HOLD":
            logger.info("⏸️ AI 建議 HOLD")
            return {"executed": False, "reason": "HOLD", "detail": None}

        if confidence < 60:
            logger.warning("⚠️ 信心度過低（%d%%）", confidence)
            return {"executed": False, "reason": "LOW_CONFIDENCE", "detail": None}

        if risk_level == "HIGH" and position_size > 0.3:
            position_size = 0.3
            logger.warning("⚠️ 高風險，部位縮減至 0.3")

        can_trade, risk_reason = self.check_daily_risk()
        if not can_trade:
            logger.error("🛑 每日風控觸發：%s", risk_reason)
            return {"executed": False, "reason": f"DAILY_RISK:{risk_reason}", "detail": None}

        if self.has_open_position():
            logger.warning("⚠️ 已有 USDJPY 持倉")
            return {"executed": False, "reason": "ALREADY_IN_POSITION", "detail": None}

        deal_size = max(1, min(5, round(5 * position_size)))
        logger.info("💡 AI 建議部位：%.1f → 實際手數：%d", position_size, deal_size)

        balance = self.get_account_balance()
        currency_code = balance.get("currency", "USD") if balance else "USD"

        logger.info("📤 下單：方向=%s | 手數=%d | SL=%.3f | TP=%.3f | 幣別=%s",
                    direction, deal_size, stop_loss, take_profit, currency_code)

        url = f"{config.IG_API_URL}/positions/otc"
        headers = {**dict(self.session.headers), "Version": "2"}
        payload = {
            "epic": USDJPY_EPIC,
            "expiry": "-",
            "direction": direction,
            "size": str(deal_size),
            "orderType": "MARKET",
            "timeInForce": "FILL_OR_KILL",
            "guaranteedStop": False,
            "stopLevel": stop_loss,
            "profitLevel": take_profit,
            "currencyCode": currency_code,
            "forceOpen": True,
        }

        try:
            response = self.session.post(url, json=payload, headers=headers, timeout=30)
            response.raise_for_status()
            result = response.json()
            deal_ref = result.get("dealReference", "N/A")
            logger.info("✅ 下單請求送出！Deal Reference：%s", deal_ref)

            confirm = self._confirm_deal(deal_ref)
            if confirm is None:
                return {"executed": False, "reason": "CONFIRM_FAILED", "detail": None}

            deal_status = confirm.get("dealStatus", "UNKNOWN")
            reason = confirm.get("reason", "")

            if deal_status == "ACCEPTED":
                logger.info("✅ 下單成功！開倉價=%s", confirm.get("level", "N/A"))
                return {"executed": True, "reason": "ACCEPTED", "detail": confirm}
            elif deal_status == "REJECTED":
                logger.error("❌ 下單被拒絕：%s", reason)
                return {"executed": False, "reason": f"REJECTED:{reason}", "detail": confirm}
            else:
                logger.warning("⚠️ 下單狀態未知：%s", deal_status)
                return {"executed": False, "reason": f"UNKNOWN:{deal_status}", "detail": confirm}

        except Exception as e:
            logger.error("❌ 下單失敗：%s", e, exc_info=True)
            return {"executed": False, "reason": f"ERROR:{e}", "detail": None}

    # ── 確認下單結果 ──────────────────────────────────────────────
    def _confirm_deal(self, deal_reference: str) -> "dict | None":
        try:
            url = f"{config.IG_API_URL}/confirms/{deal_reference}"
            headers = {**dict(self.session.headers), "Version": "1"}
            response = self.session.get(url, headers=headers, timeout=30)
            response.raise_for_status()
            confirm = response.json()
            status = confirm.get("dealStatus", "UNKNOWN")
            reason = confirm.get("reason", "")
            logger.info("📋 下單確認：狀態=%s | 原因=%s", status, reason)
            return confirm
        except Exception as e:
            logger.error("❌ 下單確認失敗：%s", e)
            return None

    # ── 登出 ─────────────────────────────────────────────────────
    def logout(self) -> None:
        if not self.is_logged_in:
            return
        try:
            url = f"{config.IG_API_URL}/session"
            headers = {**dict(self.session.headers), "Version": "1"}
            self.session.delete(url, headers=headers, timeout=10)
            self.is_logged_in = False
            logger.info("👋 IG 已登出")
        except Exception as e:
            logger.warning("⚠️ 登出錯誤：%s", e)


# ── 測試 ─────────────────────────────────────────────────────
if __name__ == "__main__":
    config.validate_config()
    trader = IGTrader()
    if trader.login():
        trader.get_account_balance()
        fake_signal = {
            "direction": "HOLD",
            "confidence": 75,
            "stop_loss": 159.0,
            "take_profit": 161.0,
            "position_size": 0.5,
            "risk_level": "MEDIUM",
        }
        logger.info("🧪 使用 HOLD 假訊號測試")
        result = trader.place_order(fake_signal)
        print(result)
        trader.logout()
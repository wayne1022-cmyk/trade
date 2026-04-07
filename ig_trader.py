"""
ig_trader.py - 净头寸管理版（不平仓，通过两次开仓翻转仓位）
目标净头寸：±0.2 手，通过调整开仓量实现，避免平仓 API。
"""

import logging
import time
import requests
import config

logger = logging.getLogger(__name__)

# 请根据实际持仓 EPIC 修改（运行诊断代码确认）
USDJPY_EPIC = "CS.D.USDJPY.MINI.IP"   # 迷你合约（最小 0.2 手）
TARGET_NET_SIZE = 0.2                # 目标净头寸大小
MIN_DEAL_SIZE = 0.2                  # 最小交易量 0.2
MAX_DEAL_SIZE = 1.0
DEAL_SIZE_STEP = 0.01                # 步进（虽小，但实际下单需为 0.2 倍数）


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

    # ==================== 持倉查詢與淨頭寸計算 ====================
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

    def get_net_position(self) -> float:
        """計算當前淨頭寸（多頭手數 - 空頭手數）"""
        positions = self.get_open_positions()
        net = 0.0
        for pos in positions:
            epic = pos.get("market", {}).get("epic", "")
            if USDJPY_EPIC in epic:
                direction = pos.get("position", {}).get("direction")
                size = float(pos.get("position", {}).get("size", 0))
                if direction == "BUY":
                    net += size
                elif direction == "SELL":
                    net -= size
        logger.info("當前淨頭寸：%.3f", net)
        return net

    # ==================== 開倉（單筆） ====================
    def _open_position(self, direction: str, size: float, stop_loss: float = None, take_profit: float = None) -> dict:
        """開一筆訂單（內部使用）"""
        # 限制大小並確保是 0.2 的倍數（步進 0.2，最小 0.2）
        size = max(0.2, min(MAX_DEAL_SIZE, size))
        size = round(size / 0.2) * 0.2
        if size < 0.2:
            size = 0.2
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

    # ==================== 主要下單入口（拆分為合理訂單） ====================
    def place_order(self, signal: dict) -> dict | None:
        if not self.is_logged_in or not self.account_id:
            return {"executed": False, "reason": "NOT_LOGGED_IN"}

        direction = signal["direction"]
        confidence = signal.get("confidence", 0)
        stop_loss = signal.get("stop_loss")
        take_profit = signal.get("take_profit")

        if direction == "HOLD":
            logger.info("AI 建議 HOLD，不調整倉位")
            return {"executed": False, "reason": "HOLD", "detail": None}
        if confidence < 60:
            logger.warning("信心度過低（%d%%），不操作", confidence)
            return {"executed": False, "reason": "LOW_CONFIDENCE", "detail": None}

        can_trade, reason = self.check_daily_risk()
        if not can_trade:
            logger.error("每日風控觸發：%s", reason)
            return {"executed": False, "reason": f"DAILY_RISK:{reason}", "detail": None}

        # 計算需要調整的淨頭寸
        current_net = self.get_net_position()
        target_net = TARGET_NET_SIZE if direction == "BUY" else -TARGET_NET_SIZE
        delta = target_net - current_net   # 需要變化的淨頭寸

        if abs(delta) < 0.001:
            logger.info("淨頭寸已達目標 (%.2f)，無需操作", current_net)
            return {"executed": False, "reason": "ALREADY_ALIGNED", "detail": None}

        # 確定開倉方向和大小
        if delta > 0:
            order_direction = "BUY"
            order_size = delta
        else:
            order_direction = "SELL"
            order_size = -delta

        # 將 order_size 調整為 0.2 的倍數（因為最小 0.2）
        step = 0.2
        order_size = round(order_size / step) * step
        if order_size < step:
            order_size = step

        # 決定是否拆分（只有當 order_size >= 0.4 時才拆成兩筆 0.2）
        if order_size >= 0.4:
            sizes = [step, order_size - step]
        else:
            sizes = [order_size]

        logger.info("目標淨頭寸：%.2f (%s)，當前淨頭寸：%.2f，需調整 %.3f 手 %s，訂單拆分: %s",
                    target_net, direction, current_net, order_size, order_direction, sizes)

        results = []
        all_success = True
        for i, sz in enumerate(sizes, 1):
            logger.info("發送第 %d 筆訂單：%s %.2f 手", i, order_direction, sz)
            res = self._open_position(order_direction, sz, stop_loss, take_profit)
            results.append(res)
            if not res.get("executed"):
                all_success = False
                logger.error("第 %d 筆訂單失敗，停止後續下單", i)
                break
            time.sleep(0.5)  # 避免過於密集

        if all_success and len(results) == len(sizes):
            return {"executed": True, "reason": "MULTI_ACCEPTED", "detail": results}
        else:
            return {"executed": False, "reason": "PARTIAL_FAILED", "detail": results}

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
            "stop_loss": 159.00,
            "take_profit": 159.50,
            "risk_level": "MEDIUM",
        }
        result = trader.place_order(fake_signal)
        print("下單結果：", result)
        trader.logout()
    else:
        print("登入失敗")

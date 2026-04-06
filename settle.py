"""
settle.py - 每日结算：平仓所有 USDJPY 持仓，并记录当日盈亏
"""
import logging
import config
from ig_trader import IGTrader

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    trader = IGTrader()
    if not trader.login():
        logger.error("登录失败，无法结算")
        return

    # 获取所有持仓
    positions = trader.get_open_positions()
    if not positions:
        logger.info("无持仓需要结算")
    else:
        for pos in positions:
            epic = pos.get("market", {}).get("epic", "")
            if config.IG_EPIC in epic:   # 只平仓USDJPY
                deal_id = pos.get("position", {}).get("dealId")
                if deal_id:
                    # 调用平仓API (DELETE /positions/{epic})
                    url = f"{config.IG_API_URL}/positions/{epic}"
                    headers = {**dict(trader.session.headers), "Version": "1"}
                    resp = trader.session.delete(url, headers=headers)
                    if resp.status_code == 200:
                        logger.info(f"平仓成功：{epic} dealId={deal_id}")
                    else:
                        logger.error(f"平仓失败：{resp.text}")
                else:
                    logger.warning(f"无法获取dealId，跳过平仓 {epic}")

    # 记录当日盈亏（可从账户余额获取）
    balance = trader.get_account_balance()
    if balance:
        logger.info(f"结算完成 - 当日盈亏：{balance.get('profitLoss', 0)}")

    trader.logout()

if __name__ == "__main__":
    main()

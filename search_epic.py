import config
import requests

def search_usdjpy_epics():
    print("🔐 正在登入取得 Token...")
    auth_url = f"{config.IG_API_URL}/session"
    headers = {
        "X-IG-API-KEY": config.IG_API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json; charset=UTF-8",
        "Version": "3"                     # 改为 Version 首字母大写
    }
    payload = {
        "identifier": config.IG_IDENTIFIER,
        "password": config.IG_PASSWORD
    }
    
    resp = requests.post(auth_url, json=payload, headers=headers)
    if resp.status_code != 200:
        print(f"❌ 登入失敗: {resp.text}")
        return

    cst = resp.headers.get("CST")
    x_sec = resp.headers.get("X-SECURITY-TOKEN")
    
    print("🔍 開始搜尋 USDJPY 相關市場...")
    search_url = f"{config.IG_API_URL}/markets?searchTerm=USDJPY"
    search_headers = {
        "X-IG-API-KEY": config.IG_API_KEY,
        "CST": cst,
        "X-SECURITY-TOKEN": x_sec,
        "Accept": "application/json; charset=UTF-8",
        "Version": "1"                     # 统一 Version
    }
    
    search_resp = requests.get(search_url, headers=search_headers)
    if search_resp.status_code == 200:
        markets = search_resp.json().get("markets", [])
        print(f"\n✅ 找到 {len(markets)} 個相關市場：")
        for m in markets:
            print(f"- 名稱: {m.get('instrumentName')}")
            print(f"  EPIC: {m.get('epic')}")
            print(f"  狀態: {m.get('marketStatus')}")
            print(f"  類型: {m.get('instrumentType')}")          # CFD / SPREADBET
            # 打印最小交易量和最小停损距离（如果有）
            dealing_rules = m.get('dealingRules', {})
            min_size = dealing_rules.get('minDealSize', {}).get('value', 'N/A')
            min_step = dealing_rules.get('minStepSize', {}).get('value', 'N/A')
            min_stop_dist = dealing_rules.get('minStopDistance', {}).get('value', 'N/A')
            print(f"  最小交易量: {min_size}, 最小步进: {min_step}, 最小停损距离: {min_stop_dist}")
            print("---")
    else:
        print(f"❌ 搜尋失敗: {search_resp.text}")

if __name__ == "__main__":
    search_usdjpy_epics()

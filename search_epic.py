import config
import requests

def search_usdjpy_epics():
    print("🔐 正在登入取得 Token...")
    auth_url = f"{config.IG_API_URL}/session"
    headers = {
        "X-IG-API-KEY": config.IG_API_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json; charset=UTF-8",
        "VERSION": "3"
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
        "VERSION": "1"
    }
    
    search_resp = requests.get(search_url, headers=search_headers)
    if search_resp.status_code == 200:
        markets = search_resp.json().get("markets", [])
        print(f"\n✅ 找到 {len(markets)} 個相關市場：")
        for m in markets:
            print(f"- 名稱: {m.get('instrumentName')} | EPIC: {m.get('epic')} | 狀態: {m.get('marketStatus')}")
    else:
        print(f"❌ 搜尋失敗: {search_resp.text}")

if __name__ == "__main__":
    search_usdjpy_epics()
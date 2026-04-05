import requests
import json

api_key = "sk_L2IutDj13Qg07rf5ZhdhWGdyb3FYhm2Hfzpc1v9mtNY2N96G5mKM"
url = "https://api.groq.com/openai/v1/chat/completions"
headers = {
    "Authorization": f"Bearer {api_key}",
    "Content-Type": "application/json"
}
payload = {
    "model": "llama-3.3-70b-versatile",
    "messages": [{"role": "user", "content": "Say hello"}],
    "max_tokens": 10
}
resp = requests.post(url, json=payload, headers=headers, timeout=30)
print(resp.status_code)
print(resp.text)

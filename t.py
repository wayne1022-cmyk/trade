import requests
import json

url = "https://demo-api.ig.com/gateway/deal/session"
headers = {
    "X-IG-API-KEY": "8f32a84ad7ded0520543113aea9fb4c0854dd3ae",
    "Content-Type": "application/json",
    "Accept": "application/json; charset=UTF-8",
    "Version": "2",
}
payload = {
    "identifier": "waynecc",
    "password": "Wayne8787",
    "encryptedPassword": False,
}

response = requests.post(url, headers=headers, json=payload)
print(f"Status Code: {response.status_code}")
print(f"Response Body: {response.text}")
import os
import requests
from urllib.parse import quote
from dotenv import load_dotenv

load_dotenv()

COC_API_TOKEN = os.getenv("COC_API_TOKEN")
CLAN_TAG = os.getenv("CLAN_TAG")

encoded = quote(CLAN_TAG)

url = f"https://api.clashofclans.com/v1/clans/{encoded}/currentwar"
headers = {
    "Authorization": f"Bearer {COC_API_TOKEN}",
    "Accept": "application/json"
}

resp = requests.get(url, headers=headers)
print(resp.status_code)
print(resp.json())
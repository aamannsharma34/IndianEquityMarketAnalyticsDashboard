"""
Run this once to see the EXACT names Kite uses for NSE sector indices.
Usage: python list_indices.py
"""
from kiteconnect import KiteConnect
import datetime, os

def load_credentials(path="credentials.txt"):
    creds = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                creds[k.strip()] = v.strip()
    return creds["KITE_API_KEY"], creds["KITE_API_SECRET"]

API_KEY, _ = load_credentials()

with open("access_token.txt") as f:
    saved_date, token = f.read().strip().split("|", 1)
    if saved_date != datetime.date.today().isoformat():
        raise SystemExit("Token expired for today — log in via the app first (http://127.0.0.1:5000), then rerun this.")

kite = KiteConnect(api_key=API_KEY)
kite.set_access_token(token)

instruments = kite.instruments("NSE")
print(f"Total NSE instruments: {len(instruments)}\n")
print("All NIFTY sector/thematic indices:\n")

for inst in instruments:
    if inst.get("segment") == "INDICES" and "NIFTY" in inst.get("name", "").upper():
        print(f"  {inst['name']}")

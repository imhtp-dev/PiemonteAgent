"""
Quick slot search test — tweak the variables below and run:
    python tests/test_slot_api.py
"""

import json
import sys
import os
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ╔══════════════════════════════════════════════════════════════════╗
# ║  TWEAK THESE                                                     ║
# ╚══════════════════════════════════════════════════════════════════╝

CENTER_UUID = "6cff89d8-1f40-4eb8-bed7-f36e94a3355c"   
SERVICE_UUIDS = ["9a93d65f-396a-45e4-9284-94481bdd2b51"]  
DATE = "2026-03-07"
GENDER = "f"               # "m" or "f"
DOB = "19990106"           # YYYYMMDD
START_TIME = None          # e.g. "2026-03-20 08:00:00+00" or None
END_TIME = None            # e.g. "2026-03-20 12:00:00+00" or None
PROVIDING_ENTITY = None    # doctor UUID or None
AVAILABILITIES_LIMIT = 2   # sent directly to API — tweak to see effect

# ╔══════════════════════════════════════════════════════════════════╝

from services.slotAgenda import get_token

token = get_token()
api_url = f"https://3z0xh9v1f4.execute-api.eu-south-1.amazonaws.com/prod/amb/health-center/{CENTER_UUID}/slot"

params = {
    "gender": GENDER,
    "date_of_birth": DOB,
    "health_services": SERVICE_UUIDS,
    "start_date": DATE,
    "start_time": START_TIME,
    "end_time": END_TIME,
    "availabilities_limit": AVAILABILITIES_LIMIT,
}
if PROVIDING_ENTITY:
    params["providing_entity"] = PROVIDING_ENTITY

print(f"\n{'='*60}")
print(f"Center:             {CENTER_UUID}")
print(f"Services:           {SERVICE_UUIDS}")
print(f"Date:               {DATE}")
print(f"Time:               {START_TIME or 'any'} — {END_TIME or 'any'}")
print(f"Doctor:             {PROVIDING_ENTITY or 'any'}")
print(f"Availabilities Lim: {AVAILABILITIES_LIMIT}")
print(f"{'='*60}")
print(f"API params: {json.dumps(params, indent=2)}")
print(f"{'='*60}\n")

response = requests.get(api_url, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, params=params)

print(f"Status: {response.status_code}")

if response.status_code == 200:
    slots = response.json()
    print(f"✅ {len(slots)} slots returned\n")
    for i, slot in enumerate(slots, 1):
        start = slot.get("start_time", "?")
        end = slot.get("end_time", "?")
        pe = slot.get("providing_entity", {})
        doctor = f"{pe.get('name', '?')} {pe.get('surname', '')}".strip() if pe else "?"
        print(f"  {i}. {start} — {end}  |  {doctor}")
else:
    print(f"❌ Error: {response.status_code} — {response.text}")
    slots = []

# Save raw response
out_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "slot_response.json")
with open(out_path, "w") as f:
    json.dump(slots if isinstance(slots, list) else [], f, indent=2, ensure_ascii=False)
print(f"\n{'='*60}")
print(f"💾 Raw response saved to {out_path}")

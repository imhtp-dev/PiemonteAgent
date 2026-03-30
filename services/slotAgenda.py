import json
import requests
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from loguru import logger
from utils.tracing import trace_sync_call, add_span_attributes

load_dotenv(override=True)
#from auth import get_token


def get_token():
    url = os.getenv("CERBA_COGNITO_URL", "https://cerbahc.auth.eu-central-1.amazoncognito.com/oauth2/token")
    client_id = os.getenv("CERBA_CLIENT_ID", "")
    client_secret = os.getenv("CERBA_CLIENT_SECRET", "")
    payload = {
    "client_id": client_id,
    "client_secret": client_secret,
    "grant_type": "client_credentials",
    "scope": "voila/api"
}
    headers = {
    "Content-Type": "application/x-www-form-urlencoded"
}

    response = requests.post(url, data=payload, headers=headers)
    
    token=""
    if response.status_code == 200:
        token = response.json()['access_token']
    else:
        logger.error(f'❌ Token request failed: {response.status_code} - {response.text[:200]}')
    return token




@trace_sync_call("api.slot_search")
def list_slot(health_center_uuid, date_search, uuid_exam, gender='m', date_of_birth='1980-04-13', start_time=None, end_time=None, providing_entity=None):
    # Add search params to span for debugging
    add_span_attributes({
        "slot.center_uuid": health_center_uuid,
        "slot.date": date_search,
        "slot.service_count": len(uuid_exam) if isinstance(uuid_exam, list) else 1,
        "slot.start_time": start_time or "not_specified",
        "slot.end_time": end_time or "not_specified"
    })
    token = get_token()
    ambiente="prod"
    api_url = f'https://3z0xh9v1f4.execute-api.eu-south-1.amazonaws.com/{ambiente}/amb/health-center/{health_center_uuid}/slot'

    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
    }



    request_data = {
        'gender': gender,
        'date_of_birth': date_of_birth,
        'health_services': uuid_exam,  # uuid_exam is already a list
        'start_date': date_search, # Date of appointment
        'start_time': start_time, # 2025-09-12 09:00:00+00
        'end_time': end_time, # 2025-09-12 10:00:00+00
        'availabilities_limit': 5 if providing_entity else 3
    }

    if providing_entity:
        request_data['providing_entity'] = providing_entity

    logger.info(f'🔍 SLOT API: {api_url}')
    logger.info(f'🔍 SLOT API: params={request_data}')

    response = requests.get(api_url, headers=headers, params=request_data)

    logger.info(f'🔍 SLOT API: Status {response.status_code}')

    if response.status_code == 200:
        slots = response.json()

        slot_count = len(slots) if isinstance(slots, list) else 0
        logger.info(f'🔍 SLOT API: {slot_count} slots returned')
        logger.info(f'🔍 SLOT API RAW RESPONSE: {json.dumps(slots, default=str)}')

        # Log all slots with price details
        if isinstance(slots, list):
            for i, slot in enumerate(slots):
                hs = slot.get("health_services", [])
                start_time = slot.get("start_time", "N/A")
                pe = slot.get("providing_entity") or {}
                pe_type = (pe.get("providing_entity_type") or {}).get("name", "")
                prof = pe.get("professional") or {}
                if prof:
                    doctor = f"{prof.get('name', '')} {prof.get('surname', '')}".strip()
                else:
                    doctor = pe.get("name", "") or f"[{pe_type}]"
                if hs:
                    s = hs[0]
                    cerba_raw = s.get("cerba_card_price")
                    cerba_str = f"{cerba_raw}€" if cerba_raw is not None else "NULL"
                    logger.info(f'  Slot {i+1}/{slot_count}: {start_time} | {s.get("name", "N/A")} | {s.get("price", "N/A")}€ | Cerba: {cerba_str} | Doctor: {doctor}')

        return slots
    else:
        logger.error(f'❌ SLOT API: Status {response.status_code} - {response.text[:200]}')
        raise Exception(f"Slot API returned {response.status_code}: {response.text[:200]}")


@trace_sync_call("api.slot_create")
def create_slot(start_slot,end_slot,pea):
    # Add slot details to span for debugging
    add_span_attributes({
        "slot.start": start_slot,
        "slot.end": end_slot,
        "slot.pea": pea
    })
    # Use slot times as-is (no timezone conversion needed)
    # Input format: 2025-10-27 11:25:00
    # API expects: 2025-10-27 11:25:00 (same format)
    ambiente="prod"

    token = get_token()

    api_url = f'https://3z0xh9v1f4.execute-api.eu-south-1.amazonaws.com/{ambiente}/amb/slot'

    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
    }

    request_data = {
        'start_time':start_slot,
        'end_time':end_slot,
        'providing_entity_availability':pea # unique identifier of the availability
    }

    logger.info(f'🔍 SLOT CREATE: PEA={pea}, {start_slot} → {end_slot}')

    response = requests.post(api_url, headers=headers, json=request_data)

    logger.info(f'🔍 SLOT CREATE: Status {response.status_code}')

    uuid_slot=""
    crea_at=""
    if response.status_code == 200 or response.status_code == 201:
        data = response.json()
        uuid_slot = data.get('uuid', '')
        crea_at = data.get('created_at', '')
        logger.success(f'✅ SLOT CREATED: UUID={uuid_slot}, created_at={crea_at}')
    else:
        # LOG ERROR DETAILS
        try:
            error_data = response.json()
            logger.error(f'❌ SLOT CREATE ERROR: Status {response.status_code}')
            logger.error(f'❌ SLOT CREATE ERROR: Response body: {error_data}')
        except:
            logger.error(f'❌ SLOT CREATE ERROR: Status {response.status_code}')
            logger.error(f'❌ SLOT CREATE ERROR: Response text: {response.text}')

    return response.status_code,uuid_slot,crea_at


def delete_slot(slot_uuid):
    token = get_token()
    ambiente="prod"
    api_url = f'https://3z0xh9v1f4.execute-api.eu-south-1.amazonaws.com/{ambiente}/amb/slot/{slot_uuid}'

    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
    }
    logger.info(f'🔍 SLOT DELETE: UUID={slot_uuid}')
    response = requests.delete(api_url, headers=headers)
    logger.info(f'🔍 SLOT DELETE: Status {response.status_code}')
    return response


#print(list_slot("b6766932-8b4f-4ce3-a959-b1142e8daf11","2026-03-19",['0f1b2c75-e84b-432a-8f7e-d172dc8eae7a'], start_time=None, end_time=None))
#print(create_slot('2025-11-20 15:40:00','2025-11-20 15:55:00',"3a4c9547-bd61-4557-a1b5-f681b3d9da25"))
#print(create_slot('2025-10-27 11:25:00','2025-10-27 11:30:00',"d1bbc9cd-e7e8-4e1e-8075-b637824504a6"))
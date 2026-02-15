import requests
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from loguru import logger
from utils.tracing import trace_sync_call, add_span_attributes
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
        print(f'Error while requesting: {response.status_code} - {response.text}')
    return token




@trace_sync_call("api.slot_search")
def list_slot(health_center_uuid, date_search, uuid_exam, gender='m', date_of_birth='1980-04-13', start_time=None, end_time=None):
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
        'availabilities_limit': 3
    }

    logger.info(f'🔍 SLOT API REQUEST: Making API request to: {api_url}')
    logger.info(f'🔍 SLOT API REQUEST: Request params: {request_data}')
    print(f'🔍 SLOT FETCH DEBUG: Making API request to: {api_url}')
    print(f'🔍 SLOT FETCH DEBUG: Request params: {request_data}')

    response = requests.get(api_url, headers=headers, params=request_data)

    logger.info(f'🔍 SLOT API RESPONSE: Status {response.status_code}')
    print(f'🔍 SLOT FETCH DEBUG: Response status: {response.status_code}')

    if response.status_code == 200:
        slots = response.json()

        logger.info(f'🔍 SLOT API RESPONSE: Received {len(slots) if isinstance(slots, list) else 0} slots')

        # Log first 3 slots with their health_services data
        if isinstance(slots, list) and len(slots) > 0:
            for i, slot in enumerate(slots[:3]):  # First 3 slots only
                health_services = slot.get("health_services", [])
                if health_services:
                    service = health_services[0]
                    logger.info(f'🔍 SLOT API RESPONSE: Slot {i+1} - Service: {service.get("name", "N/A")}, UUID: {service.get("uuid", "N/A")}, Price: {service.get("price", "N/A")}€, Cerba: {service.get("cerba_card_price", "N/A")}€')
                else:
                    logger.warning(f'🔍 SLOT API RESPONSE: Slot {i+1} - No health_services data!')

        print(f'🔍 SLOT FETCH DEBUG: ===== FULL API RESPONSE =====')
        print(f'🔍 SLOT FETCH DEBUG: Raw response: {slots}')
        print(f'🔍 SLOT FETCH DEBUG: Response type: {type(slots)}')

        if isinstance(slots, list):
            print(f'🔍 SLOT FETCH DEBUG: Number of slots returned: {len(slots)}')
            for i, slot in enumerate(slots):
                print(f'🔍 SLOT FETCH DEBUG: --- SLOT {i+1} ---')
                print(f'🔍 SLOT FETCH DEBUG: Full slot data: {slot}')
                print(f'🔍 SLOT FETCH DEBUG: start_time: {slot.get("start_time", "MISSING")}')
                print(f'🔍 SLOT FETCH DEBUG: end_time: {slot.get("end_time", "MISSING")}')
                print(f'🔍 SLOT FETCH DEBUG: providing_entity_availability_uuid: {slot.get("providing_entity_availability_uuid", "MISSING")}')
                print(f'🔍 SLOT FETCH DEBUG: health_services: {slot.get("health_services", "MISSING")}')
        else:
            print(f'🔍 SLOT FETCH DEBUG: Response is not a list: {slots}')

        print(f'🔍 SLOT FETCH DEBUG: ===== END RESPONSE =====')
        return slots  # Return the slots data
    else:
        logger.error(f'🔍 SLOT API ERROR: Status {response.status_code} - {response.text}')
        print(f'🔍 SLOT FETCH DEBUG: ❌ API Error: {response.status_code} - {response.text}')
        return []  # Return empty list on error


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

    # LOG REQUEST DETAILS
    logger.info(f'🔍 SLOT CREATE REQUEST: Making API request to: {api_url}')
    logger.info(f'🔍 SLOT CREATE REQUEST: Request data: {request_data}')
    logger.info(f'🔍 SLOT CREATE REQUEST: PEA: {pea}')
    logger.info(f'🔍 SLOT CREATE REQUEST: Start: {start_slot}, End: {end_slot}')

    response = requests.post(api_url, headers=headers, json=request_data)

    # LOG RESPONSE
    logger.info(f'🔍 SLOT CREATE RESPONSE: Status {response.status_code}')

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
    response = requests.delete(api_url, headers=headers)
    uuid_slot=""
    upd_at=""
    return response


#print(list_slot("6cff89d8-1f40-4eb8-bed7-f36e94a3355c","2025-10-24",['9a93d65f-396a-45e4-9284-94481bdd2b51', '7de81336-c7ce-4dad-a04b-ad4b2193113d'], start_time="2025-11-17 07:00:00+00", end_time="2025-11-17 17:00:00+00"))
#print(create_slot('2025-11-20 15:40:00','2025-11-20 15:55:00',"3a4c9547-bd61-4557-a1b5-f681b3d9da25"))
#print(create_slot('2025-10-27 11:25:00','2025-10-27 11:30:00',"d1bbc9cd-e7e8-4e1e-8075-b637824504a6"))
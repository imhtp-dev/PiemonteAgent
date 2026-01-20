"""
Sorting API Service
Handles calls to the health-service sorting API for optimized service packages
"""

import aiohttp
from loguru import logger
from typing import Dict, List, Any
from models.requests import HealthService
from services.auth import auth_service
from services.config import config
from utils.tracing import trace_api_call, add_span_attributes


@trace_api_call("api.sorting_call")
async def call_sorting_api(
    health_center_uuid: str,
    gender: str,
    date_of_birth: str,
    selected_services: List[HealthService],
    ambiente: str = None
) -> Dict[str, Any]:
    """
    Call the health-service sorting API to get optimized service packages

    This API analyzes the selected services and may return alternative packages
    that bundle services together or replace individual services with package deals.

    Args:
        health_center_uuid: UUID of the selected health center
        gender: Patient gender ('m' or 'f')
        date_of_birth: Patient date of birth in YYYYMMDD format (no dashes)
        selected_services: List of HealthService objects with sector field
        ambiente: API environment ('prod' or 'test'), defaults to 'prod'

    Returns:
        Dictionary with structure:
        {
            "success": True/False,
            "data": [...],  # API response data (if success)
            "error": "...",  # Error message (if failure)
            "package_detected": True/False,  # Whether center offers alternative package
            "original_services": [...],  # UUIDs requested
            "response_services": [...]  # UUIDs in response (may differ if package)
        }
    """
    # Add search params to span for debugging
    add_span_attributes({
        "sorting.center_uuid": health_center_uuid,
        "sorting.service_count": len(selected_services) if selected_services else 0,
        "sorting.gender": gender,
        "sorting.ambiente": ambiente or "prod"
    })

    logger.info("=" * 80)
    logger.info("🔄 SORTING API CALL INITIATED")
    logger.info("=" * 80)

    try:
        # Use default ambiente if not specified
        if ambiente is None:
            ambiente = "prod"  # Default to production environment

        # === STEP 1: Validate Input ===
        logger.info(f"📋 Input Validation:")
        logger.info(f"   Health Center UUID: {health_center_uuid}")
        logger.info(f"   Gender: {gender}")
        logger.info(f"   Date of Birth: {date_of_birth}")
        logger.info(f"   Number of Services: {len(selected_services)}")
        logger.info(f"   Environment: {ambiente}")

        if not health_center_uuid:
            logger.error("❌ Validation failed: health_center_uuid is empty")
            return {"success": False, "error": "Missing health center UUID"}

        if not selected_services:
            logger.error("❌ Validation failed: No services provided")
            return {"success": False, "error": "No services provided"}

        if gender not in ['m', 'f']:
            logger.warning(f"⚠️ Invalid gender '{gender}', defaulting to 'm'")
            gender = 'm'

        if len(date_of_birth) != 8 or not date_of_birth.isdigit():
            logger.error(f"❌ Validation failed: Invalid date_of_birth format '{date_of_birth}' (expected YYYYMMDD)")
            return {"success": False, "error": "Invalid date_of_birth format (expected YYYYMMDD)"}

        # === STEP 2: Organize Services by Sector ===
        logger.info("📊 Organizing services by sector...")

        sectors = {
            'health_services': [],
            'prescriptions': [],
            'preliminary_visits': [],
            'optionals': [],
            'opinions': []
        }

        for idx, service in enumerate(selected_services):
            # sector field is now required in HealthService model
            sector = service.sector

            logger.debug(f"   [{idx}] {service.name}")
            logger.debug(f"       UUID: {service.uuid}")
            logger.debug(f"       Code: {service.code}")
            logger.debug(f"       Sector: {sector}")

            if sector in sectors:
                sectors[sector].append(service.uuid)
            else:
                logger.warning(f"⚠️ Unknown sector '{sector}' for service {service.name}, adding to health_services")
                sectors['health_services'].append(service.uuid)

        # === STEP 3: Prepare API Request Data ===
        request_data = {
            'gender': gender,
            'date_of_birth': date_of_birth,
            'health_services': ','.join(sectors['health_services']) if sectors['health_services'] else '',
            'prescriptions': ','.join(sectors['prescriptions']) if sectors['prescriptions'] else '',
            'preliminary_visits': ','.join(sectors['preliminary_visits']) if sectors['preliminary_visits'] else '',
            'optionals': ','.join(sectors['optionals']) if sectors['optionals'] else '',
            'opinions': ','.join(sectors['opinions']) if sectors['opinions'] else ''
        }

        logger.info("📡 API Request Parameters:")
        logger.info(f"   gender: {request_data['gender']}")
        logger.info(f"   date_of_birth: {request_data['date_of_birth']}")
        logger.info(f"   health_services: {request_data['health_services']}")
        logger.info(f"   prescriptions: {request_data['prescriptions']}")
        logger.info(f"   preliminary_visits: {request_data['preliminary_visits']}")
        logger.info(f"   optionals: {request_data['optionals']}")
        logger.info(f"   opinions: {request_data['opinions']}")

        # === STEP 4: Get Authentication Token ===
        logger.info("🔐 Acquiring authentication token...")

        try:
            token = auth_service.get_token()
            logger.success("✅ Authentication token acquired")
        except Exception as e:
            logger.error(f"❌ Failed to acquire authentication token: {e}")
            return {"success": False, "error": f"Authentication failed: {str(e)}"}

        # === STEP 5: Make API Call ===
        api_url = f'https://3z0xh9v1f4.execute-api.eu-south-1.amazonaws.com/{ambiente}/amb/sort/health-center/{health_center_uuid}/health-service'

        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json',
        }

        logger.info(f"📡 Making API request to: {api_url}")

        async with aiohttp.ClientSession() as session:
            async with session.get(api_url, headers=headers, params=request_data, timeout=aiohttp.ClientTimeout(total=30)) as response:
                status = response.status
                logger.info(f"📊 API Response Status: {status}")

                if status == 200:
                    data = await response.json()
                    logger.success("✅ Sorting API call successful")

                    # === STEP 6: Analyze Response for Package Detection ===
                    logger.info("🔍 Analyzing API response for package detection...")

                    package_detected = False
                    requested_uuids = {service.uuid for service in selected_services}
                    response_uuids = set()

                    if data and isinstance(data, list) and len(data) > 0:
                        response_services = data[0].get('health_services', [])

                        logger.info(f"📋 Response contains {len(response_services)} services")

                        # Extract UUIDs from response
                        for service in response_services:
                            if isinstance(service, dict) and 'uuid' in service:
                                response_uuids.add(service['uuid'])

                        logger.info(f"🔍 Package Analysis:")
                        logger.info(f"   Requested UUIDs ({len(requested_uuids)}): {requested_uuids}")
                        logger.info(f"   Response UUIDs ({len(response_uuids)}): {response_uuids}")

                        # Check if UUIDs differ (package detected)
                        if requested_uuids != response_uuids:
                            package_detected = True
                            missing_in_response = requested_uuids - response_uuids
                            additional_in_response = response_uuids - requested_uuids

                            logger.info("🎁 PACKAGE DETECTED!")
                            logger.info(f"   Services missing in response: {missing_in_response}")
                            logger.info(f"   Additional services in response: {additional_in_response}")

                            # Log response service details
                            logger.info("📋 Services in package:")
                            for idx, service in enumerate(response_services):
                                if isinstance(service, dict):
                                    service_name = service.get('name', 'N/A')
                                    service_uuid = service.get('uuid', 'N/A')
                                    service_code = service.get('code', 'N/A')
                                    logger.info(f"   [{idx}] {service_name}")
                                    logger.info(f"        UUID: {service_uuid}")
                                    logger.info(f"        Code: {service_code}")
                        else:
                            logger.info("✅ No package detected - services match exactly")

                    logger.info("=" * 80)
                    logger.success("🎯 SORTING API CALL COMPLETED SUCCESSFULLY")
                    logger.info("=" * 80)

                    return {
                        "success": True,
                        "data": data,
                        "package_detected": package_detected,
                        "original_services": list(requested_uuids),
                        "response_services": list(response_uuids)
                    }

                elif status == 401:
                    # Authentication error - clear token and suggest retry
                    logger.error("❌ Authentication error (401) - token may be invalid")
                    auth_service.clear_token()

                    error_text = await response.text()
                    logger.error(f"   Response: {error_text}")

                    return {
                        "success": False,
                        "error": "Authentication failed",
                        "status_code": status,
                        "response_text": error_text,
                        "retry_suggested": True
                    }

                elif status == 404:
                    # Health center or service not found
                    logger.error(f"❌ Resource not found (404) - health center or service may not exist")
                    error_text = await response.text()
                    logger.error(f"   Response: {error_text}")

                    return {
                        "success": False,
                        "error": "Health center or service not found",
                        "status_code": status,
                        "response_text": error_text
                    }

                else:
                    # Other API error
                    error_text = await response.text()
                    logger.error("=" * 80)
                    logger.error(f"❌ SORTING API ERROR")
                    logger.error(f"   Status Code: {status}")
                    logger.error(f"   Response: {error_text}")
                    logger.error("=" * 80)

                    return {
                        "success": False,
                        "error": f"API returned status {status}",
                        "status_code": status,
                        "response_text": error_text
                    }

    except aiohttp.ClientError as e:
        logger.error("=" * 80)
        logger.error(f"❌ SORTING API NETWORK ERROR")
        logger.error(f"   Error Type: {type(e).__name__}")
        logger.error(f"   Error Message: {str(e)}")
        logger.error("=" * 80)
        return {
            "success": False,
            "error": f"Network error: {str(e)}",
            "error_type": "network"
        }

    except Exception as e:
        logger.error("=" * 80)
        logger.error(f"❌ SORTING API UNEXPECTED ERROR")
        logger.error(f"   Error Type: {type(e).__name__}")
        logger.error(f"   Error Message: {str(e)}")
        logger.error("=" * 80)
        logger.exception("Full traceback:")

        return {
            "success": False,
            "error": f"Unexpected error: {str(e)}",
            "error_type": "unexpected"
        }

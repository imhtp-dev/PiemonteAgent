"""
Google Maps Geocoding service for resolving patient addresses to Italian regions.
Used by sports medicine flow to map address → region → MDS group ID.

Key function: geocode_address() returns region (administrative_area_level_1)
plus corrected city name for voice-transcribed input.
"""

import os
import requests
from typing import Optional, Dict, Any
from loguru import logger


# Bounding box covering Piemonte + Lombardia (North Italy operational area)
# SW corner: ~44.0N, 6.6E (south-west Piemonte near Cuneo)
# NE corner: ~46.5N, 10.5E (north-east Lombardia near Sondrio)
NORTH_ITALY_BOUNDS = "44.0,6.6|46.5,10.5"


class GeocodingError(Exception):
    """Custom exception for geocoding errors"""
    pass


def _get_api_key() -> str:
    key = os.getenv("GOOGLE_MAPS_API_KEY", "")
    if not key:
        raise GeocodingError("GOOGLE_MAPS_API_KEY not set")
    return key


def _geocode_request(params: dict) -> Optional[dict]:
    """Make a geocoding API request, return first result or None."""
    try:
        response = requests.get(
            "https://maps.googleapis.com/maps/api/geocode/json",
            params=params,
            timeout=5
        )
        data = response.json()
        if data["status"] == "OK" and data["results"]:
            return data["results"][0]
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Geocoding request failed: {e}")
        return None


def _is_in_operational_area(lat: float, lng: float) -> bool:
    """Check if coordinates fall within Piemonte/Lombardia bounding box."""
    return 44.0 <= lat <= 46.5 and 6.6 <= lng <= 10.5


def _extract_component(components: list, comp_type: str) -> Optional[str]:
    """Extract a specific address component by type."""
    for comp in components:
        if comp_type in comp.get("types", []):
            return comp.get("long_name")
    return None


def geocode_address(address: str) -> Optional[Dict[str, Any]]:
    """Geocode a patient address and extract Italian region.

    Uses multiple strategies for voice-transcribed Italian addresses:
    1. Bias with Piemonte/Lombardia bounding box
    2. Append "Piemonte, Italia" context
    3. Append "Lombardia, Italia" context

    Returns:
        {
            "transcribed": original input,
            "corrected": corrected city/location name,
            "region": Italian region (administrative_area_level_1), e.g. "Lombardia",
            "full_address": formatted address,
            "lat": latitude,
            "lng": longitude,
        }
        or None if not resolved.
    """
    api_key = _get_api_key()

    base_params = {
        "address": address,
        "language": "it",
        "bounds": NORTH_ITALY_BOUNDS,
        "region": "it",
        "key": api_key
    }

    # Strategy 1: direct geocode with bounds bias
    result = _geocode_request(base_params)
    if result:
        lat = result["geometry"]["location"]["lat"]
        lng = result["geometry"]["location"]["lng"]
        if _is_in_operational_area(lat, lng):
            return _parse_result(address, result)

    # Strategy 2: append Piemonte context
    params2 = {**base_params, "address": f"{address}, Piemonte, Italia"}
    result = _geocode_request(params2)
    if result:
        return _parse_result(address, result)

    # Strategy 3: append Lombardia context
    params3 = {**base_params, "address": f"{address}, Lombardia, Italia"}
    result = _geocode_request(params3)
    if result:
        return _parse_result(address, result)

    logger.warning(f"Geocoding: Location not resolved: '{address}'")
    return None


def _parse_result(transcribed: str, result: dict) -> Dict[str, Any]:
    """Parse geocoding result into standardized dict with region."""
    components = result["address_components"]
    location = result["geometry"]["location"]

    # Extract city (most specific component)
    city = components[0]["long_name"] if components else transcribed

    # Extract region (administrative_area_level_1)
    region = _extract_component(components, "administrative_area_level_1")

    return {
        "transcribed": transcribed,
        "corrected": city,
        "region": region,
        "full_address": result.get("formatted_address", ""),
        "lat": location.get("lat"),
        "lng": location.get("lng"),
    }

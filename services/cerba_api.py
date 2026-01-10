"""
Cerba API service for making authenticated requests
Handles all interactions with the Cerba healthcare API
"""

import requests
import logging
from typing import Dict, List, Optional, Any
from fastapi import HTTPException

from services.config import config
from services.auth import auth_service
from models.requests import HealthService, HealthCenter

logger = logging.getLogger(__name__)

class CerbaAPIError(Exception):
    """Custom exception for Cerba API errors"""
    pass

class CerbaAPIService:
    """Service for interacting with Cerba API"""
    
    def __init__(self):
        self.base_url = config.CERBA_BASE_URL
    
    def _make_request(self, endpoint: str, params: Optional[Dict] = None) -> Any:
        """
        Make authenticated request to Cerba API
        
        Args:
            endpoint: API endpoint (e.g., "amb/health-center")
            params: Query parameters
            
        Returns:
            JSON response data
            
        Raises:
            CerbaAPIError: If request fails
        """
        try:
            token = auth_service.get_token()
            
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}"
            }
            
            url = f"{self.base_url}/{endpoint}"
            
            logger.debug(f"Making API request to: {url}")
            logger.debug(f"Request parameters: {params}")
            
            response = requests.get(
                url,
                headers=headers,
                params=params,
                timeout=config.REQUEST_TIMEOUT
            )
            
            logger.debug(f"API response status: {response.status_code}")
            logger.debug(f"Full request URL: {response.url}")
            
            # Handle specific error cases
            if response.status_code == 401:
                logger.warning("Authentication failed, clearing token cache")
                auth_service.clear_token()
                raise CerbaAPIError("Authentication failed - token may be expired")
            
            if response.status_code >= 400:
                error_msg = f"API request failed with status {response.status_code}"
                try:
                    error_details = response.json()
                    logger.error(f"{error_msg}: {error_details}")
                    raise CerbaAPIError(f"{error_msg}: {error_details}")
                except:
                    logger.error(f"{error_msg}: {response.text}")
                    raise CerbaAPIError(f"{error_msg}: {response.text}")
            
            response.raise_for_status()
            
            return response.json()
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Cerba API request failed: {e}")
            raise CerbaAPIError(f"API request failed: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error in API request: {e}")
            raise CerbaAPIError(f"Unexpected error: {str(e)}")
    
    def get_health_services(self, health_center: Optional[str] = None) -> List[HealthService]:
        """
        Get list of available ambulatory health services
        
        Args:
            health_center: Optional UUID of specific health center
            
        Returns:
            List of health services
        """
        params = {}
        if health_center:
            params["health_center"] = health_center
        
        try:
            response = self._make_request("amb/health-service", params)
            
            services = []
            for service_data in response:
                service = HealthService(
                    uuid=service_data["uuid"],
                    name=service_data["name"],
                    code=service_data["code"],
                    synonyms=service_data.get("synonyms", [])
                )
                services.append(service)
            
            logger.info(f"Retrieved {len(services)} health services")
            return services
            
        except Exception as e:
            logger.error(f"Failed to get health services: {e}")
            raise CerbaAPIError(f"Failed to retrieve health services: {str(e)}")
    
    def get_health_centers(
        self,
        health_services: List[str],
        gender: str,
        date_of_birth: str,
        address: str,
        health_services_availability: bool = True
    ) -> List[HealthCenter]:
        """
        Get health centers that provide specified services
        
        Args:
            health_services: List of service UUIDs
            gender: Patient gender ("m" or "f")
            date_of_birth: Date in YYYYMMDD format
            address: Location to search
            health_services_availability: Filter by availability
            
        Returns:
            List of health centers
        """
        # Validate required parameters
        if not all([health_services, gender, date_of_birth, address]):
            missing = []
            if not health_services: missing.append("health_services")
            if not gender: missing.append("gender")
            if not date_of_birth: missing.append("date_of_birth")
            if not address: missing.append("address")
            
            raise ValueError(f"Missing required parameters: {', '.join(missing)}")
        
        # Handle health_services parameter format
        # If it's a list, join with commas; if single string, use as-is
        if isinstance(health_services, list):
            health_services_param = ",".join(health_services)
        else:
            health_services_param = health_services
        
        params = {
            "health_services": health_services_param,
            "gender": gender,
            "date_of_birth": date_of_birth,
            "address": address,
            "health_services_availability": health_services_availability
        }
        
        try:
            response = self._make_request("amb/health-center", params)
            
            centers = []
            for center_data in response:
                center = HealthCenter(
                    uuid=center_data["uuid"],
                    name=center_data["name"],
                    address=f"{center_data['address']} {center_data['street_number']}, {center_data['city']}",
                    city=center_data["city"],
                    district=center_data["district"],
                    phone=center_data["phone"],
                    region=center_data["region"]
                )
                centers.append(center)
            
            logger.info(f"Found {len(centers)} health centers for {len(health_services)} services")
            return centers
            
        except Exception as e:
            logger.error(f"Failed to get health centers: {e}")
            raise CerbaAPIError(f"Failed to retrieve health centers: {str(e)}")

    def search_patient_by_phone(self, phone: str) -> List[Dict[str, Any]]:
        """
        Search for patients by phone number using Cerba API
        
        Args:
            phone: Patient's phone number (with or without country code)
            
        Returns:
            List of patient records matching the phone number.
            Each record contains: uuid, name, surname, fiscal_code, 
            date_of_birth, phone, email
            
        Raises:
            CerbaAPIError: If API request fails
        """
        if not phone:
            logger.warning("search_patient_by_phone called with empty phone")
            return []
        
        params = {"phone": phone}
        
        try:
            response = self._make_request("search/patient", params)
            
            # Response is an array of patient objects
            if isinstance(response, list):
                logger.info(f"Found {len(response)} patient(s) for phone: {phone[-4:] if len(phone) > 4 else '***'}")
                return response
            else:
                logger.warning(f"Unexpected response format from search/patient: {type(response)}")
                return []
                
        except CerbaAPIError as e:
            # Log but don't crash - patient not found is not an error
            if "404" in str(e) or "not found" in str(e).lower():
                logger.info(f"No patient found for phone: {phone[-4:] if len(phone) > 4 else '***'}")
                return []
            logger.error(f"Failed to search patient by phone: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error searching patient: {e}")
            raise CerbaAPIError(f"Failed to search patient: {str(e)}")

# Global API service instance
cerba_api = CerbaAPIService()


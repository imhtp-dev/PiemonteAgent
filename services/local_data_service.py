"""
Local Data Service - Alternative to Cerba API
Uses local JSON data from /data/all_services.json instead of external API
"""

import json
import os
from typing import List, Optional
from loguru import logger

from models.requests import HealthService


class LocalDataService:
    """Service for loading health services from local JSON data"""

    def __init__(self):
        # Docker-compatible path resolution
        # Try multiple path strategies for maximum compatibility
        self.data_file = self._resolve_data_file_path()
        self._services_cache: Optional[List[HealthService]] = None

    def _resolve_data_file_path(self) -> str:
        """
        Resolve data file path in a Docker-compatible way

        Returns:
            Absolute path to all_services.json
        """
        # Strategy 1: Environment variable (most reliable for Docker)
        env_data_path = os.getenv("DATA_FILE_PATH")
        if env_data_path and os.path.exists(env_data_path):
            logger.debug(f"🐳 Using DATA_FILE_PATH from environment: {env_data_path}")
            return env_data_path

        # Strategy 2: Relative to current working directory (Docker default)
        cwd_path = os.path.join(os.getcwd(), "data", "all_services.json")
        if os.path.exists(cwd_path):
            logger.debug(f"🐳 Using data file from working directory: {cwd_path}")
            return cwd_path

        # Strategy 3: Relative to project root (fallback)
        project_root = os.path.dirname(os.path.dirname(__file__))
        project_path = os.path.join(project_root, "data", "all_services.json")
        if os.path.exists(project_path):
            logger.debug(f"🐳 Using data file from project root: {project_path}")
            return project_path

        # Strategy 4: Absolute path in container (if mounted)
        container_path = "/app/data/all_services.json"
        if os.path.exists(container_path):
            logger.debug(f"🐳 Using data file from container mount: {container_path}")
            return container_path

        # Default fallback (original logic)
        fallback_path = os.path.join(project_root, "data", "all_services.json")
        logger.warning(f"⚠️ Data file not found, using fallback: {fallback_path}")
        return fallback_path

    def _load_services_from_file(self) -> List[HealthService]:
        """Load services from local JSON file"""
        try:
            logger.info(f"📁 Loading services from local file: {self.data_file}")

            with open(self.data_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            services = []
            services_data = data.get("services", [])

            for service_data in services_data:
                service = HealthService(
                    uuid=service_data["uuid"],
                    name=service_data["name"],
                    code=service_data["code"],
                    synonyms=service_data.get("synonyms", []),
                    sector="health_services"  # Primary services are always in health_services sector
                )
                services.append(service)

            logger.success(f"✅ Loaded {len(services)} services from local data")
            return services

        except FileNotFoundError:
            logger.error(f"❌ Local data file not found: {self.data_file}")
            return []
        except json.JSONDecodeError as e:
            logger.error(f"❌ Invalid JSON in local data file: {e}")
            return []
        except Exception as e:
            logger.error(f"❌ Error loading local data: {e}")
            return []

    def get_health_services(self, health_center: Optional[str] = None) -> List[HealthService]:
        """
        Get list of available ambulatory health services from local data

        Args:
            health_center: Optional UUID of specific health center (ignored for local data)

        Returns:
            List of health services
        """
        if self._services_cache is None:
            self._services_cache = self._load_services_from_file()

        logger.info(f"📋 Returning {len(self._services_cache)} services from local data")
        return self._services_cache.copy()  # Return copy to avoid modifications

    def get_service_count(self) -> int:
        """Get total number of services available"""
        services = self.get_health_services()
        return len(services)

    def search_services_by_name(self, search_term: str, limit: int = 10) -> List[HealthService]:
        """
        Simple search by service name (for testing)

        Args:
            search_term: Term to search for
            limit: Maximum number of results

        Returns:
            List of matching services
        """
        services = self.get_health_services()
        search_lower = search_term.lower()

        matching_services = []
        for service in services:
            # Check name
            if search_lower in service.name.lower():
                matching_services.append(service)
                continue

            # Check synonyms
            for synonym in service.synonyms:
                if search_lower in synonym.lower():
                    matching_services.append(service)
                    break

        logger.info(f"🔍 Found {len(matching_services)} services matching '{search_term}'")
        return matching_services[:limit]


# Create global instance
local_data_service = LocalDataService()


def test_local_data_service():
    """Test function to verify local data service works"""
    print("🧪 Testing Local Data Service")
    print("=" * 40)

    # Test 1: Load all services
    services = local_data_service.get_health_services()
    print(f"✅ Loaded {len(services)} services")

    # Test 2: Search for ECG services
    ecg_services = local_data_service.search_services_by_name("ECG", limit=5)
    print(f"\n🔍 ECG Services Found ({len(ecg_services)}):")
    for i, service in enumerate(ecg_services, 1):
        print(f"  {i}. {service.name} (Code: {service.code})")
        if service.synonyms:
            print(f"     Synonyms: {', '.join(service.synonyms[:3])}...")

    # Test 3: Search for radiology services
    rx_services = local_data_service.search_services_by_name("radiografia", limit=3)
    print(f"\n🔍 Radiografia Services Found ({len(rx_services)}):")
    for i, service in enumerate(rx_services, 1):
        print(f"  {i}. {service.name} (Code: {service.code})")

    print("\n✅ Local Data Service Test Complete")


if __name__ == "__main__":
    test_local_data_service()
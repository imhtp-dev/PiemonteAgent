"""
Configuration management for the application
Handles environment variables and app settings
"""

import os
from typing import Optional
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

class Config:
    """Application configuration"""
    
    # Cerba API Configuration
    CERBA_TOKEN_URL: str = os.getenv("CERBA_TOKEN_URL", "")
    CERBA_CLIENT_ID: str = os.getenv("CERBA_CLIENT_ID", "")
    CERBA_CLIENT_SECRET: str = os.getenv("CERBA_CLIENT_SECRET", "")
    CERBA_BASE_URL: str = os.getenv("CERBA_BASE_URL", "")
    
        
    # Server Configuration
    SERVER_URL: str = os.getenv("SERVER_URL", "")
    
    # Cache Configuration
    CACHE_EXPIRY_HOURS: int = int(os.getenv("CACHE_EXPIRY_HOURS", "1"))
    
    # Request Configuration
    REQUEST_TIMEOUT: int = int(os.getenv("REQUEST_TIMEOUT", "50"))
    
    # Service Search Configuration
    DEFAULT_SEARCH_LIMIT: int = int(os.getenv("DEFAULT_SEARCH_LIMIT", "5"))
    
    
    
    @classmethod
    def validate(cls) -> bool:
        """Validate that all required configuration is present"""
        # Cerba API credentials are optional when using local data service
        cerba_fields = [
            cls.CERBA_TOKEN_URL,
            cls.CERBA_CLIENT_ID,
            cls.CERBA_CLIENT_SECRET,
            cls.CERBA_BASE_URL
        ]

        # Check if any Cerba credentials are provided
        cerba_provided = any(field for field in cerba_fields)

        if cerba_provided:
            # If some Cerba credentials are provided, all must be provided
            missing_cerba = [name for field, name in zip(cerba_fields,
                           ['CERBA_TOKEN_URL', 'CERBA_CLIENT_ID', 'CERBA_CLIENT_SECRET', 'CERBA_BASE_URL'])
                           if not field]

            if missing_cerba:
                raise ValueError(f"Missing required Cerba API configuration: {missing_cerba}")
        else:
            # No Cerba credentials provided - using local data service
            import os
            logger = __import__('loguru').logger
            logger.info("🔧 Cerba API credentials not provided - using local data service")

        return True


# Initialize configuration
config = Config()
config.validate()
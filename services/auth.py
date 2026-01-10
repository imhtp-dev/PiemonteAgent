"""
Authentication service for Cerba API
Handles OAuth2 token management with automatic refresh
"""

import requests
import logging
from datetime import datetime, timedelta
from typing import Optional
from fastapi import HTTPException

from services.config import config

logger = logging.getLogger(__name__)

class AuthenticationError(Exception):
    """Custom exception for authentication errors"""
    pass

class AuthService:
    """Handles authentication with Cerba API"""
    
    def __init__(self):
        self._token: Optional[str] = None
        self._token_expiry: Optional[datetime] = None
    
    def get_token(self) -> str:
        """
        Get valid authentication token
        Automatically refreshes if expired or missing
        """
        if self._is_token_valid():
            logger.debug("Using existing valid token")
            return self._token
        
        logger.info("Refreshing authentication token")
        return self._refresh_token()
    
    def _is_token_valid(self) -> bool:
        """Check if current token is valid and not expired"""
        if not self._token or not self._token_expiry:
            return False
        
        # Add 5 minute buffer before expiry
        return datetime.now() < (self._token_expiry - timedelta(minutes=5))
    
    def _refresh_token(self) -> str:
        """Refresh the authentication token from Cerba API"""
        headers = {
            "Content-Type": "application/x-www-form-urlencoded"
        }
        
        data = {
            "client_id": config.CERBA_CLIENT_ID,
            "client_secret": config.CERBA_CLIENT_SECRET,
            "grant_type": "client_credentials",
            "scope": "voila/api"
        }
        
        try:
            logger.debug(f"Making token request to: {config.CERBA_TOKEN_URL}")
            
            response = requests.post(
                config.CERBA_TOKEN_URL,
                headers=headers,
                data=data,
                timeout=config.REQUEST_TIMEOUT
            )
            
            response.raise_for_status()
            
            token_data = response.json()
            
            # Validate response structure
            if "access_token" not in token_data:
                raise AuthenticationError("Invalid token response: missing access_token")
            
            self._token = token_data["access_token"]
            expires_in = token_data.get("expires_in", 3600)
            
            # Set expiry with 5 minute safety buffer
            self._token_expiry = datetime.now() + timedelta(seconds=expires_in - 300)
            
            logger.info(f"Token refreshed successfully. Expires at: {self._token_expiry}")
            return self._token
            
        except requests.exceptions.Timeout:
            logger.error("Token request timed out")
            raise AuthenticationError("Authentication request timed out")
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Token request failed: {e}")
            raise AuthenticationError(f"Authentication failed: {str(e)}")
            
        except Exception as e:
            logger.error(f"Unexpected error during authentication: {e}")
            raise AuthenticationError(f"Authentication error: {str(e)}")
    
    def clear_token(self):
        """Clear stored token (for testing or forced refresh)"""
        self._token = None
        self._token_expiry = None
        logger.info("Authentication token cleared")

# Global auth service instance
auth_service = AuthService()
"""
Response models for API endpoints
"""

from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime

class HealthServiceResponse(BaseModel):
    """Response model for health service data"""
    uuid: str
    name: str
    code: str
    synonyms: List[str] = Field(default_factory=list)

class HealthCenterResponse(BaseModel):
    """Response model for health center data"""
    uuid: str
    name: str
    address: str
    city: str
    district: str
    phone: str
    region: str

class ServiceSearchResponse(BaseModel):
    """Response model for service search results"""
    found: bool
    count: int
    services: List[HealthServiceResponse]
    service_uuids: List[str] = Field(default_factory=list)
    search_term: str
    message: Optional[str] = None

class ToolCallResult(BaseModel):
    """Individual tool call result"""
    toolCallId: str
    result: Dict[str, Any]


class HealthCheckResponse(BaseModel):
    """Health check endpoint response"""
    status: str
    timestamp: str
    version: str

class AuthTestResponse(BaseModel):
    """Authentication test response"""
    status: str
    token_received: bool
    token_length: int
    token_expiry: Optional[str] = None

class ConfigTestResponse(BaseModel):
    """Configuration test response"""
    status: str
    cerba_base_url: str
    cache_expiry_hours: int
    default_search_limit: int
    error: Optional[str] = None

class ErrorResponse(BaseModel):
    """Standard error response"""
    error: str
    detail: Optional[str] = None
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())

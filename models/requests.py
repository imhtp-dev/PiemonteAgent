"""
Pydantic models for request and response validation
"""

from pydantic import BaseModel, Field
from typing import List, Optional



class HealthCenterRequest(BaseModel):
    """Model for health center search requests"""
    health_services: List[str] = Field(..., description="List of health service UUIDs")
    gender: str = Field(..., pattern="^[mf]$", description="Patient gender: 'm' or 'f'")
    date_of_birth: str = Field(..., pattern="^\\d{8}$", description="Date of birth in YYYYMMDD format")
    address: str = Field(..., description="Address or city to search")
    health_services_availability: bool = Field(default=True)

class ServiceSearchRequest(BaseModel):
    """Model for service search requests"""
    search_term: str = Field(..., min_length=1, description="Search term for health services")
    limit: int = Field(default=5, ge=1, le=20, description="Maximum number of results")

class HealthService(BaseModel):
    """Model for health service data"""
    uuid: str
    name: str
    code: str
    synonyms: List[str] = Field(default_factory=list)
    sector: str = Field(..., description="Service sector: health_services, prescriptions, preliminary_visits, optionals, opinions")

class HealthCenter(BaseModel):
    """Model for health center data"""
    uuid: str
    name: str
    address: str
    city: str
    district: str
    phone: str
    region: str

class ServiceSearchResponse(BaseModel):
    """Model for service search response"""
    found: bool
    count: int
    services: List[HealthService]
    search_term: str
    message: Optional[str] = None
    auto_book: bool = Field(default=False, description="Whether to auto-book the top service")
    confidence_score: Optional[float] = Field(default=None, description="Confidence score of top result")

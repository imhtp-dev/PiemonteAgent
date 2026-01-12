"""
Pricing Service
Handles Agonisticaand non-Agonisticasports medicine visit pricing
"""

import json
import asyncio
import aiohttp
from typing import Optional
from dataclasses import dataclass
from loguru import logger

from config.settings import settings
from utils.tracing import trace_api_call, add_span_attributes


@dataclass
class PriceResult:
    """Result from pricing API query"""
    price: float
    visit_type: str
    currency: str = "EUR"
    success: bool = True
    error: Optional[str] = None


class PricingService:
    """Service for getting sports medicine visit prices"""
    
    def __init__(self):
        self.agonistic_url = settings.info_api_endpoints["price_agonistic"]
        self.non_agonistic_url = settings.info_api_endpoints["price_non_agonistic"]
        self.timeout = settings.api_timeout
        self.session: Optional[aiohttp.ClientSession] = None
        logger.info(f"💰 Pricing Service initialized")
        logger.debug(f"💰 Agonistica URL: {self.agonistic_url}")
        logger.debug(f"💰 Non-Agonistica URL: {self.non_agonistic_url}")
    
    async def initialize(self):
        """Initialize HTTP session"""
        if not self.session:
            self.session = aiohttp.ClientSession()
            logger.debug("💰 HTTP session created for pricing service")
    
    @trace_api_call("api.pricing_competitive")
    async def get_competitive_price(
        self,
        age: int,
        gender: str,
        sport: str,
        region: str
    ) -> PriceResult:
        """
        Get price for Agonistica(agonistic) sports medicine visit
        
        Args:
            age: Patient age in years
            gender: Patient gender - "M" for Male, "F" for Female
            sport: Sport practiced by patient
            region: Italian region where visit will be performed
            
        Returns:
            PriceResult with price and visit type
        """
        try:
            await self.initialize()
            
            # Normalize gender to uppercase
            gender = gender.upper()
            if gender not in ["M", "F"]:
                logger.warning(f"⚠️ Invalid gender '{gender}', defaulting to M")
                gender = "M"
            
            logger.info(f"💰 Getting Agonistica price: age={age}, gender={gender}, sport={sport}, region={region}")

            # VAPI-compatible request format
            request_data = {
                "message": {
                    "toolCallList": [
                        {
                            "toolCallId": "pipecat_agonistic_pricing",
                            "function": {
                                "name": "get_price_agonistic_visit",
                                "arguments": json.dumps({
                                    "age": age,
                                    "gender": gender,
                                    "sport": sport,
                                    "region": region
                                })
                            }
                        }
                    ]
                }
            }

            async with self.session.post(
                self.agonistic_url,
                json=request_data,
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            ) as response:
                response.raise_for_status()
                data = await response.json()

                # Debug: Log full API response
                logger.debug(f"🔍 API Response: {data}")

                # API returns {'results': [{'toolCallId': '...', 'result': {...}}]}
                results = data.get("results", [])

                if not results or len(results) == 0:
                    logger.warning("⚠️ API returned empty results")
                    return PriceResult(
                        price=0.0,
                        visit_type="Error",
                        success=False,
                        error="No results found"
                    )

                # Extract the pricing data from results[0]["result"]
                # Note: result is a dict object (not JSON string) for pricing API
                pricing_data = results[0].get("result", {})

                if not pricing_data:
                    logger.warning("⚠️ API returned empty pricing data")
                    return PriceResult(
                        price=0.0,
                        visit_type="Error",
                        success=False,
                        error="Empty pricing data"
                    )

                price = pricing_data.get("price", 0.0)
                visit_type = pricing_data.get("type_visit", "Agonistic Visit")

                logger.success(f"✅ Agonistica price: €{price} (visit type: {visit_type})")

                return PriceResult(
                    price=price,
                    visit_type=visit_type,
                    currency="EUR",
                    success=True
                )
                
        except aiohttp.ClientResponseError as e:
            logger.error(f"❌ Agonistica price API error {e.status}: {e.message}")
            return PriceResult(
                price=0.0,
                visit_type="Error",
                success=False,
                error=f"API error: {e.status}"
            )
        
        except asyncio.TimeoutError:
            logger.error(f"❌ Agonistica price query timeout after {self.timeout}s")
            return PriceResult(
                price=0.0,
                visit_type="Timeout",
                success=False,
                error="Timeout"
            )
        
        except Exception as e:
            logger.error(f"❌ Agonisticaprice query failed: {e}")
            return PriceResult(
                price=0.0,
                visit_type="Error",
                success=False,
                error=str(e)
            )
    
    @trace_api_call("api.pricing_non_competitive")
    async def get_non_competitive_price(
        self,
        ecg_under_stress: bool
    ) -> PriceResult:
        """
        Get price for non-Agonistica(non-agonistic) sports medicine visit
        
        Args:
            ecg_under_stress: Whether ECG under stress is needed (True) or standard ECG (False)
            
        Returns:
            PriceResult with price
        """
        try:
            await self.initialize()
            
            logger.info(f"💰 Getting non-Agonistica price: ECG under stress={ecg_under_stress}")

            # VAPI-compatible request format
            request_data = {
                "message": {
                    "toolCallList": [
                        {
                            "toolCallId": "pipecat_non_agonistic_pricing",
                            "function": {
                                "name": "get_price_non_agonistic_visit",
                                "arguments": json.dumps({"ecg_under_stress": ecg_under_stress})
                            }
                        }
                    ]
                }
            }

            async with self.session.post(
                self.non_agonistic_url,
                json=request_data,
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            ) as response:
                response.raise_for_status()
                data = await response.json()

                # Debug: Log full API response
                logger.debug(f"🔍 API Response: {data}")

                # API returns {'results': [{'toolCallId': '...', 'result': {...}}]}
                results = data.get("results", [])

                if not results or len(results) == 0:
                    logger.warning("⚠️ API returned empty results")
                    return PriceResult(
                        price=0.0,
                        visit_type="Error",
                        success=False,
                        error="No results found"
                    )

                # Extract the pricing data from results[0]["result"]
                # Note: result is a dict object (not JSON string) for pricing API
                pricing_data = results[0].get("result", {})

                if not pricing_data:
                    logger.warning("⚠️ API returned empty pricing data")
                    return PriceResult(
                        price=0.0,
                        visit_type="Error",
                        success=False,
                        error="Empty pricing data"
                    )

                price = pricing_data.get("price", 0.0)
                visit_type = pricing_data.get("type_visit", "Non-Agonistic Visit")

                logger.success(f"✅ Non-Agonistica price: €{price}")

                return PriceResult(
                    price=price,
                    visit_type=visit_type,
                    currency="EUR",
                    success=True
                )
                
        except aiohttp.ClientResponseError as e:
            logger.error(f"❌ Non-Agonisticaprice API error {e.status}: {e.message}")
            return PriceResult(
                price=0.0,
                visit_type="Error",
                success=False,
                error=f"API error: {e.status}"
            )
        
        except asyncio.TimeoutError:
            logger.error(f"❌ Non-Agonistica price query timeout after {self.timeout}s")
            return PriceResult(
                price=0.0,
                visit_type="Timeout",
                success=False,
                error="Timeout"
            )
        
        except Exception as e:
            logger.error(f"❌ Non-Agonistica price query failed: {e}")
            return PriceResult(
                price=0.0,
                visit_type="Error",
                success=False,
                error=str(e)
            )
    
    async def cleanup(self):
        """Close HTTP session"""
        if self.session:
            await self.session.close()
            logger.debug("💰 HTTP session closed for pricing service")
            self.session = None


# Global instance
pricing_service = PricingService()
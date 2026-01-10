"""
Clinic Information Service
Provides clinic hours, locations, summer closures, blood collection times via call_graph API
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
class ClinicInfoResult:
    """Result from clinic information query"""
    answer: str
    success: bool = True
    error: Optional[str] = None


class ClinicInfoService:
    """Service for getting clinic information"""
    
    def __init__(self):
        self.api_url = settings.info_api_endpoints["call_graph_lombardia"]
        self.timeout = settings.api_timeout
        self.session: Optional[aiohttp.ClientSession] = None
        logger.info(f"🏥 Clinic Info Service initialized: {self.api_url}")
    
    async def initialize(self):
        """Initialize HTTP session"""
        if not self.session:
            self.session = aiohttp.ClientSession()
            logger.debug("🏥 HTTP session created for clinic info service")
    
    @trace_api_call("api.clinic_info_call_graph")
    async def get_clinic_info(
        self,
        query: str
    ) -> ClinicInfoResult:
        """
        Get clinic information using call_graph API

        Args:
            query: Natural language query including location (e.g., 'orari della sede di Biella', 'chiusure estive Novara')

        Returns:
            ClinicInfoResult with answer
        """
        try:
            await self.initialize()

            logger.info(f"🏥 Getting clinic info: '{query}'")

            # VAPI-compatible request format
            request_data = {
                "message": {
                    "toolCallList": [
                        {
                            "toolCallId": "pipecat_clinic_info",
                            "function": {
                                "name": "call_graph_lombardia",
                                "arguments": json.dumps({"q": query})
                            }
                        }
                    ]
                }
            }

            async with self.session.post(
                self.api_url,
                json=request_data,
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=self.timeout)
            ) as response:
                response.raise_for_status()
                data = await response.json()

                # Debug: Log full API response
                logger.debug(f"🔍 API Response: {data}")

                # API returns {'results': [{'toolCallId': '...', 'result': '...'}]}
                results = data.get("results", [])

                if not results or len(results) == 0:
                    logger.warning("⚠️ API returned empty results")
                    return ClinicInfoResult(
                        answer="",
                        success=False,
                        error="No results found"
                    )

                # Extract the actual answer from results[0]["result"]
                answer = results[0].get("result", "") if results else ""

                if not answer:
                    logger.warning("⚠️ API returned empty answer")
                    return ClinicInfoResult(
                        answer="",
                        success=False,
                        error="Empty answer"
                    )

                logger.success(f"✅ Clinic info retrieved")
                logger.debug(f"🏥 Answer preview: {answer[:200]}...")

                return ClinicInfoResult(
                    answer=answer,
                    success=True
                )
                
        except aiohttp.ClientResponseError as e:
            logger.error(f"❌ Clinic info API error {e.status}: {e.message}")
            return ClinicInfoResult(
                answer="Mi dispiace, non riesco a recuperare le informazioni sulla clinica. Vuoi parlare con un operatore?",
                success=False,
                error=f"API error: {e.status}"
            )

        except asyncio.TimeoutError:
            logger.error(f"❌ Clinic info query timeout after {self.timeout}s")
            return ClinicInfoResult(
                answer="Mi dispiace, la ricerca sta richiedendo troppo tempo. Vuoi parlare con un operatore?",
                success=False,
                error="Timeout"
            )

        except Exception as e:
            logger.error(f"❌ Clinic info query failed: {e}")
            return ClinicInfoResult(
                answer="Mi dispiace, ho riscontrato un errore. Vuoi parlare con un operatore?",
                success=False,
                error=str(e)
            )
    
    async def cleanup(self):
        """Close HTTP session"""
        if self.session:
            await self.session.close()
            logger.debug("🏥 HTTP session closed for clinic info service")
            self.session = None


# Global instance
clinic_info_service = ClinicInfoService()
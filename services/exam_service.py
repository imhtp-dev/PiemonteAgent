"""
Exam Service
Provides exam requirements for sports medicine visits by visit type or sport
"""

import json
import asyncio
import aiohttp
from typing import Optional, List, Dict, Any
from dataclasses import dataclass
from loguru import logger

from config.settings import settings
from utils.tracing import trace_api_call, add_span_attributes


@dataclass
class ExamResult:
    """Result from exam list query"""
    exams: List[str]
    visit_type: Optional[str] = None
    sport: Optional[str] = None
    visit_code: Optional[str] = None  # Visit code from API (B1, B2, etc.)
    success: bool = True
    error: Optional[str] = None  # Complete API result object


class ExamService:
    """Service for getting exam requirements for sports medicine visits"""
    
    def __init__(self):
        self.exam_by_visit_url = settings.info_api_endpoints["exam_by_visit"]
        self.exam_by_sport_url = settings.info_api_endpoints["exam_by_sport"]
        self.timeout = settings.api_timeout
        self.session: Optional[aiohttp.ClientSession] = None
        logger.info(f"🔬 Exam Service initialized")
        logger.debug(f"🔬 Exam by visit URL: {self.exam_by_visit_url}")
        logger.debug(f"🔬 Exam by sport URL: {self.exam_by_sport_url}")
    
    async def initialize(self):
        """Initialize HTTP session"""
        if not self.session:
            self.session = aiohttp.ClientSession()
            logger.debug("🔬 HTTP session created for exam service")
    
    @trace_api_call("api.exam_by_visit_type")
    async def get_exams_by_visit_type(
        self,
        visit_type: str
    ) -> ExamResult:
        """
        Get list of examinations required for a specific visit type
        
        Args:
            visit_type: Type of visit (A1, A2, A3, B1, B2, B3, B4, B5)
            
        Returns:
            ExamResult with list of exams
        """
        try:
            await self.initialize()
            
            # Validate visit type
            valid_types = ["A1", "A2", "A3", "B1", "B2", "B3", "B4", "B5"]
            visit_type = visit_type.upper()
            
            if visit_type not in valid_types:
                logger.warning(f"⚠️ Invalid visit type '{visit_type}'")
                return ExamResult(
                    exams=[],
                    visit_type=visit_type,
                    success=False,
                    error=f"Invalid visit type. Must be one of: {', '.join(valid_types)}"
                )
            
            logger.info(f"🔬 Getting exams for visit type: {visit_type}")

            # VAPI-compatible request format
            request_data = {
                "message": {
                    "toolCallList": [
                        {
                            "toolCallId": "pipecat_exam_by_visit",
                            "function": {
                                "name": "get_list_exam_by_visit",
                                "arguments": json.dumps({"visit": visit_type})
                            }
                        }
                    ]
                }
            }

            async with self.session.post(
                self.exam_by_visit_url,
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
                    return ExamResult(
                        exams=[],
                        visit_type=visit_type,
                        success=False,
                        error="No results found"
                    )

                # Extract the exam data from results[0]["result"]
                exam_data = results[0].get("result", {})

                if not exam_data:
                    logger.warning("⚠️ API returned empty exam data")
                    return ExamResult(
                        exams=[],
                        visit_type=visit_type,
                        success=False,
                        error="Empty exam data"
                    )

                # API may return error string instead of dict
                if isinstance(exam_data, str):
                    logger.error(f"❌ API returned error string: {exam_data}")
                    return ExamResult(
                        exams=[],
                        visit_type=visit_type,
                        success=False,
                        error=exam_data
                    )

                # Extract fields from API response
                exams = exam_data.get("preparations", [])
                visit_code = exam_data.get("visita")  # Extract visit code (B1, B2, etc.)

                logger.success(f"✅ Found {len(exams)} exams for visit type {visit_type} (code: {visit_code})")
                logger.debug(f"🔬 Exams: {', '.join(exams)}")

                return ExamResult(
                    exams=exams,
                    visit_type=visit_type,
                    visit_code=visit_code,
                    success=True
                )
                
        except aiohttp.ClientResponseError as e:
            logger.error(f"❌ Exam by visit API error {e.status}: {e.message}")
            return ExamResult(
                exams=[],
                visit_type=visit_type,
                success=False,
                error=f"API error: {e.status}"
            )
        
        except asyncio.TimeoutError:
            logger.error(f"❌ Exam by visit query timeout after {self.timeout}s")
            return ExamResult(
                exams=[],
                visit_type=visit_type,
                success=False,
                error="Timeout"
            )
        
        except Exception as e:
            logger.error(f"❌ Exam by visit query failed: {e}")
            return ExamResult(
                exams=[],
                visit_type=visit_type,
                success=False,
                error=str(e)
            )
    
    @trace_api_call("api.exam_by_sport")
    async def get_exams_by_sport(
        self,
        sport: str
    ) -> ExamResult:
        """
        Get list of examinations required for a specific sport
        
        Args:
            sport: Name of the sport (e.g., 'calcio', 'basket', 'nuoto')
            
        Returns:
            ExamResult with list of exams
        """
        try:
            await self.initialize()
            
            logger.info(f"🔬 Getting exams for sport: {sport}")

            # VAPI-compatible request format
            request_data = {
                "message": {
                    "toolCallList": [
                        {
                            "toolCallId": "pipecat_exam_by_sport",
                            "function": {
                                "name": "get_list_exam_by_sport",
                                "arguments": json.dumps({"sport": sport})
                            }
                        }
                    ]
                }
            }

            async with self.session.post(
                self.exam_by_sport_url,
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
                    return ExamResult(
                        exams=[],
                        sport=sport,
                        success=False,
                        error="No results found"
                    )

                # Extract the exam data from results[0]["result"]
                exam_data = results[0].get("result", {})

                if not exam_data:
                    logger.warning("⚠️ API returned empty exam data")
                    return ExamResult(
                        exams=[],
                        sport=sport,
                        success=False,
                        error="Empty exam data"
                    )

                # API may return error string instead of dict
                if isinstance(exam_data, str):
                    logger.error(f"❌ API returned error string: {exam_data}")
                    return ExamResult(
                        exams=[],
                        sport=sport,
                        success=False,
                        error=exam_data
                    )

                # Extract fields from API response
                exams = exam_data.get("preparations", [])
                visit_code = exam_data.get("visita")  # Extract visit code (B1, B2, etc.)

                logger.success(f"✅ Found {len(exams)} exams for sport '{sport}' (code: {visit_code})")
                logger.debug(f"🔬 Exams: {', '.join(exams)}")

                return ExamResult(
                    exams=exams,
                    sport=sport,
                    visit_code=visit_code,
                    success=True
                )
                
        except aiohttp.ClientResponseError as e:
            logger.error(f"❌ Exam by sport API error {e.status}: {e.message}")
            return ExamResult(
                exams=[],
                sport=sport,
                success=False,
                error=f"API error: {e.status}"
            )
        
        except asyncio.TimeoutError:
            logger.error(f"❌ Exam by sport query timeout after {self.timeout}s")
            return ExamResult(
                exams=[],
                sport=sport,
                success=False,
                error="Timeout"
            )
        
        except Exception as e:
            logger.error(f"❌ Exam by sport query failed: {e}")
            return ExamResult(
                exams=[],
                sport=sport,
                success=False,
                error=str(e)
            )
    
    async def cleanup(self):
        """Close HTTP session"""
        if self.session:
            await self.session.close()
            logger.debug("🔬 HTTP session closed for exam service")
            self.session = None


# Global instance
exam_service = ExamService()
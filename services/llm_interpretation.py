"""
LLM-based interpretation service for sorting API responses

This module uses LLM to intelligently interpret sorting API responses
and determine the appropriate booking scenario (bundle/combined/separate).
"""

import json
from typing import Dict, List, Any, Optional
from loguru import logger
from openai import AsyncOpenAI


# System prompt for LLM interpretation
SORTING_INTERPRETATION_SYSTEM_PROMPT = """You are an expert at analyzing healthcare appointment booking scenarios.

Your task is to determine the correct booking scenario based on the sorting API response structure.

BOOKING SCENARIOS:

1. **BUNDLE** - Multiple services booked together in ONE appointment
   - Characteristics: Single group with multiple services, group=true
   - Example: Blood test + Urinalysis booked together in one visit
   - Booking: ONE appointment slot with ALL services included
   - User Experience: Patient visits once for all services

2. **COMBINED** - Multiple services replaced with ONE combined service
   - Characteristics: Single group with one or more combined services, group=false
   - Example: Three separate tests replaced by "Complete Health Package" service
   - Booking: ONE appointment slot with the combined service
   - User Experience: Patient visits once for the combined package

3. **SEPARATE** - Multiple separate appointments needed
   - Characteristics: Multiple groups (each may contain one or more services)
   - Example: X-ray (group 1) + Blood test (group 2) must be booked separately
   - Booking: SEPARATE appointment slots for each group
   - User Experience: Patient makes multiple visits on different days/times

ANALYSIS RULES:
- If there is ONLY ONE group AND group=true → BUNDLE
- If there is ONLY ONE group AND group=false → COMBINED
- If there are MULTIPLE groups (regardless of individual group values) → SEPARATE

IMPORTANT:
- Analyze the structure carefully
- Provide clear, concise reasoning
- Be precise about the number of appointments needed
- Consider the patient experience in your summary

You must provide:
1. The correct booking scenario (bundle/combined/separate)
2. Clear reasoning explaining your decision
3. Number of appointments that will be needed
4. Brief summary of what will be booked"""


def format_group_details(service_groups: List[Dict]) -> str:
    """
    Format service groups for LLM context

    Args:
        service_groups: List of parsed service groups

    Returns:
        Formatted string with group details
    """
    details = []
    for idx, group in enumerate(service_groups):
        services = group.get("services", [])
        is_group = group.get("is_group", False)

        service_names = [s.name for s in services]
        details.append(
            f"Group {idx + 1}:\n"
            f"  - Services: {', '.join(service_names)}\n"
            f"  - Number of services: {len(services)}\n"
            f"  - group field: {is_group}"
        )

    return "\n\n".join(details)


async def interpret_sorting_scenario(
    api_response_data: List[Dict],
    service_groups: List[Dict],
    openai_api_key: str
) -> Dict[str, Any]:
    """
    Use LLM to interpret sorting API response and determine booking scenario

    Args:
        api_response_data: Raw API response from sorting API
        service_groups: Parsed service groups structure
        openai_api_key: OpenAI API key

    Returns:
        {
            "booking_scenario": "bundle|combined|separate",
            "reasoning": "Explanation of decision",
            "num_appointments": int,
            "service_summary": "Brief summary",
            "success": bool,
            "error": Optional[str]
        }

    Raises:
        Exception: If LLM interpretation fails
    """
    try:
        logger.info("=" * 80)
        logger.info("🤖 LLM INTERPRETATION: Starting analysis")
        logger.info(f"   API Response Groups: {len(service_groups)}")
        logger.info(f"   Total Services: {sum(len(g['services']) for g in service_groups)}")

        # Format the user prompt with response details
        group_details = format_group_details(service_groups)

        user_prompt = f"""Analyze this sorting API response and determine the booking scenario:

API Response Structure:
```json
{json.dumps(api_response_data, indent=2)}
```

Number of groups: {len(service_groups)}

Detailed Group Information:
{group_details}

Based on the ANALYSIS RULES provided, determine:
1. The correct booking scenario (bundle/combined/separate)
2. Clear reasoning for your decision
3. Number of appointments needed
4. Brief summary of what will be booked for the patient"""

        # Create OpenAI client
        client = AsyncOpenAI(api_key=openai_api_key)

        # Define function schema for structured output
        function_schema = {
            "name": "interpret_sorting_scenario",
            "description": "Interpret the sorting API response and determine the booking scenario",
            "parameters": {
                "type": "object",
                "properties": {
                    "booking_scenario": {
                        "type": "string",
                        "enum": ["bundle", "combined", "separate"],
                        "description": "The determined booking scenario based on analysis rules"
                    },
                    "reasoning": {
                        "type": "string",
                        "description": "Clear explanation of why this scenario was chosen, referencing the analysis rules"
                    },
                    "num_appointments": {
                        "type": "integer",
                        "description": "Number of separate appointments that will be needed (1 for bundle/combined, N for separate)"
                    },
                    "service_summary": {
                        "type": "string",
                        "description": "Brief summary of what services will be booked and how (e.g., '2 X-ray services in one appointment' or '3 separate appointments for different services')"
                    }
                },
                "required": ["booking_scenario", "reasoning", "num_appointments", "service_summary"]
            }
        }

        # Call LLM with function calling
        logger.debug("🔄 Calling OpenAI API for interpretation...")

        response = await client.chat.completions.create(
            model="gpt-4.1",  # Full GPT-4.1 model
            temperature=0.1,  # Low temperature for consistent logic
            max_tokens=500,
            timeout=15.0,  # 15 second timeout
            messages=[
                {"role": "system", "content": SORTING_INTERPRETATION_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt}
            ],
            tools=[{
                "type": "function",
                "function": function_schema
            }],
            tool_choice={"type": "function", "function": {"name": "interpret_sorting_scenario"}}
        )

        # Extract function call result
        message = response.choices[0].message

        if not message.tool_calls:
            error_msg = "LLM did not return function call"
            logger.error(f"❌ {error_msg}")
            raise ValueError(error_msg)

        tool_call = message.tool_calls[0]
        function_args = json.loads(tool_call.function.arguments)

        booking_scenario = function_args.get("booking_scenario")
        reasoning = function_args.get("reasoning")
        num_appointments = function_args.get("num_appointments")
        service_summary = function_args.get("service_summary")

        # Validate response
        if booking_scenario not in ["bundle", "combined", "separate"]:
            error_msg = f"Invalid booking scenario from LLM: {booking_scenario}"
            logger.error(f"❌ {error_msg}")
            raise ValueError(error_msg)

        if not reasoning or not service_summary:
            error_msg = "LLM response missing required fields"
            logger.error(f"❌ {error_msg}")
            raise ValueError(error_msg)

        # Log successful interpretation
        logger.info("=" * 80)
        logger.info("🤖 LLM INTERPRETATION: Complete")
        logger.info(f"   Scenario: {booking_scenario.upper()}")
        logger.info(f"   Appointments: {num_appointments}")
        logger.info(f"   Reasoning: {reasoning}")
        logger.info(f"   Summary: {service_summary}")
        logger.info("=" * 80)

        return {
            "booking_scenario": booking_scenario,
            "reasoning": reasoning,
            "num_appointments": num_appointments,
            "service_summary": service_summary,
            "success": True,
            "error": None
        }

    except Exception as e:
        error_msg = f"LLM interpretation failed: {str(e)}"
        logger.error("=" * 80)
        logger.error(f"❌ LLM INTERPRETATION ERROR: {error_msg}")
        logger.error("=" * 80)

        # Re-raise the exception - no fallback, fail explicitly
        raise Exception(error_msg) from e

"""
Timezone conversion utilities for healthcare booking system
Handles conversion between UTC (database/API) and Italian local time (user display)
"""

from datetime import datetime
from zoneinfo import ZoneInfo
from loguru import logger
from typing import Optional


def utc_to_italian_display(utc_datetime_str: str) -> Optional[str]:
    """
    Convert UTC datetime from API to Italian local time for user display

    Args:
        utc_datetime_str: UTC datetime string like "2025-11-08T09:55:00+00:00"

    Returns:
        Italian time string like "2025-11-08 10:55" or None if conversion fails
    """
    try:
        # Parse the UTC datetime
        dt_utc = datetime.fromisoformat(utc_datetime_str)

        # Convert to Italian timezone (handles DST automatically)
        dt_italian = dt_utc.astimezone(ZoneInfo("Europe/Rome"))

        # Format for display (same format as current system uses)
        italian_display = dt_italian.strftime("%Y-%m-%d %H:%M:%S")

        logger.debug(f"🔄 UTC to Italian: {utc_datetime_str} → {italian_display}")
        return italian_display

    except Exception as e:
        logger.error(f"❌ Error converting UTC to Italian: {e}")
        logger.error(f"❌ Input was: {utc_datetime_str}")
        return None


def italian_to_utc_for_api(italian_datetime_str: str) -> Optional[str]:
    """
    Convert Italian local time selection back to UTC for booking API

    Args:
        italian_datetime_str: Italian time string like "2025-11-08 10:55:00"

    Returns:
        UTC datetime string like "2025-11-08 09:55:00" or None if conversion fails
    """
    try:
        # Parse the Italian datetime (no timezone info yet)
        dt_italian = datetime.strptime(italian_datetime_str, "%Y-%m-%d %H:%M:%S")

        # Set timezone to Italy (handles DST automatically)
        dt_italian = dt_italian.replace(tzinfo=ZoneInfo("Europe/Rome"))

        # Convert back to UTC
        dt_utc = dt_italian.astimezone(ZoneInfo("UTC"))

        # Format for booking API (same format as current system expects)
        utc_for_api = dt_utc.strftime("%Y-%m-%d %H:%M:%S")

        logger.debug(f"🔄 Italian to UTC: {italian_datetime_str} → {utc_for_api}")
        return utc_for_api

    except Exception as e:
        logger.error(f"❌ Error converting Italian to UTC: {e}")
        logger.error(f"❌ Input was: {italian_datetime_str}")
        return None


def convert_slot_times_to_italian(slot_data: dict) -> dict:
    """
    Convert slot start_time and end_time from UTC to Italian time

    Args:
        slot_data: Slot dictionary with start_time and end_time in UTC

    Returns:
        New slot dictionary with Italian times, or original if conversion fails
    """
    try:
        converted_slot = slot_data.copy()

        # Convert start_time
        if "start_time" in slot_data:
            italian_start = utc_to_italian_display(slot_data["start_time"])
            if italian_start:
                converted_slot["start_time"] = italian_start

        # Convert end_time
        if "end_time" in slot_data:
            italian_end = utc_to_italian_display(slot_data["end_time"])
            if italian_end:
                converted_slot["end_time"] = italian_end

        logger.debug(f"🔄 Converted slot times to Italian: {slot_data.get('start_time')} → {converted_slot.get('start_time')}")
        return converted_slot

    except Exception as e:
        logger.error(f"❌ Error converting slot times: {e}")
        # Return original slot on error
        return slot_data


def format_time_for_display(datetime_str: str) -> str:
    """
    Format datetime string for user display (removes seconds, clean format)

    Args:
        datetime_str: Datetime string like "2025-11-08 10:55:00"

    Returns:
        Clean time string like "10:55" or original if parsing fails
    """
    try:
        dt = datetime.strptime(datetime_str, "%Y-%m-%d %H:%M:%S")
        return dt.strftime("%-H:%M")  # Remove leading zero from hour
    except Exception as e:
        logger.warning(f"⚠️ Could not format time for display: {e}")
        # Fallback: try to extract just the time part
        try:
            if " " in datetime_str:
                time_part = datetime_str.split(" ")[1]
                if ":" in time_part:
                    hour_min = ":".join(time_part.split(":")[:2])
                    return hour_min
        except:
            pass
        return datetime_str
"""
API Retry Utility

Provides retry logic for external API calls with configurable retries.
After all retries exhausted, returns failure for graceful error handling.
"""

import time
import asyncio
from typing import Callable, Any, Optional, Tuple
from loguru import logger


def retry_api_call(
    api_func: Callable,
    max_retries: int = 3,
    retry_delay: float = 1.0,
    func_name: str = "API call",
    **kwargs
) -> Tuple[Any, Optional[Exception]]:
    """
    Retry a synchronous API call up to max_retries times.

    Args:
        api_func: The API function to call
        max_retries: Maximum number of retry attempts (default: 3)
        retry_delay: Seconds to wait between retries (default: 1.0)
        func_name: Name for logging purposes
        **kwargs: Arguments to pass to the API function

    Returns:
        Tuple of (result, error):
        - On success: (api_result, None)
        - On failure: (None, last_exception)
    """
    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"🔄 {func_name} - Attempt {attempt}/{max_retries}")
            result = api_func(**kwargs)
            logger.success(f"✅ {func_name} - Success on attempt {attempt}")
            return result, None

        except Exception as e:
            last_error = e
            logger.warning(f"⚠️ {func_name} - Attempt {attempt} failed: {str(e)[:100]}")

            if attempt < max_retries:
                logger.info(f"⏳ Waiting {retry_delay}s before retry...")
                time.sleep(retry_delay)
            else:
                logger.error(f"❌ {func_name} - All {max_retries} attempts failed")

    return None, last_error


async def retry_api_call_async(
    api_func: Callable,
    max_retries: int = 3,
    retry_delay: float = 1.0,
    func_name: str = "API call",
    **kwargs
) -> Tuple[Any, Optional[Exception]]:
    """
    Retry an async API call up to max_retries times.

    Args:
        api_func: The async API function to call
        max_retries: Maximum number of retry attempts (default: 3)
        retry_delay: Seconds to wait between retries (default: 1.0)
        func_name: Name for logging purposes
        **kwargs: Arguments to pass to the API function

    Returns:
        Tuple of (result, error):
        - On success: (api_result, None)
        - On failure: (None, last_exception)
    """
    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"🔄 {func_name} - Attempt {attempt}/{max_retries}")
            result = await api_func(**kwargs)
            logger.success(f"✅ {func_name} - Success on attempt {attempt}")
            return result, None

        except Exception as e:
            last_error = e
            logger.warning(f"⚠️ {func_name} - Attempt {attempt} failed: {str(e)[:100]}")

            if attempt < max_retries:
                logger.info(f"⏳ Waiting {retry_delay}s before retry...")
                await asyncio.sleep(retry_delay)
            else:
                logger.error(f"❌ {func_name} - All {max_retries} attempts failed")

    return None, last_error
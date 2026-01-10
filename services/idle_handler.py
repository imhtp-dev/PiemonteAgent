"""
User Idle Handler for Healthcare Booking Agent
Handles situations where transcription fails or user doesn't respond
"""

import asyncio
from loguru import logger
from pipecat.processors.user_idle_processor import UserIdleProcessor
from pipecat.frames.frames import (
    TTSSpeakFrame,
    LLMMessagesFrame,
    EndFrame
)
from config.settings import settings


async def healthcare_idle_callback(idle_processor: UserIdleProcessor, retry_count: int) -> bool:
    """
    Handle user idle scenarios with escalating responses for healthcare context

    Args:
        idle_processor: The UserIdleProcessor instance
        retry_count: Number of times user has been idle (1, 2, 3...)

    Returns:
        bool: True to continue monitoring, False to stop
    """

    language_instruction = settings.language_config

    if retry_count == 1:
        # First timeout: Gentle reminder - maybe they're thinking or STT failed
        logger.info("🔇 User idle (first time) - sending gentle reminder")

        reminder_message = "Per favore potresti ripetere, grazie ?." if "Italian" in language_instruction else "I'm sorry, I didn't catch that. Could you please repeat?"

        # Use LLM for more natural response
        messages = [{
            "role": "system",
            "content": f"The user hasn't responded for a while. This could be due to transcription failure or user thinking. Gently ask 'Mi scusi, non ho sentito la sua risposta. Può ripetere per favore?' {language_instruction}"
        }, {
            "role": "assistant",
            "content": reminder_message
        }]

        await idle_processor.push_frame(LLMMessagesFrame(messages))
        return True  # Continue monitoring

    elif retry_count == 2:
        # Second timeout: More direct - check if they're still there
        logger.info("🔇 User idle (second time) - checking if still present")

        presence_check = "Ci sei ancora? Se hai difficoltà a parlare, puoi provare a parlare più forte o più lentamente." if "Italian" in language_instruction else "Are you still there? If you're having trouble, please try speaking a bit louder or slower."

        messages = [{
            "role": "system",
            "content": f"The user hasn't responded twice. Check if they're still present and offer help with audio issues. Be helpful and understanding. {language_instruction}"
        }, {
            "role": "assistant",
            "content": presence_check
        }]

        await idle_processor.push_frame(LLMMessagesFrame(messages))
        return True  # Continue monitoring

    elif retry_count == 3:
        # Third timeout: Final attempt before ending
        logger.info("🔇 User idle (third time) - final attempt")

        final_attempt = "Sembra che ci siano problemi di connessione. Proverò ancora una volta - sei ancora lì?" if "Italian" in language_instruction else "It seems there might be connection issues. I'll try one more time - are you still there?"

        messages = [{
            "role": "system",
            "content": f"This is the final attempt. The user may have connection issues. Give them one last chance. {language_instruction}"
        }, {
            "role": "assistant",
            "content": final_attempt
        }]

        await idle_processor.push_frame(LLMMessagesFrame(messages))
        return True  # Give one more chance

    else:
        # Fourth timeout: End the session gracefully
        logger.info("🔇 User idle (final) - ending session due to extended inactivity")

        goodbye_message = "Mi dispiace, sembra che ci siano problemi tecnici. La chiamata verrà terminata. Può richiamare quando vuole per completare la prenotazione. Arrivederci!" if "Italian" in language_instruction else "I'm sorry, there seem to be technical issues. The call will end now. Please call back anytime to complete your booking. Goodbye!"

        messages = [{
            "role": "system",
            "content": f"End the session gracefully due to extended user inactivity. Be polite and invite them to call back. {language_instruction}"
        }, {
            "role": "assistant",
            "content": goodbye_message
        }]

        await idle_processor.push_frame(LLMMessagesFrame(messages))

        # Give time for the message to be spoken
        await asyncio.sleep(3)

        # End the session
        await idle_processor.push_frame(EndFrame())
        return False  # Stop monitoring


def create_user_idle_processor(timeout_seconds: float = 20.0) -> UserIdleProcessor:
    """
    Create a UserIdleProcessor for healthcare booking agent

    Args:
        timeout_seconds: Seconds to wait before considering user idle (default: 20 seconds)

    Returns:
        UserIdleProcessor: Configured processor
    """

    logger.info(f"🕐 Creating UserIdleProcessor with {timeout_seconds}s timeout (allows for API processing delays)")

    return UserIdleProcessor(
        callback=healthcare_idle_callback,
        timeout=timeout_seconds
    )


# Alternative: Simple callback for basic use cases
async def simple_idle_callback(idle_processor: UserIdleProcessor) -> None:
    """Simple single-response idle callback"""

    language_instruction = settings.language_config
    reminder = "Mi scusi, può ripetere?" if "Italian" in language_instruction else "Sorry, could you repeat that?"

    await idle_processor.push_frame(TTSSpeakFrame(reminder))


def create_simple_idle_processor(timeout_seconds: float = 50.0) -> UserIdleProcessor:
    """Create simple idle processor with single reminder"""

    return UserIdleProcessor(
        callback=simple_idle_callback,
        timeout=timeout_seconds
    )
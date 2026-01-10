"""
STT Service Dynamic Switching Utilities
"""

from loguru import logger
from pipecat.frames.frames import STTUpdateSettingsFrame
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat_flows import FlowManager
from deepgram import LiveOptions
from config.settings import settings


class STTSwitcher:
    """Utility class for dynamically switching STT models and languages"""

    def __init__(self, stt_service: DeepgramSTTService, flow_manager: FlowManager):
        self.stt_service = stt_service
        self.flow_manager = flow_manager
        self.original_model = "nova-2-general"
        # Get language from global settings instead of hardcoded
        self.original_language = settings.deepgram_config["language"]

    async def switch_to_email_mode(self):
        """Switch to Nova-3 with multi-language for email collection"""
        try:
            logger.info("🔄 Switching STT to Nova-3 (multi-language) for email collection")

            # Update model to nova-3-general
            await self.stt_service.set_model("nova-3-general")

            # Update language to multi (auto-detect)
            await self.stt_service.set_language("multi")

            logger.success("✅ STT switched to Nova-3 multi-language mode")

        except Exception as e:
            logger.error(f"❌ Failed to switch STT to email mode: {e}")

    async def switch_to_default_mode(self):
        """Switch back to Nova-2 with configured language for default operation"""
        try:
            # Get current language from settings to ensure it's up to date
            current_language = settings.deepgram_config["language"]
            logger.info(f"🔄 Switching STT back to Nova-2 ({current_language}) for default operation")

            # Update model back to nova-2-general
            await self.stt_service.set_model(self.original_model)

            # Update language back to configured language
            await self.stt_service.set_language(current_language)

            logger.success(f"✅ STT switched back to Nova-2 {current_language} mode")

        except Exception as e:
            logger.error(f"❌ Failed to switch STT to default mode: {e}")

    async def switch_using_frames(self, model: str, language: str):
        """Alternative method using STTUpdateSettingsFrame"""
        try:
            if self.flow_manager.task:
                settings_frame = STTUpdateSettingsFrame(
                    settings={
                        "model": model,
                        "language": language,
                        "smart_format": True,
                        "punctuate": True,
                        "interim_results": True,
                        "keywords": settings.deepgram_config["keywords"]
                    }
                )
                await self.flow_manager.task.queue_frames([settings_frame])
                logger.info(f"📡 Queued STT settings update: {model} ({language}) with keywords")

        except Exception as e:
            logger.error(f"❌ Failed to update STT via frames: {e}")


# Global STT switcher instance (will be initialized in pipeline setup)
stt_switcher: STTSwitcher = None


def initialize_stt_switcher(stt_service: DeepgramSTTService, flow_manager: FlowManager):
    """Initialize the global STT switcher"""
    global stt_switcher
    stt_switcher = STTSwitcher(stt_service, flow_manager)
    logger.info("🔧 STT Switcher initialized")


async def switch_to_email_transcription():
    """Convenience function to switch to email transcription mode"""
    if stt_switcher:
        await stt_switcher.switch_to_email_mode()
    else:
        logger.warning("⚠️ STT Switcher not initialized")


async def switch_to_default_transcription():
    """Convenience function to switch to default transcription mode"""
    if stt_switcher:
        await stt_switcher.switch_to_default_mode()
    else:
        logger.warning("⚠️ STT Switcher not initialized")
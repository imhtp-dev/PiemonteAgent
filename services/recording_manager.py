"""
Audio recording manager for call sessions

Manages audio buffering and saves recordings to Azure Blob Storage:
- User track (mono WAV)
- Bot track (mono WAV)
- Stereo mix (user left, bot right)
"""

import os
from typing import Optional, Dict
from loguru import logger


class RecordingManager:
    """Manages audio recording for a call session"""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.enabled = os.getenv("RECORDING_ENABLED", "false").lower() == "true"
        self.sample_rate = 16000
        self.user_audio = bytearray()
        self.bot_audio = bytearray()

        if self.enabled:
            logger.info(f"🎙️ RecordingManager initialized for session: {session_id}")
        else:
            logger.debug(f"🎙️ RecordingManager disabled for session: {session_id}")

    def add_user_audio(self, audio: bytes):
        """Buffer user audio"""
        if self.enabled:
            self.user_audio.extend(audio)

    def add_bot_audio(self, audio: bytes):
        """Buffer bot audio"""
        if self.enabled:
            self.bot_audio.extend(audio)

    def get_duration_seconds(self) -> float:
        """Get recording duration based on buffered audio"""
        max_bytes = max(len(self.user_audio), len(self.bot_audio))
        return max_bytes / (self.sample_rate * 2)  # 16-bit = 2 bytes per sample

    async def save_recordings(self) -> Optional[Dict[str, str]]:
        """
        Save all recordings to Azure and return URLs

        Returns:
            Dict with keys: stereo_url, user_url, bot_url, or None if disabled/no audio
        """
        if not self.enabled:
            logger.info("🎙️ Recording disabled, skipping save")
            return None

        if len(self.user_audio) == 0 and len(self.bot_audio) == 0:
            logger.warning("🎙️ No audio to save")
            return None

        from services.call_storage import CallDataStorage
        from pipecat.audio.utils import interleave_stereo_audio

        storage = CallDataStorage()
        urls = {}

        try:
            # Pad shorter track with silence to match lengths
            user_bytes = bytes(self.user_audio)
            bot_bytes = bytes(self.bot_audio)

            max_len = max(len(user_bytes), len(bot_bytes))
            if len(user_bytes) < max_len:
                user_bytes += b'\x00' * (max_len - len(user_bytes))
            if len(bot_bytes) < max_len:
                bot_bytes += b'\x00' * (max_len - len(bot_bytes))

            # Upload user track (mono)
            urls["user_url"] = await storage.upload_recording(
                self.session_id, user_bytes, "user",
                sample_rate=self.sample_rate, num_channels=1
            )
            logger.success(f"🎙️ User recording saved: {len(user_bytes)} bytes")

            # Upload bot track (mono)
            urls["bot_url"] = await storage.upload_recording(
                self.session_id, bot_bytes, "bot",
                sample_rate=self.sample_rate, num_channels=1
            )
            logger.success(f"🎙️ Bot recording saved: {len(bot_bytes)} bytes")

            # Create and upload stereo mix (user left, bot right)
            stereo_audio = interleave_stereo_audio(user_bytes, bot_bytes)
            urls["stereo_url"] = await storage.upload_recording(
                self.session_id, stereo_audio, "stereo",
                sample_rate=self.sample_rate, num_channels=2
            )
            logger.success(f"🎙️ Stereo recording saved: {len(stereo_audio)} bytes")

            return urls

        except Exception as e:
            logger.error(f"❌ Failed to save recordings: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return None

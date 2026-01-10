"""
Recording components for transcript and audio capture with chunked audio processing
"""

import os
import json
import wave
import subprocess
from datetime import datetime
from typing import Dict, List, Any
from loguru import logger

from pipecat.processors.transcript_processor import TranscriptProcessor
from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor


class SessionRecorder:
    """Manages recording session data with chunked audio processing"""

    def __init__(self):
        self.session_data = {
            "session_id": None,
            "start_time": None,
            "end_time": None,
            "participant_info": {},
            "conversation": [],
            "audio_files": []
        }
        self.conversation_history = []
        self.recording_active = False
        self.chunk_counter = 0
        self.audio_chunks = []  # Track all chunks for final combination

        # Audio recording constants - VAD compatible quality
        self.SAMPLE_RATE = 16000  # VAD-compatible quality for voice clarity
        self.CHUNK_DURATION = 30  # 30 seconds per chunk as recommended

        # Base directories
        self.base_recordings_dir = "recordings"
        self.transcripts_dir = f"{self.base_recordings_dir}/transcripts"

    def start_session(self, participant_id: str = None):
        """Initialize new recording session with organized folder structure"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.session_data["session_id"] = f"healthcare_session_{timestamp}"
        self.session_data["start_time"] = datetime.now().isoformat()
        if participant_id:
            self.session_data["participant_info"]["id"] = participant_id

        # Create organized directory structure
        session_id = self.session_data["session_id"]
        self.audio_session_dir = f"{self.base_recordings_dir}/audios/{session_id}"
        self.chunks_dir = f"{self.audio_session_dir}/chunks"
        self.final_audio_dir = f"{self.audio_session_dir}/final_audio"

        # Create all required directories
        os.makedirs(self.chunks_dir, exist_ok=True)
        os.makedirs(self.final_audio_dir, exist_ok=True)
        os.makedirs(self.transcripts_dir, exist_ok=True)

        # Reset counters and tracking
        self.chunk_counter = 0
        self.audio_chunks = []
        self.recording_active = True

        logger.info(f"🎬 Recording session started: {session_id}")
        logger.info(f"📁 Audio chunks will be saved to: {self.chunks_dir}")

    def get_next_chunk_filename(self, audio_type: str) -> str:
        """Generate next chunk filename with readable naming"""
        self.chunk_counter += 1
        timestamp = datetime.now().strftime("%H%M%S")
        return f"{self.chunks_dir}/chunk_{self.chunk_counter:03d}_{audio_type}_{timestamp}.wav"

    def combine_audio_chunks(self):
        """Combine all audio chunks into final files using FFmpeg"""
        if not self.audio_chunks:
            logger.warning("No audio chunks to combine")
            return

        session_id = self.session_data["session_id"]

        # Group chunks by type
        complete_chunks = [chunk for chunk in self.audio_chunks if chunk["type"] == "complete"]
        patient_chunks = [chunk for chunk in self.audio_chunks if chunk["type"] == "patient"]
        assistant_chunks = [chunk for chunk in self.audio_chunks if chunk["type"] == "assistant"]

        # Combine complete audio (stereo)
        if complete_chunks:
            self._combine_chunks_ffmpeg(
                complete_chunks,
                f"{self.final_audio_dir}/{session_id}_complete_conversation.wav",
                "Complete stereo conversation"
            )

        # Combine patient audio (mono)
        if patient_chunks:
            self._combine_chunks_ffmpeg(
                patient_chunks,
                f"{self.final_audio_dir}/{session_id}_patient_audio.wav",
                "Patient audio only"
            )

        # Combine assistant audio (mono)
        if assistant_chunks:
            self._combine_chunks_ffmpeg(
                assistant_chunks,
                f"{self.final_audio_dir}/{session_id}_assistant_audio.wav",
                "Assistant audio only"
            )

    def _combine_chunks_ffmpeg(self, chunks: List[Dict], output_file: str, description: str):
        """Use FFmpeg or Python fallback to combine chunks into final audio file"""
        try:
            # Try FFmpeg first
            if self._has_ffmpeg():
                self._combine_with_ffmpeg(chunks, output_file, description)
            else:
                # Fallback to Python wave module
                self._combine_with_python(chunks, output_file, description)

        except Exception as e:
            logger.error(f"Error combining chunks for {description}: {e}")

    def _has_ffmpeg(self) -> bool:
        """Check if FFmpeg is available"""
        try:
            result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True)
            return result.returncode == 0
        except FileNotFoundError:
            return False

    def _combine_with_ffmpeg(self, chunks: List[Dict], output_file: str, description: str):
        """Use FFmpeg to combine chunks"""
        filelist_path = f"{self.chunks_dir}/filelist_{os.path.basename(output_file)}.txt"

        with open(filelist_path, "w") as f:
            for chunk in sorted(chunks, key=lambda x: x["chunk_number"]):
                f.write(f"file '{os.path.abspath(chunk['filename'])}'\n")

        cmd = [
            "ffmpeg", "-f", "concat", "-safe", "0",
            "-i", filelist_path, "-c", "copy",
            output_file, "-y"
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode == 0:
            logger.success(f"🎵 {description} combined with FFmpeg: {output_file}")
            self._add_final_audio_to_session(description, output_file, len(chunks))
            os.remove(filelist_path)
        else:
            logger.error(f"FFmpeg error: {result.stderr}")
            raise Exception("FFmpeg combination failed")

    def _combine_with_python(self, chunks: List[Dict], output_file: str, description: str):
        """Use Python wave module to combine chunks (fallback)"""
        sorted_chunks = sorted(chunks, key=lambda x: x["chunk_number"])

        if not sorted_chunks:
            return

        # Get audio properties from first chunk
        first_chunk = sorted_chunks[0]
        sample_rate = first_chunk["sample_rate"]
        channels = first_chunk["channels"]

        # Combine all audio data
        combined_audio = bytearray()

        for chunk in sorted_chunks:
            try:
                with wave.open(chunk["filename"], "rb") as wf:
                    audio_data = wf.readframes(wf.getnframes())
                    combined_audio.extend(audio_data)
            except Exception as e:
                logger.warning(f"Error reading chunk {chunk['filename']}: {e}")

        # Write combined audio
        with wave.open(output_file, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(sample_rate)
            wf.writeframes(combined_audio)

        logger.success(f"🎵 {description} combined with Python: {output_file}")
        self._add_final_audio_to_session(description, output_file, len(chunks))

    def _add_final_audio_to_session(self, description: str, output_file: str, chunk_count: int):
        """Add final audio file info to session data"""
        self.session_data["audio_files"].append({
            "type": description.lower().replace(" ", "_"),
            "filename": output_file,
            "chunk_count": chunk_count,
            "creation_time": datetime.now().isoformat(),
            "file_size_bytes": os.path.getsize(output_file) if os.path.exists(output_file) else 0
        })

    def end_session(self):
        """Finalize recording session with audio chunk combination"""
        if not self.session_data["session_id"]:
            return

        self.recording_active = False
        self.session_data["end_time"] = datetime.now().isoformat()
        self.session_data["conversation"] = self.conversation_history.copy()

        # Combine all audio chunks into final files
        logger.info("🎵 Combining audio chunks into final files...")
        self.combine_audio_chunks()

        # Save transcript
        transcript_file = f"{self.transcripts_dir}/{self.session_data['session_id']}_transcript.json"

        with open(transcript_file, "w", encoding="utf-8") as f:
            json.dump({
                **self.session_data,
                "total_messages": len(self.conversation_history),
                "total_chunks": len(self.audio_chunks),
                "duration_seconds": self._calculate_duration(),
                "language": "en",  # English healthcare bot
                "audio_session_dir": self.audio_session_dir,
                "chunks_dir": self.chunks_dir,
                "final_audio_dir": self.final_audio_dir
            }, f, indent=2, ensure_ascii=False)

        logger.success(f"📝 Transcript saved: {transcript_file}")
        logger.success(f"🎬 Session ended: {self.session_data['session_id']}")
        logger.success(f"📁 Final audio files saved to: {self.final_audio_dir}")

        # Clear for next session
        self.conversation_history.clear()
        self.audio_chunks.clear()
        self.chunk_counter = 0
        self.session_data = {
            "session_id": None,
            "start_time": None,
            "end_time": None,
            "participant_info": {},
            "conversation": [],
            "audio_files": []
        }

    def add_transcript_message(self, role: str, content: str, timestamp: str = None):
        """Add message to conversation history"""
        message = {
            "role": role,
            "content": content,
            "timestamp": timestamp or datetime.now().isoformat()
        }
        self.conversation_history.append(message)

    def _calculate_duration(self) -> float:
        """Calculate session duration in seconds"""
        if not self.session_data["start_time"] or not self.session_data["end_time"]:
            return 0

        start = datetime.fromisoformat(self.session_data["start_time"])
        end = datetime.fromisoformat(self.session_data["end_time"])
        return (end - start).total_seconds()


def create_transcript_processor(session_recorder: SessionRecorder) -> TranscriptProcessor:
    """Create transcript processor with session recording"""
    transcript = TranscriptProcessor()

    @transcript.event_handler("on_transcript_update")
    async def handle_transcript_update(processor, frame):
        """Handle transcript updates"""
        for message in frame.messages:
            session_recorder.add_transcript_message(
                role=message.role,
                content=message.content,
                timestamp=message.timestamp
            )

            # Log in real-time for debugging
            logger.info(f"💬 [{message.role}]: {message.content}")

    return transcript


def create_audio_buffer_processor(session_recorder: SessionRecorder) -> AudioBufferProcessor:
    """Create audio buffer processor with chunked recording"""
    # Configure for 30-second chunks with VAD-compatible audio
    SAMPLE_RATE = 16000  # VAD-compatible quality for voice clarity
    CHUNK_DURATION = 30  # seconds
    BUFFER_SIZE = SAMPLE_RATE * 2 * CHUNK_DURATION  # 2 bytes per sample

    audiobuffer = AudioBufferProcessor(
        sample_rate=SAMPLE_RATE,
        num_channels=2,     # Stereo: user left, bot right
        buffer_size=BUFFER_SIZE,  # 30-second chunks
        enable_turn_audio=True,  # Enable per-turn recording
        user_continuous_stream=True
    )

    @audiobuffer.event_handler("on_audio_data")
    async def save_audio_chunk(processor, audio, sample_rate, num_channels):
        """Save audio chunks every 30 seconds during conversation"""
        if not session_recorder.session_data["session_id"] or not session_recorder.recording_active:
            return

        # Generate chunk filename
        chunk_filename = session_recorder.get_next_chunk_filename("complete")

        # Save stereo chunk (user left, bot right)
        with wave.open(chunk_filename, "wb") as wf:
            wf.setnchannels(num_channels)
            wf.setsampwidth(2)  # 16-bit audio
            wf.setframerate(sample_rate)
            wf.writeframes(audio)

        # Track chunk for final combination
        chunk_info = {
            "type": "complete",
            "filename": chunk_filename,
            "chunk_number": session_recorder.chunk_counter,
            "channels": num_channels,
            "sample_rate": sample_rate,
            "timestamp": datetime.now().isoformat(),
            "duration_seconds": len(audio) / (sample_rate * num_channels * 2)
        }

        session_recorder.audio_chunks.append(chunk_info)

        logger.info(f"🎵 Audio chunk saved: chunk_{session_recorder.chunk_counter:03d} ({len(audio)} bytes, {chunk_info['duration_seconds']:.1f}s)")

    @audiobuffer.event_handler("on_track_audio_data")
    async def save_separate_track_chunks(processor, user_audio, bot_audio, sample_rate, num_channels):
        """Save separate user and bot audio track chunks every 30 seconds"""
        if not session_recorder.session_data["session_id"] or not session_recorder.recording_active:
            return

        current_chunk = session_recorder.chunk_counter

        # Save patient audio chunk (mono)
        patient_chunk_filename = session_recorder.get_next_chunk_filename("patient")
        with wave.open(patient_chunk_filename, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(user_audio)

        # Save assistant audio chunk (mono) - use same chunk number
        assistant_chunk_filename = f"{session_recorder.chunks_dir}/chunk_{current_chunk:03d}_assistant_{datetime.now().strftime('%H%M%S')}.wav"
        with wave.open(assistant_chunk_filename, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(bot_audio)

        # Track separate chunks for final combination
        patient_chunk_info = {
            "type": "patient",
            "filename": patient_chunk_filename,
            "chunk_number": current_chunk,
            "channels": 1,
            "sample_rate": sample_rate,
            "timestamp": datetime.now().isoformat(),
            "duration_seconds": len(user_audio) / (sample_rate * 2)
        }

        assistant_chunk_info = {
            "type": "assistant",
            "filename": assistant_chunk_filename,
            "chunk_number": current_chunk,
            "channels": 1,
            "sample_rate": sample_rate,
            "timestamp": datetime.now().isoformat(),
            "duration_seconds": len(bot_audio) / (sample_rate * 2)
        }

        session_recorder.audio_chunks.extend([patient_chunk_info, assistant_chunk_info])

        logger.info(f"🎵 Separate track chunks saved: Patient={len(user_audio)} bytes, Assistant={len(bot_audio)} bytes")

    @audiobuffer.event_handler("on_user_turn_audio_data")
    async def save_user_turn_audio(processor, audio, sample_rate, num_channels):
        """Save individual user speaking turns"""
        if not session_recorder.session_data["session_id"]:
            return

        turn_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        filename = f"{session_recorder.chunks_dir}/{session_recorder.session_data['session_id']}_user_turn_{turn_timestamp}.wav"

        with wave.open(filename, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(audio)

        logger.debug(f"🎵 User turn saved: {filename}")

    return audiobuffer
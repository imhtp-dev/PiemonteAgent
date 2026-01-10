"""
Per-call logging system for debugging healthcare agent calls
"""

import os
import sys
from datetime import datetime
from typing import Optional
from loguru import logger
from pathlib import Path
import asyncio
import threading


class CallLogger:
    """Manages per-call log files for debugging"""

    def __init__(self, session_id: str = None):
        self.call_logs_dir = Path("call_logs")
        self.call_logs_dir.mkdir(exist_ok=True)
        self.session_id = session_id  # Unique per instance
        self.current_session_id: Optional[str] = None
        self.current_log_file: Optional[str] = None
        self.handler_id: Optional[int] = None
        self.python_handler = None

        # Azure storage for log persistence
        self.azure_storage = None
        self._init_azure_storage()

    def _init_azure_storage(self):
        """Initialize Azure storage for log persistence"""
        try:
            from services.call_storage import CallDataStorage
            self.azure_storage = CallDataStorage()
            logger.info("✅ Azure storage initialized for call logs")
        except Exception as e:
            logger.warning(f"⚠️ Azure storage not available for call logs: {e}")
            self.azure_storage = None

    def start_call_logging(self, session_id: str, caller_phone: str = "") -> str:
        """Start logging for a specific call session - captures ALL terminal output"""
        self.current_session_id = session_id

        # Create log file name with timestamp and session info
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        phone_suffix = f"_{caller_phone.replace('+', '')}" if caller_phone else ""
        log_filename = f"{timestamp}_{session_id[:8]}{phone_suffix}.log"

        # Create full path
        self.current_log_file = self.call_logs_dir / log_filename

        # Add loguru handler with session filter to capture only this session's logs
        def session_filter(record):
            # Only log messages from this session
            return True  # We'll rely on the session-specific logger instead of global filtering

        self.handler_id = logger.add(
            str(self.current_log_file),
            format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
            level="DEBUG",  # Capture everything
            enqueue=True,
            rotation=None,
            retention=None,
            catch=True,  # Catch all exceptions
            filter=session_filter
        )

        # Create session-specific Python logger instead of using global root logger
        import logging

        # Create a session-specific logger name
        session_logger_name = f"session_{session_id.replace('-', '_')}"
        self.session_logger = logging.getLogger(session_logger_name)
        self.session_logger.setLevel(logging.DEBUG)

        # Create a custom handler that writes to our log file
        class CallFileHandler(logging.Handler):
            def __init__(self, log_file_path, session_id):
                super().__init__()
                self.log_file_path = log_file_path
                self.session_id = session_id

            def emit(self, record):
                try:
                    # Skip noisy audio/binary logs
                    if (record.name == "websockets.client" and
                        ("> BINARY" in record.getMessage() or "< BINARY" in record.getMessage())):
                        return

                    # Skip overly verbose logs
                    message = record.getMessage()
                    if any(skip in message for skip in [
                        "BINARY",
                        "bytes]",
                        "connection is OPEN",
                        "websocket.client"
                    ]):
                        return

                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                    level = record.levelname
                    name = record.name

                    # Format similar to loguru with session info
                    log_line = f"{timestamp} | {level: <8} | {name} | {self.session_id} - {message}\n"

                    with open(self.log_file_path, 'a', encoding='utf-8') as f:
                        f.write(log_line)
                except Exception:
                    pass

        # Create handler for this session only
        self.python_handler = CallFileHandler(str(self.current_log_file), session_id)
        self.python_handler.setLevel(logging.DEBUG)

        # Add to session-specific logger only (not global root logger)
        self.session_logger.addHandler(self.python_handler)

        # Prevent propagation to avoid duplicate logs in other sessions
        self.session_logger.propagate = False

        # Log call start
        logger.info("🚀 CALL STARTED - FULL TERMINAL LOGGING ENABLED")
        logger.info(f"📋 Session ID: {session_id}")
        logger.info(f"📞 Caller Phone: {caller_phone or 'Unknown'}")
        logger.info(f"📁 Log File: {self.current_log_file}")
        logger.info("=" * 80)

        return str(self.current_log_file)

    def stop_call_logging(self) -> Optional[str]:
        """Stop logging for current call session"""
        if not self.handler_id:
            return None

        # Log call end
        logger.info("=" * 80)
        logger.info("🛑 CALL ENDED - FULL TERMINAL LOGGING STOPPED")
        logger.info(f"📁 Complete log saved to: {self.current_log_file}")

        # Remove loguru handler
        logger.remove(self.handler_id)

        # Remove Python logging handler from session-specific logger
        if self.python_handler and hasattr(self, 'session_logger'):
            import logging
            self.session_logger.removeHandler(self.python_handler)
            # Clear all handlers to ensure complete cleanup
            self.session_logger.handlers.clear()

        log_file = str(self.current_log_file)

        # Upload log to Azure storage (async operation)
        if self.azure_storage and self.current_log_file and Path(self.current_log_file).exists():
            try:
                # Run async upload in background thread
                threading.Thread(
                    target=self._upload_log_to_azure,
                    args=(str(self.current_log_file), self.current_session_id),
                    daemon=True
                ).start()
                logger.info("☁️ Uploading call log to Azure storage...")
            except Exception as e:
                logger.error(f"❌ Failed to start Azure log upload: {e}")

        # Reset state
        self.handler_id = None
        self.python_handler = None
        self.session_logger = None
        self.current_session_id = None
        self.current_log_file = None

        return log_file

    def get_session_logger(self):
        """Get the session-specific logger for custom logging"""
        return getattr(self, 'session_logger', None)

    def log_phone_debug(self, event: str, data: dict):
        """Log phone-related debugging information"""
        logger.info(f"📞 PHONE DEBUG - {event}")
        for key, value in data.items():
            logger.info(f"   {key}: {value}")

    def log_flow_transition(self, from_node: str, to_node: str, context: dict = None):
        """Log flow transitions"""
        logger.info(f"🔄 FLOW TRANSITION: {from_node} → {to_node}")
        if context:
            for key, value in context.items():
                logger.info(f"   {key}: {value}")

    def log_user_input(self, input_text: str, transcription_confidence: float = None):
        """Log user input and transcription details"""
        logger.info(f"🎤 USER INPUT: '{input_text}'")
        if transcription_confidence:
            logger.info(f"   Confidence: {transcription_confidence}")

    def log_agent_response(self, response_text: str, response_time_ms: float = None):
        """Log agent responses"""
        logger.info(f"🤖 AGENT RESPONSE: '{response_text}'")
        if response_time_ms:
            logger.info(f"   Response Time: {response_time_ms}ms")

    def log_api_call(self, api_name: str, request_data: dict, response_data: dict, duration_ms: float):
        """Log API calls for debugging"""
        logger.info(f"🌐 API CALL: {api_name}")
        logger.info(f"   Duration: {duration_ms}ms")
        logger.info(f"   Request: {request_data}")
        logger.info(f"   Response: {response_data}")

    def log_error(self, error: Exception, context: dict = None):
        """Log errors with context"""
        logger.error(f"❌ ERROR: {str(error)}")
        logger.error(f"   Type: {type(error).__name__}")
        if context:
            logger.error(f"   Context: {context}")

    def get_current_log_file(self) -> Optional[str]:
        """Get current call log file path"""
        return str(self.current_log_file) if self.current_log_file else None

    def list_recent_logs(self, limit: int = 10) -> list:
        """List recent call log files"""
        try:
            log_files = sorted(
                self.call_logs_dir.glob("*.log"),
                key=lambda f: f.stat().st_mtime,
                reverse=True
            )
            return [str(f) for f in log_files[:limit]]
        except Exception as e:
            logger.error(f"Failed to list recent logs: {e}")
            return []

    def cleanup_old_logs(self, days_to_keep: int = 7):
        """Clean up log files older than specified days"""
        try:
            import time
            cutoff_time = time.time() - (days_to_keep * 24 * 60 * 60)

            for log_file in self.call_logs_dir.glob("*.log"):
                if log_file.stat().st_mtime < cutoff_time:
                    log_file.unlink()
                    logger.info(f"🗑️ Deleted old log file: {log_file}")
        except Exception as e:
            logger.error(f"Failed to cleanup old logs: {e}")

    def _upload_log_to_azure(self, log_file_path: str, session_id: str):
        """Upload log file to Azure Blob Storage (runs in background thread)"""
        try:
            # Read the log file content
            with open(log_file_path, 'r', encoding='utf-8') as f:
                log_content = f.read()

            # Create Azure blob path (similar to call data structure)
            log_filename = Path(log_file_path).name
            today = datetime.now().strftime("%Y-%m-%d")
            blob_path = f"call-logs/{today}/{log_filename}"

            # Create new event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            try:
                # Upload to Azure
                result = loop.run_until_complete(
                    self.azure_storage._upload_text_content(blob_path, log_content)
                )

                if result:
                    logger.success(f"☁️ Call log uploaded to Azure: {blob_path}")
                    # Optionally delete local file after successful upload (keep for now)
                    # Path(log_file_path).unlink()
                else:
                    logger.error(f"❌ Failed to upload call log: {blob_path}")

            finally:
                loop.close()

        except Exception as e:
            logger.error(f"❌ Error uploading call log to Azure: {e}")


# Global call logger instance
call_logger = CallLogger()
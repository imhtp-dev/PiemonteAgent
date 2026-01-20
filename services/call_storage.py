"""
Azure Storage service for storing call data, transcripts, and fiscal codes
"""

import os
import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional
from azure.storage.blob import BlobServiceClient
from azure.core.exceptions import AzureError
from loguru import logger

class CallDataStorage:
    """Azure Blob Storage service for call data persistence"""

    def __init__(self):
        self.connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
        if not self.connection_string:
            raise ValueError("AZURE_STORAGE_CONNECTION_STRING not found in environment")

        try:
            self.blob_service = BlobServiceClient.from_connection_string(self.connection_string)
            self.container_name = "call-data"

            # Blob prefix for separating different agents (e.g., "piemonte/")
            # Lombardy uses empty prefix, Piemonte uses "piemonte/"
            self.blob_prefix = os.getenv("AZURE_BLOB_PREFIX", "")
            if self.blob_prefix and not self.blob_prefix.endswith("/"):
                self.blob_prefix += "/"

            # Ensure container exists
            self._ensure_container_exists()
            prefix_info = f" (prefix: {self.blob_prefix})" if self.blob_prefix else ""
            logger.info(f"✅ Azure Storage initialized successfully{prefix_info}")

        except Exception as e:
            logger.error(f"❌ Failed to initialize Azure Storage: {e}")
            raise

    def _ensure_container_exists(self):
        """Create container if it doesn't exist"""
        try:
            container_client = self.blob_service.get_container_client(self.container_name)
            container_client.get_container_properties()
            logger.debug(f"Container '{self.container_name}' exists")
        except Exception:
            # Container doesn't exist, create it
            try:
                self.blob_service.create_container(self.container_name)
                logger.info(f"Created container: {self.container_name}")
            except Exception as e:
                logger.error(f"Failed to create container: {e}")
                raise

    async def store_call_data(self, session_id: str, call_data: Dict[str, Any]) -> str:
        """
        Store complete call data in Azure Blob Storage

        Args:
            session_id: Unique session identifier
            call_data: Complete call data including transcript, summary, fiscal code

        Returns:
            str: Blob name where data was stored
        """
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            date_folder = datetime.now().strftime("%Y-%m-%d")
            blob_name = f"{self.blob_prefix}calls/{date_folder}/{timestamp}_{session_id}.json"

            blob_client = self.blob_service.get_blob_client(
                container=self.container_name,
                blob=blob_name
            )

            # Add metadata for easier searching and filtering
            metadata = {
                "session_id": session_id,
                "timestamp": timestamp,
                "date": date_folder,
                "has_fiscal_code": str(bool(call_data.get('fiscal_code'))),
                "patient_name": call_data.get('patient_data', {}).get('name', 'unknown'),
                "has_booking": str(bool(call_data.get('booking_data', {}).get('booking_code'))),
                "transcript_messages": str(len(call_data.get('transcript', []))),
                "call_duration_seconds": str(call_data.get('call_duration_seconds', 0))
            }

            # Convert call data to JSON with proper encoding
            json_data = json.dumps(call_data, indent=2, ensure_ascii=False)

            # Upload to Azure Blob Storage
            blob_client.upload_blob(
                json_data.encode('utf-8'),
                overwrite=True,
                metadata=metadata
            )

            logger.success(f"✅ Call data stored successfully: {blob_name}")
            logger.info(f"📊 Stored data: {len(call_data.get('transcript', []))} messages, "
                       f"fiscal_code: {bool(call_data.get('fiscal_code'))}")

            return blob_name

        except AzureError as e:
            logger.error(f"❌ Azure Storage error: {e}")
            raise
        except Exception as e:
            logger.error(f"❌ Unexpected error storing call data: {e}")
            raise

    async def retrieve_call_data(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve call data by session ID

        Args:
            session_id: Session identifier to search for

        Returns:
            Dict containing call data or None if not found
        """
        try:
            # List blobs with session_id in metadata
            container_client = self.blob_service.get_container_client(self.container_name)

            for blob in container_client.list_blobs(include='metadata'):
                if blob.metadata and blob.metadata.get('session_id') == session_id:
                    blob_client = self.blob_service.get_blob_client(
                        container=self.container_name,
                        blob=blob.name
                    )
                    content = blob_client.download_blob().readall().decode('utf-8')
                    return json.loads(content)

            logger.warning(f"No call data found for session: {session_id}")
            return None

        except Exception as e:
            logger.error(f"❌ Failed to retrieve call data: {e}")
            return None

    async def list_recent_calls(self, days: int = 7) -> list:
        """
        List recent calls with metadata

        Args:
            days: Number of days to look back

        Returns:
            List of call metadata
        """
        try:
            container_client = self.blob_service.get_container_client(self.container_name)
            calls = []

            for blob in container_client.list_blobs(include='metadata'):
                if blob.metadata:
                    calls.append({
                        "blob_name": blob.name,
                        "session_id": blob.metadata.get('session_id'),
                        "timestamp": blob.metadata.get('timestamp'),
                        "patient_name": blob.metadata.get('patient_name'),
                        "has_fiscal_code": blob.metadata.get('has_fiscal_code') == 'True',
                        "has_booking": blob.metadata.get('has_booking') == 'True',
                        "transcript_messages": int(blob.metadata.get('transcript_messages', 0)),
                        "caller_phone": blob.metadata.get('caller_phone'),
                        "blob_type": blob.metadata.get('type', 'call_data'),
                        "created": blob.creation_time
                    })

            # Sort by creation time, most recent first
            calls.sort(key=lambda x: x['created'], reverse=True)

            logger.info(f"📋 Found {len(calls)} recent calls")
            return calls

        except Exception as e:
            logger.error(f"❌ Failed to list recent calls: {e}")
            return []

    async def store_fiscal_code_only(self, session_id: str, fiscal_code: str, patient_data: Dict[str, Any]) -> str:
        """
        Store fiscal code separately for quick access

        Args:
            session_id: Session identifier
            fiscal_code: Generated fiscal code
            patient_data: Patient information used for generation

        Returns:
            str: Blob name where fiscal code was stored
        """
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            date_folder = datetime.now().strftime("%Y-%m-%d")
            blob_name = f"{self.blob_prefix}fiscal-codes/{date_folder}/{timestamp}_{session_id}_fiscal.json"

            fiscal_data = {
                "session_id": session_id,
                "timestamp": timestamp,
                "fiscal_code": fiscal_code,
                "patient_data": patient_data,
                "generated_at": datetime.now().isoformat()
            }

            blob_client = self.blob_service.get_blob_client(
                container=self.container_name,
                blob=blob_name
            )

            metadata = {
                "session_id": session_id,
                "fiscal_code": fiscal_code,
                "patient_name": patient_data.get('name', 'unknown'),
                "type": "fiscal_code_only"
            }

            blob_client.upload_blob(
                json.dumps(fiscal_data, indent=2, ensure_ascii=False).encode('utf-8'),
                overwrite=True,
                metadata=metadata
            )

            logger.success(f"✅ Fiscal code stored separately: {fiscal_code}")
            return blob_name

        except Exception as e:
            logger.error(f"❌ Failed to store fiscal code: {e}")
            raise

    async def store_caller_phone(self, session_id: str, caller_phone: str) -> str:
        """
        Store Talkdesk caller phone number for session tracking

        Args:
            session_id: Session identifier
            caller_phone: Phone number from Talkdesk bridge

        Returns:
            str: Blob name where caller phone was stored
        """
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            date_folder = datetime.now().strftime("%Y-%m-%d")
            blob_name = f"{self.blob_prefix}caller-phones/{date_folder}/{timestamp}_{session_id}_phone.json"

            phone_data = {
                "session_id": session_id,
                "timestamp": timestamp,
                "caller_phone": caller_phone,
                "source": "talkdesk_bridge",
                "stored_at": datetime.now().isoformat()
            }

            blob_client = self.blob_service.get_blob_client(
                container=self.container_name,
                blob=blob_name
            )

            metadata = {
                "session_id": session_id,
                "caller_phone": caller_phone,
                "source": "talkdesk_bridge",
                "type": "caller_phone"
            }

            blob_client.upload_blob(
                json.dumps(phone_data, indent=2, ensure_ascii=False).encode('utf-8'),
                overwrite=True,
                metadata=metadata
            )

            logger.success(f"✅ Caller phone stored: {caller_phone} for session {session_id}")
            return blob_name

        except Exception as e:
            logger.error(f"❌ Failed to store caller phone: {e}")
            raise

    async def retrieve_caller_phone(self, session_id: str) -> Optional[str]:
        """
        Retrieve caller phone number by session ID

        Args:
            session_id: Session identifier to search for

        Returns:
            str: Caller phone number or None if not found
        """
        try:
            # List blobs with session_id in metadata and type=caller_phone
            container_client = self.blob_service.get_container_client(self.container_name)

            for blob in container_client.list_blobs(include='metadata'):
                if (blob.metadata and
                    blob.metadata.get('session_id') == session_id and
                    blob.metadata.get('type') == 'caller_phone'):

                    blob_client = self.blob_service.get_blob_client(
                        container=self.container_name,
                        blob=blob.name
                    )
                    content = blob_client.download_blob().readall().decode('utf-8')
                    phone_data = json.loads(content)

                    caller_phone = phone_data.get('caller_phone')
                    logger.info(f"📞 Retrieved caller phone for session {session_id}: {caller_phone}")
                    return caller_phone

            logger.warning(f"No caller phone found for session: {session_id}")
            return None

        except Exception as e:
            logger.error(f"❌ Failed to retrieve caller phone: {e}")
            return None

    async def _upload_text_content(self, blob_path: str, text_content: str) -> bool:
        """
        Upload text content to Azure Blob Storage

        Args:
            blob_path: Full blob path (e.g., "call-logs/2025-10-06/log.txt")
            text_content: Text content to upload

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            blob_client = self.blob_service.get_blob_client(
                container=self.container_name,
                blob=blob_path
            )

            # Upload text content with UTF-8 encoding
            blob_client.upload_blob(
                text_content.encode('utf-8'),
                overwrite=True,
                metadata={
                    "content_type": "text/plain",
                    "uploaded_at": datetime.now().isoformat(),
                    "source": "call_logger"
                }
            )

            return True

        except Exception as e:
            logger.error(f"❌ Failed to upload text content to {blob_path}: {e}")
            return False
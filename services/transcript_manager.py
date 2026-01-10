"""
Transcript Manager for recording conversation transcripts and generating summaries
"""

import asyncio
import json
from datetime import datetime
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from loguru import logger

from services.call_storage import CallDataStorage


@dataclass
class TranscriptMessage:
    """Represents a single message in the conversation transcript"""
    role: str  # "user" or "assistant"
    content: str
    timestamp: Optional[str] = None


class TranscriptManager:
    """Manages conversation transcripts and call data extraction"""

    def __init__(self):
        self.conversation_log: List[TranscriptMessage] = []
        self.session_start_time: Optional[datetime] = None
        self.session_id: Optional[str] = None
        self.storage: Optional[CallDataStorage] = None

        # Initialize Azure storage
        try:
            self.storage = CallDataStorage()
        except Exception as e:
            logger.error(f"❌ Failed to initialize storage: {e}")
            self.storage = None

    def start_session(self, session_id: str) -> None:
        """Start a new conversation session"""
        self.session_id = session_id
        self.session_start_time = datetime.now()
        self.conversation_log.clear()
        logger.info(f"📝 Started transcript recording for session: {session_id}")

    def add_message(self, role: str, content: str) -> None:
        """Add a message to the conversation transcript"""
        if not content.strip():
            return

        timestamp = datetime.now().isoformat()
        message = TranscriptMessage(
            role=role,
            content=content.strip(),
            timestamp=timestamp
        )

        self.conversation_log.append(message)
        logger.debug(f"📝 Added {role} message: {content[:100]}{'...' if len(content) > 100 else ''}")

    def add_user_message(self, content: str) -> None:
        """Add a user message to transcript"""
        self.add_message("user", content)

    def add_assistant_message(self, content: str) -> None:
        """Add an assistant message to transcript"""
        self.add_message("assistant", content)

    def get_conversation_duration(self) -> int:
        """Get conversation duration in seconds"""
        if not self.session_start_time:
            return 0
        return int((datetime.now() - self.session_start_time).total_seconds())

    def generate_conversation_summary(self) -> str:
        """Generate a basic conversation summary"""
        if not self.conversation_log:
            return "No conversation recorded."

        user_messages = [msg for msg in self.conversation_log if msg.role == "user"]
        assistant_messages = [msg for msg in self.conversation_log if msg.role == "assistant"]

        # Basic summary template
        summary_parts = []

        if user_messages:
            summary_parts.append(f"User sent {len(user_messages)} messages")

        if assistant_messages:
            summary_parts.append(f"Assistant sent {len(assistant_messages)} messages")

        duration = self.get_conversation_duration()
        if duration > 0:
            summary_parts.append(f"Call duration: {duration} seconds")

        # Try to extract key information from the conversation
        conversation_text = " ".join([msg.content for msg in self.conversation_log])

        key_info = []
        if "prenotazione" in conversation_text.lower() or "booking" in conversation_text.lower():
            key_info.append("Booking-related conversation")

        if "nome" in conversation_text.lower() or "name" in conversation_text.lower():
            key_info.append("Personal information collected")

        if "email" in conversation_text.lower():
            key_info.append("Email address provided")

        if key_info:
            summary_parts.extend(key_info)

        return ". ".join(summary_parts) + "."

    async def generate_ai_summary(self, flow_manager=None) -> str:
        """Generate AI-powered conversation summary using OpenAI"""
        try:
            if not self.conversation_log:
                return "No conversation to summarize."

            # Create conversation text for summarization (without personal details)
            conversation_text = "\n".join([
                f"{msg.role.title()}: {msg.content}"
                for msg in self.conversation_log
            ])

            # Professional healthcare summarization prompt
            summary_prompt = f"""You are a healthcare call center supervisor reviewing a conversation transcript. Please provide a comprehensive summary of this healthcare booking conversation.

ANALYZE THE CONVERSATION AND SUMMARIZE:
1. What services did the patient want to book?
2. Was the booking completed successfully? If yes, provide booking details (date, time, location, services)
3. What was the conversation flow and outcome?
4. Were there any issues, cancellations, or rescheduling?
5. What authorizations were given (reminders, marketing)?
6. Overall call success and patient satisfaction

Be accurate and only state what actually happened in the conversation. Do not make assumptions.

CONVERSATION TRANSCRIPT:
{conversation_text}

SUMMARY (must in Italian):"""

            # Use LLM if available
            if flow_manager and hasattr(flow_manager, 'llm'):
                try:
                    from openai import AsyncOpenAI
                    import os

                    # Initialize OpenAI client
                    client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

                    # Generate summary using OpenAI
                    response = await client.chat.completions.create(
                        model="gpt-4.1",
                        messages=[
                            {"role": "system", "content": "You are a professional healthcare call center supervisor. Provide accurate, detailed summaries of healthcare booking conversations."},
                            {"role": "user", "content": summary_prompt}
                        ],
                        max_tokens=500,
                        temperature=0.1  # Low temperature for factual accuracy
                    )

                    ai_summary = response.choices[0].message.content.strip()
                    logger.success("🤖 AI summary generated successfully")

                    # Now append personal details that weren't sent to LLM
                    return self._append_personal_details_to_summary(ai_summary, flow_manager)

                except Exception as e:
                    logger.error(f"❌ AI summary generation failed: {e}")
                    return self._generate_fallback_summary(flow_manager)
            else:
                logger.warning("⚠️ Flow manager or LLM not available, using fallback summary")
                return self._generate_fallback_summary(flow_manager)

        except Exception as e:
            logger.error(f"❌ Error generating summary: {e}")
            return self._generate_fallback_summary(flow_manager)

    def _append_personal_details_to_summary(self, ai_summary: str, flow_manager) -> str:
        """Append personal details to AI-generated summary (not sent to LLM)"""
        personal_details = []

        if flow_manager:
            # Extract personal information
            patient_name = flow_manager.state.get("patient_name", "")
            patient_surname = flow_manager.state.get("patient_surname", "")
            patient_phone = flow_manager.state.get("patient_phone", "")
            patient_email = flow_manager.state.get("patient_email", "")
            caller_phone = flow_manager.state.get("caller_phone_from_talkdesk", "")
            fiscal_code = flow_manager.state.get("generated_fiscal_code", "")

            personal_details.append("\n" + "="*50)
            personal_details.append("INFORMAZIONI PAZIENTE (Riservato)")
            personal_details.append("="*50)

            if patient_name or patient_surname:
                personal_details.append(f"Nome Paziente: {patient_name} {patient_surname}".strip())
            if patient_phone:
                personal_details.append(f"Numero di Telefono: {patient_phone}")
            if caller_phone and caller_phone != patient_phone:
                personal_details.append(f"Telefono Chiamante (Talkdesk): {caller_phone}")
            if patient_email:
                personal_details.append(f"Indirizzo Email: {patient_email}")
            if fiscal_code:
                personal_details.append(f"Codice Fiscale: {fiscal_code}")

            # Add call metadata
            duration = self.get_conversation_duration()
            personal_details.append(f"Durata Chiamata: {duration} secondi ({duration // 60}:{duration % 60:02d})")
            personal_details.append(f"ID Sessione: {self.session_id}")

        return ai_summary + "\n".join(personal_details)

    def _generate_fallback_summary(self, flow_manager) -> str:
        """Generate fallback summary when AI is not available"""
        duration = self.get_conversation_duration()
        user_messages = [msg for msg in self.conversation_log if msg.role == "user"]
        assistant_messages = [msg for msg in self.conversation_log if msg.role == "assistant"]

        fallback_parts = []
        fallback_parts.append("=== CALL SUMMARY (Fallback) ===")
        fallback_parts.append(f"Call Duration: {duration} seconds")
        fallback_parts.append(f"Total Messages: {len(self.conversation_log)} ({len(user_messages)} user, {len(assistant_messages)} assistant)")

        if flow_manager:
            # Add booking information
            selected_services = flow_manager.state.get("selected_services", [])
            final_booking = flow_manager.state.get("final_booking", {})

            if selected_services:
                fallback_parts.append(f"Services: {len(selected_services)} service(s) selected")
            if final_booking.get("code"):
                fallback_parts.append(f"Booking created with code: {final_booking.get('code')}")

        # Add personal details 
        return self._append_personal_details_to_summary("\n".join(fallback_parts), flow_manager)

    async def extract_and_store_call_data(self, flow_manager) -> bool:
        """Extract all call data and store in Azure Storage"""
        try:
            if not self.session_id:
                logger.error("❌ No session ID available for storage")
                return False

            logger.info(f"📊 Extracting call data for session: {self.session_id}")

            # Generate conversation summary
            summary = await self.generate_ai_summary(flow_manager)

            # Extract patient data from flow manager state
            patient_data = {
                "name": flow_manager.state.get("patient_name", ""),
                "surname": flow_manager.state.get("patient_surname", ""),
                "birth_date": flow_manager.state.get("patient_dob", ""),
                "gender": flow_manager.state.get("patient_gender", ""),
                "birth_city": flow_manager.state.get("patient_birth_city", ""),
                "address": flow_manager.state.get("patient_address", ""),
                "phone": flow_manager.state.get("patient_phone", ""),
                "email": flow_manager.state.get("patient_email", "")
            }

            # Extract booking data if available (serialize HealthService objects)
            selected_services = flow_manager.state.get("selected_services", [])
            serialized_services = []
            for service in selected_services:
                if hasattr(service, '__dict__'):
                    # Serialize HealthService object to dict
                    serialized_services.append({
                        "uuid": getattr(service, 'uuid', ''),
                        "name": getattr(service, 'name', ''),
                        "description": getattr(service, 'description', ''),
                        "price": getattr(service, 'price', 0),
                        "duration": getattr(service, 'duration', 0)
                    })
                else:
                    serialized_services.append(service)

            booking_data = {
                "booking_code": flow_manager.state.get("final_booking", {}).get("code", ""),
                "booking_uuid": flow_manager.state.get("final_booking", {}).get("uuid", ""),
                "selected_services": serialized_services,
                "booked_slots": flow_manager.state.get("booked_slots", [])
            }

            # Prepare complete call data
            call_data = {
                "session_id": self.session_id,
                "timestamp": datetime.now().isoformat(),
                "call_start_time": self.session_start_time.isoformat() if self.session_start_time else None,
                "call_duration_seconds": self.get_conversation_duration(),

                # Generated fiscal code
                "fiscal_code": flow_manager.state.get("generated_fiscal_code", ""),
                "fiscal_code_generation_data": flow_manager.state.get("fiscal_code_generation_data", {}),
                "fiscal_code_error": flow_manager.state.get("fiscal_code_error", ""),

                # Patient information
                "patient_data": patient_data,

                # Booking information
                "booking_data": booking_data,

                # Conversation data
                "transcript": [
                    {
                        "role": msg.role,
                        "content": msg.content,
                        "timestamp": msg.timestamp
                    }
                    for msg in self.conversation_log
                ],
                "summary": summary,
                "total_messages": len(self.conversation_log),
                "user_messages": len([msg for msg in self.conversation_log if msg.role == "user"]),
                "assistant_messages": len([msg for msg in self.conversation_log if msg.role == "assistant"]),

                # Authorizations
                "reminder_authorization": flow_manager.state.get("reminder_authorization", False),
                "marketing_authorization": flow_manager.state.get("marketing_authorization", False)
            }

            # Store in Azure Storage
            if self.storage:
                blob_name = await self.storage.store_call_data(self.session_id, call_data)
                logger.success(f"✅ Call data stored successfully: {blob_name}")

                # Also store fiscal code separately if generated
                fiscal_code = call_data.get("fiscal_code")
                if fiscal_code:
                    await self.storage.store_fiscal_code_only(
                        self.session_id,
                        fiscal_code,
                        patient_data
                    )

                return True
            else:
                logger.error("❌ Storage not available")
                return False

        except Exception as e:
            logger.error(f"❌ Failed to extract and store call data: {e}")
            return False

    def get_transcript_json(self) -> str:
        """Get transcript as JSON string"""
        return json.dumps([
            {
                "role": msg.role,
                "content": msg.content,
                "timestamp": msg.timestamp
            }
            for msg in self.conversation_log
        ], ensure_ascii=False, indent=2)

    def clear_session(self) -> None:
        """Clear current session data"""
        self.conversation_log.clear()
        self.session_id = None
        self.session_start_time = None
        logger.info("🗑️ Transcript session cleared")


# Session-specific transcript managers
_transcript_managers = {}

def get_transcript_manager(session_id: str) -> TranscriptManager:
    """Get or create session-specific transcript manager"""
    if session_id not in _transcript_managers:
        _transcript_managers[session_id] = TranscriptManager()
        logger.debug(f"📝 Created new transcript manager for session: {session_id}")
    return _transcript_managers[session_id]

def cleanup_transcript_manager(session_id: str) -> None:
    """Clean up transcript manager for session"""
    if session_id in _transcript_managers:
        del _transcript_managers[session_id]
        logger.debug(f"🗑️ Cleaned up transcript manager for session: {session_id}")

# Global transcript manager instance (for backward compatibility)
transcript_manager = TranscriptManager()
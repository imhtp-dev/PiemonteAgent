"""
Call Data Extractor Service
Extracts call data and saves to tb_stat table in Supabase
Based on PDF documentation
Enhanced with LLM analysis and backup mechanism
"""

import uuid
import os
import json
import asyncio
from datetime import datetime
from typing import Dict, Any, Optional, List
from pathlib import Path
from loguru import logger
from openai import AsyncOpenAI
from config.settings import settings
from services.database import db
from utils.tracing import trace_api_call

# Valid enum values for call categorization
VALID_ESITO_CHIAMATA = ["COMPLETATA", "TRASFERITA", "NON COMPLETATA"]

VALID_MOTIVAZIONE = {
    "COMPLETATA": ["Info fornite", "Pren. effettuata"],
    "TRASFERITA": ["Mancata comprensione", "Argomento sconosciuto", "Richiesta paziente", "Prenotazione"],
    "NON COMPLETATA": ["Interrotta dal paziente", "Fuori orario", "Problema Tecnico"]
}

ALL_MOTIVAZIONI = [m for motiv_list in VALID_MOTIVAZIONE.values() for m in motiv_list]

# Pricing constants per minute (EUR)
PRICE_PER_MINUTE_INFO = 0.006      # €0.006/min for info calls
PRICE_PER_MINUTE_BOOKING = 0.44   # €0.44/min for booking calls (including incomplete)


def validate_and_fix_llm_output(analysis: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate LLM output and fix invalid values.
    Ensures esito_chiamata and motivazione are valid enum values.
    """
    esito = analysis.get("esito_chiamata", "")
    motivazione = analysis.get("motivazione", "")

    # Normalize common LLM variations (LLM adds periods to Italian abbreviations)
    motivazione_fixes = {
        "Info. fornite": "Info fornite",
        "Pren. effettuata": "Pren. effettuata",  # This one IS correct with period
        "info fornite": "Info fornite",
        "INFO FORNITE": "Info fornite",
    }
    if motivazione in motivazione_fixes:
        logger.debug(f"🔧 Normalizing motivazione '{motivazione}' → '{motivazione_fixes[motivazione]}'")
        motivazione = motivazione_fixes[motivazione]
        analysis["motivazione"] = motivazione

    # Validate esito_chiamata
    if esito not in VALID_ESITO_CHIAMATA:
        logger.warning(f"⚠️ Invalid esito_chiamata '{esito}', defaulting to 'COMPLETATA'")
        esito = "COMPLETATA"
        analysis["esito_chiamata"] = esito

    # Validate motivazione matches esito
    valid_for_esito = VALID_MOTIVAZIONE.get(esito, [])
    if motivazione not in valid_for_esito:
        # Try to find closest match in ALL motivazioni
        if motivazione in ALL_MOTIVAZIONI:
            # Valid motivazione but wrong esito - fix esito to match
            for e, motiv_list in VALID_MOTIVAZIONE.items():
                if motivazione in motiv_list:
                    logger.warning(f"⚠️ Fixing esito_chiamata from '{esito}' to '{e}' to match motivazione '{motivazione}'")
                    analysis["esito_chiamata"] = e
                    break
        else:
            # Invalid motivazione - use default for esito
            default_motiv = valid_for_esito[0] if valid_for_esito else "Info fornite"
            logger.warning(f"⚠️ Invalid motivazione '{motivazione}', defaulting to '{default_motiv}'")
            analysis["motivazione"] = default_motiv

    # Validate action
    valid_actions = ["completed", "question", "transfer", "book"]
    if analysis.get("action") not in valid_actions:
        analysis["action"] = "completed"

    # Validate sentiment
    valid_sentiments = ["positive", "neutral", "negative"]
    if analysis.get("sentiment") not in valid_sentiments:
        analysis["sentiment"] = "neutral"

    # Validate service
    valid_services = ["1", "2", "3", "4", "5"]
    if str(analysis.get("service", "5")) not in valid_services:
        analysis["service"] = "5"
    else:
        analysis["service"] = str(analysis.get("service", "5"))

    return analysis


# OpenAI client - will be initialized lazily when needed
openai_client = None

def get_openai_client():
    """Get or create OpenAI client (lazy initialization)"""
    global openai_client
    if openai_client is None:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable not set")
        openai_client = AsyncOpenAI(api_key=api_key)
    return openai_client


class CallDataExtractor:
    """Extract and store call data to tb_stat table"""

    def __init__(self, session_id: str):
        self.session_id = session_id
        self.call_id = str(uuid.uuid4())
        self.started_at = None
        self.ended_at = None
        self.transcript = []
        self.llm_token_count = 0
        self.functions_called = []  # ✅ NEW - Track function calls
        self.assistant_id = os.getenv("INFO_AGENT_ASSISTANT_ID", "pipecat-info-lombardy-001")  # ✅ From env
        self.region = os.getenv("INFO_AGENT_REGION", "Lombardia")  # ✅ Region for filtering
        self.caller_phone = None
        self.interaction_id = None

        # Recording fields (populated by RecordingManager)
        self.recording_url_stereo: Optional[str] = None
        self.recording_url_user: Optional[str] = None
        self.recording_url_bot: Optional[str] = None
        self.recording_duration: Optional[float] = None

        logger.info(f"📊 Call data extractor initialized for session: {session_id}")
        logger.info(f"📞 Call ID: {self.call_id}")
    
    def start_call(self, caller_phone: Optional[str] = None, interaction_id: Optional[str] = None):
        """Mark call start time"""
        self.started_at = datetime.now()
        self.caller_phone = caller_phone
        self.interaction_id = interaction_id
        logger.info(f"⏱️ Call started at: {self.started_at}")
    
    def end_call(self):
        """Mark call end time"""
        self.ended_at = datetime.now()
        logger.info(f"⏱️ Call ended at: {self.ended_at}")
    
    def add_transcript_entry(self, role: str, content: str):
        """Add entry to transcript"""
        self.transcript.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat()
        })
        logger.debug(f"📝 Transcript entry added: {role} - {content[:50]}...")

    def add_function_call(self, function_name: str, parameters: dict = None, result: dict = None):
        """Track function calls for analytics"""
        self.functions_called.append({
            "function_name": function_name,
            "parameters": parameters or {},
            "result": result or {},
            "timestamp": datetime.now().isoformat()
        })
        logger.info(f"🔧 Function called: {function_name}")

    def increment_tokens(self, tokens: int):
        """Track LLM token usage"""
        self.llm_token_count += tokens
        logger.debug(f"🔢 Token count: +{tokens} (total: {self.llm_token_count})")
    
    def _calculate_duration(self) -> Optional[float]:
        """Calculate call duration in seconds"""
        if self.started_at and self.ended_at:
            delta = self.ended_at - self.started_at
            return delta.total_seconds()
        return None
    
    def _calculate_cost(self, duration_seconds: Optional[float], call_type: str = "info") -> Optional[float]:
        """Calculate call cost based on call type (minutes × rate)"""
        if not duration_seconds:
            return None
        duration_minutes = duration_seconds / 60
        rate = PRICE_PER_MINUTE_BOOKING if call_type in ["booking", "booking_incomplete"] else PRICE_PER_MINUTE_INFO
        return round(duration_minutes * rate, 4)

    def _determine_call_type(self, flow_state: Dict[str, Any], booking_data: Dict[str, Any]) -> str:
        """
        Determine if call is booking or info type.
        - booking: Completed booking (has booking_code)
        - booking_incomplete: Started booking but didn't complete (has selected_services)
        - info: Information/question call only
        """
        if booking_data.get("booking_code"):
            return "booking"
        if flow_state.get("selected_services"):
            return "booking_incomplete"
        return "info"
    
    def _determine_action(self, flow_state: Dict[str, Any]) -> str:
        """
        Determine action type based on flow state
        Values: completed, question, transfer, book
        """
        if flow_state.get("transfer_requested"):
            transfer_reason = flow_state.get("transfer_reason", "").lower()
            if "unknown" in transfer_reason or "don't know" in transfer_reason:
                return "question"
            elif "book" in transfer_reason:
                return "book"
            else:
                return "transfer"
        
        # Check if info was provided successfully
        functions_called = flow_state.get("functions_called", [])
        if functions_called:
            return "completed"
        
        return "completed"
    
    def _determine_sentiment(self, flow_state: Dict[str, Any], summary: str) -> str:
        """
        Determine sentiment from conversation
        Values: positive, negative, neutral, N/A
        """
        # Check if transfer was requested (usually negative)
        if flow_state.get("transfer_requested"):
            transfer_reason = flow_state.get("transfer_reason", "").lower()
            if "frustrat" in transfer_reason or "angry" in transfer_reason:
                return "negative"
            return "neutral"
        
        # Check if functions were called successfully
        functions_called = flow_state.get("functions_called", [])
        if functions_called:
            # If info was provided, likely positive
            return "positive"
        
        # Try to analyze summary for sentiment keywords
        if summary:
            summary_lower = summary.lower()
            positive_words = ["grazie", "perfetto", "ottimo", "bene", "soddisfatto"]
            negative_words = ["problema", "male", "pessimo", "frustrato", "arrabbiato"]
            
            positive_count = sum(1 for word in positive_words if word in summary_lower)
            negative_count = sum(1 for word in negative_words if word in summary_lower)
            
            if positive_count > negative_count:
                return "positive"
            elif negative_count > positive_count:
                return "negative"
        
        return "neutral"
    
    def _determine_esito_chiamata(self, flow_state: Dict[str, Any]) -> str:
        """
        Determine call outcome
        Values: COMPLETATA, TRASFERITA, NON COMPLETATA
        """
        if flow_state.get("transfer_requested"):
            return "TRASFERITA"
        
        # Check if conversation ended naturally
        final_node = flow_state.get("current_node", "")
        if final_node == "goodbye" or flow_state.get("conversation_ended"):
            return "COMPLETATA"
        
        # If call ended abruptly (no goodbye)
        functions_called = flow_state.get("functions_called", [])
        if functions_called:
            return "COMPLETATA"
        
        # Patient likely ended call prematurely
        return "NON COMPLETATA"
    
    def _determine_motivazione(self, flow_state: Dict[str, Any], action: str) -> str:
        """
        Determine call motivation/reason
        Values: Info fornite, Argomento sconosciuto, Interrotta dal paziente, 
                Prenotazione, Mancata comprensione, Richiesta paziente
        """
        if action == "completed":
            return "Info fornite"
        elif action == "question":
            return "Argomento sconosciuto"
        elif action == "book":
            return "Prenotazione"
        elif action == "transfer":
            transfer_reason = flow_state.get("transfer_reason", "").lower()
            if "understand" in transfer_reason or "comprens" in transfer_reason:
                return "Mancata comprensione"
            else:
                return "Richiesta paziente"
        
        # Check if patient interrupted
        if flow_state.get("user_interrupted"):
            return "Interrotta dal paziente"
        
        return "Info fornite"
    
    def _extract_patient_intent(self, flow_state: Dict[str, Any]) -> Optional[str]:
        """
        Extract brief summary of patient's intent
        """
        # Get functions that were called
        functions_called = flow_state.get("functions_called", [])
        if functions_called:
            intent_parts = []
            
            for func in functions_called:
                if "knowledge" in func:
                    intent_parts.append("richiesta informazioni generali")
                elif "price" in func:
                    intent_parts.append("richiesta prezzi visite")
                elif "exam" in func:
                    intent_parts.append("informazioni su esami richiesti")
                elif "clinic" in func:
                    intent_parts.append("informazioni su orari/sede")
            
            if intent_parts:
                return "; ".join(intent_parts)
        
        # Check transfer reason
        if flow_state.get("transfer_requested"):
            transfer_reason = flow_state.get("transfer_reason", "")
            if transfer_reason:
                return f"Richiesta trasferimento: {transfer_reason}"
        
        return "Richiesta informazioni mediche"
    
    def _generate_transcript_text(self) -> str:
        """Generate formatted transcript text"""
        if not self.transcript:
            return ""
        
        lines = []
        for entry in self.transcript:
            role = "Paziente" if entry["role"] == "user" else "Assistente"
            lines.append(f"[{role}]: {entry['content']}")
        
        return "\n".join(lines)
    
    @trace_api_call("llm.call_analysis", add_args=False)
    async def _analyze_call_with_llm(self, transcript_text: str, flow_state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Use LLM to analyze call and extract structured data

        Returns:
            Dict with: action, sentiment, service (1-5), motivazione, esito_chiamata, patient_intent, summary
        """
        try:
            logger.info("🤖 Analyzing call with LLM...")

            # Build prompt with strict enum values and examples
            prompt = f"""You are an expert analyst of telephone conversations in the healthcare sector.
Analyze the transcript and classify the call.

## CLASSIFICATION RULES

### ESITO_CHIAMATA + MOTIVAZIONE (STRICT - use ONLY these combinations):

**COMPLETATA** (call completed successfully):
- "Info fornite" → L'AI ha risposto con successo alla richiesta del paziente (info/domande)
- "Pren. effettuata" → L'AI ha completato una prenotazione per il paziente (booking confermato)

**TRASFERITA** (call transferred to human operator):
- "Mancata comprensione" → AI non comprende o non è certa di aver compreso la domanda del paziente
- "Argomento sconosciuto" → AI non sa rispondere poichè non possiede conoscenza sull'argomento richiesto
- "Richiesta paziente" → Paziente ha richiesto di parlare con un operatore umano
- "Prenotazione" → Paziente viene trasferito per effettuare prenotazione

**NON COMPLETATA** (call ended without resolution):
- "Interrotta dal paziente" → Paziente interrompe in modo inaspettato la chiamata (chiusura dopo pochi secondi / a metà)
- "Fuori orario" → AI ha necessità di trasferire la chiamata ad un operatore ma in quel momento gli operatori umani non sono disponibili
- "Problema Tecnico" → L'AI non è riuscita a rispondere alla richiesta del paziente a causa di un problema tecnico

### OTHER FIELDS:
- ACTION: completed | question | transfer | book
- SENTIMENT: positive | neutral | negative
- SERVICE (IVR code):
  - 1: Blood sampling times or laboratory services
  - 2: Visits, Ultrasounds, or Outpatient Services
  - 3: MRIs, X-rays, CT scans, DEXA scans, Mammograms
  - 4: Sports medical visits
  - 5: OTHER INFORMATION
- PATIENT_INTENT: Brief description (max 100 chars)
- SUMMARY: Max 250 characters

## OUTPUT FORMAT (JSON only, no explanations):
{{"summary": "...", "action": "...", "sentiment": "...", "service": "1-5", "esito_chiamata": "COMPLETATA|TRASFERITA|NON COMPLETATA", "motivazione": "...", "patient_intent": "..."}}

CRITICAL: motivazione MUST be one of the exact values listed above. No variations allowed.

TRANSCRIPT:
{transcript_text}"""

            # Call OpenAI with same model as conversation (lazy initialization)
            client = get_openai_client()
            response = await client.chat.completions.create(
                model="gpt-4.1",  # Full GPT-4.1 model
                messages=[
                    {"role": "system", "content": f"You are a call analysis expert. Always generate output in {settings.agent_language}. Reply only with valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.3,
                max_tokens=500
            )

            # Parse JSON response
            analysis_text = response.choices[0].message.content.strip()

            # Remove markdown code blocks if present
            if analysis_text.startswith("```json"):
                analysis_text = analysis_text.replace("```json", "").replace("```", "").strip()
            elif analysis_text.startswith("```"):
                analysis_text = analysis_text.replace("```", "").strip()

            analysis = json.loads(analysis_text)

            # Validate and fix LLM output to ensure valid enum values
            analysis = validate_and_fix_llm_output(analysis)

            logger.success(f"✅ LLM Analysis completed:")
            logger.info(f"   Action: {analysis.get('action')}")
            logger.info(f"   Sentiment: {analysis.get('sentiment')}")
            logger.info(f"   Service: {analysis.get('service')}")
            logger.info(f"   Esito: {analysis.get('esito_chiamata')}")
            logger.info(f"   Motivazione: {analysis.get('motivazione')}")

            return analysis

        except json.JSONDecodeError as e:
            logger.error(f"❌ Failed to parse LLM JSON response: {e}")
            logger.error(f"   Raw response: {analysis_text if 'analysis_text' in locals() else 'N/A'}")
            # Return fallback values
            return self._get_fallback_analysis(flow_state)

        except Exception as e:
            logger.error(f"❌ LLM analysis failed: {e}")
            import traceback
            traceback.print_exc()
            # Return fallback values
            return self._get_fallback_analysis(flow_state)

    def _get_fallback_analysis(self, flow_state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Fallback analysis when LLM fails
        Uses rule-based logic
        """
        logger.warning("⚠️ Using fallback rule-based analysis")

        action = self._determine_action(flow_state)
        sentiment = self._determine_sentiment(flow_state, "")
        esito_chiamata = self._determine_esito_chiamata(flow_state)
        motivazione = self._determine_motivazione(flow_state, action)
        patient_intent = self._extract_patient_intent(flow_state)

        # Determine service code based on functions called
        service = "5"  # Default: OTHER
        functions_called = flow_state.get("functions_called", [])
        for func in functions_called:
            if "clinic" in func.lower() or "blood" in func.lower():
                service = "1"
                break
            elif "price" in func.lower() or "visit" in func.lower():
                service = "4"  # Sports medical visits
                break
            elif "exam" in func.lower():
                service = "2"
                break

        summary = f"Chiamata {esito_chiamata.lower()}. Paziente ha richiesto: {patient_intent or 'informazioni'}."

        fallback_result = {
            "summary": summary[:250],
            "action": action,
            "sentiment": sentiment,
            "service": service,
            "esito_chiamata": esito_chiamata,
            "motivazione": motivazione,
            "patient_intent": patient_intent or "Richiesta informazioni"
        }

        # Validate fallback result to ensure valid enum values
        return validate_and_fix_llm_output(fallback_result)

    def _save_to_backup_file(self, data: Dict[str, Any]) -> bool:
        """
        Save call data to backup JSON file when database save fails

        Args:
            data: Call data to backup

        Returns:
            True if successful, False otherwise
        """
        try:
            # Create backup directory if it doesn't exist
            backup_dir = Path("info_agent/call_logs/failed_saves")
            backup_dir.mkdir(parents=True, exist_ok=True)

            # Create backup file
            backup_file = backup_dir / f"{self.call_id}.json"

            # Add metadata
            backup_data = {
                "call_id": self.call_id,
                "session_id": self.session_id,
                "saved_at": datetime.now().isoformat(),
                "retry_count": 0,
                "data": data
            }

            # Write to file
            with open(backup_file, "w", encoding="utf-8") as f:
                json.dump(backup_data, f, indent=2, ensure_ascii=False)

            logger.success(f"💾 Backup file created: {backup_file}")
            return True

        except Exception as e:
            logger.error(f"❌ Failed to create backup file: {e}")
            import traceback
            traceback.print_exc()
            return False

    def _generate_summary(self, flow_state: Dict[str, Any], patient_intent: str) -> str:
        """Generate AI summary of the call"""
        # For now, create a structured summary
        # In future, could use LLM to generate more natural summary

        action = self._determine_action(flow_state)
        esito = self._determine_esito_chiamata(flow_state)

        summary_parts = [
            f"Chiamata {esito.lower()}.",
            f"Paziente ha richiesto: {patient_intent}.",
        ]

        if action == "completed":
            summary_parts.append("Informazioni fornite con successo dall'assistente vocale.")
        elif action == "transfer":
            transfer_reason = flow_state.get("transfer_reason", "richiesta del paziente")
            summary_parts.append(f"Chiamata trasferita a operatore umano per: {transfer_reason}.")
        elif action == "question":
            summary_parts.append("Argomento sconosciuto, trasferita a operatore per assistenza.")

        functions_called = flow_state.get("functions_called", [])
        if functions_called:
            summary_parts.append(f"Funzioni utilizzate: {', '.join(functions_called)}.")

        return " ".join(summary_parts)

    @trace_api_call("llm.transfer_analysis", add_args=False)
    async def analyze_for_transfer(self, flow_state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Run early analysis for transfer escalation (before WebSocket closes)
        Reuses LLM analysis logic to extract data needed for escalation API

        This is called BEFORE transfer happens to get:
        - summary (max 250 chars)
        - sentiment
        - action (will be "transfer")
        - duration_seconds (from call start to NOW)
        - service (IVR code 1-5)

        Args:
            flow_state: Flow manager state

        Returns:
            Dict with escalation data
        """
        try:
            logger.info("🔄 Running early analysis for transfer escalation...")

            # Calculate duration from start to NOW (not call end)
            if self.started_at:
                duration_seconds = (datetime.now() - self.started_at).total_seconds()
            else:
                duration_seconds = 0

            # Generate transcript text
            transcript_text = self._generate_transcript_text()

            # Run LLM analysis (same as post-call)
            if transcript_text:
                analysis = await self._analyze_call_with_llm(transcript_text, flow_state)
            else:
                logger.warning("⚠️ No transcript for transfer analysis, using fallback")
                analysis = self._get_fallback_analysis(flow_state)

            # Extract and format data for escalation API (pass through all LLM fields)
            escalation_data = {
                "summary": analysis.get("summary", "Transfer richiesto")[:250],
                "sentiment": analysis.get("sentiment", "neutral"),
                "action": analysis.get("action", "transfer"),
                "duration_seconds": int(duration_seconds),
                "service": str(analysis.get("service", "5")),
                # Pass through LLM-generated categorization
                "esito_chiamata": analysis.get("esito_chiamata", "TRASFERITA"),
                "motivazione": analysis.get("motivazione", "Richiesta paziente"),
                "patient_intent": analysis.get("patient_intent", "Richiesta assistenza operatore")
            }

            logger.success(f"✅ Transfer analysis completed:")
            logger.info(f"   Duration: {escalation_data['duration_seconds']}s")
            logger.info(f"   Sentiment: {escalation_data['sentiment']}")
            logger.info(f"   Service: {escalation_data['service']}")
            logger.info(f"   Esito: {escalation_data['esito_chiamata']}")
            logger.info(f"   Motivazione: {escalation_data['motivazione']}")
            logger.info(f"   Summary: {escalation_data['summary'][:100]}...")

            return escalation_data

        except Exception as e:
            logger.error(f"❌ Transfer analysis failed: {e}")
            import traceback
            traceback.print_exc()

            # Return safe defaults with valid enum values
            return {
                "summary": "Transfer richiesto dal paziente",
                "sentiment": "neutral",
                "action": "transfer",
                "duration_seconds": 0,
                "service": "5",
                "esito_chiamata": "TRASFERITA",
                "motivazione": "Richiesta paziente",
                "patient_intent": "Richiesta assistenza operatore"
            }

    def _extract_booking_data(self, flow_state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract all booking-related data from flow state.
        Returns dict with booking fields, using None for missing values.
        """
        booking_data = {}

        # Patient details
        booking_data["patient_first_name"] = flow_state.get("patient_first_name")
        booking_data["patient_surname"] = flow_state.get("patient_surname")
        booking_data["patient_dob"] = flow_state.get("patient_dob")
        booking_data["patient_gender"] = flow_state.get("patient_gender")
        booking_data["patient_address"] = flow_state.get("patient_address")

        # Selected services (JSONB array)
        selected_services = flow_state.get("selected_services", [])
        if selected_services:
            # Convert service objects to JSON-serializable format
            services_json = []
            for svc in selected_services:
                if hasattr(svc, '__dict__'):
                    # It's an object, convert to dict
                    services_json.append({
                        "uuid": getattr(svc, 'uuid', None),
                        "name": getattr(svc, 'name', None),
                        "code": getattr(svc, 'code', None),
                        "price": getattr(svc, 'price', None),
                    })
                elif isinstance(svc, dict):
                    services_json.append(svc)
            booking_data["selected_services"] = json.dumps(services_json) if services_json else None
        else:
            booking_data["selected_services"] = None

        # Search terms used
        search_term = flow_state.get("current_search_term")
        if search_term:
            booking_data["search_terms_used"] = json.dumps([search_term])
        else:
            booking_data["search_terms_used"] = None

        # Selected center
        selected_center = flow_state.get("selected_center")
        if selected_center:
            if hasattr(selected_center, '__dict__'):
                booking_data["selected_center_uuid"] = getattr(selected_center, 'uuid', None)
                booking_data["selected_center_name"] = getattr(selected_center, 'name', None)
                booking_data["selected_center_address"] = getattr(selected_center, 'address', None)
                booking_data["selected_center_city"] = getattr(selected_center, 'city', None)
            elif isinstance(selected_center, dict):
                booking_data["selected_center_uuid"] = selected_center.get('uuid')
                booking_data["selected_center_name"] = selected_center.get('name')
                booking_data["selected_center_address"] = selected_center.get('address')
                booking_data["selected_center_city"] = selected_center.get('city')
        else:
            booking_data["selected_center_uuid"] = None
            booking_data["selected_center_name"] = None
            booking_data["selected_center_address"] = None
            booking_data["selected_center_city"] = None

        # Booked slots (JSONB array)
        booked_slots = flow_state.get("booked_slots", [])
        if booked_slots:
            booking_data["booked_slots"] = json.dumps(booked_slots)
            # Extract appointment datetime from first slot and convert to datetime object
            first_slot = booked_slots[0] if booked_slots else {}
            start_time_str = first_slot.get("start_time")
            if start_time_str:
                try:
                    # Parse ISO format string to datetime object (required by asyncpg)
                    from datetime import datetime as dt
                    booking_data["appointment_datetime"] = dt.fromisoformat(start_time_str)
                except (ValueError, AttributeError) as e:
                    logger.warning(f"⚠️ Could not parse appointment datetime: {start_time_str} - {e}")
                    booking_data["appointment_datetime"] = None
            else:
                booking_data["appointment_datetime"] = None
        else:
            booking_data["booked_slots"] = None
            booking_data["appointment_datetime"] = None

        # Date/time preferences
        booking_data["preferred_date"] = flow_state.get("preferred_date")
        booking_data["preferred_time"] = flow_state.get("preferred_time")

        # Booking result
        final_booking = flow_state.get("final_booking", {})
        if final_booking:
            booking_data["booking_code"] = final_booking.get("code") or final_booking.get("booking_code")
        else:
            booking_data["booking_code"] = None

        # Calculate total booking cost from booked slots
        total_cost = 0
        if booked_slots:
            for slot in booked_slots:
                slot_price = slot.get("price") or slot.get("slot_price") or 0
                if isinstance(slot_price, (int, float)):
                    total_cost += slot_price
        booking_data["total_booking_cost"] = total_cost if total_cost > 0 else None

        # Cerba membership and authorizations
        booking_data["is_cerba_member"] = flow_state.get("is_cerba_member", False)
        booking_data["reminder_authorization"] = flow_state.get("reminder_authorization", False)
        booking_data["marketing_authorization"] = flow_state.get("marketing_authorization", False)

        # Transfer info
        booking_data["transfer_reason"] = flow_state.get("transfer_reason")
        transfer_ts = flow_state.get("transfer_timestamp")
        if transfer_ts:
            try:
                # Convert to datetime if it's a string timestamp
                if isinstance(transfer_ts, str):
                    booking_data["transfer_timestamp"] = datetime.fromisoformat(transfer_ts) if 'T' in transfer_ts else None
                else:
                    booking_data["transfer_timestamp"] = None
            except:
                booking_data["transfer_timestamp"] = None
        else:
            booking_data["transfer_timestamp"] = None

        return booking_data

    @trace_api_call("db.save_call_data", add_args=False)
    async def save_to_database(self, flow_state: Dict[str, Any]) -> bool:
        """
        Extract all data and UPDATE tb_stat table row (bridge already created it)
        Uses LLM analysis for intelligent field extraction
        Now includes ALL booking data fields for unified storage

        Args:
            flow_state: Flow manager state containing call information

        Returns:
            True if successful, False otherwise
        """
        try:
            logger.info(f"💾 Extracting call data for session: {self.session_id}")

            # Calculate basic metrics
            duration_seconds = self._calculate_duration()

            # Generate transcript
            transcript_text = self._generate_transcript_text()

            # Get phone number
            phone_number = flow_state.get("caller_phone") or flow_state.get("caller_phone_from_talkdesk") or self.caller_phone

            # Check if we have pre-computed transfer analysis
            if flow_state.get("transfer_requested") and flow_state.get("transfer_analysis"):
                logger.info("📋 Using pre-computed transfer analysis for Supabase save")
                transfer_data = flow_state["transfer_analysis"]

                # Use LLM-generated values from transfer analysis (no hardcoding)
                action = transfer_data.get("action", "transfer")
                sentiment = transfer_data.get("sentiment", "neutral")
                service = str(transfer_data.get("service", "5"))
                esito_chiamata = transfer_data.get("esito_chiamata", "TRASFERITA")
                motivazione = transfer_data.get("motivazione", "Richiesta paziente")
                patient_intent = transfer_data.get("patient_intent", "Richiesta assistenza operatore")
                summary = transfer_data.get("summary", "Transfer richiesto")[:250]

                # Duration already calculated during transfer
                duration_seconds = transfer_data.get("duration_seconds", duration_seconds)

                logger.info("✅ Transfer analysis data loaded from flow_state (LLM-generated)")

            else:
                # Normal post-call analysis (no transfer)
                logger.info("🤖 Running normal post-call LLM analysis")

                # Use LLM to analyze call and extract structured data
                if transcript_text:
                    analysis = await self._analyze_call_with_llm(transcript_text, flow_state)
                else:
                    logger.warning("⚠️ No transcript available, using fallback analysis")
                    analysis = self._get_fallback_analysis(flow_state)

                # Extract fields from LLM analysis
                action = analysis.get("action", "completed")
                sentiment = analysis.get("sentiment", "neutral")
                service = str(analysis.get("service", "5"))  # IVR code 1-5
                esito_chiamata = analysis.get("esito_chiamata", "COMPLETATA")
                motivazione = analysis.get("motivazione", "Info fornite")
                patient_intent = analysis.get("patient_intent", "Richiesta informazioni")
                summary = analysis.get("summary", "")[:250]  # Limit to 250 chars

            # ✅ Extract all booking data from flow state FIRST (needed for call_type)
            booking_data = self._extract_booking_data(flow_state)

            # ✅ Determine call type (booking, booking_incomplete, or info)
            call_type = self._determine_call_type(flow_state, booking_data)

            # ✅ Override esito/motivazione when booking is completed (more reliable than LLM)
            if call_type == "booking":
                esito_chiamata = "COMPLETATA"
                motivazione = "Pren. effettuata"
                logger.info(f"📅 Booking completed - overriding: esito=COMPLETATA, motivazione=Pren. effettuata")

            # ✅ Calculate cost based on call type (different rates for booking vs info)
            cost = self._calculate_cost(duration_seconds, call_type)

            logger.info(f"📊 Call Data Summary:")
            logger.info(f"   Call ID: {self.call_id}")
            logger.info(f"   Call Type: {call_type}")
            logger.info(f"   Duration: {duration_seconds:.2f}s" if duration_seconds else "   Duration: N/A")
            logger.info(f"   Cost: €{cost:.4f}" if cost else "   Cost: N/A")
            logger.info(f"   Action: {action}")
            logger.info(f"   Sentiment: {sentiment}")
            logger.info(f"   Service: {service}")
            logger.info(f"   Esito: {esito_chiamata}")
            logger.info(f"   Motivazione: {motivazione}")
            logger.info(f"   Phone: {phone_number or 'N/A'}")
            logger.info(f"   LLM Tokens: {self.llm_token_count}")

            # Log booking data if present
            if booking_data.get("booking_code"):
                logger.info(f"   📅 Booking Code: {booking_data['booking_code']}")
            if booking_data.get("selected_center_name"):
                logger.info(f"   🏥 Center: {booking_data['selected_center_name']}")
            if booking_data.get("patient_first_name"):
                logger.info(f"   👤 Patient: {booking_data['patient_first_name']} {booking_data.get('patient_surname', '')}")

            # Log recording data if present
            if self.recording_url_stereo:
                logger.info(f"   🎙️ Recording: {self.recording_duration:.1f}s" if self.recording_duration else "   🎙️ Recording: saved")

            # Prepare data for database/backup
            call_data = {
                "call_id": self.call_id,
                "phone_number": phone_number,
                "assistant_id": self.assistant_id,
                "region": self.region,
                "started_at": self.started_at.isoformat() if self.started_at else None,
                "ended_at": self.ended_at.isoformat() if self.ended_at else None,
                "duration_seconds": duration_seconds,
                "action": action,
                "sentiment": sentiment,
                "esito_chiamata": esito_chiamata,
                "motivazione": motivazione,
                "patient_intent": patient_intent,
                "transcript": transcript_text,
                "summary": summary,
                "cost": cost,
                "llm_token": self.llm_token_count,
                "service": service,
                "interaction_id": self.interaction_id,
                "call_type": call_type,  # booking, booking_incomplete, or info
                **booking_data  # Include all booking fields
            }

            # UPDATE database row with ALL fields (original + booking + call_type + recordings)
            query = """
            UPDATE tb_stat SET
                phone_number = $2,
                assistant_id = $3,
                region = $4,
                started_at = $5,
                ended_at = $6,
                duration_seconds = $7,
                action = $8,
                sentiment = $9,
                esito_chiamata = $10,
                motivazione = $11,
                patient_intent = $12,
                transcript = $13,
                summary = $14,
                cost = $15,
                llm_token = $16,
                service = $17,
                call_type = $18,
                patient_first_name = $19,
                patient_surname = $20,
                patient_dob = $21,
                patient_gender = $22,
                patient_address = $23,
                selected_services = $24,
                search_terms_used = $25,
                selected_center_uuid = $26,
                selected_center_name = $27,
                selected_center_address = $28,
                selected_center_city = $29,
                booked_slots = $30,
                preferred_date = $31,
                preferred_time = $32,
                appointment_datetime = $33,
                booking_code = $34,
                total_booking_cost = $35,
                is_cerba_member = $36,
                reminder_authorization = $37,
                marketing_authorization = $38,
                transfer_reason = $39,
                transfer_timestamp = $40,
                recording_url_stereo = $41,
                recording_url_user = $42,
                recording_url_bot = $43,
                recording_duration_seconds = $44,
                updated_at = CURRENT_TIMESTAMP
            WHERE call_id = $1
            """

            result = await db.execute(
                query,
                self.call_id,
                phone_number,
                self.assistant_id,
                self.region,
                self.started_at,
                self.ended_at,
                duration_seconds,
                action,
                sentiment,
                esito_chiamata,
                motivazione,
                patient_intent,
                transcript_text,
                summary,
                cost,
                self.llm_token_count,
                service,
                call_type,  # $18 - booking, booking_incomplete, or info
                # Booking fields
                booking_data["patient_first_name"],
                booking_data["patient_surname"],
                booking_data["patient_dob"],
                booking_data["patient_gender"],
                booking_data["patient_address"],
                booking_data["selected_services"],
                booking_data["search_terms_used"],
                booking_data["selected_center_uuid"],
                booking_data["selected_center_name"],
                booking_data["selected_center_address"],
                booking_data["selected_center_city"],
                booking_data["booked_slots"],
                booking_data["preferred_date"],
                booking_data["preferred_time"],
                booking_data["appointment_datetime"],
                booking_data["booking_code"],
                booking_data["total_booking_cost"],
                booking_data["is_cerba_member"],
                booking_data["reminder_authorization"],
                booking_data["marketing_authorization"],
                booking_data["transfer_reason"],
                booking_data["transfer_timestamp"],
                # Recording fields
                self.recording_url_stereo,
                self.recording_url_user,
                self.recording_url_bot,
                self.recording_duration
            )

            logger.success(f"✅ Call data updated in tb_stat table (with booking fields)")
            logger.info(f"   Database Call ID: {self.call_id}")
            logger.info(f"   Rows updated: {result}")

            return True

        except Exception as e:
            logger.error(f"❌ Error saving call data to database: {e}")
            import traceback
            traceback.print_exc()

            # Create backup file for failed save
            logger.warning("⚠️ Creating backup file for retry...")
            backup_success = self._save_to_backup_file(call_data if 'call_data' in locals() else {
                "call_id": self.call_id,
                "session_id": self.session_id,
                "error": str(e)
            })

            if backup_success:
                logger.info("💾 Backup file created successfully for retry service")
            else:
                logger.error("❌ Failed to create backup file - data may be lost!")

            return False


# Global storage for active extractors
_active_extractors: Dict[str, CallDataExtractor] = {}


def get_call_extractor(session_id: str) -> CallDataExtractor:
    """Get or create call data extractor for session"""
    if session_id not in _active_extractors:
        _active_extractors[session_id] = CallDataExtractor(session_id)
    return _active_extractors[session_id]


def cleanup_call_extractor(session_id: str):
    """Remove call data extractor for session"""
    if session_id in _active_extractors:
        del _active_extractors[session_id]
        logger.debug(f"🧹 Cleaned up call extractor for session: {session_id}")

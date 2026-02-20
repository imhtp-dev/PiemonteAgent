#!/usr/bin/env python3
"""
Pipecat-Talkdesk Bridge Server
"""
import asyncio
import websockets
import json
import base64
import audioop
import logging
import requests
import signal
import sys
from typing import Optional, Dict, Any, Set, List
from dataclasses import dataclass
from enum import Enum
import uuid
import os
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
import time

import aiohttp

ACTIVE_SESSIONS: Dict[str, "BridgeSession"] = {}

#############################################
# Database Configuration (PostgreSQL/Supabase)
#############################################
import asyncpg
from datetime import datetime

async def save_call_to_supabase(
    call_id: str,
    assistant_id: str,
    interaction_id: str,
    phone_number: str = "",
    stream_sid: str = ""
) -> bool:
    """
    Create initial tb_stat row in Supabase PostgreSQL
    This row will be updated by Pipecat on call end
    ALL fields are initialized to 'N/A' or appropriate defaults
    """
    connection = None
    try:
        # Get database credentials from environment
        database_url = os.getenv("DATABASE_URL")

        if not database_url:
            # Fallback to individual components
            db_host = os.getenv("DB_HOST")
            db_port = int(os.getenv("DB_PORT", 5432))
            db_user = os.getenv("DB_USER")
            db_password = os.getenv("DB_PASSWORD")
            db_name = os.getenv("DB_NAME", "postgres")

            if not all([db_host, db_user, db_password]):
                logger.warning("Supabase credentials not configured - skipping initial save")
                return False

            database_url = f"postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}"

        # SSL context for Supabase
        import ssl
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE

        # Connect to Supabase
        connection = await asyncpg.connect(
            dsn=database_url,
            ssl=ssl_context,
            timeout=10
        )

        # Insert initial row with ALL fields set to 'N/A' or appropriate defaults
        # Will be updated by Pipecat on call end
        query = """
        INSERT INTO tb_stat (
            call_id,
            interaction_id,
            phone_number,
            assistant_id,
            started_at,
            service,
            action,
            sentiment,
            esito_chiamata,
            summary,
            motivazione,
            patient_intent,
            transcript,
            region,
            ended_at,
            duration_seconds,
            cost,
            llm_token,
            call_type,
            patient_first_name,
            patient_surname,
            patient_dob,
            patient_gender,
            patient_address,
            selected_services,
            search_terms_used,
            selected_center_uuid,
            selected_center_name,
            selected_center_address,
            selected_center_city,
            booked_slots,
            preferred_date,
            preferred_time,
            appointment_datetime,
            booking_code,
            total_booking_cost,
            is_cerba_member,
            reminder_authorization,
            marketing_authorization,
            transfer_reason,
            transfer_timestamp,
            recording_url_stereo,
            recording_url_user,
            recording_url_bot,
            recording_duration_seconds
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
            $11, $12, $13, $14, $15, $16, $17, $18, $19, $20,
            $21, $22, $23, $24, $25, $26, $27, $28, $29, $30,
            $31, $32, $33, $34, $35, $36, $37, $38, $39, $40,
            $41, $42, $43, $44, $45
        )
        ON CONFLICT (call_id) DO NOTHING
        """

        await connection.execute(
            query,
            call_id,                                          # $1 call_id (UUID)
            interaction_id or "N/A",                          # $2 interaction_id
            phone_number or "N/A",                            # $3 phone_number
            assistant_id,                                     # $4 assistant_id
            datetime.now(),                                   # $5 started_at
            "N/A",                                            # $6 service
            "N/A",                                            # $7 action
            "N/A",                                            # $8 sentiment
            "N/A",                                            # $9 esito_chiamata
            "N/A",                                            # $10 summary
            "N/A",                                            # $11 motivazione
            "N/A",                                            # $12 patient_intent
            "N/A",                                            # $13 transcript
            "Piemonte",                                       # $14 region (hardcoded for this bridge)
            None,                                             # $15 ended_at (null until call ends)
            None,                                             # $16 duration_seconds (null until call ends)
            None,                                             # $17 cost (null until call ends)
            0,                                                # $18 llm_token (default 0)
            "N/A",                                            # $19 call_type (booking/booking_incomplete/info)
            "N/A",                                            # $20 patient_first_name
            "N/A",                                            # $21 patient_surname
            "N/A",                                            # $22 patient_dob
            "N/A",                                            # $23 patient_gender
            "N/A",                                            # $24 patient_address
            None,                                             # $25 selected_services (JSONB, null)
            None,                                             # $26 search_terms_used (JSONB, null)
            None,                                             # $27 selected_center_uuid (UUID, must be null not N/A)
            "N/A",                                            # $28 selected_center_name
            "N/A",                                            # $29 selected_center_address
            "N/A",                                            # $30 selected_center_city
            None,                                             # $31 booked_slots (JSONB, null)
            "N/A",                                            # $32 preferred_date
            "N/A",                                            # $33 preferred_time
            None,                                             # $34 appointment_datetime (timestamp, null)
            "N/A",                                            # $35 booking_code
            None,                                             # $36 total_booking_cost (numeric, null)
            False,                                            # $37 is_cerba_member (boolean, default false)
            False,                                            # $38 reminder_authorization (boolean, default false)
            False,                                            # $39 marketing_authorization (boolean, default false)
            "N/A",                                            # $40 transfer_reason
            None,                                             # $41 transfer_timestamp (timestamp, null)
            "N/A",                                            # $42 recording_url_stereo
            "N/A",                                            # $43 recording_url_user
            "N/A",                                            # $44 recording_url_bot
            None                                              # $45 recording_duration_seconds (numeric, null)
        )

        logger.info(f"✅ Supabase: Initial tb_stat row created with ALL fields - call_id: {call_id}, "
                   f"interaction_id: {interaction_id}, phone: {phone_number}")
        return True

    except Exception as e:
        logger.error(f"❌ Supabase save error for call {call_id}: {e}")
        return False
    finally:
        if connection:
            await connection.close()

##############################################
# Pipecat Stat Configuration
##############################################
PIPECAT_STAT_CONFIG = {
    "base_url": "https://voilavoiceagent-cyf2e9bshnguaebh.westeurope-01.azurewebsites.net",
    "endpoint": "/pipecat_stat",  # Cambiato da /vapi_stat
    "timeout": 30
}

def get_required_env(var_name: str) -> str:
    value = os.getenv(var_name)
    if value is None:
        logger.error(f"Variabile d'ambiente richiesta '{var_name}' non trovata!")
        sys.exit(1)
    return value

##############################################
# Server Configuration
##############################################
PORT = 8080

# URL del server Pipecat
#PIPECAT_SERVER_URL ="wss://voiladevpipecat-e9g6f7bxhhgreefq.francecentral-01.azurewebsites.net/ws"
#PIPECAT_SERVER_URL="wss://2f26d18c02d3.ngrok.app"
#PIPECAT_SERVER_URL="wss://fe38b408791c.ngrok-free.app" #Rudy Mac
PIPECAT_SERVER_URL = os.getenv("PIPECAT_SERVER_URL", "ws://localhost:8765")
PIPECAT_ASSISTANT_ID = "12689"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bridge.log')
    ]
)
logger = logging.getLogger('PipecatBridge')

class ConnectionState(Enum):
    INIT = "init"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    CLOSING = "closing"
    CLOSED = "closed"
    ERROR = "error"

class BridgeState(Enum):
    WAITING_START = "waiting_start"
    ACTIVE = "active"
    ESCALATING = "escalating"
    PIPECAT_CLOSED = "pipecat_closed"
    CLOSING = "closing"
    CLOSED = "closed"
    ERROR = "error"

@dataclass
class BridgeConfig:
    host: str = "0.0.0.0"
    port: int = PORT
    pipecat_server_url: str = PIPECAT_SERVER_URL
    pipecat_assistant_id: str = PIPECAT_ASSISTANT_ID
    talkdesk_sample_rate: int = 8000
    pipecat_sample_rate: int = 16000
    channels: int = 1
    chunk_size: int = 160

cfg = BridgeConfig()
app = FastAPI()

class AudioProcessor:
    @staticmethod
    def mulaw_to_pcm(mulaw_data: bytes) -> bytes:
        try:
            return audioop.ulaw2lin(mulaw_data, 2)
        except Exception as e:
            logger.error(f"Errore conversione μ-law → PCM: {e}")
            return b''
    
    @staticmethod
    def pcm_to_mulaw(pcm_data: bytes) -> bytes:
        try:
            return audioop.lin2ulaw(pcm_data, 2)
        except Exception as e:
            logger.error(f"Errore conversione PCM → μ-law: {e}")
            return b''
    
    @staticmethod
    def resample(audio_data: bytes, from_rate: int, to_rate: int, 
                 channels: int = 1, sample_width: int = 2) -> bytes:
        try:
            if from_rate == to_rate:
                return audio_data
                
            resampled, _ = audioop.ratecv(
                audio_data,
                sample_width,
                channels,
                from_rate,
                to_rate,
                None
            )
            return resampled
        except Exception as e:
            logger.error(f"Errore resampling {from_rate}Hz → {to_rate}Hz: {e}")
            return audio_data

class PipecatConnection:
    """Gestisce la connessione WebSocket con il server Pipecat"""
    
    def __init__(self, config: BridgeConfig):
        self.config = config
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        self.call_id: Optional[str] = None
        self.websocket_url: Optional[str] = None
        self.state = ConnectionState.INIT
        self.session_data: Dict[str, Any] = {}
        
    async def create_connection(
        self,
        business_status: str = "open",
        caller_phone: str = "",
        interaction_id: str = "",
        stream_sid: str = ""
    ) -> Dict[str, Any]:
        """Crea una nuova connessione WebSocket con Pipecat con TUTTI i parametri"""
        try:
            self.state = ConnectionState.CONNECTING

            # Genera un call_id univoco per questa sessione
            self.call_id = str(uuid.uuid4())

            # ✅ Costruisci l'URL con TUTTI i parametri
            from urllib.parse import quote

            encoded_phone = quote(caller_phone) if caller_phone else ""
            encoded_interaction = quote(interaction_id) if interaction_id else ""
            encoded_stream = quote(stream_sid) if stream_sid else ""

            ws_url = (
                f"{self.config.pipecat_server_url}?"
                f"session_id={self.call_id}&"
                f"caller_phone={encoded_phone}&"
                f"interaction_id={encoded_interaction}&"
                f"stream_sid={encoded_stream}&"
                f"business_status={business_status}"
            )

            self.websocket_url = ws_url

            logger.info(f"Creating Pipecat connection:")
            logger.info(f"  Session ID (call_id): {self.call_id}")
            logger.info(f"  Caller Phone: {caller_phone or 'N/A'}")
            logger.info(f"  Interaction ID: {interaction_id or 'N/A'}")
            logger.info(f"  Stream SID: {stream_sid or 'N/A'}")
            logger.info(f"  Business Status: {business_status}")
            logger.info(f"  WebSocket URL: {ws_url}")

            # Salva i dati della sessione
            self.session_data = {
                'id': self.call_id,
                'business_status': business_status,
                'caller_phone': caller_phone,
                'interaction_id': interaction_id,
                'stream_sid': stream_sid,
                'created_at': time.time()
            }

            return self.session_data

        except Exception as e:
            self.state = ConnectionState.ERROR
            logger.error(f"Failed to prepare Pipecat connection: {e}")
            raise
    
    async def connect(self):
        """Connetti al server Pipecat WebSocket"""
        if not self.websocket_url:
            raise ValueError("No WebSocket URL available")
            
        try:
            logger.info(f"Connecting to Pipecat server: {self.websocket_url}")
            self.websocket = await websockets.connect(
                self.websocket_url,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=10
            )
            self.state = ConnectionState.CONNECTED
            logger.info(f"Connected to Pipecat WebSocket: {self.call_id}")
            
            # Invia un messaggio iniziale di configurazione se necessario
            # Pipecat potrebbe non richiederlo, ma lo lasciamo per compatibilità
            
        except Exception as e:
            self.state = ConnectionState.ERROR
            logger.error(f"Failed to connect to Pipecat: {e}")
            raise
    
    async def send_audio(self, pcm_data: bytes):
        """Invia audio PCM raw al server Pipecat"""
        if self.websocket and self.state == ConnectionState.CONNECTED:
            try:
                # Pipecat si aspetta PCM raw direttamente
                await self.websocket.send(pcm_data)
            except Exception as e:
                logger.error(f"Error sending audio to Pipecat: {e}")
                self.state = ConnectionState.ERROR
                raise
    
    async def receive(self) -> bytes:
        """Ricevi dati dal server Pipecat"""
        if self.websocket and self.state == ConnectionState.CONNECTED:
            data = await self.websocket.recv()
            # Pipecat dovrebbe inviare PCM raw
            if isinstance(data, bytes):
                return data
            else:
                # Se ricevi JSON, gestiscilo come messaggio di controllo
                try:
                    control_msg = json.loads(data) if isinstance(data, str) else data
                    logger.debug(f"Pipecat control message: {control_msg}")
                    return b''  # Ritorna bytes vuoti per i messaggi di controllo
                except:
                    return b''
        raise ConnectionError("Not connected to Pipecat")
    
    async def close(self):
        """Chiudi la connessione con Pipecat"""
        self.state = ConnectionState.CLOSING
        if self.websocket:
            try:
                await self.websocket.close()
                logger.info(f"Pipecat connection closed: {self.call_id}")
            except Exception as e:
                logger.error(f"Error closing Pipecat connection: {e}")
        self.state = ConnectionState.CLOSED

class BridgeSession:
    def __init__(self, session_id: str, talkdesk_ws: WebSocket, config: BridgeConfig):
        self.session_id = session_id
        self.talkdesk_ws = talkdesk_ws
        self.config = config
        self.pipecat_conn = PipecatConnection(config)
        self.audio_processor = AudioProcessor()
        self.is_active = False
        self.tasks: Set[asyncio.Task] = set()
        
        self.bridge_state = BridgeState.WAITING_START
        self.escalation_event = asyncio.Event()
        
        self.stream_sid = None
        self.chunk_counter = 0
        self.interaction_id = None
        self.caller_id = None
        self.business_status = None
        
        # Buffer per messaggi ricevuti prima che Pipecat sia pronto
        self.audio_buffer = []
        
        self.stats = {
            'talkdesk_to_pipecat_packets': 0,
            'pipecat_to_talkdesk_packets': 0,
            'errors': 0
        }
    
    def extract_business_status(self, business_hours_string: str) -> str:
        """Estrae lo status (open/close) dalla stringa business_hours"""
        try:
            if business_hours_string and '::' in business_hours_string:
                parts = business_hours_string.split('::')
                if len(parts) >= 4:
                    status = parts[-1].strip().lower()
                    logger.info(f"Session {self.session_id}: Extracted business status: {status}")
                    return status
            
            logger.warning(f"Session {self.session_id}: Could not extract business status from: {business_hours_string}")
            return "close"
            
        except Exception as e:
            logger.error(f"Session {self.session_id}: Error extracting business status: {e}")
            return "close"
    
    async def initialize_pipecat_with_business_status(self, business_status: str):
        """Inizializza Pipecat con il business_status corretto"""
        try:
            logger.info(f"Session {self.session_id}: Initializing Pipecat with business_status: {business_status}")

            # ✅ Crea la connessione Pipecat con TUTTI i parametri
            await self.pipecat_conn.create_connection(
                business_status=business_status,
                caller_phone=self.caller_id or "",
                interaction_id=self.interaction_id or "",
                stream_sid=self.stream_sid or ""
            )
            await self.pipecat_conn.connect()

            # ✅ Create initial Supabase tb_stat row
            try:
                assistant_id = os.getenv("INFO_AGENT_ASSISTANT_ID", "pipecat-lombardia-001")
                supabase_success = await save_call_to_supabase(
                    call_id=self.pipecat_conn.call_id,
                    assistant_id=assistant_id,
                    interaction_id=self.interaction_id or "",
                    phone_number=self.caller_id or "",
                    stream_sid=self.stream_sid or ""
                )

                if supabase_success:
                    logger.info(f"✅ Initial tb_stat row created in Supabase for call {self.pipecat_conn.call_id}")
                else:
                    logger.warning(f"⚠️ Could not create initial tb_stat row (will retry on call end)")
            except Exception as db_error:
                logger.error(f"❌ Supabase initial save error: {db_error}")
                # Continue anyway - Pipecat will handle on call end

            # Cambia stato a ACTIVE
            self.set_bridge_state(BridgeState.ACTIVE)

            logger.info(f"Session {self.session_id}: Pipecat initialized successfully with business_status: {business_status}")

            # Invia eventuale audio buffered
            if self.audio_buffer:
                logger.info(f"Session {self.session_id}: Sending {len(self.audio_buffer)} buffered audio packets to Pipecat")
                for audio_data in self.audio_buffer:
                    try:
                        await self.pipecat_conn.send_audio(audio_data)
                        self.stats['talkdesk_to_pipecat_packets'] += 1
                    except Exception as e:
                        logger.error(f"Session {self.session_id}: Error sending buffered audio: {e}")
                        break
                self.audio_buffer.clear()

            return True

        except Exception as e:
            logger.error(f"Session {self.session_id}: Failed to initialize Pipecat: {e}")
            self.set_bridge_state(BridgeState.ERROR)
            return False
    
    def set_bridge_state(self, new_state: BridgeState):
        old_state = self.bridge_state
        self.bridge_state = new_state
        logger.info(f"Session {self.session_id}: Bridge state changed {old_state.value} → {new_state.value}")
    
    async def start_escalation(self) -> bool:
        try:
            if self.bridge_state != BridgeState.ACTIVE:
                logger.warning(f"Session {self.session_id}: Cannot start escalation, state is {self.bridge_state.value}")
                return False
            
            logger.info(f"Session {self.session_id}: Starting escalation process")
            self.set_bridge_state(BridgeState.ESCALATING)
            
            await self.pipecat_conn.close()
            logger.info(f"Session {self.session_id}: Pipecat WebSocket closed for escalation")
            
            self.escalation_event.set()
            await asyncio.sleep(2)  # Ridotto da 4 a 2 secondi per Pipecat
            
            self.set_bridge_state(BridgeState.PIPECAT_CLOSED)
            logger.info(f"Session {self.session_id}: Escalation ready - Pipecat session completed")
            
            return True
            
        except Exception as e:
            logger.error(f"Session {self.session_id}: Error during escalation start: {e}")
            return False
    
    async def complete_escalation(self, stop_msg: Dict[str, Any]) -> bool:
        try:
            if self.bridge_state not in [BridgeState.ESCALATING, BridgeState.PIPECAT_CLOSED]:
                logger.warning(f"Session {self.session_id}: Cannot complete escalation, state is {self.bridge_state.value}")
                return False
            
            logger.info(f"Session {self.session_id}: Completing escalation")
            
            await self.talkdesk_ws.send_text(json.dumps(stop_msg))
            logger.info(f"Session {self.session_id}: Escalation message sent to Talkdesk")
            
            self.set_bridge_state(BridgeState.CLOSING)
            
            return True
            
        except Exception as e:
            logger.error(f"Session {self.session_id}: Error completing escalation: {e}")
            return False
    
    async def start(self):
        """Avvia la sessione bridge - Pipecat viene inizializzato dopo START"""
        try:
            logger.info(f"Starting bridge session: {self.session_id}")
            
            self.is_active = True
            self.set_bridge_state(BridgeState.WAITING_START)
            
            # Avvia solo il task di forwarding da Talkdesk
            forward_task = asyncio.create_task(self._forward_talkdesk_to_pipecat())
            backward_task = None
            
            self.tasks = {forward_task}
            
            while self.is_active and self.bridge_state not in [BridgeState.CLOSING, BridgeState.CLOSED]:
                # Dopo che Pipecat è inizializzato, aggiungi il backward task
                if self.bridge_state == BridgeState.ACTIVE and backward_task is None:
                    backward_task = asyncio.create_task(self._forward_pipecat_to_talkdesk())
                    self.tasks.add(backward_task)
                    logger.info(f"Session {self.session_id}: Started Pipecat→Talkdesk forwarding")
                
                done, pending = await asyncio.wait(
                    self.tasks, 
                    return_when=asyncio.FIRST_COMPLETED,
                    timeout=1.0
                )
                
                if self.bridge_state in [BridgeState.ESCALATING, BridgeState.PIPECAT_CLOSED]:
                    for task in done:
                        if task in self.tasks:
                            self.tasks.remove(task)
                    continue
                
                if done and self.bridge_state == BridgeState.ACTIVE:
                    logger.info(f"Session {self.session_id}: Normal termination - task completed")
                    break
                    
            for task in self.tasks:
                if not task.done():
                    task.cancel()
                    
        except Exception as e:
            logger.error(f"Session {self.session_id} error: {e}")
            self.stats['errors'] += 1
        finally:
            await self.stop()
    
    async def _forward_talkdesk_to_pipecat(self):
        """Inoltra audio da Talkdesk a Pipecat con attesa di START"""
        logger.info(f"Session {self.session_id}: Starting Talkdesk → Pipecat forwarding (waiting for START)")
        
        try:
            while self.is_active:
                if self.bridge_state in [BridgeState.PIPECAT_CLOSED, BridgeState.ESCALATING]:
                    await asyncio.sleep(0.5)
                    continue
                
                message = await self.talkdesk_ws.receive_text()
                
                try:
                    data = json.loads(message)
                    event = data.get('event')
                    
                    if event == 'start':
                        logger.info(f"Session {self.session_id}: Received START from Talkdesk")
                        
                        # Estrai tutti i dati necessari
                        self.stream_sid = data.get('streamSid')
                        if not self.stream_sid and 'start' in data:
                            self.stream_sid = data['start'].get('streamSid')
                        
                        if 'start' in data:
                            self.interaction_id = data['start'].get('customParameters', {}).get('interaction_id')
                            
                            # Estrai business_hours e determina lo status
                            custom_params = data['start'].get('customParameters', {})
                            business_hours = custom_params.get('business_hours', '')
                            
                            # Estrai caller_id
                            self.caller_id = custom_params.get('caller_id', '')
                            
                            logger.info(f"[{self.session_id}] Raw business_hours: {business_hours}")
                            logger.info(f"[{self.session_id}] Caller ID: {self.caller_id}")
                            
                            # Estrai lo status (open/close)
                            self.business_status = self.extract_business_status(business_hours)
                            
                            logger.info(f"[{self.session_id}] Extracted business status: {self.business_status}")
                            
                            # ORA INIZIALIZZA PIPECAT con il business_status corretto
                            logger.info(f"Session {self.session_id}: Initializing Pipecat with business_status: {self.business_status}")
                            
                            pipecat_initialized = await self.initialize_pipecat_with_business_status(self.business_status)
                            
                            if pipecat_initialized:
                                logger.info(f"✅ [{self.session_id}] Pipecat initialized successfully with status: {self.business_status}")
                            else:
                                logger.error(f"❌ [{self.session_id}] Failed to initialize Pipecat")
                                break
                        
                        logger.info(f"[{self.session_id}] streamSid: {self.stream_sid} | "
                                   f"Pipecat Call ID: {self.pipecat_conn.call_id} | "
                                   f"Interaction ID: {self.interaction_id} | "
                                   f"Business Status: {self.business_status} | "
                                   f"Caller ID: {self.caller_id}")
                        
                        if self.stream_sid:
                            ACTIVE_SESSIONS[self.stream_sid] = self

                        logger.info(f"Session {self.session_id}: START processing completed with caller_id: {self.caller_id}")
                        continue
                        
                    elif event == 'stop':
                        logger.info(f"🛑 Session {self.session_id}: Received STOP from Talkdesk (patient hung up)")
                        break
                        
                    elif event == 'media':
                        # Gestione media con stato WAITING_START
                        if self.bridge_state == BridgeState.WAITING_START:
                            # Buffer audio se Pipecat non è ancora pronto
                            media = data.get('media', {})
                            if media.get('track') == 'inbound':
                                payload = media.get('payload', '')
                                mulaw_data = base64.b64decode(payload)
                                pcm_8khz = self.audio_processor.mulaw_to_pcm(mulaw_data)
                                pcm_16khz = self.audio_processor.resample(
                                    pcm_8khz,
                                    self.config.talkdesk_sample_rate,
                                    self.config.pipecat_sample_rate,
                                    self.config.channels
                                )
                                
                                # Buffer l'audio invece di inviarlo
                                self.audio_buffer.append(pcm_16khz)
                                
                                # Limita la dimensione del buffer
                                if len(self.audio_buffer) > 100:
                                    self.audio_buffer.pop(0)
                                    
                                logger.debug(f"Session {self.session_id}: Buffered audio packet (buffer size: {len(self.audio_buffer)})")
                                
                        elif self.bridge_state == BridgeState.ACTIVE:
                            # Forwarding normale se Pipecat è pronto
                            media = data.get('media', {})
                            if media.get('track') == 'inbound':
                                payload = media.get('payload', '')
                                
                                mulaw_data = base64.b64decode(payload)
                                pcm_8khz = self.audio_processor.mulaw_to_pcm(mulaw_data)
                                pcm_16khz = self.audio_processor.resample(
                                    pcm_8khz,
                                    self.config.talkdesk_sample_rate,
                                    self.config.pipecat_sample_rate,
                                    self.config.channels
                                )
                                
                                try:
                                    await self.pipecat_conn.send_audio(pcm_16khz)
                                    self.stats['talkdesk_to_pipecat_packets'] += 1
                                except Exception:
                                    pass
                            
                except json.JSONDecodeError:
                    logger.error(f"Session {self.session_id}: Invalid JSON from Talkdesk")
                except Exception as e:
                    logger.error(f"Session {self.session_id}: Error processing Talkdesk message: {e}")
                    self.stats['errors'] += 1
                    
        except Exception as e:
            logger.error(f"Session {self.session_id}: Forward error: {e}")
            self.stats['errors'] += 1
    
    async def _forward_pipecat_to_talkdesk(self):
        """Inoltra audio da Pipecat a Talkdesk (inizia solo dopo START)"""
        logger.info(f"Session {self.session_id}: Starting Pipecat → Talkdesk forwarding")
        
        try:
            while self.is_active:
                try:
                    if self.bridge_state in [BridgeState.ESCALATING, BridgeState.PIPECAT_CLOSED]:
                        logger.info(f"Session {self.session_id}: Pipecat forwarding paused - waiting for escalation completion")
                        while (self.bridge_state in [BridgeState.ESCALATING, BridgeState.PIPECAT_CLOSED] 
                               and self.is_active):
                            await asyncio.sleep(0.5)
                        continue
                    
                    if self.bridge_state != BridgeState.ACTIVE:
                        break
                        
                    data = await self.pipecat_conn.receive()
                    
                    if isinstance(data, bytes) and len(data) > 0:
                        # Pipecat invia PCM a 16kHz
                        pcm_16khz = data
                        
                        # Resample a 8kHz per Talkdesk
                        pcm_8khz = self.audio_processor.resample(
                            pcm_16khz,
                            self.config.pipecat_sample_rate,
                            self.config.talkdesk_sample_rate,
                            self.config.channels
                        )
                        
                        # Converti in μ-law
                        mulaw_data = self.audio_processor.pcm_to_mulaw(pcm_8khz)
                        payload = base64.b64encode(mulaw_data).decode()
                        
                        self.chunk_counter += 1
                        
                        message = {
                            "event": "media",
                            "streamSid": self.stream_sid,
                            "media": {
                                "track": "outbound",
                                "chunk": str(self.chunk_counter),
                                "timestamp": str(int(time.time() * 1000)),
                                "payload": payload
                            }
                        }
                        
                        await self.talkdesk_ws.send_text(json.dumps(message))
                        self.stats['pipecat_to_talkdesk_packets'] += 1
                        
                        if self.stats['pipecat_to_talkdesk_packets'] == 1:
                            logger.info(f"First message to Talkdesk: {json.dumps(message)[:200]}...")
                            
                except ConnectionError:
                    if self.bridge_state == BridgeState.ACTIVE:
                        logger.error(f"Session {self.session_id}: Pipecat connection lost unexpectedly")
                        break
                    else:
                        logger.info(f"Session {self.session_id}: Pipecat disconnected for escalation")
                        while (self.bridge_state in [BridgeState.ESCALATING, BridgeState.PIPECAT_CLOSED] 
                               and self.is_active):
                            await asyncio.sleep(0.5)
                        break
                        
                except Exception as e:
                    if self.bridge_state == BridgeState.ACTIVE:
                        logger.error(f"Session {self.session_id}: Backward error: {e}")
                        self.stats['errors'] += 1
                        break
                    else:
                        logger.debug(f"Session {self.session_id}: Pipecat error during escalation (expected): {e}")
                        break
                        
        except Exception as e:
            logger.error(f"Session {self.session_id}: Fatal backward error: {e}")
            self.stats['errors'] += 1
    
    async def stop(self):
        logger.info(f"Stopping session {self.session_id}")
        self.is_active = False
        self.set_bridge_state(BridgeState.CLOSED)
        
        logger.info(f"Session {self.session_id} stats: "
                   f"Talkdesk→Pipecat: {self.stats['talkdesk_to_pipecat_packets']}, "
                   f"Pipecat→Talkdesk: {self.stats['pipecat_to_talkdesk_packets']}, "
                   f"Errors: {self.stats['errors']}")
        
        if self.pipecat_conn.state not in [ConnectionState.CLOSED, ConnectionState.CLOSING]:
            await self.pipecat_conn.close()
        
        if self.bridge_state != BridgeState.PIPECAT_CLOSED:
            try:
                await self.talkdesk_ws.send_text(json.dumps({"event": "stop"}))
            except Exception:
                pass

@app.get("/healthz")
async def healthz():
    return {"status": "ok", "service": "pipecat-bridge"}

@app.websocket("/talkdesk")
async def talkdesk_ws(ws: WebSocket):
    await ws.accept()
    session_id = str(uuid.uuid4())
    logger.info(f"New Talkdesk connection – Session: {session_id}")
    session = BridgeSession(session_id, ws, cfg)
    ACTIVE_SESSIONS[session_id] = session

    try:
        await session.start()
    except WebSocketDisconnect:
        logger.info(f"Session {session_id} disconnected")
    finally:
        ACTIVE_SESSIONS.pop(session_id, None)
        logger.info(f"Session {session_id} ended")

# Modifica la funzione per chiamare pipecat_stat invece di vapi_stat
async def call_pipecat_stat_internal(call_id: str, interaction_id: str) -> Optional[Dict[str, Any]]:
    """Chiama il servizio di statistiche Pipecat (se implementato)"""
    try:
        # Puoi implementare un endpoint nel tuo server Pipecat per ottenere statistiche
        return {
            "success": True,
            "action": "transfer",
            "sentiment": "neutral",
            "duration_seconds": 0,
            "cost": 0,
            "summary": "Chiamata gestita da Pipecat",
            "service": "5"
        }
        
    except Exception as e:
        logger.error(f"Errore chiamata pipecat_stat: {str(e)}")
        return None

def limita_testo_256(text):
    max_length = 240
    if len(text) <= max_length:
        return text
    truncated = text[:max_length]
    last_space = truncated.rfind(" ")
    if last_space != -1:
        truncated = truncated[:last_space]
    return truncated.strip() + ""

def build_talkdesk_message(stream_sid: str, pipecat_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    logger.info("=" * 80)
    logger.info("🔨 build_talkdesk_message() CALLED")
    logger.info(f"   stream_sid: {stream_sid}")
    logger.info(f"   pipecat_data: {pipecat_data}")

    action = "transfer"
    sentiment = "neutral"
    duration = "0"
    cost = "0"
    summary = "richiesta di assistenza"
    service = "2|2|5"

    if pipecat_data:
        logger.info("✅ Using pipecat_data from analysis")
        action = pipecat_data.get("action", "transfer")
        sentiment = pipecat_data.get("sentiment", "neutral")
        duration = str(int(pipecat_data.get("duration_seconds", 0)))
        cost = str(pipecat_data.get("cost", 0))
        summary = pipecat_data.get("summary", "richiesta di assistenza")
        servicex = str(pipecat_data.get("service", "5"))
        if servicex is None or str(servicex).strip() == "":
            servicex = "5"
        else:
            servicex = str(servicex).strip()
        # Use sector to determine prefix: booking=1|1|x, info=2|2|x
        sector = pipecat_data.get("sector", "info")
        if sector == "booking":
            service = f"1|1|{servicex}"
        else:
            service = f"2|2|{servicex}"
        logger.info(f"   Extracted: action={action}, sentiment={sentiment}, duration={duration}s, service={service}, sector={sector}")
    else:
        logger.warning("⚠️  Using DEFAULT values (no pipecat_data)")

    summary = limita_testo_256(summary)
    ring_group = f"{summary}::{sentiment}::{action}::{duration}::{service}"
    logger.info(f"🔍 Built ring_group: {ring_group}")

    stop_msg = {
        "event": "stop",
        "streamSid": stream_sid,
        "stop": {
            "command": "escalate",
            "ringGroup": ring_group
        }
    }

    logger.info("📨 FINAL STOP MESSAGE TO TALKDESK:")
    logger.info(json.dumps(stop_msg, indent=2))
    logger.info("=" * 80)

    return stop_msg

@app.post("/escalation")
async def escalation(request: Request) -> Dict[str, Any]:
    """
    Endpoint per gestire l'escalation

    NEW: Accepts stream_sid directly from Pipecat payload (eliminates Redis dependency)
    FALLBACK: Uses Redis lookup for backward compatibility
    """
    # 🔥 ENTRY POINT LOG
    logger.info("=" * 80)
    logger.info("🚨🚨🚨 ESCALATION ENDPOINT HIT - ENTRY POINT 🚨🚨🚨")
    logger.info("=" * 80)

    try:
        payload = await request.json()
        logger.info(f"📦 Raw payload received: {json.dumps(payload, indent=2)}")

        call_id = payload.get("message", {}).get("call", {}).get("id")
        logger.info(f"🔑 Extracted call_id: {call_id}")

        tool_calls = payload.get("message", {}).get("toolCallList", []) or [{}]
        logger.info(f"🛠️  Tool calls count: {len(tool_calls)}")

        results = [{
            "toolCallId": tc.get("id"),
            "result": call_id or "Errore: call_id non trovato"
        } for tc in tool_calls]
        logger.info(f"📤 Response results prepared: {results}")

        if not call_id:
            logger.error("❌ ESCALATION FAILED: call_id mancante nel payload")
            return {"results": results}

        logger.info(f"✅ call_id presente: {call_id}")

    except Exception as parse_error:
        logger.error(f"🔥 ERROR parsing payload: {str(parse_error)}")
        import traceback
        logger.error(f"Traceback:\n{traceback.format_exc()}")
        return {
            "results": [{
                "toolCallId": "error",
                "result": f"Payload parse error: {str(parse_error)}"
            }]
        }

    if call_id:
        # ✅ NEW: Try to get stream_sid from payload first (direct passing from Pipecat)
        stream_sid = payload.get("message", {}).get("stream_sid")
        logger.info(f"🔍 Extracted stream_sid from payload: {stream_sid or 'NOT PROVIDED'}")

        if stream_sid:
            logger.info(f"✅ Using stream_sid from payload (direct): {stream_sid}")
        else:
            logger.warning("⚠️  stream_sid not in payload")

        if not stream_sid:
            logger.error(f"❌ ESCALATION FAILED: streamSid non trovato per call_id {call_id}")
            return {"results": results}

        logger.info(f"✅ stream_sid presente: {stream_sid}")

        # Check ACTIVE_SESSIONS
        logger.info("=" * 80)
        logger.info(f"🔍 Checking ACTIVE_SESSIONS...")
        logger.info(f"📊 Total active sessions: {len(ACTIVE_SESSIONS)}")
        logger.info(f"🔑 Active session keys: {list(ACTIVE_SESSIONS.keys())}")
        logger.info("=" * 80)

        session = ACTIVE_SESSIONS.get(stream_sid)

        if not session:
            logger.error("=" * 80)
            logger.error(f"❌❌❌ ESCALATION FAILED: Session NOT FOUND")
            logger.error(f"❌ Requested stream_sid: {stream_sid}")
            logger.error(f"❌ Available sessions: {list(ACTIVE_SESSIONS.keys())}")
            logger.error(f"❌ Session count: {len(ACTIVE_SESSIONS)}")
            logger.error("=" * 80)
            return {"results": results}

        logger.info("=" * 80)
        logger.info(f"✅✅✅ SESSION FOUND for stream_sid: {stream_sid}")
        logger.info(f"🔍 Session ID: {session.session_id}")
        logger.info(f"🔍 Session bridge_state: {session.bridge_state}")
        logger.info(f"🔍 Session is_active: {session.is_active}")
        logger.info("=" * 80)

        if session:
                try:
                    await asyncio.sleep(1.5)
                    logger.info(f"🔄 Starting immediate Pipecat closure for call {call_id}")

                    escalation_started = await session.start_escalation()

                    if not escalation_started:
                        logger.error("❌ session.start_escalation() returned False")
                        raise Exception("Failed to start escalation process")

                    logger.info(f"✅ Pipecat WebSocket closed immediately for call {call_id}")

                    logger.info(f"⏳ Waiting for Pipecat to complete...")
                    await asyncio.sleep(2)

                    logger.info("=" * 80)
                    logger.info(f"📊 Extracting call analysis from payload...")

                    # ✅ NEW: Extract analysis data from toolCallList (passed by Pipecat)
                    pipecat_data = None
                    if tool_calls:
                        first_tool = tool_calls[0]
                        logger.info(f"🔍 First tool call: {json.dumps(first_tool, indent=2)}")

                        if "function" in first_tool:
                            args = first_tool["function"].get("arguments", {})
                            logger.info(f"🔍 Function arguments: {json.dumps(args, indent=2)}")

                            if args:
                                pipecat_data = {
                                    "action": args.get("action", "transfer"),
                                    "sentiment": args.get("sentiment", "neutral"),
                                    "duration_seconds": int(args.get("duration", "0")),
                                    "summary": args.get("summary", "richiesta di assistenza"),
                                    "service": args.get("service", "5"),
                                    "sector": args.get("sector", "info")
                                }
                                logger.info(f"✅ Extracted pipecat_data:")
                                logger.info(f"   - Action: {pipecat_data['action']}")
                                logger.info(f"   - Sentiment: {pipecat_data['sentiment']}")
                                logger.info(f"   - Duration: {pipecat_data['duration_seconds']}s")
                                logger.info(f"   - Service: {pipecat_data['service']}")
                                logger.info(f"   - Sector: {pipecat_data['sector']}")
                                logger.info(f"   - Summary: {pipecat_data['summary'][:100]}...")

                    if not pipecat_data:
                        logger.warning(f"⚠️  No analysis in payload, using defaults")

                    logger.info("=" * 80)

                    # Build stop message
                    stop_msg = build_talkdesk_message(stream_sid, pipecat_data)

                    # Complete escalation
                    logger.info("🚀 Calling session.complete_escalation()...")
                    escalation_completed = await session.complete_escalation(stop_msg)

                    if escalation_completed:
                        logger.info("=" * 80)
                        logger.info(f"✅✅✅ ESCALATION COMPLETED SUCCESSFULLY")
                        logger.info(f"📞 Stop message sent to Talkdesk")
                        logger.info(f"✅ Escalation process finished for stream_sid: {stream_sid}")
                        logger.info("=" * 80)
                    else:
                        logger.error("❌ session.complete_escalation() returned False")
                        raise Exception("Failed to complete escalation process")
                    
                except Exception as e:
                    logger.error("=" * 80)
                    logger.error(f"❌❌❌ ERROR during escalation execution")
                    logger.error(f"Error: {str(e)}")
                    logger.error(f"Error type: {type(e).__name__}")
                    import traceback
                    logger.error(f"Traceback:\n{traceback.format_exc()}")
                    logger.error("=" * 80)

                    try:
                        logger.info(f"🔄 Attempting fallback escalation with default data...")
                        stop_msg = build_talkdesk_message(stream_sid, None)

                        if session.bridge_state not in [BridgeState.CLOSED]:
                            await session.talkdesk_ws.send_text(json.dumps(stop_msg))
                            logger.info(f"✅ Fallback escalation sent")
                        else:
                            logger.error(f"❌ Session already closed (state={session.bridge_state}), cannot send fallback")

                    except Exception as fallback_error:
                        logger.error(f"❌ Fallback escalation also failed: {str(fallback_error)}")
                        import traceback
                        logger.error(f"Fallback traceback:\n{traceback.format_exc()}")

    logger.info("=" * 80)
    logger.info("🚨 ESCALATION ENDPOINT - RETURNING RESPONSE")
    logger.info(f"Response: {results}")
    logger.info("=" * 80)

    return {"results": results}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
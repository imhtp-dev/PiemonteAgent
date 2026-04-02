import os
from typing import Dict, Any
from dotenv import load_dotenv
from loguru import logger

load_dotenv(override=True)

class Settings:
    """Centralized configuration management"""
    
    def __init__(self):
        self._validate_required_keys()
    
    @property
    def api_keys(self) -> Dict[str, str]:
        """Get all required API keys"""
        return {
            "deepgram": os.getenv("DEEPGRAM_API_KEY"),
            "elevenlabs": os.getenv("ELEVENLABS_API_KEY"),
            "openai": os.getenv("OPENAI_API_KEY"),
            "azure_speech_key": os.getenv("AZURE_SPEECH_API_KEY"),
            "azure_speech_region": os.getenv("AZURE_SPEECH_REGION")
        }
    
    @property
    def stt_provider(self) -> str:
        """STT provider toggle: 'deepgram' or 'azure'"""
        return os.getenv("STT_PROVIDER", "deepgram").lower()

    @property
    def deepgram_config(self) -> Dict[str, Any]:
        """Deepgram STT configuration with Nova-3"""
        return {
            "api_key": self.api_keys["deepgram"],
            "sample_rate": 16000,
            "model": "nova-3-general",
            "language": os.getenv("DEEPGRAM_STT_LANGUAGE", "it"),
            "encoding": "linear16",
            "channels": 1,
            "interim_results": True,
            "smart_format": True,
            "punctuate": True,
            "vad_events": False,
            "profanity_filter": False,
            "numerals": True,
            "endpointing": 300,  # 300ms silence before finalizing (default 10ms too aggressive)
            # Nova-3 keyterm prompting — boosts recognition of Italian medical/booking vocabulary
            "keyterm": self._build_deepgram_keyterms()
        }

    @property
    def azure_stt_config(self) -> Dict[str, Any]:
        """Azure STT configuration"""
        return {
            "api_key": self.api_keys["azure_speech_key"],
            "region": self.api_keys["azure_speech_region"],
            "language": os.getenv("AZURE_STT_LANGUAGE", "it-IT"),
            "sample_rate": 16000,
            "endpoint_id": os.getenv("AZURE_SPEECH_ENDPOINT_ID"),  # Optional custom model endpoint
            # Phrase list: base keywords + doctor names from data/doctor_names.json
            # To disable, set DISABLE_PHRASE_LIST=true in .env
            "phrase_list": [] if os.getenv("DISABLE_PHRASE_LIST", "").lower() == "true" else self._load_phrase_list(),
            "phrase_list_weight": 2  # Boost recognition confidence for these phrases
        }
    
    def _build_deepgram_keyterms(self):
        """Build Deepgram keyterm list: medical vocabulary + doctor full names."""
        keyterms = [
            # Booking actions
            "procediamo", "confermo", "conferma", "annulla", "annullare",
            "va bene", "sì", "prenota", "proseguiamo", "cambiare", "modificare",
            # Gender
            "maschio", "femmina",
            # Brand / Locations
            "Cerba Healthcare", "Leini",
            # Common medical services
            "RX", "RX torace", "RX gamba", "RX colonna",
            "ecografia", "radiografia", "risonanza magnetica", "TAC",
            "elettrocardiogramma", "ECG", "emocromo", "analisi del sangue",
            "visita specialistica", "visita cardiologica", "visita ortopedica",
            "visita dermatologica", "visita ginecologica", "visita oculistica",
            "visita urologica", "visita neurologica", "visita otorinolaringoiatrica",
            "fisioterapia", "mammografia", "densitometria ossea", "MOC",
            "holter", "ecocardiogramma", "spirometria", "colonscopia",
            "gastroscopia", "ecocolordoppler", "pap test", "tampone",
            # Body parts
            "addome", "torace", "ginocchio", "spalla", "caviglia", "anca",
            "colonna vertebrale", "cervicale", "lombare",
            # Patient data
            "codice fiscale", "tessera sanitaria",
            # Medical terms
            "prescrizione", "impegnativa", "medicina sportiva",
            "medicina del lavoro", "laboratorio", "prelievo",
        ]
        # Add doctor full names from data file
        try:
            import json
            json_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "doctor_names_piemonte.json")
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for doc in data.get("doctors", []):
                keyterms.append(f"{doc['name']} {doc['surname']}")
            logger.info(f"🎯 Deepgram keyterms: {len(keyterms)} total ({len(data.get('doctors', []))} doctors)")
        except Exception as e:
            logger.warning(f"⚠️ Could not load doctor names for Deepgram keyterms: {e}")
        return keyterms

    def _load_phrase_list(self):
        """Load base phrases + doctor names from data/doctor_names.json"""
        base_phrases = [
            "maschio", "femmina", "cerba healthcare",
            "RX", "RX torace", "RX gamba", "RX colonna", "RX Caviglia Destra",
            # Booking confirmation/action keywords — prevent STT misrecognition
            "procediamo", "confermo", "conferma", "annulla", "annullare",
            "cambiare", "modificare", "va bene", "sì", "no",
            "prenota", "prenotare", "proseguiamo",
        ]
        try:
            import json
            json_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "doctor_names_piemonte.json")
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Add full names (first + surname) for better recognition
            for doc in data.get("doctors", []):
                full = f"{doc['name']} {doc['surname']}"
                base_phrases.append(full)
            logger.info(f"🎯 Loaded {len(base_phrases)} phrases for Azure STT ({len(data.get('doctors', []))} doctors)")
        except Exception as e:
            logger.warning(f"⚠️ Could not load doctor names for phrase list: {e}")
        return base_phrases

    @property
    def elevenlabs_config(self) -> Dict[str, Any]:
        """ElevenLabs TTS configuration"""
        return {
            "api_key": self.api_keys["elevenlabs"],
            "voice_id": "gfKKsLN1k0oYYN9n2dXX",
            "model": "eleven_multilingual_v2",
            "sample_rate": 16000,
            "stability": 0.6,
            "similarity_boost": 0.8,
            "style": 0.1,
            "use_speaker_boost": True
        }
    
    @property
    def openai_config(self) -> Dict[str, Any]:
        """OpenAI LLM configuration"""
        return {
            "api_key": self.api_keys["openai"],
            "model": "gpt-4.1"
        }
    

    
    @property
    def vad_config(self) -> Dict[str, Any]:
        """Voice Activity Detection configuration optimized for Nova-3"""
        return {
            "start_secs": 0.2,
            "stop_secs": 0.5,
            "min_volume": 0.4
        }
    
    @property
    def pipeline_config(self) -> Dict[str, Any]:
        """Pipeline configuration"""
        return {
            "allow_interruptions": True,
            "enable_metrics": False,
            "enable_usage_metrics": False
        }

    @property
    def language_config(self) -> str:
        """Global language instruction for prompts"""
        return f"You need to speak {self.agent_language}."

    @property
    def agent_language(self) -> str:
        """Agent language from env, fallback Italian"""
        return os.getenv("AGENT_LANGUAGE", "Italian")

    @property
    def current_year(self) -> str:
        """Current year from env var (avoids hardcoding in prompts)"""
        return os.getenv("CURRENT_YEAR", "2026")

    @property
    def api_timeout(self) -> int:
        """Timeout for external API calls in seconds"""
        return int(os.getenv("API_TIMEOUT", 30))

    @property
    def info_api_endpoints(self) -> Dict[str, str]:
        """External API endpoints for info tools (KB, pricing, exams, clinic)"""
        base_url = os.getenv(
            "INFO_API_BASE_URL",
            "https://voilavoiceagent-cyf2e9bshnguaebh.westeurope-01.azurewebsites.net"
        )
        return {
            "knowledge_base_new": os.getenv(
                "KNOWLEDGE_BASE_URL",
                f"{base_url}/query_new"
            ),
            "exam_by_visit": os.getenv(
                "EXAM_BY_VISIT_URL",
                f"{base_url}/get_list_exam_by_visit"
            ),
            "exam_by_sport": os.getenv(
                "EXAM_BY_SPORT_URL",
                f"{base_url}/get_list_exam_by_sport"
            ),
            "price_non_agonistic": os.getenv(
                "PRICE_NON_AGONISTIC_URL",
                f"{base_url}/get_price_non_agonistic_visit"
            ),
            "price_agonistic": os.getenv(
                "PRICE_AGONISTIC_URL",
                f"{base_url}/get_price_agonistic_visit"
            ),
            "call_graph": os.getenv(
                "CALL_GRAPH_URL",
                f"{base_url}/call_graph"
            )
        }

    @property
    def booking_enabled(self) -> bool:
        """Whether booking flows are enabled. When False, booking requests escalate to operator."""
        return os.getenv("BOOKING_ENABLED", "false").lower() == "true"

    @property
    def max_concurrent_calls(self) -> int:
        """Max concurrent calls per container. Used for monitoring dashboard."""
        return int(os.getenv("MAX_CONCURRENT_CALLS", "5"))

    @property
    def smart_turn_enabled(self) -> bool:
        """Whether Smart Turn V3 ML-based end-of-turn detection is enabled"""
        return os.getenv("SMART_TURN_ENABLED", "true").lower() == "true"

    @property
    def smart_turn_config(self) -> Dict[str, Any]:
        """Smart Turn V3 configuration"""
        return {
            "stop_secs": float(os.getenv("SMART_TURN_STOP_SECS", "3.0")),
            "pre_speech_ms": float(os.getenv("SMART_TURN_PRE_SPEECH_MS", "500")),
            "max_duration_secs": float(os.getenv("SMART_TURN_MAX_DURATION", "8")),
            "cpu_count": int(os.getenv("SMART_TURN_CPU_COUNT", "1")),
        }

    @property
    def llm_interpretation_config(self) -> Dict[str, Any]:
        """LLM interpretation configuration for sorting API analysis"""
        return {
            "model": "gpt-4.1",  # Full GPT-4.1 model
            "temperature": 0.1,  # Low temperature for consistent, deterministic logic
            "max_tokens": 500,  # Enough for reasoning and analysis
            "timeout": 15.0  # 15 second timeout for reliability
        }

    @property
    def mds_api_config(self) -> Dict[str, str]:
        """CHC MDS sports medicine API configuration (separate from Cerba ambulatory API)"""
        return {
            "base_url": os.getenv(
                "MDS_API_BASE_URL",
                "https://visitesportiveur.cerbahealthcare.it/api/endpoint"
            ),
            "username": os.getenv("MDS_API_USERNAME", ""),
            "password": os.getenv("MDS_API_PASSWORD", ""),
        }

    @property
    def google_maps_api_key(self) -> str:
        """Google Maps API key for geocoding service"""
        return os.getenv("GOOGLE_MAPS_API_KEY", "")

    @property
    def sports_medicine_enabled(self) -> bool:
        """Whether sports medicine non-agonistic flow is enabled. When False, escalates to operator."""
        return os.getenv("SPORTS_MEDICINE_ENABLED", "false").lower() == "true"

    def _validate_required_keys(self) -> None:
        """Validate that all required API keys are present"""
        required_keys = [
            ("DEEPGRAM_API_KEY", "Deepgram"),
            ("ELEVENLABS_API_KEY", "ElevenLabs"), 
            ("OPENAI_API_KEY", "OpenAI")
        ]
        
        missing_keys = []
        for key_name, service_name in required_keys:
            if not os.getenv(key_name):
                missing_keys.append(f"{key_name} required for {service_name}")
        
        if missing_keys:
            raise Exception("Missing required environment variables:\n" + "\n".join(missing_keys))

# Global settings instance
settings = Settings()
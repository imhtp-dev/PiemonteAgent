"""
Pipeline components setup for TTS, STT, and LLM services
"""

from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.frames.frames import StartFrame
from deepgram import LiveOptions
from loguru import logger
from typing import Union

try:
    from pipecat.services.azure.stt import AzureSTTService
    from pipecat.transcriptions.language import Language
    import azure.cognitiveservices.speech as speechsdk
    AZURE_AVAILABLE = True
except ImportError:
    AZURE_AVAILABLE = False
    Language = None
    speechsdk = None
    logger.warning("Azure STT not available. Install with: pip install 'pipecat-ai[azure]'")

from config.settings import settings


class AzureSTTServiceWithPhrases(AzureSTTService):
    """Extended Azure STT Service with phrase list support"""

    def __init__(self, phrase_list=None, phrase_list_weight=1.0, **kwargs):
        super().__init__(**kwargs)
        self._phrase_list = phrase_list or []
        self._phrase_list_weight = phrase_list_weight
        self._phrase_list_grammar = None
        self._audio_chunks_received = 0
        self._model_name = "azure-stt"

    @property
    def model_name(self) -> str:
        return self._model_name

    def _on_handle_recognized(self, event):
        """Override to add debug logging"""
        logger.info(f"🎤 Azure RECOGNIZED event: reason={event.result.reason}, text='{event.result.text}'")
        super()._on_handle_recognized(event)

    def _on_handle_recognizing(self, event):
        """Override to add debug logging"""
        logger.debug(f"🎤 Azure RECOGNIZING event: text='{event.result.text}'")
        super()._on_handle_recognizing(event)

    async def run_stt(self, audio: bytes):
        """Override to track audio chunks"""
        self._audio_chunks_received += 1
        if self._audio_chunks_received % 100 == 0:
            logger.debug(f"🔊 Azure STT received {self._audio_chunks_received} audio chunks")
        async for frame in super().run_stt(audio):
            yield frame

    def _setup_phrase_list(self, recognizer):
        """Setup phrase list grammar for the recognizer"""
        if not self._phrase_list or not speechsdk:
            logger.info("🔍 No phrase list configured or Azure Speech SDK not available")
            return

        try:
            logger.info(f"🎯 Setting up phrase list with {len(self._phrase_list)} phrases")
            logger.debug(f"🎯 Phrases: {', '.join(self._phrase_list)}")

            # Create phrase list grammar from recognizer
            self._phrase_list_grammar = speechsdk.PhraseListGrammar.from_recognizer(recognizer)
            logger.debug("🎯 Created PhraseListGrammar from recognizer")

            # Add all phrases
            for phrase in self._phrase_list:
                self._phrase_list_grammar.addPhrase(phrase)
                logger.debug(f"🎯 Added phrase: '{phrase}'")

            # Set weight for phrase list (if method exists)
            if hasattr(self._phrase_list_grammar, 'setWeight'):
                self._phrase_list_grammar.setWeight(self._phrase_list_weight)
                logger.debug(f"🎯 Set phrase list weight to: {self._phrase_list_weight}")
            else:
                logger.debug(f"🎯 PhraseListGrammar.setWeight() not available in this Azure SDK version")

            logger.success(f"✅ Azure STT Phrase List Active: {len(self._phrase_list)} phrases with weight {self._phrase_list_weight}")
            logger.info(f"🎯 Phrases should now be recognized better: {', '.join(self._phrase_list)}")

        except Exception as e:
            logger.error(f"❌ Failed to setup phrase list: {e}")
            import traceback
            logger.debug(f"❌ Full error: {traceback.format_exc()}")

    async def start(self, frame: StartFrame):
        """Override start method to setup phrase list after recognizer creation"""
        # Call parent start method first (this creates self._speech_recognizer)
        await super().start(frame)

        # Setup phrase list if speech recognizer exists
        if hasattr(self, '_speech_recognizer') and self._speech_recognizer:
            self._setup_phrase_list(self._speech_recognizer)
        else:
            logger.warning("⚠️ No speech recognizer found to setup phrase list")


def create_stt_service() -> Union[DeepgramSTTService, "AzureSTTServiceWithPhrases"]:
    """Create and configure STT service based on provider setting"""
    provider = settings.stt_provider

    logger.info(f"🎙️ Creating {provider.upper()} STT service")

    if provider == "azure":
        return create_azure_stt_service()
    else:
        return create_deepgram_stt_service()


def create_deepgram_stt_service() -> DeepgramSTTService:
    """Create and configure Deepgram STT service"""
    config = settings.deepgram_config

    # ADD DEBUGGING LOGS
    logger.debug(f"🔍 Creating Deepgram STT with API key: {config['api_key'][:10]}...")
    logger.debug(f"🔍 Deepgram config: {config}")

    try:
        stt_service = DeepgramSTTService(
            api_key=config["api_key"],
            sample_rate=config["sample_rate"],
            audio_passthrough=True,  # Pass raw audio frames through for AudioBufferProcessor
            live_options=LiveOptions(
                model=config["model"],
                language=config["language"],
                encoding=config["encoding"],
                channels=config["channels"],
                sample_rate=config["sample_rate"],
                interim_results=config["interim_results"],
                smart_format=config["smart_format"],
                punctuate=config["punctuate"],
                vad_events=config["vad_events"],
                profanity_filter=config["profanity_filter"],
                numerals=config["numerals"]
            )
        )

        # ADD SUCCESS LOG
        logger.success("✅ Deepgram STT service created successfully")
        return stt_service

    except Exception as e:
        # ADD ERROR LOG
        logger.error(f"❌ Failed to create Deepgram STT service: {e}")
        raise


def create_azure_stt_service() -> "AzureSTTServiceWithPhrases":
    """Create and configure Azure STT service with phrase list support"""
    if not AZURE_AVAILABLE:
        logger.error("❌ Azure STT not available. Install with: pip install 'pipecat-ai[azure]'")
        raise ImportError("Azure STT service not available")

    config = settings.azure_stt_config

    # ADD DEBUGGING LOGS
    logger.debug(f"🔍 Creating Azure STT with region: {config['region']}")
    logger.debug(f"🔍 Azure STT config: {config}")

    try:
        # Prepare service parameters
        service_params = {
            "api_key": config["api_key"],
            "region": config["region"],
            "sample_rate": config["sample_rate"],
            "audio_passthrough": True  # Pass raw audio frames through for AudioBufferProcessor
        }

        # Add language if available (convert string to Language enum if needed)
        language_code = config.get("language", "it-IT")
        if Language:
            # Map language codes to Language enum values
            language_map = {
                "it-IT": Language.IT_IT,
                "en-US": Language.EN_US,
                "es-ES": Language.ES_ES,
                "fr-FR": Language.FR_FR,
                "de-DE": Language.DE_DE
            }
            service_params["language"] = language_map.get(language_code, Language.IT_IT)

        # Add optional endpoint_id if provided
        if config.get("endpoint_id"):
            service_params["endpoint_id"] = config["endpoint_id"]

        # Add phrase list support
        if config.get("phrase_list"):
            service_params["phrase_list"] = config["phrase_list"]
            service_params["phrase_list_weight"] = config.get("phrase_list_weight", 1.0)

        stt_service = AzureSTTServiceWithPhrases(**service_params)

        logger.success("✅ Azure STT service with phrase list created successfully")
        return stt_service

    except Exception as e:
        # ADD ERROR LOG
        logger.error(f"❌ Failed to create Azure STT service: {e}")
        raise


def create_tts_service() -> ElevenLabsTTSService:
    """Create and configure ElevenLabs TTS service"""
    config = settings.elevenlabs_config

    return ElevenLabsTTSService(
        api_key=config["api_key"],
        voice_id=config["voice_id"],
        model=config["model"],
        sample_rate=config["sample_rate"],
        stability=config["stability"],
        similarity_boost=config["similarity_boost"],
        style=config["style"],
        use_speaker_boost=config["use_speaker_boost"]
    )


def create_llm_service() -> OpenAILLMService:
    """Create and configure OpenAI LLM service"""
    config = settings.openai_config
    
    return OpenAILLMService(
        api_key=config["api_key"],
        model=config["model"]
    )


def create_context_aggregator(llm_service: OpenAILLMService) -> LLMContextAggregatorPair:
    """Create context aggregator for the LLM using universal LLMContext"""
    return LLMContextAggregatorPair(LLMContext())
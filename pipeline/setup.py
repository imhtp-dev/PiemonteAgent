"""
Pipeline setup and configuration
"""

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.transports.daily.transport import DailyParams, DailyTransport
from pipecat.audio.vad.silero import SileroVADAnalyzer, VADParams

from config.settings import settings
from .components import create_stt_service, create_tts_service, create_llm_service, create_context_aggregator
from .recording import SessionRecorder, create_transcript_processor, create_audio_buffer_processor


def create_transport(room_url: str, token: str) -> DailyTransport:
    """Create and configure Daily transport"""
    config = settings.daily_config
    vad_config = settings.vad_config
    
    return DailyTransport(
        room_url,
        token,
        "Healthcare Flow Assistant",
        DailyParams(
            audio_in_enabled=config["params"]["audio_in_enabled"],
            audio_out_enabled=config["params"]["audio_out_enabled"],
            transcription_enabled=config["params"]["transcription_enabled"],
            audio_in_sample_rate=config["params"]["audio_in_sample_rate"],
            audio_out_sample_rate=config["params"]["audio_out_sample_rate"],
            camera_enabled=config["params"]["camera_enabled"],
            mic_enabled=config["params"]["mic_enabled"],
            dial_in_timeout=config["params"]["dial_in_timeout"],
            connection_timeout=config["params"]["connection_timeout"],
            vad_analyzer=SileroVADAnalyzer(
                params=VADParams(
                    start_secs=vad_config["start_secs"],
                    stop_secs=vad_config["stop_secs"],
                    min_volume=vad_config["min_volume"]
                )
            )
        )
    )


def create_pipeline_task(transport: DailyTransport) -> tuple:
    """Create the complete pipeline task with recording capabilities"""
    # Create all services
    stt = create_stt_service()
    tts = create_tts_service()
    llm = create_llm_service()
    context_aggregator, _node_mute = create_context_aggregator(llm)

    # Create recording components
    session_recorder = SessionRecorder()
    transcript_processor = create_transcript_processor(session_recorder)
    audio_buffer_processor = create_audio_buffer_processor(session_recorder)

    # Create pipeline with recording processors
    pipeline = Pipeline([
        transport.input(),
        stt,
        transcript_processor.user(),        # Capture user transcripts
        context_aggregator.user(),
        llm,
        tts,
        transport.output(),
        audio_buffer_processor,             # Capture audio after output
        transcript_processor.assistant(),   # Capture assistant transcripts
        context_aggregator.assistant()
    ])

    # Create task with pipeline
    config = settings.pipeline_config
    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            allow_interruptions=config["allow_interruptions"],
            enable_metrics=config["enable_metrics"],
            enable_usage_metrics=config["enable_usage_metrics"]
        )
    )

    return task, llm, context_aggregator, session_recorder, audio_buffer_processor, stt
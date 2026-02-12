"""
OpenTelemetry & Arize Phoenix Integration
Provides tracing capabilities for Pipecat healthcare agent.

Traces are exported via OTLP to a self-hosted Phoenix instance.
Phoenix provides hierarchical span visualization, session grouping,
and token usage queries — all free/unlimited when self-hosted.

IMPORTANT: This module uses Pipecat's native setup_tracing() function to ensure
that Pipecat's internal tracing observers (turn_trace_observer, etc.) use the
same TracerProvider and exporter as our application code.
"""
import os
import re
import json
import asyncio
from typing import Optional, Dict, Sequence
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider, ReadableSpan
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SpanExporter, SpanExportResult
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource, SERVICE_NAME
from opentelemetry.trace import Status, StatusCode
from loguru import logger

# Import Pipecat's native tracing setup
try:
    from pipecat.utils.tracing.setup import setup_tracing as pipecat_setup_tracing
    PIPECAT_TRACING_AVAILABLE = True
except ImportError:
    PIPECAT_TRACING_AVAILABLE = False
    logger.warning("Pipecat tracing module not available, using fallback")

# Global storage for mapping conversation_id -> OpenTelemetry trace_id
_conversation_trace_map: Dict[str, str] = {}


# ============================================================================
# OpenInference Exporter Wrapper
# Maps Pipecat's gen_ai.* attributes → OpenInference conventions for Phoenix UI
# (icons, token counts, model name display)
# ============================================================================

# Session IDs are UUIDs — validate before interpolating into SpanQuery
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)

# Max input messages to flatten into span attributes (prevents attribute explosion)
_MAX_INPUT_MESSAGES = 20

# Phoenix project name for OTLP-ingested traces
_PHOENIX_PROJECT = os.getenv("PHOENIX_PROJECT_NAME", "default")

# Pipecat span name → OpenInference span kind
_SPAN_KIND_MAP = {
    "llm": "LLM",
    "tts": "TOOL",
    "stt": "TOOL",
    "turn": "CHAIN",
    "conversation": "CHAIN",
}

# gen_ai.* → OpenInference attribute mapping
_ATTR_MAP = {
    "gen_ai.usage.input_tokens": "llm.token_count.prompt",
    "gen_ai.usage.output_tokens": "llm.token_count.completion",
    "gen_ai.request.model": "llm.model_name",
}


class OpenInferenceExporter(SpanExporter):
    """Wraps an OTLP exporter, adding OpenInference attributes before export.

    Phoenix uses OpenInference semantic conventions for:
    - Span icons (openinference.span.kind)
    - Token count display (llm.token_count.prompt/completion)
    - Model name display (llm.model_name)

    Pipecat uses standard gen_ai.* conventions, so this wrapper bridges the gap.
    """

    def __init__(self, wrapped: SpanExporter):
        self._wrapped = wrapped

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        for span in spans:
            oi_kind = _SPAN_KIND_MAP.get(span.name)
            if not oi_kind:
                continue

            # NOTE: We mutate ReadableSpan._attributes (private dict) because creating
            # a new ReadableSpan loses internal state (instrumentation_scope, etc.) that
            # Phoenix needs for span detail views. Pinned to opentelemetry-sdk >=1.33,<2.0.
            a = span._attributes
            a["openinference.span.kind"] = oi_kind

            for src, dst in _ATTR_MAP.items():
                if src in a:
                    a[dst] = a[src]

            if oi_kind == "LLM":
                # Token totals
                prompt = a.get("llm.token_count.prompt", 0)
                completion = a.get("llm.token_count.completion", 0)
                if prompt or completion:
                    a["llm.token_count.total"] = (prompt or 0) + (completion or 0)

                # Parse input messages for Phoenix LLM replay
                raw_input = a.get("input")
                if raw_input and isinstance(raw_input, str):
                    try:
                        messages = json.loads(raw_input)
                        if isinstance(messages, list):
                            a["input.value"] = raw_input
                            a["input.mime_type"] = "json"
                            # Only flatten last N messages to avoid attribute explosion
                            recent = messages[-_MAX_INPUT_MESSAGES:]
                            for i, msg in enumerate(recent):
                                if isinstance(msg, dict):
                                    a[f"llm.input_messages.{i}.message.role"] = msg.get("role", "")
                                    content = msg.get("content", "")
                                    if isinstance(content, list):
                                        content = json.dumps(content)
                                    a[f"llm.input_messages.{i}.message.content"] = str(content)
                    except (json.JSONDecodeError, TypeError):
                        a["input.value"] = raw_input

                # Output message
                raw_output = a.get("output")
                if raw_output:
                    a["output.value"] = str(raw_output)
                    a["llm.output_messages.0.message.role"] = "assistant"
                    a["llm.output_messages.0.message.content"] = str(raw_output)

            elif span.name == "tts":
                # TTS: text is the input, speech is the output
                text = a.get("text")
                if text:
                    a["input.value"] = str(text)
                a["output.value"] = "[audio]"

            elif span.name == "stt":
                # STT: audio is the input, transcript is the output
                a["input.value"] = "[audio]"
                transcript = a.get("transcript")
                if transcript:
                    a["output.value"] = str(transcript)

        return self._wrapped.export(spans)

    def shutdown(self):
        self._wrapped.shutdown()

    def force_flush(self, timeout_millis: int = 30000):
        return self._wrapped.force_flush(timeout_millis)


def setup_tracing(
    service_name: str = "pipecat-healthcare-agent",
    enable_console: bool = False
) -> Optional[trace.Tracer]:
    """
    Initialize OpenTelemetry tracing with Phoenix OTLP exporter.

    Uses Pipecat's native setup_tracing() to ensure all Pipecat internal
    tracing (conversation spans, turn spans, LLM spans) goes through the
    same TracerProvider and gets exported to Phoenix.
    """
    if not os.getenv("ENABLE_TRACING", "false").lower() == "true":
        logger.info("Tracing disabled (ENABLE_TRACING not set)")
        return None

    try:
        otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
        if not otlp_endpoint:
            logger.error("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT not set")
            return None

        # Configure OTLP exporter wrapped with OpenInference attribute mapping
        otlp_exporter = OpenInferenceExporter(OTLPSpanExporter(timeout=10))

        if PIPECAT_TRACING_AVAILABLE:
            logger.info("Using Pipecat's native tracing setup with OpenInference mapping...")
            success = pipecat_setup_tracing(
                service_name=service_name,
                exporter=otlp_exporter,
                console_export=enable_console or os.getenv("OTEL_CONSOLE_EXPORT", "false").lower() == "true"
            )
            if success:
                logger.success(f"OpenTelemetry tracing initialized: {service_name}")
                logger.info(f"Exporting to: {otlp_endpoint}")
                return trace.get_tracer(__name__)
            else:
                logger.error("Pipecat tracing setup failed")
                return None
        else:
            # Fallback: Manual setup if Pipecat's tracing module not available
            logger.info("Using fallback tracing setup...")
            resource = Resource(attributes={
                SERVICE_NAME: service_name,
                "deployment.environment": os.getenv("ENVIRONMENT", "production"),
                "service.version": os.getenv("VERSION", "1.0.0")
            })

            provider = TracerProvider(resource=resource)

            batch_processor = BatchSpanProcessor(
                otlp_exporter,
                max_queue_size=2048,
                schedule_delay_millis=5000,
                max_export_batch_size=512,
                export_timeout_millis=10000
            )
            provider.add_span_processor(batch_processor)

            if enable_console or os.getenv("OTEL_CONSOLE_EXPORT", "false").lower() == "true":
                console_exporter = ConsoleSpanExporter()
                provider.add_span_processor(BatchSpanProcessor(console_exporter))
                logger.info("Console trace export enabled")

            trace.set_tracer_provider(provider)

            logger.success(f"OpenTelemetry tracing initialized (fallback): {service_name}")
            logger.info(f"Exporting to: {otlp_endpoint}")

            return trace.get_tracer(__name__)

    except Exception as e:
        logger.error(f"Failed to initialize tracing: {e}")
        import traceback
        logger.error(f"Full error: {traceback.format_exc()}")
        return None


def get_tracer() -> trace.Tracer:
    """Get the current tracer instance"""
    return trace.get_tracer(__name__)


def flush_traces():
    """
    Force flush all pending traces to Phoenix.

    IMPORTANT: Call this before your application exits to ensure
    all traces are sent. BatchSpanProcessor queues spans and sends
    them asynchronously, so without flushing, traces may be lost.
    """
    try:
        provider = trace.get_tracer_provider()
        if hasattr(provider, 'force_flush'):
            logger.info("Flushing traces to Phoenix...")
            provider.force_flush()
            logger.success("All traces flushed")
    except Exception as e:
        logger.error(f"Failed to flush traces: {e}")


def get_current_trace_id() -> Optional[str]:
    """
    Get the current OpenTelemetry trace ID in hex format.

    Returns:
        Trace ID as hex string or None if no active trace
    """
    current_span = trace.get_current_span()
    if current_span and current_span.get_span_context().is_valid:
        trace_id = format(current_span.get_span_context().trace_id, '032x')
        return trace_id
    return None


def register_conversation_trace(conversation_id: str, trace_id: str) -> None:
    """Register mapping between conversation_id and OpenTelemetry trace_id."""
    _conversation_trace_map[conversation_id] = trace_id
    logger.info(f"Registered trace mapping: conversation_id={conversation_id} -> trace_id={trace_id}")


def get_trace_id_for_conversation(conversation_id: str) -> Optional[str]:
    """Look up the OpenTelemetry trace ID for a given conversation_id."""
    trace_id = _conversation_trace_map.get(conversation_id)
    if trace_id:
        logger.debug(f"Found trace_id={trace_id} for conversation_id={conversation_id}")
    else:
        logger.warning(f"No trace_id found for conversation_id={conversation_id}")
    return trace_id


def cleanup_conversation_trace(conversation_id: str) -> None:
    """Remove the trace mapping for a conversation when it ends."""
    if conversation_id in _conversation_trace_map:
        del _conversation_trace_map[conversation_id]
        logger.debug(f"Cleaned up trace mapping for conversation_id={conversation_id}")


def _get_usage_from_phoenix(session_id: str) -> dict:
    """
    Query Phoenix for all usage metrics (LLM tokens, TTS characters) in a session.

    Uses SpanQuery with get_spans_dataframe() for server-side filtering.
    Strategy: find trace_ids for the session, then query LLM + TTS spans in those traces.
    """
    zero = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "tts_characters": 0}
    try:
        from phoenix.client import Client
        from phoenix.client.types.spans import SpanQuery

        # Validate session_id format before interpolating into query
        if not _UUID_RE.match(session_id):
            logger.warning(f"Invalid session_id format (expected UUID): {session_id[:50]}")
            return zero

        endpoint = os.getenv("PHOENIX_ENDPOINT", "http://phoenix:6006")
        client = Client(base_url=endpoint)

        # Step 1: find trace_ids for this session
        session_df = client.spans.get_spans_dataframe(
            query=SpanQuery().where(f"session.id == '{session_id}'").select("context.trace_id"),
            project_identifier=_PHOENIX_PROJECT,
        )
        if session_df.empty:
            logger.warning(f"No traces found in Phoenix for session: {session_id}")
            return zero

        trace_ids = set(session_df["context.trace_id"].unique())

        # Step 2: LLM token usage
        llm_df = client.spans.get_spans_dataframe(
            query=SpanQuery().where("name == 'llm'").select(
                "context.trace_id",
                "gen_ai.usage.input_tokens",
                "gen_ai.usage.output_tokens",
            ),
            project_identifier=_PHOENIX_PROJECT, limit=1000,
        )
        llm_matched = llm_df[llm_df["context.trace_id"].isin(trace_ids)]
        prompt = int(llm_matched["gen_ai.usage.input_tokens"].fillna(0).sum())
        completion = int(llm_matched["gen_ai.usage.output_tokens"].fillna(0).sum())

        # Step 3: TTS character count
        tts_df = client.spans.get_spans_dataframe(
            query=SpanQuery().where("name == 'tts'").select(
                "context.trace_id",
                "metrics.character_count",
            ),
            project_identifier=_PHOENIX_PROJECT, limit=1000,
        )
        tts_matched = tts_df[tts_df["context.trace_id"].isin(trace_ids)]
        tts_chars = int(tts_matched["metrics.character_count"].fillna(0).sum())

        total = prompt + completion
        logger.info(
            f"Phoenix usage for session {session_id}: "
            f"LLM prompt={prompt} completion={completion} total={total}, "
            f"TTS chars={tts_chars}"
        )
        return {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
            "tts_characters": tts_chars,
        }

    except ImportError as e:
        logger.warning(f"Phoenix client import failed: {e}")
        return zero
    except Exception as e:
        logger.error(f"Phoenix usage query error: {e}")
        return zero


async def get_conversation_usage(session_id: Optional[str] = None) -> dict:
    """
    Query Phoenix for all usage metrics (LLM tokens, TTS characters) for a session.

    Returns:
        dict with keys: prompt_tokens, completion_tokens, total_tokens, tts_characters
    """
    zero = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "tts_characters": 0}
    try:
        if not session_id:
            logger.warning("No session_id provided for usage query")
            return zero

        logger.info(f"Querying Phoenix for usage with session_id: {session_id}")

        loop = asyncio.get_event_loop()
        usage_data = await loop.run_in_executor(None, _get_usage_from_phoenix, session_id)

        logger.success(
            f"Phoenix usage: LLM={usage_data['total_tokens']} tokens, "
            f"TTS={usage_data['tts_characters']} chars"
        )
        return usage_data

    except Exception as e:
        logger.error(f"Failed to get usage from Phoenix: {e}")
        return zero


async def update_trace_metadata(
    session_id: str,
    input_text: str,
    output_text: str,
    call_type: str = "call",
    caller_phone: str = None,
    metadata: dict = None
) -> bool:
    """
    Log trace metadata. With Phoenix, metadata is set as span attributes
    directly on the conversation span in bot.py — no SDK upsert needed.

    This function is kept for API compatibility with callers in bot.py/voice_test.py.
    """
    logger.info(f"Trace metadata for session {session_id}: type={call_type}")
    return True

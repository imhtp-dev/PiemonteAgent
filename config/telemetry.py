"""
OpenTelemetry and LangFuse Integration
Provides tracing capabilities for Pipecat healthcare agent

IMPORTANT: This module uses Pipecat's native setup_tracing() function to ensure
that Pipecat's internal tracing observers (turn_trace_observer, etc.) use the
same TracerProvider and exporter as our application code.
"""
import os
import asyncio
from typing import Optional, Dict
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource, SERVICE_NAME
from opentelemetry.trace import Status, StatusCode
from langfuse import Langfuse
from loguru import logger

# Import Pipecat's native tracing setup
try:
    from pipecat.utils.tracing.setup import setup_tracing as pipecat_setup_tracing
    PIPECAT_TRACING_AVAILABLE = True
except ImportError:
    PIPECAT_TRACING_AVAILABLE = False
    logger.warning("⚠️ Pipecat tracing module not available, using fallback")

# Global storage for mapping conversation_id -> OpenTelemetry trace_id
_conversation_trace_map: Dict[str, str] = {}

# Track if patch has been applied (prevent double-patching)
_trace_patch_applied = False


def patch_trace_input_output():
    """
    DISABLED: Pipecat's internal tracing API is unstable.

    Instead, we use update_trace_io() to update traces via LangFuse SDK
    after the call ends. This is more reliable and doesn't depend on
    Pipecat's internal implementation.

    The update_trace_io() function is called in bot.py and voice_test.py
    after token retrieval, using the transcript to get input/output.
    """
    global _trace_patch_applied

    if _trace_patch_applied:
        return

    # Mark as "applied" but don't actually patch - we use SDK approach instead
    _trace_patch_applied = True
    logger.info("📝 Trace I/O will be updated via LangFuse SDK after call ends")


def setup_tracing(
    service_name: str = "pipecat-healthcare-agent",
    enable_console: bool = False
) -> Optional[trace.Tracer]:
    """
    Initialize OpenTelemetry tracing with LangFuse OTLP exporter.

    Uses Pipecat's native setup_tracing() to ensure all Pipecat internal
    tracing (conversation spans, turn spans, LLM spans) goes through the
    same TracerProvider and gets exported to LangFuse.

    Args:
        service_name: Name of the service for trace identification
        enable_console: Whether to also export traces to console (debugging)

    Returns:
        Configured tracer instance or None if tracing disabled
    """
    if not os.getenv("ENABLE_TRACING", "false").lower() == "true":
        logger.info("🔍 Tracing disabled (ENABLE_TRACING not set)")
        return None

    try:
        # Apply trace I/O patch BEFORE Pipecat setup (critical for LangFuse visibility)
        patch_trace_input_output()

        otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
        if not otlp_endpoint:
            logger.error("❌ OTEL_EXPORTER_OTLP_TRACES_ENDPOINT not set")
            return None

        # Configure OTLP exporter with extended timeout for LangFuse cloud
        otlp_exporter = OTLPSpanExporter(
            timeout=30  # 30 seconds timeout (up from default ~5s)
        )

        # Use Pipecat's native setup_tracing if available
        # This ensures Pipecat's internal tracing uses the same exporter
        if PIPECAT_TRACING_AVAILABLE:
            logger.info("🔧 Using Pipecat's native tracing setup...")
            success = pipecat_setup_tracing(
                service_name=service_name,
                exporter=otlp_exporter,
                console_export=enable_console or os.getenv("OTEL_CONSOLE_EXPORT", "false").lower() == "true"
            )
            if success:
                logger.success(f"✅ Pipecat OpenTelemetry tracing initialized: {service_name}")
                logger.info(f"📊 Exporting to: {otlp_endpoint}")
                return trace.get_tracer(__name__)
            else:
                logger.error("❌ Pipecat tracing setup failed")
                return None
        else:
            # Fallback: Manual setup if Pipecat's tracing module not available
            logger.info("🔧 Using fallback tracing setup...")
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
                export_timeout_millis=30000
            )
            provider.add_span_processor(batch_processor)

            if enable_console or os.getenv("OTEL_CONSOLE_EXPORT", "false").lower() == "true":
                console_exporter = ConsoleSpanExporter()
                provider.add_span_processor(BatchSpanProcessor(console_exporter))
                logger.info("🔍 Console trace export enabled")

            trace.set_tracer_provider(provider)

            logger.success(f"✅ OpenTelemetry tracing initialized (fallback): {service_name}")
            logger.info(f"📊 Exporting to: {otlp_endpoint}")

            return trace.get_tracer(__name__)

    except Exception as e:
        logger.error(f"❌ Failed to initialize tracing: {e}")
        import traceback
        logger.error(f"❌ Full error: {traceback.format_exc()}")
        return None


def get_tracer() -> trace.Tracer:
    """Get the current tracer instance"""
    return trace.get_tracer(__name__)


def flush_traces():
    """
    Force flush all pending traces to Langfuse

    IMPORTANT: Call this before your application exits to ensure
    all traces are sent to Langfuse. BatchSpanProcessor queues spans
    and sends them asynchronously, so without flushing, traces may be lost.

    Usage:
        # At the end of your script or in cleanup
        from config.telemetry import flush_traces
        flush_traces()
    """
    try:
        provider = trace.get_tracer_provider()
        if hasattr(provider, 'force_flush'):
            logger.info("🔄 Flushing traces to Langfuse...")
            provider.force_flush()
            logger.success("✅ All traces flushed to Langfuse")
    except Exception as e:
        logger.error(f"❌ Failed to flush traces: {e}")


def get_current_trace_id() -> Optional[str]:
    """
    Get the current OpenTelemetry trace ID in hex format (for LangFuse queries)

    Returns:
        Trace ID as hex string (e.g., 'c04dca2bf957960bf2b4e9a7f8c8bb98') or None if no active trace
    """
    current_span = trace.get_current_span()
    if current_span and current_span.get_span_context().is_valid:
        # Convert trace ID (int) to 32-character hex string
        trace_id = format(current_span.get_span_context().trace_id, '032x')
        return trace_id
    return None


def register_conversation_trace(conversation_id: str, trace_id: str) -> None:
    """
    Register the mapping between a conversation_id (session_id) and its OpenTelemetry trace_id.

    This should be called when the pipeline starts and a trace is created, so that later
    we can look up the correct trace ID when querying LangFuse.

    Args:
        conversation_id: The session/conversation ID used in the application
        trace_id: The actual OpenTelemetry trace ID in hex format
    """
    _conversation_trace_map[conversation_id] = trace_id
    logger.info(f"📊 Registered trace mapping: conversation_id={conversation_id} -> trace_id={trace_id}")


def get_trace_id_for_conversation(conversation_id: str) -> Optional[str]:
    """
    Look up the OpenTelemetry trace ID for a given conversation_id.

    Args:
        conversation_id: The session/conversation ID used in the application

    Returns:
        The OpenTelemetry trace ID in hex format, or None if not found
    """
    trace_id = _conversation_trace_map.get(conversation_id)
    if trace_id:
        logger.debug(f"📊 Found trace_id={trace_id} for conversation_id={conversation_id}")
    else:
        logger.warning(f"⚠️ No trace_id found for conversation_id={conversation_id}")
    return trace_id


def cleanup_conversation_trace(conversation_id: str) -> None:
    """
    Remove the trace mapping for a conversation when it ends.

    Args:
        conversation_id: The session/conversation ID to clean up
    """
    if conversation_id in _conversation_trace_map:
        del _conversation_trace_map[conversation_id]
        logger.debug(f"🗑️ Cleaned up trace mapping for conversation_id={conversation_id}")


def get_langfuse_client() -> Langfuse:
    """Get initialized LangFuse client for API queries with extended timeout"""
    import httpx

    # Create httpx client with extended timeout (default 5s is too short for cloud API)
    timeout = float(os.getenv("LANGFUSE_TIMEOUT", "30"))  # 30 seconds default
    httpx_client = httpx.Client(timeout=timeout)

    return Langfuse(
        public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
        secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
        host=os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"),
        httpx_client=httpx_client
    )


async def get_conversation_tokens(session_id: Optional[str] = None) -> dict:
    """
    Query LangFuse API to get total token usage for a conversation session.

    This function queries LangFuse by session_id (which we set via langfuse.session.id
    attribute in PipelineTask) to find all traces for the conversation and sum up tokens.

    Args:
        session_id: The session/conversation ID used in the application.
                   This matches the langfuse.session.id we set in PipelineTask.

    Returns:
        dict with keys: prompt_tokens, completion_tokens, total_tokens
    """
    try:
        if not session_id:
            logger.warning("⚠️ No session_id provided for token query")
            return {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0
            }

        logger.info(f"🔍 Querying LangFuse for tokens with session_id: {session_id}")

        # Run synchronous LangFuse API call in thread pool
        loop = asyncio.get_event_loop()
        token_data = await loop.run_in_executor(
            None,
            _get_tokens_by_session_sync,
            session_id
        )

        logger.success(f"✅ Retrieved tokens from LangFuse: {token_data['total_tokens']}")
        return token_data

    except Exception as e:
        logger.error(f"❌ Failed to get tokens from LangFuse: {e}")
        return {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0
        }


def _get_tokens_by_session_sync(session_id: str) -> dict:
    """
    Synchronous helper to query LangFuse API by session_id.
    Finds all traces for the session and sums up token usage.

    Args:
        session_id: The session ID that was set via langfuse.session.id attribute
    """
    try:
        client = get_langfuse_client()

        # Query traces by session_id
        logger.info(f"🔍 Querying LangFuse traces with session_id: {session_id}")

        # Use the trace.list() API to find traces by session_id
        traces_response = client.api.trace.list(
            session_id=session_id,
            limit=100  # Get up to 100 traces for this session
        )

        if not traces_response or not hasattr(traces_response, 'data') or not traces_response.data:
            logger.warning(f"⚠️ No traces found for session_id: {session_id}")
            return {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0
            }

        logger.info(f"📊 Found {len(traces_response.data)} traces for session_id: {session_id}")

        # Sum tokens across all traces
        total_prompt_tokens = 0
        total_completion_tokens = 0

        for trace_summary in traces_response.data:
            # Get the full trace details to access observations
            try:
                trace_data = client.api.trace.get(trace_summary.id)
                tokens = _extract_tokens_from_trace(trace_data)
                total_prompt_tokens += tokens["prompt_tokens"]
                total_completion_tokens += tokens["completion_tokens"]
                logger.debug(f"📊 Trace {trace_summary.id}: prompt={tokens['prompt_tokens']}, completion={tokens['completion_tokens']}")
            except Exception as e:
                logger.warning(f"⚠️ Failed to get trace {trace_summary.id}: {e}")

        total_tokens = total_prompt_tokens + total_completion_tokens
        logger.info(f"📊 Session total tokens: prompt={total_prompt_tokens}, completion={total_completion_tokens}, total={total_tokens}")

        return {
            "prompt_tokens": total_prompt_tokens,
            "completion_tokens": total_completion_tokens,
            "total_tokens": total_tokens
        }

    except Exception as e:
        logger.error(f"❌ LangFuse API session query error: {e}")
        import traceback
        logger.error(f"❌ Full error: {traceback.format_exc()}")
        return {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0
        }


def _extract_tokens_from_trace(trace_data) -> dict:
    """
    Extract token counts from a single trace's observations.

    Args:
        trace_data: LangFuse trace data object with observations

    Returns:
        dict with prompt_tokens, completion_tokens, total_tokens
    """
    prompt_tokens = 0
    completion_tokens = 0

    if not hasattr(trace_data, 'observations'):
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

    for observation in trace_data.observations:
        # CRITICAL: LangFuse uses uppercase "GENERATION" not lowercase "generation"
        if observation.type == "GENERATION":
            input_tokens = 0
            output_tokens = 0

            # Debug: Log observation attributes to help diagnose token extraction
            logger.debug(f"📊 GENERATION observation: model={getattr(observation, 'model', 'unknown')}")
            logger.debug(f"📊 Has usageDetails: {hasattr(observation, 'usageDetails')}, Has inputUsage: {hasattr(observation, 'inputUsage')}")

            # Strategy 1: Direct attributes
            if hasattr(observation, 'promptTokens') and observation.promptTokens:
                input_tokens = observation.promptTokens
            if hasattr(observation, 'completionTokens') and observation.completionTokens:
                output_tokens = observation.completionTokens

            # Strategy 2: Nested usage object
            if input_tokens == 0 or output_tokens == 0:
                usage = getattr(observation, 'usage', None)
                if usage and isinstance(usage, dict):
                    if input_tokens == 0:
                        input_tokens = usage.get("input", 0) or usage.get("promptTokens", 0) or usage.get("input_tokens", 0) or 0
                    if output_tokens == 0:
                        output_tokens = usage.get("output", 0) or usage.get("completionTokens", 0) or usage.get("output_tokens", 0) or 0

            # Strategy 3: usageDetails object (OTLP traces use this format)
            if input_tokens == 0 or output_tokens == 0:
                usage_details = getattr(observation, 'usageDetails', None) or getattr(observation, 'usage_details', None)
                if usage_details and isinstance(usage_details, dict):
                    if input_tokens == 0:
                        input_tokens = usage_details.get("input", 0) or usage_details.get("prompt_tokens", 0) or 0
                    if output_tokens == 0:
                        output_tokens = usage_details.get("output", 0) or usage_details.get("completion_tokens", 0) or 0

            # Strategy 4: Direct inputUsage/outputUsage attributes (seen in trace JSON)
            if input_tokens == 0:
                input_tokens = getattr(observation, 'inputUsage', 0) or 0
            if output_tokens == 0:
                output_tokens = getattr(observation, 'outputUsage', 0) or 0

            prompt_tokens += input_tokens
            completion_tokens += output_tokens

    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens
    }


def _get_tokens_sync(trace_id: str) -> dict:
    """
    DEPRECATED: Use _get_tokens_by_session_sync instead.
    Synchronous helper to query LangFuse API by trace_id.

    Args:
        trace_id: OpenTelemetry trace ID in hex format (e.g., 'c04dca2bf957960bf2b4e9a7f8c8bb98')
    """
    try:
        client = get_langfuse_client()

        # Get trace by OpenTelemetry trace ID using SDK v3 API
        logger.info(f"🔍 Querying LangFuse with trace ID: {trace_id}")
        trace_data = client.api.trace.get(trace_id)

        # DEBUG: Print trace structure to understand what we have
        logger.info(f"📊 Trace data type: {type(trace_data)}")
        logger.info(f"📊 Trace attributes: {dir(trace_data)}")

        # Check if observations exist
        if hasattr(trace_data, 'observations'):
            logger.info(f"📊 Number of observations: {len(trace_data.observations)}")

            # Debug first observation structure
            if len(trace_data.observations) > 0:
                first_obs = trace_data.observations[0]
                logger.info(f"📊 First observation type: {first_obs.type}")
                logger.info(f"📊 First observation attributes: {dir(first_obs)}")

                # Check for direct token attributes
                if hasattr(first_obs, 'promptTokens'):
                    logger.info(f"📊 First observation promptTokens (direct): {first_obs.promptTokens}")
                if hasattr(first_obs, 'completionTokens'):
                    logger.info(f"📊 First observation completionTokens (direct): {first_obs.completionTokens}")
                if hasattr(first_obs, 'totalTokens'):
                    logger.info(f"📊 First observation totalTokens (direct): {first_obs.totalTokens}")

                # Check nested usage object
                if hasattr(first_obs, 'usage'):
                    logger.info(f"📊 First observation usage (nested): {first_obs.usage}")

        # Calculate total tokens across all LLM spans
        prompt_tokens = 0
        completion_tokens = 0

        # Navigate through observations to find LLM generations
        if hasattr(trace_data, 'observations'):
            for i, observation in enumerate(trace_data.observations):
                logger.debug(f"📊 Observation {i}: type={observation.type}")

                # CRITICAL: LangFuse uses uppercase "GENERATION" not lowercase "generation"
                if observation.type == "GENERATION":  # LLM calls
                    input_tokens = 0
                    output_tokens = 0

                    # Strategy 1: Try to get tokens from direct observation attributes first
                    # (LangFuse stores OTLP data as attributes on the observation object)
                    if hasattr(observation, 'promptTokens') and observation.promptTokens:
                        input_tokens = observation.promptTokens
                        logger.info(f"📊 Found promptTokens as direct attribute: {input_tokens}")

                    if hasattr(observation, 'completionTokens') and observation.completionTokens:
                        output_tokens = observation.completionTokens
                        logger.info(f"📊 Found completionTokens as direct attribute: {output_tokens}")

                    # Strategy 2: Fallback to nested usage object if attributes not found
                    if input_tokens == 0 or output_tokens == 0:
                        usage = observation.usage
                        if usage:
                            logger.info(f"📊 Checking nested usage object: {usage}")
                            # Try different field names in usage dict
                            if input_tokens == 0:
                                input_tokens = usage.get("input", 0) or usage.get("promptTokens", 0) or usage.get("input_tokens", 0)
                            if output_tokens == 0:
                                output_tokens = usage.get("output", 0) or usage.get("completionTokens", 0) or usage.get("output_tokens", 0)

                    if input_tokens > 0 or output_tokens > 0:
                        prompt_tokens += input_tokens
                        completion_tokens += output_tokens
                        logger.success(f"✅ Added tokens from observation {i}: input={input_tokens}, output={output_tokens}")
                    else:
                        logger.warning(f"⚠️ Observation {i} has no token data")

        total_tokens = prompt_tokens + completion_tokens
        logger.info(f"📊 Total tokens calculated: prompt={prompt_tokens}, completion={completion_tokens}, total={total_tokens}")

        return {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens
        }

    except Exception as e:
        logger.error(f"❌ LangFuse API query error: {e}")
        import traceback
        logger.error(f"❌ Full error: {traceback.format_exc()}")
        return {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0
        }


async def update_trace_io(
    session_id: str,
    input_text: str,
    output_text: str,
    call_type: str = "call",
    caller_phone: str = None,
    metadata: dict = None
) -> bool:
    """
    Update a LangFuse trace with input/output, name, and call metadata.

    Also sets a meaningful trace name for easy identification in LangFuse:
    Format: "{call_type} | {session_id[:8]} | {phone_last4}"

    Args:
        session_id: The session/conversation ID (used as langfuse.session.id)
        input_text: First user message (shown in LangFuse Input field)
        output_text: Last assistant response (shown in LangFuse Output field)
        call_type: Type of call - "booking", "info", "transfer" (for trace name)
        caller_phone: Caller phone number (last 4 digits shown in trace name)
        metadata: Optional dict with call enrichment data (outcome, cost, nodes, etc.)

    Returns:
        True if update successful, False otherwise
    """
    try:
        if not session_id:
            logger.warning("⚠️ No session_id provided for trace I/O update")
            return False

        logger.info(f"📝 Updating trace I/O for session: {session_id}")

        # Run synchronous update in thread pool
        loop = asyncio.get_event_loop()
        success = await loop.run_in_executor(
            None,
            _update_trace_io_sync,
            session_id,
            input_text,
            output_text,
            call_type,
            caller_phone,
            metadata
        )

        return success

    except Exception as e:
        logger.error(f"❌ Failed to update trace I/O: {e}")
        return False


def _update_trace_io_sync(
    session_id: str,
    input_text: str,
    output_text: str,
    call_type: str = "call",
    caller_phone: str = None,
    metadata: dict = None
) -> bool:
    """
    Update trace I/O, name, tags, and metadata via LangFuse Ingestion API (upsert).

    Sets trace name format: "{call_type} | {session_id[:8]} | {phone_last4}"
    Example: "booking | abc12345 | *7890"

    Metadata can include: outcome, last_node, node_history, failure_count,
    cost breakdown, duration, etc.
    """
    try:
        from langfuse.api.resources.ingestion.types import TraceBody, IngestionEvent_TraceCreate
        from datetime import datetime, timezone
        import uuid

        client = get_langfuse_client()

        # Find trace by session_id to get its ID
        traces_response = client.api.trace.list(
            session_id=session_id,
            limit=1
        )

        if not traces_response or not hasattr(traces_response, 'data') or not traces_response.data:
            logger.warning(f"⚠️ No traces found for session_id: {session_id}")
            return False

        trace_id = traces_response.data[0].id
        logger.info(f"📊 Found trace {trace_id} for session {session_id}")

        # Build meaningful trace name
        phone_suffix = f"*{caller_phone[-4:]}" if caller_phone and len(caller_phone) >= 4 else "unknown"
        trace_name = f"{call_type} | {session_id[:8]} | {phone_suffix}"

        # Build tags from call type and outcome
        tags = [call_type]
        if metadata:
            outcome = metadata.get("outcome")
            if outcome:
                tags.append(outcome)
            last_node = metadata.get("last_node")
            if last_node:
                tags.append(f"node:{last_node}")
            stt_provider = metadata.get("stt_provider")
            if stt_provider:
                tags.append(f"stt:{stt_provider}")

        # Build TraceBody for upsert
        body = TraceBody(
            id=trace_id,
            name=trace_name,
            input=input_text[:1000] if input_text else None,
            output=output_text[:1000] if output_text else None,
            tags=tags,
            metadata=metadata,
        )

        # Create ingestion event
        event_id = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:23] + "Z"
        event = IngestionEvent_TraceCreate(id=event_id, timestamp=timestamp, body=body)

        # Send via ingestion batch API (upserts the trace)
        response = client.api.ingestion.batch(batch=[event])
        logger.info(f"📤 Ingestion response: {response}")

        logger.success(f"✅ Updated trace {trace_id}: name='{trace_name}' tags={tags}")
        if metadata:
            cost_total = metadata.get("cost_total_usd")
            if cost_total is not None:
                logger.info(f"💰 Cost attached: ${cost_total:.4f} (${metadata.get('cost_per_minute_usd', 0):.4f}/min)")

        return True

    except Exception as e:
        logger.error(f"❌ Failed to update trace I/O: {e}")
        import traceback
        logger.error(f"❌ Full error: {traceback.format_exc()}")
        return False

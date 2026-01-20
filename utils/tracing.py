"""
OpenTelemetry Tracing Utilities for Info Agent
Provides decorators and context managers for tracking API calls in LangFuse
"""

import time
import functools
from typing import Optional, Dict, Any, Callable
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from loguru import logger


# Get tracer for info agent
tracer = trace.get_tracer("info_agent")


def trace_api_call(span_name: str, add_args: bool = True):
    """
    Decorator to automatically trace async API calls with OpenTelemetry

    This creates a span around the decorated function and automatically:
    - Captures function execution time
    - Records success/failure status
    - Logs exceptions with full stack traces
    - Adds function arguments as span attributes (if add_args=True)

    Args:
        span_name: Name for the span (e.g., "api.knowledge_base_query")
        add_args: Whether to add function kwargs as span attributes (default: True)

    Usage:
        @trace_api_call("api.knowledge_base")
        async def query(self, question: str):
            # Your API call logic here
            return result

    Example Span in LangFuse:
        api.knowledge_base_query (1.2s)
        ├── function_name: query
        ├── arg.question: "Quali esami servono?"
        ├── success: True
        ├── latency_ms: 1234
        └── status_code: 200
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # Start a new span
            with tracer.start_as_current_span(span_name) as span:
                # Add function metadata
                span.set_attribute("function_name", func.__name__)
                span.set_attribute("module", func.__module__)

                # Add kwargs as attributes (for debugging) - truncate long strings
                if add_args:
                    for key, value in kwargs.items():
                        if isinstance(value, (str, int, float, bool)):
                            # Truncate strings to 200 chars to avoid huge spans
                            str_value = str(value)
                            if len(str_value) > 200:
                                str_value = str_value[:200] + "..."
                            span.set_attribute(f"arg.{key}", str_value)

                try:
                    start_time = time.time()

                    # Execute the actual function
                    result = await func(*args, **kwargs)

                    # Calculate elapsed time
                    elapsed_ms = (time.time() - start_time) * 1000

                    # Track success metrics
                    span.set_attribute("success", True)
                    span.set_attribute("latency_ms", round(elapsed_ms, 2))

                    logger.debug(f"✅ {span_name} completed in {elapsed_ms:.0f}ms")
                    return result

                except Exception as e:
                    # Track error with full details
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    span.record_exception(e)
                    span.set_attribute("success", False)
                    span.set_attribute("error_type", type(e).__name__)
                    span.set_attribute("error_message", str(e))

                    logger.error(f"❌ {span_name} failed: {e}")
                    raise

        return wrapper
    return decorator


class APICallSpan:
    """
    Context manager for manual span tracking in synchronous or complex code

    Use this when you need more control over span attributes or when
    the decorator approach doesn't fit your use case.

    Usage:
        async with APICallSpan("api.custom_call", {"query": "test"}) as span:
            result = await some_api_call()
            span.set_attribute("result_count", len(result))
            return result

    Or synchronously:
        with APICallSpan("processing.data_transform", {"rows": 100}) as span:
            processed = transform_data(data)
            span.set_attribute("output_rows", len(processed))
    """

    def __init__(self, name: str, attributes: Optional[Dict[str, Any]] = None):
        """
        Initialize the span context manager

        Args:
            name: Span name (e.g., "api.knowledge_base")
            attributes: Dictionary of attributes to add to the span
        """
        self.name = name
        self.attributes = attributes or {}
        self.span = None
        self.start_time = None

    def __enter__(self):
        """Enter the context - start the span"""
        self.span = tracer.start_span(self.name)
        self.span.__enter__()
        self.start_time = time.time()

        # Add initial attributes
        for key, value in self.attributes.items():
            if isinstance(value, (str, int, float, bool)):
                str_value = str(value)
                if len(str_value) > 200:
                    str_value = str_value[:200] + "..."
                self.span.set_attribute(key, str_value)

        return self.span

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit the context - close the span"""
        if self.start_time:
            elapsed_ms = (time.time() - self.start_time) * 1000
            self.span.set_attribute("latency_ms", round(elapsed_ms, 2))

        if exc_type:
            # Error occurred
            self.span.set_status(Status(StatusCode.ERROR, str(exc_val)))
            self.span.record_exception(exc_val)
            self.span.set_attribute("success", False)
            self.span.set_attribute("error_type", exc_type.__name__)
        else:
            # Success
            self.span.set_attribute("success", True)

        self.span.__exit__(exc_type, exc_val, exc_tb)


def add_span_attributes(attributes: Dict[str, Any]):
    """
    Add attributes to the current active span

    This is useful for adding contextual information during execution
    without needing to pass the span object around.

    Usage:
        # Inside a traced function
        add_span_attributes({
            "status_code": 200,
            "result_count": 5,
            "cache_hit": True
        })

    Args:
        attributes: Dictionary of key-value pairs to add as span attributes
    """
    current_span = trace.get_current_span()
    if current_span and current_span.is_recording():
        for key, value in attributes.items():
            if isinstance(value, (str, int, float, bool)):
                str_value = str(value)
                if len(str_value) > 200:
                    str_value = str_value[:200] + "..."
                current_span.set_attribute(key, str_value)


def record_span_error(error: Exception, message: Optional[str] = None):
    """
    Record an error on the current span without raising

    Useful for non-fatal errors that you want to track but not propagate.

    Usage:
        try:
            cache_result = await get_from_cache()
        except CacheError as e:
            record_span_error(e, "Cache miss - falling back to API")
            cache_result = None

    Args:
        error: The exception to record
        message: Optional custom error message
    """
    current_span = trace.get_current_span()
    if current_span and current_span.is_recording():
        current_span.record_exception(error)
        if message:
            current_span.set_attribute("error_context", message)
        current_span.set_attribute("error_type", type(error).__name__)


def create_child_span(name: str, attributes: Optional[Dict[str, Any]] = None):
    """
    Create a child span under the current span

    Use this to break down complex operations into sub-operations for better visibility.

    Usage:
        with create_child_span("api.knowledge_base") as parent_span:
            with create_child_span("step.query_formatting"):
                formatted_query = format_query(query)

            with create_child_span("step.http_request"):
                response = await make_request(formatted_query)

            with create_child_span("step.response_parsing"):
                result = parse_response(response)

    Args:
        name: Name for the child span
        attributes: Optional attributes to add to the span

    Returns:
        Context manager for the child span
    """
    return APICallSpan(name, attributes)


def trace_error(
    error: Exception,
    context: str = None,
    extra_attrs: Optional[Dict[str, Any]] = None,
    set_error_status: bool = True
):
    """
    Log error to current LangFuse span with full details.

    This is the recommended way to capture errors in LangFuse for debugging.
    Use this in except blocks to ensure errors show up in traces.

    Args:
        error: The exception to record
        context: Optional context describing where/why the error occurred
        extra_attrs: Additional attributes to add (e.g., {"api": "booking", "retry_count": 2})
        set_error_status: Whether to mark the span as ERROR status (default True)

    Usage:
        try:
            result = await booking_api.create_booking(data)
        except Exception as e:
            trace_error(e, "booking_creation_failed", {"patient_id": patient_id})
            # Handle error...

    In LangFuse, this will show:
        span: booking_creation (ERROR)
        ├── error.context: "booking_creation_failed"
        ├── error.type: "APIError"
        ├── error.message: "Connection timeout"
        ├── error.patient_id: "12345"
        └── exception stack trace
    """
    current_span = trace.get_current_span()
    if current_span and current_span.is_recording():
        # Set span status to ERROR
        if set_error_status:
            current_span.set_status(Status(StatusCode.ERROR, str(error)[:200]))

        # Record the full exception with stack trace
        current_span.record_exception(error)

        # Add error context
        current_span.set_attribute("error.type", type(error).__name__)
        current_span.set_attribute("error.message", str(error)[:500])

        if context:
            current_span.set_attribute("error.context", context)

        # Add any extra attributes
        if extra_attrs:
            for key, value in extra_attrs.items():
                if value is not None:
                    str_value = str(value)[:200]
                    current_span.set_attribute(f"error.{key}", str_value)

        logger.debug(f"📊 Traced error to LangFuse: {type(error).__name__}: {str(error)[:100]}")


def trace_sync_call(span_name: str, add_args: bool = True):
    """
    Decorator to trace synchronous function calls with OpenTelemetry.

    Similar to @trace_api_call but for sync functions (not async).
    Use for synchronous API calls like httpx.Client or requests.

    Args:
        span_name: Name for the span (e.g., "api.talkdesk_send")
        add_args: Whether to add function kwargs as span attributes

    Usage:
        @trace_sync_call("api.talkdesk_report")
        def send_to_talkdesk(data: dict) -> bool:
            response = requests.post(url, json=data)
            return response.ok
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            with tracer.start_as_current_span(span_name) as span:
                span.set_attribute("function_name", func.__name__)
                span.set_attribute("module", func.__module__)

                if add_args:
                    for key, value in kwargs.items():
                        if isinstance(value, (str, int, float, bool)):
                            str_value = str(value)[:200]
                            span.set_attribute(f"arg.{key}", str_value)

                try:
                    start_time = time.time()
                    result = func(*args, **kwargs)
                    elapsed_ms = (time.time() - start_time) * 1000

                    span.set_attribute("success", True)
                    span.set_attribute("latency_ms", round(elapsed_ms, 2))

                    logger.debug(f"✅ {span_name} completed in {elapsed_ms:.0f}ms")
                    return result

                except Exception as e:
                    span.set_status(Status(StatusCode.ERROR, str(e)))
                    span.record_exception(e)
                    span.set_attribute("success", False)
                    span.set_attribute("error_type", type(e).__name__)
                    span.set_attribute("error_message", str(e)[:500])

                    logger.error(f"❌ {span_name} failed: {e}")
                    raise

        return wrapper
    return decorator


def add_flow_state_attributes(flow_state: Dict[str, Any]):
    """
    Add booking flow state attributes to the current span.

    Use this to track booking progress in LangFuse traces.
    Extracts key flow state fields safely (handles missing keys, objects).

    Args:
        flow_state: The flow_manager.state dictionary

    Usage:
        # In a handler function
        add_flow_state_attributes(flow_manager.state)

    Adds these span attributes:
        - flow.current_node
        - flow.selected_services (comma-separated names)
        - flow.selected_center
        - flow.patient_name
        - flow.failure_count
        - flow.is_cerba_member
    """
    current_span = trace.get_current_span()
    if not current_span or not current_span.is_recording():
        return

    try:
        # Current node
        current_node = flow_state.get("current_node", "unknown")
        current_span.set_attribute("flow.current_node", str(current_node))

        # Selected services (extract names from objects)
        selected_services = flow_state.get("selected_services", [])
        if selected_services:
            service_names = []
            for svc in selected_services:
                if hasattr(svc, "name"):
                    service_names.append(svc.name)
                elif isinstance(svc, dict):
                    service_names.append(svc.get("name", "Unknown"))
            if service_names:
                current_span.set_attribute("flow.selected_services", ", ".join(service_names)[:200])

        # Selected center
        selected_center = flow_state.get("selected_center")
        if selected_center:
            center_name = getattr(selected_center, "name", None) or selected_center.get("name", "Unknown") if isinstance(selected_center, dict) else str(selected_center)
            current_span.set_attribute("flow.selected_center", str(center_name)[:100])

        # Patient name
        patient_first = flow_state.get("patient_first_name", "")
        patient_surname = flow_state.get("patient_surname", "")
        if patient_first or patient_surname:
            current_span.set_attribute("flow.patient_name", f"{patient_first} {patient_surname}".strip()[:100])

        # Failure tracking
        failure_tracker = flow_state.get("failure_tracker", {})
        if failure_tracker:
            current_span.set_attribute("flow.failure_count", failure_tracker.get("count", 0))

        # Cerba membership
        is_cerba_member = flow_state.get("is_cerba_member")
        if is_cerba_member is not None:
            current_span.set_attribute("flow.is_cerba_member", is_cerba_member)

        # Call type (booking vs info)
        current_agent = flow_state.get("current_agent")
        if current_agent:
            current_span.set_attribute("flow.agent_type", str(current_agent))

    except Exception as e:
        logger.debug(f"⚠️ Could not add flow state attributes: {e}")
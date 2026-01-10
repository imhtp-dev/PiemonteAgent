"""
Logging configuration for the application
Provides structured logging with different levels and formatters
"""

import logging
import sys
import os
from typing import Optional
from datetime import datetime
from pythonjsonlogger import jsonlogger

class ColoredFormatter(logging.Formatter):
    """Colored console formatter for better readability"""
    
    # Color codes
    COLORS = {
        'DEBUG': '\033[36m',      # Cyan
        'INFO': '\033[32m',       # Green
        'WARNING': '\033[33m',    # Yellow
        'ERROR': '\033[31m',      # Red
        'CRITICAL': '\033[35m',   # Magenta
        'RESET': '\033[0m'        # Reset
    }
    
    def format(self, record):
        # Add color to levelname
        if record.levelname in self.COLORS:
            record.levelname = (
                f"{self.COLORS[record.levelname]}"
                f"{record.levelname}"
                f"{self.COLORS['RESET']}"
            )
        
        return super().format(record)

class RequestLogger:
    """Logger for HTTP requests with correlation IDs"""
    
    def __init__(self, logger_name: str = "requests"):
        self.logger = logging.getLogger(logger_name)
    
    def log_request(self, method: str, url: str, status_code: int, 
                   duration: float, request_id: Optional[str] = None):
        """Log HTTP request with timing"""
        extra = {
            "method": method,
            "url": url,
            "status_code": status_code,
            "duration_ms": round(duration * 1000, 2),
            "request_id": request_id
        }
        
        if status_code >= 400:
            self.logger.error(f"{method} {url} - {status_code} ({duration:.2f}s)", extra=extra)
        else:
            self.logger.info(f"{method} {url} - {status_code} ({duration:.2f}s)", extra=extra)

def setup_logging(
    level: str = "INFO",
    log_format: str = "console",
    log_file: Optional[str] = None
) -> None:
    """
    Setup application logging
    
    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_format: Format type ("console", "json")
        log_file: Optional log file path
    """
    # Convert string level to logging constant
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    
    # Create root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)
    
    # Clear existing handlers
    root_logger.handlers.clear()
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(numeric_level)
    
    if log_format.lower() == "json":
        # JSON formatter for production
        json_formatter = jsonlogger.JsonFormatter(
            fmt='%(asctime)s %(name)s %(levelname)s %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        console_handler.setFormatter(json_formatter)
    else:
        # Colored formatter for development
        console_formatter = ColoredFormatter(
            fmt='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        console_handler.setFormatter(console_formatter)
    
    root_logger.addHandler(console_handler)
    
    # File handler (if specified)
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(numeric_level)
        
        # Always use JSON format for file logs
        file_formatter = jsonlogger.JsonFormatter(
            fmt='%(asctime)s %(name)s %(levelname)s %(message)s %(pathname)s %(lineno)d',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(file_formatter)
        root_logger.addHandler(file_handler)
    
    # Set third-party library log levels
    logging.getLogger("uvicorn").setLevel(logging.WARNING)
    logging.getLogger("fastapi").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    
    # Log setup completion
    logger = logging.getLogger(__name__)
    logger.info(f"Logging configured: level={level}, format={log_format}")
    if log_file:
        logger.info(f"File logging enabled: {log_file}")

def get_logger(name: str) -> logging.Logger:
    """
    Get logger instance with consistent configuration
    
    Args:
        name: Logger name (usually __name__)
        
    Returns:
        Configured logger instance
    """
    return logging.getLogger(name)

def log_function_call(func_name: str, args: dict, result: any = None, error: Exception = None):
    """
    Log function call with parameters and result
    
    Args:
        func_name: Name of the function
        args: Function arguments
        result: Function result (if successful)
        error: Exception (if failed)
    """
    logger = get_logger("function_calls")
    
    extra = {
        "function": func_name,
        "args": args,
        "timestamp": datetime.now().isoformat()
    }
    
    if error:
        extra["error"] = str(error)
        extra["error_type"] = type(error).__name__
        logger.error(f"Function {func_name} failed: {error}", extra=extra)
    else:
        if result is not None:
            extra["result_type"] = type(result).__name__
            if hasattr(result, '__len__'):
                extra["result_length"] = len(result)
        logger.info(f"Function {func_name} completed", extra=extra)

def log_api_call(endpoint: str, method: str, status_code: int, 
                response_time: float, error: Optional[str] = None):
    """
    Log API call with timing and status
    
    Args:
        endpoint: API endpoint
        method: HTTP method
        status_code: Response status code
        response_time: Response time in seconds
        error: Error message (if any)
    """
    logger = get_logger("api_calls")
    
    extra = {
        "endpoint": endpoint,
        "method": method,
        "status_code": status_code,
        "response_time_ms": round(response_time * 1000, 2),
        "timestamp": datetime.now().isoformat()
    }
    
    if error:
        extra["error"] = error
        logger.error(f"{method} {endpoint} failed: {error}", extra=extra)
    elif status_code >= 400:
        logger.warning(f"{method} {endpoint} - {status_code}", extra=extra)
    else:
        logger.info(f"{method} {endpoint} - {status_code}", extra=extra)

# Environment-based logging setup
def setup_environment_logging():
    """Setup logging based on environment variables"""
    log_level = os.getenv("LOG_LEVEL", "INFO")
    log_format = os.getenv("LOG_FORMAT", "console")  # "console" or "json"
    log_file = os.getenv("LOG_FILE")  # Optional log file path
    
    # Use JSON format in production
    if os.getenv("ENVIRONMENT") == "production":
        log_format = "json"
    
    setup_logging(level=log_level, log_format=log_format, log_file=log_file)
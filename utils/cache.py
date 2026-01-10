"""
Caching utilities for the application
Provides in-memory caching with TTL (Time To Live) support
"""

import time
import logging
from typing import Any, Optional, Callable, TypeVar, Generic
from threading import Lock
from dataclasses import dataclass

logger = logging.getLogger(__name__)

T = TypeVar('T')

@dataclass
class CacheEntry:
    """Cache entry with data and expiry time"""
    data: Any
    expires_at: float
    created_at: float

class TTLCache(Generic[T]):
    """
    Thread-safe Time-To-Live cache
    Automatically expires entries after specified duration
    """
    
    def __init__(self, default_ttl: int = 3600):
        """
        Initialize cache
        
        Args:
            default_ttl: Default time-to-live in seconds
        """
        self._cache: dict[str, CacheEntry] = {}
        self._lock = Lock()
        self.default_ttl = default_ttl
        
    def get(self, key: str) -> Optional[T]:
        """
        Get item from cache
        
        Args:
            key: Cache key
            
        Returns:
            Cached value or None if not found/expired
        """
        with self._lock:
            entry = self._cache.get(key)
            
            if entry is None:
                logger.debug(f"Cache miss: {key}")
                return None
            
            if time.time() > entry.expires_at:
                logger.debug(f"Cache expired: {key}")
                del self._cache[key]
                return None
            
            logger.debug(f"Cache hit: {key}")
            return entry.data
    
    def set(self, key: str, value: T, ttl: Optional[int] = None) -> None:
        """
        Set item in cache
        
        Args:
            key: Cache key
            value: Value to cache
            ttl: Time-to-live in seconds (uses default if None)
        """
        if ttl is None:
            ttl = self.default_ttl
        
        current_time = time.time()
        expires_at = current_time + ttl
        
        with self._lock:
            self._cache[key] = CacheEntry(
                data=value,
                expires_at=expires_at,
                created_at=current_time
            )
        
        logger.debug(f"Cached: {key} (expires in {ttl}s)")
    
    def delete(self, key: str) -> bool:
        """
        Delete item from cache
        
        Args:
            key: Cache key
            
        Returns:
            True if item was deleted, False if not found
        """
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                logger.debug(f"Cache deleted: {key}")
                return True
            return False
    
    def clear(self) -> None:
        """Clear all cache entries"""
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            logger.info(f"Cache cleared: {count} entries removed")
    
    def cleanup_expired(self) -> int:
        """
        Clean up expired entries
        
        Returns:
            Number of entries removed
        """
        current_time = time.time()
        expired_keys = []
        
        with self._lock:
            for key, entry in self._cache.items():
                if current_time > entry.expires_at:
                    expired_keys.append(key)
            
            for key in expired_keys:
                del self._cache[key]
        
        if expired_keys:
            logger.info(f"Cleaned up {len(expired_keys)} expired cache entries")
        
        return len(expired_keys)
    
    def get_stats(self) -> dict:
        """
        Get cache statistics
        
        Returns:
            Dictionary with cache statistics
        """
        with self._lock:
            current_time = time.time()
            total_entries = len(self._cache)
            expired_entries = sum(
                1 for entry in self._cache.values()
                if current_time > entry.expires_at
            )
            
            return {
                "total_entries": total_entries,
                "active_entries": total_entries - expired_entries,
                "expired_entries": expired_entries,
                "cache_keys": list(self._cache.keys())
            }

class CachedFunction:
    """
    Decorator for caching function results
    """
    
    def __init__(self, ttl: int = 3600, cache_instance: Optional[TTLCache] = None):
        """
        Initialize cached function decorator
        
        Args:
            ttl: Time-to-live in seconds
            cache_instance: Cache instance to use (creates new if None)
        """
        self.ttl = ttl
        self.cache = cache_instance or TTLCache(default_ttl=ttl)
    
    def __call__(self, func: Callable) -> Callable:
        """
        Decorate function with caching
        
        Args:
            func: Function to cache
            
        Returns:
            Cached function
        """
        def wrapper(*args, **kwargs):
            # Create cache key from function name and arguments
            cache_key = f"{func.__name__}:{hash((args, tuple(sorted(kwargs.items()))))}"
            
            # Try to get from cache
            result = self.cache.get(cache_key)
            if result is not None:
                return result
            
            # Execute function and cache result
            result = func(*args, **kwargs)
            self.cache.set(cache_key, result, self.ttl)
            
            return result
        
        # Add cache control methods to wrapper
        wrapper.cache = self.cache
        wrapper.clear_cache = lambda: self.cache.clear()
        wrapper.cache_stats = lambda: self.cache.get_stats()
        
        return wrapper

# Global cache instances
services_cache = TTLCache[list](default_ttl=3600)  # 1 hour for services
auth_cache = TTLCache[str](default_ttl=3300)       # 55 minutes for auth tokens

def get_or_set_cache(
    cache: TTLCache[T],
    key: str,
    fetch_func: Callable[[], T],
    ttl: Optional[int] = None
) -> T:
    """
    Get value from cache or fetch and cache it
    
    Args:
        cache: Cache instance
        key: Cache key
        fetch_func: Function to fetch value if not cached
        ttl: Time-to-live override
        
    Returns:
        Cached or freshly fetched value
    """
    # Try cache first
    value = cache.get(key)
    if value is not None:
        return value
    
    # Fetch and cache
    logger.debug(f"Cache miss, fetching: {key}")
    value = fetch_func()
    cache.set(key, value, ttl)
    
    return value
"""
Database connection and query utilities for PostgreSQL (Supabase)
Uses asyncpg for async PostgreSQL operations
"""

import os
import asyncpg
from typing import Optional, List, Dict, Any
from loguru import logger


class Database:
    """PostgreSQL database connection manager using asyncpg"""
    
    def __init__(self):
        self.pool: Optional[asyncpg.Pool] = None
    
    async def connect(self):
        """Create database connection pool"""
        try:
            database_url = os.getenv("DATABASE_URL")
            if not database_url:
                raise ValueError("DATABASE_URL environment variable not set")

            # Supabase requires SSL connections
            import ssl
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

            self.pool = await asyncpg.create_pool(
                dsn=database_url,
                min_size=10,          # Increased from 5 to handle concurrent calls
                max_size=50,          # Increased from 20 to support 30-40 concurrent calls
                command_timeout=60,
                ssl=ssl_context,
                statement_cache_size=0  # Required for Supabase connection pooler
            )

            # Test connection
            async with self.pool.acquire() as conn:
                await conn.fetchval("SELECT 1")

            logger.info("✅ PostgreSQL connection pool created successfully (SSL enabled)")

        except Exception as e:
            logger.error(f"❌ Failed to connect to PostgreSQL: {e}")
            logger.warning("⚠️ Database pool will remain None (backup files will be used)")
            # Close pool if it was created but connection test failed
            if self.pool:
                try:
                    await self.pool.close()
                except:
                    pass
            # Don't raise - allow agent to continue without database
            self.pool = None
    
    async def close(self):
        """Close database connection pool"""
        if self.pool:
            await self.pool.close()
            logger.info("✅ PostgreSQL connection pool closed")
    
    async def execute(self, query: str, *args) -> str:
        """
        Execute a query that modifies data (INSERT, UPDATE, DELETE)
        Returns the status string (e.g., "INSERT 0 1")
        Raises RuntimeError if pool is not initialized (for backup handling)
        """
        if not self.pool:
            # Raise exception so caller can handle it (e.g., create backup file)
            raise RuntimeError("Database pool not initialized (connection unavailable)")

        async with self.pool.acquire() as conn:
            try:
                result = await conn.execute(query, *args)
                return result
            except Exception as e:
                logger.error(f"❌ Query execution error: {e}")
                logger.error(f"   Query: {query[:200]}...")
                logger.error(f"   Args: {args}")
                raise
    
    async def fetch(self, query: str, *args) -> List[Dict[str, Any]]:
        """
        Fetch multiple rows
        Returns list of dict-like records
        """
        if not self.pool:
            raise RuntimeError("Database pool not initialized. Call connect() first.")
        
        async with self.pool.acquire() as conn:
            try:
                rows = await conn.fetch(query, *args)
                # Convert asyncpg.Record to dict
                return [dict(row) for row in rows]
            except Exception as e:
                logger.error(f"❌ Query fetch error: {e}")
                logger.error(f"   Query: {query[:200]}...")
                logger.error(f"   Args: {args}")
                raise
    
    async def fetchrow(self, query: str, *args) -> Optional[Dict[str, Any]]:
        """
        Fetch a single row
        Returns dict-like record or None
        """
        if not self.pool:
            raise RuntimeError("Database pool not initialized. Call connect() first.")
        
        async with self.pool.acquire() as conn:
            try:
                row = await conn.fetchrow(query, *args)
                # Convert asyncpg.Record to dict
                return dict(row) if row else None
            except Exception as e:
                logger.error(f"❌ Query fetchrow error: {e}")
                logger.error(f"   Query: {query[:200]}...")
                logger.error(f"   Args: {args}")
                raise
    
    async def fetchval(self, query: str, *args) -> Any:
        """
        Fetch a single value
        Returns the first column of the first row
        """
        if not self.pool:
            raise RuntimeError("Database pool not initialized. Call connect() first.")
        
        async with self.pool.acquire() as conn:
            try:
                return await conn.fetchval(query, *args)
            except Exception as e:
                logger.error(f"❌ Query fetchval error: {e}")
                logger.error(f"   Query: {query[:200]}...")
                logger.error(f"   Args: {args}")
                raise
    
    async def transaction(self):
        """
        Get a transaction context manager
        
        Usage:
            async with db.transaction() as conn:
                await conn.execute("INSERT ...")
                await conn.execute("UPDATE ...")
        """
        if not self.pool:
            raise RuntimeError("Database pool not initialized. Call connect() first.")
        
        return self.pool.acquire()


# Global database instance
db = Database()


async def get_db() -> Database:
    """Dependency injection for FastAPI endpoints"""
    return db

"""
Database connection and query utilities for PostgreSQL (Supabase)
Uses asyncpg for async PostgreSQL operations
"""

import os
import asyncpg
from typing import Optional, List, Dict, Any
from loguru import logger
from utils.tracing import trace_api_call


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
    
    @trace_api_call("db.execute", add_args=False)
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
    
    @trace_api_call("db.fetch", add_args=False)
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
    
    @trace_api_call("db.fetchrow", add_args=False)
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
    
    async def upsert_agent_status(
        self, region: str, instance_id: int, active_calls: int, max_capacity: int, status: str = "online"
    ) -> None:
        """Upsert heartbeat row for this container into tb_agent_status."""
        if not self.pool:
            return  # Silently skip if no DB — monitoring is non-critical
        try:
            await self.execute(
                """
                INSERT INTO tb_agent_status (region, instance_id, active_calls, max_capacity, status, last_heartbeat)
                VALUES ($1, $2, $3, $4, $5, NOW())
                ON CONFLICT (region, instance_id)
                DO UPDATE SET
                    active_calls = $3,
                    max_capacity = $4,
                    status = $5,
                    last_heartbeat = NOW()
                """,
                region, instance_id, active_calls, max_capacity, status,
            )
        except Exception as e:
            logger.warning(f"⚠️ Heartbeat upsert failed: {e}")

    async def update_daily_peak(self, region: str, current_calls: int) -> None:
        """Update today's peak concurrent calls if current exceeds recorded peak."""
        if not self.pool or current_calls == 0:
            return
        try:
            await self.execute(
                """
                INSERT INTO tb_daily_peak_calls (region, date, peak_concurrent, total_calls)
                VALUES ($1, CURRENT_DATE, $2, 0)
                ON CONFLICT (region, date)
                DO UPDATE SET
                    peak_concurrent = GREATEST(tb_daily_peak_calls.peak_concurrent, $2),
                    updated_at = NOW()
                """,
                region, current_calls,
            )
        except Exception as e:
            logger.warning(f"⚠️ Daily peak update failed: {e}")

    async def increment_daily_total_calls(self, region: str) -> None:
        """Increment today's total call count (call on each new call start)."""
        if not self.pool:
            return
        try:
            await self.execute(
                """
                INSERT INTO tb_daily_peak_calls (region, date, peak_concurrent, total_calls)
                VALUES ($1, CURRENT_DATE, 0, 1)
                ON CONFLICT (region, date)
                DO UPDATE SET
                    total_calls = tb_daily_peak_calls.total_calls + 1,
                    updated_at = NOW()
                """,
                region,
            )
        except Exception as e:
            logger.warning(f"⚠️ Daily total calls increment failed: {e}")

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

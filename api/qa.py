"""
Q&A Management - Pinecone Initialization Only
(API endpoints migrated to Supabase Edge Functions)
"""

import os
from loguru import logger
from pinecone import Pinecone
from openai import OpenAI


# ==================== Pinecone & OpenAI Initialization ====================

pinecone_client = None
pinecone_index = None
openai_client = None


def initialize_ai_services():
    """Initialize Pinecone and OpenAI clients"""
    global pinecone_client, pinecone_index, openai_client

    try:
        # Initialize Pinecone
        pinecone_api_key = os.getenv("PINECONE_API_KEY")
        if pinecone_api_key:
            pinecone_client = Pinecone(api_key=pinecone_api_key)
            pinecone_index = pinecone_client.Index(
                "knowledgecerba",
                host="https://knowledgecerba-eqvpxqp.svc.apu-57e2-42f6.pinecone.io"
            )
            logger.info("✅ Pinecone client initialized (knowledgecerba)")
        else:
            logger.warning("⚠️ PINECONE_API_KEY not found - Q&A functionality will be limited")

        # Initialize OpenAI
        openai_api_key = os.getenv("OPENAI_API_KEY")
        if openai_api_key:
            openai_client = OpenAI(api_key=openai_api_key)
            logger.info("✅ OpenAI client initialized")
        else:
            logger.warning("⚠️ OPENAI_API_KEY not found - Embedding generation unavailable")

    except Exception as e:
        logger.error(f"❌ Error initializing AI services: {e}")
        raise

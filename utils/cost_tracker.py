"""
Cost tracking for voice agent calls.

Calculates per-call and per-minute costs for:
- LLM (OpenAI GPT-4.1): token-based pricing
- TTS (ElevenLabs eleven_multilingual_v2): character-based pricing
- STT (Azure Speech / Deepgram Nova-3): duration-based pricing

Pricing sources (verify periodically):
- Azure STT: https://azure.microsoft.com/en-us/pricing/details/cognitive-services/speech-services/
- ElevenLabs: https://elevenlabs.io/pricing/api
- Deepgram: https://deepgram.com/pricing
- OpenAI: https://openai.com/api/pricing/
"""

from dataclasses import dataclass, field
from typing import Optional
from loguru import logger


# ============================================================================
# PRICING CONSTANTS (EUR) - Update when plans/pricing change
# ============================================================================

# OpenAI GPT-4.1 pricing (per token, USD→EUR ~0.92)
GPT41_INPUT_PER_TOKEN = 2.0 / 1_000_000      # $2.00 per 1M input tokens
GPT41_OUTPUT_PER_TOKEN = 8.0 / 1_000_000     # $8.00 per 1M output tokens
GPT41_CACHED_INPUT_PER_TOKEN = 0.5 / 1_000_000  # $0.50 per 1M cached input

# ElevenLabs eleven_multilingual_v2 (Scale plan: $330/mo for 2M credits)
# Overage: $0.18 per 1,000 characters
# Effective rate using plan allocation: $330 / 2,000,000 = $0.000165/char
# Using overage rate as conservative estimate
ELEVENLABS_PER_CHARACTER = 0.18 / 1_000      # $0.00018 per character

# Azure Speech-to-Text (real-time standard)
AZURE_STT_PER_HOUR = 1.00                     # $1.00 per hour
AZURE_STT_PER_MINUTE = AZURE_STT_PER_HOUR / 60  # $0.0167 per minute
AZURE_STT_PER_SECOND = AZURE_STT_PER_MINUTE / 60

# Deepgram Nova-3 (streaming, pay-as-you-go)
DEEPGRAM_PER_MINUTE = 0.0077                  # $0.0077 per minute streaming
DEEPGRAM_PER_SECOND = DEEPGRAM_PER_MINUTE / 60

# Azure VM hosting cost (estimated, amortized per minute)
# Adjust based on actual VM size and monthly cost
AZURE_VM_MONTHLY_COST = float(__import__('os').getenv("AZURE_VM_MONTHLY_COST", "150"))
AZURE_VM_PER_MINUTE = AZURE_VM_MONTHLY_COST / (30 * 24 * 60)  # ~$0.0035/min


@dataclass
class CallCost:
    """Cost breakdown for a single call."""
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    tts_characters: int = 0
    stt_duration_seconds: float = 0.0
    call_duration_seconds: float = 0.0
    stt_provider: str = "azure"  # "azure" or "deepgram"

    # Computed costs (USD)
    llm_cost: float = 0.0
    tts_cost: float = 0.0
    stt_cost: float = 0.0
    hosting_cost: float = 0.0
    total_cost: float = 0.0
    cost_per_minute: float = 0.0

    def calculate(self) -> "CallCost":
        """Calculate all costs from usage metrics."""
        # LLM cost
        self.llm_cost = (
            self.llm_input_tokens * GPT41_INPUT_PER_TOKEN +
            self.llm_output_tokens * GPT41_OUTPUT_PER_TOKEN
        )

        # TTS cost
        self.tts_cost = self.tts_characters * ELEVENLABS_PER_CHARACTER

        # STT cost (use call duration as proxy if stt_duration not available)
        stt_seconds = self.stt_duration_seconds or self.call_duration_seconds
        if self.stt_provider == "azure":
            self.stt_cost = stt_seconds * AZURE_STT_PER_SECOND
        else:
            self.stt_cost = stt_seconds * DEEPGRAM_PER_SECOND

        # Hosting cost (proportional to call duration)
        call_minutes = self.call_duration_seconds / 60 if self.call_duration_seconds > 0 else 0
        self.hosting_cost = call_minutes * AZURE_VM_PER_MINUTE

        # Totals
        self.total_cost = self.llm_cost + self.tts_cost + self.stt_cost + self.hosting_cost
        self.cost_per_minute = self.total_cost / call_minutes if call_minutes > 0 else 0.0

        return self

    def to_dict(self) -> dict:
        """Convert to dict for storage/tracing."""
        return {
            "llm_input_tokens": self.llm_input_tokens,
            "llm_output_tokens": self.llm_output_tokens,
            "tts_characters": self.tts_characters,
            "stt_duration_seconds": round(self.stt_duration_seconds, 2),
            "call_duration_seconds": round(self.call_duration_seconds, 2),
            "stt_provider": self.stt_provider,
            "cost_llm_usd": round(self.llm_cost, 6),
            "cost_tts_usd": round(self.tts_cost, 6),
            "cost_stt_usd": round(self.stt_cost, 6),
            "cost_hosting_usd": round(self.hosting_cost, 6),
            "cost_total_usd": round(self.total_cost, 4),
            "cost_per_minute_usd": round(self.cost_per_minute, 4),
        }

    def summary(self) -> str:
        """Human-readable cost summary."""
        return (
            f"Call cost: ${self.total_cost:.4f} "
            f"(LLM: ${self.llm_cost:.4f}, TTS: ${self.tts_cost:.4f}, "
            f"STT: ${self.stt_cost:.4f}, Host: ${self.hosting_cost:.4f}) "
            f"| ${self.cost_per_minute:.4f}/min"
        )


def calculate_call_cost(
    llm_input_tokens: int = 0,
    llm_output_tokens: int = 0,
    tts_characters: int = 0,
    stt_duration_seconds: float = 0.0,
    call_duration_seconds: float = 0.0,
    stt_provider: str = "azure",
) -> CallCost:
    """Calculate cost for a call from usage metrics.

    Args:
        llm_input_tokens: Total LLM input tokens
        llm_output_tokens: Total LLM output tokens
        tts_characters: Total TTS characters synthesized
        stt_duration_seconds: Total STT processing time (or use call_duration as proxy)
        call_duration_seconds: Total call duration
        stt_provider: "azure" or "deepgram"

    Returns:
        CallCost with all costs calculated
    """
    cost = CallCost(
        llm_input_tokens=llm_input_tokens,
        llm_output_tokens=llm_output_tokens,
        tts_characters=tts_characters,
        stt_duration_seconds=stt_duration_seconds,
        call_duration_seconds=call_duration_seconds,
        stt_provider=stt_provider,
    ).calculate()

    logger.info(f"💰 {cost.summary()}")
    return cost

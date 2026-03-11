"""
IVR Queue Routing — Maps patient intent to Talkdesk queue codes.

Talkdesk IVR only routes TASTO 1.3.1 and 1.3.2 (diagnostic imaging) to our agent.
All other IVR paths go directly to human operator queues.

On escalation, the agent must send the correct queue code based on patient intent:
- Scenario A: intent matches IVR path → use original ivr_path
- Scenario B: intent changed mid-call → resolve correct queue from mapping

Booking queues (from IVR tree):
  1|1     Laboratorio (prelievi, analisi sangue)
  1|2|1   Poli — fondi/assicurazioni (visite, ecografie, ambulatoriali)
  1|2|2   Poli — privato
  1|3|1   Diagnostica immagini — fondi (RX, TAC, RMN, MOC, Mammografie)
  1|3|2   Diagnostica immagini — privato
  1|4     Medicina dello sport
  1|5     Disdetta/Spostare appuntamento

Info queues (unchanged from old system):
  2|2|1   Lab info
  2|2|2   Poli info
  2|2|3   Imaging info
  2|2|4   Sport info
  2|2|5   Other info
"""

from typing import List

# Valid booking queue codes
BOOKING_QUEUES = {
    "1|1",      # Laboratorio
    "1|2|1",    # Poli fondi
    "1|2|2",    # Poli privato
    "1|3|1",    # Diagnostica fondi
    "1|3|2",    # Diagnostica privato
    "1|4",      # Sport
    "1|5",      # Disdetta
}

# Valid info queue codes
INFO_QUEUES = {
    "2|2|1",    # Lab
    "2|2|2",    # Poli
    "2|2|3",    # Imaging
    "2|2|4",    # Sport
    "2|2|5",    # Other
}

ALL_VALID_QUEUES = BOOKING_QUEUES | INFO_QUEUES

# Default fallbacks
DEFAULT_INFO_QUEUE = "2|2|5"
DEFAULT_BOOKING_QUEUE = "1|3|2"  # Our agent's primary scope (diagnostic imaging private)


def is_valid_queue_code(code: str) -> bool:
    """Check if a queue code is valid."""
    return code in ALL_VALID_QUEUES


def resolve_fallback_queue(sector: str, ivr_path: str) -> str:
    """Fallback when LLM fails to produce a valid queue code.

    - If booking sector and ivr_path is valid booking queue → use ivr_path (Scenario A)
    - If info sector → default info queue
    - Otherwise → default booking queue
    """
    if sector == "info":
        return DEFAULT_INFO_QUEUE
    if ivr_path and ivr_path in BOOKING_QUEUES:
        return ivr_path
    return DEFAULT_BOOKING_QUEUE


def resolve_booking_queue_from_keywords(functions_called: List[str], ivr_path: str) -> str:
    """Keyword-based booking queue resolution (fallback when LLM fails)."""
    for func in functions_called:
        f = func.lower()
        # Sport check BEFORE visit (sport_visit contains "visit")
        if "sport" in f:
            return "1|4"
        if any(kw in f for kw in ["lab", "blood", "prelievo"]):
            return "1|1"
        if any(kw in f for kw in ["rmn", "rx", "tac", "moc", "mammograf", "radiolog", "imaging"]):
            # Preserve fondi/privato from original IVR selection if available
            if ivr_path in ("1|3|1", "1|3|2"):
                return ivr_path
            return "1|3|2"  # Default privato
        if any(kw in f for kw in ["visit", "ecograf", "ambulat", "price", "booking"]):
            return "1|2|2"  # Can't determine fondi vs privato → default privato
    # No keyword match → use original ivr_path or default
    if ivr_path and ivr_path in BOOKING_QUEUES:
        return ivr_path
    return DEFAULT_BOOKING_QUEUE


def resolve_info_digit_from_keywords(functions_called: List[str]) -> str:
    """Keyword-based info queue digit resolution (fallback)."""
    for func in functions_called:
        f = func.lower()
        # Sport check BEFORE visit (sport_visit contains "visit")
        if "sport" in f:
            return "4"
        if any(kw in f for kw in ["clinic", "blood", "lab", "prelievo"]):
            return "1"
        if any(kw in f for kw in ["rmn", "rx", "tac", "moc", "mammograf"]):
            return "3"
        if any(kw in f for kw in ["price", "visit", "booking", "exam"]):
            return "2"
    return "5"

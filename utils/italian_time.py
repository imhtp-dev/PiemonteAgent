"""
Italian Time Conversion Utility

Converts numeric time strings (HH:MM) to Italian spoken words.
Used to prevent TTS from double-speaking times (once as digits, once as words).

Examples:
    8:00  → "otto"
    9:30  → "nove e trenta"
    14:15 → "quattordici e quindici"
    22:45 → "ventidue e quarantacinque"
"""

# Italian number words (0-59)
ITALIAN_NUMBERS = {
    0: "zero", 1: "uno", 2: "due", 3: "tre", 4: "quattro",
    5: "cinque", 6: "sei", 7: "sette", 8: "otto", 9: "nove",
    10: "dieci", 11: "undici", 12: "dodici", 13: "tredici", 14: "quattordici",
    15: "quindici", 16: "sedici", 17: "diciassette", 18: "diciotto", 19: "diciannove",
    20: "venti", 21: "ventuno", 22: "ventidue", 23: "ventitré", 24: "ventiquattro",
    25: "venticinque", 26: "ventisei", 27: "ventisette", 28: "ventotto", 29: "ventinove",
    30: "trenta", 31: "trentuno", 32: "trentadue", 33: "trentatré", 34: "trentaquattro",
    35: "trentacinque", 36: "trentasei", 37: "trentasette", 38: "trentotto", 39: "trentanove",
    40: "quaranta", 41: "quarantuno", 42: "quarantadue", 43: "quarantatré", 44: "quarantaquattro",
    45: "quarantacinque", 46: "quarantasei", 47: "quarantasette", 48: "quarantotto", 49: "quarantanove",
    50: "cinquanta", 51: "cinquantuno", 52: "cinquantadue", 53: "cinquantatré", 54: "cinquantaquattro",
    55: "cinquantacinque", 56: "cinquantasei", 57: "cinquantasette", 58: "cinquantotto", 59: "cinquantanove",
}


def time_to_italian_words(time_str: str) -> str:
    """
    Convert a time string (HH:MM or H:MM) to Italian spoken words.

    Args:
        time_str: Time in "HH:MM" or "H:MM" format (e.g., "8:00", "14:30")

    Returns:
        Italian spoken form (e.g., "otto", "quattordici e trenta")

    Examples:
        >>> time_to_italian_words("8:00")
        'otto'
        >>> time_to_italian_words("9:30")
        'nove e trenta'
        >>> time_to_italian_words("14:15")
        'quattordici e quindici'
        >>> time_to_italian_words("22:45")
        'ventidue e quarantacinque'
        >>> time_to_italian_words("7:05")
        'sette e cinque'
    """
    try:
        # Parse hour and minutes
        parts = time_str.strip().split(":")
        if len(parts) != 2:
            return time_str  # Return original if invalid format

        hour = int(parts[0])
        minutes = int(parts[1])

        # Validate ranges
        if hour < 0 or hour > 23 or minutes < 0 or minutes > 59:
            return time_str  # Return original if out of range

        # Get Italian word for hour
        hour_word = ITALIAN_NUMBERS.get(hour, str(hour))

        # Handle minutes
        if minutes == 0:
            # Just the hour (e.g., "otto" for 8:00)
            return hour_word
        else:
            # Hour + "e" + minutes (e.g., "nove e trenta" for 9:30)
            minutes_word = ITALIAN_NUMBERS.get(minutes, str(minutes))
            return f"{hour_word} e {minutes_word}"

    except (ValueError, AttributeError):
        # Return original string if parsing fails
        return time_str


def format_slots_for_speech(slots: list) -> list:
    """
    Convert a list of slot times to Italian words for natural speech.

    Args:
        slots: List of dicts with 'time' key (e.g., [{'time': '8:00'}, {'time': '9:30'}])

    Returns:
        Same list with 'time_italian' added to each slot
    """
    for slot in slots:
        if 'time' in slot:
            slot['time_italian'] = time_to_italian_words(slot['time'])
    return slots


# Build reverse lookup: Italian word → number (for italian_words_to_time)
ITALIAN_WORDS_TO_NUMBERS = {word: num for num, word in ITALIAN_NUMBERS.items()}


def italian_words_to_time(italian_str: str) -> str:
    """
    Convert Italian spoken time words back to numeric format (H:MM or HH:MM).

    This is the reverse of time_to_italian_words().

    Args:
        italian_str: Italian time in spoken form (e.g., "quattordici e quaranta", "otto")

    Returns:
        Time in "H:MM" format (e.g., "14:40", "8:00"), or None if parsing fails

    Examples:
        >>> italian_words_to_time("quattordici e quaranta")
        '14:40'
        >>> italian_words_to_time("otto")
        '8:00'
        >>> italian_words_to_time("nove e trenta")
        '9:30'
        >>> italian_words_to_time("ventidue e quarantacinque")
        '22:45'
    """
    if not italian_str:
        return None

    try:
        italian_str = italian_str.strip().lower()

        # Check if already in numeric format (H:MM or HH:MM)
        if ':' in italian_str:
            return italian_str

        # Check for "hour e minutes" format
        if ' e ' in italian_str:
            parts = italian_str.split(' e ')
            if len(parts) == 2:
                hour_word = parts[0].strip()
                minutes_word = parts[1].strip()

                hour = ITALIAN_WORDS_TO_NUMBERS.get(hour_word)
                minutes = ITALIAN_WORDS_TO_NUMBERS.get(minutes_word)

                if hour is not None and minutes is not None:
                    return f"{hour}:{minutes:02d}"

        # Check for hour-only format (e.g., "otto" = 8:00)
        hour = ITALIAN_WORDS_TO_NUMBERS.get(italian_str)
        if hour is not None:
            return f"{hour}:00"

        return None

    except Exception:
        return None


# Italian month names
ITALIAN_MONTHS = {
    1: "gennaio", 2: "febbraio", 3: "marzo", 4: "aprile",
    5: "maggio", 6: "giugno", 7: "luglio", 8: "agosto",
    9: "settembre", 10: "ottobre", 11: "novembre", 12: "dicembre"
}

# Extended Italian numbers for years (1900-2100)
def _number_to_italian(n: int) -> str:
    """Convert a number (1-2100) to Italian words"""
    if n in ITALIAN_NUMBERS:
        return ITALIAN_NUMBERS[n]

    # Handle numbers 60-99
    if 60 <= n <= 99:
        tens = (n // 10) * 10
        ones = n % 10
        tens_words = {60: "sessanta", 70: "settanta", 80: "ottanta", 90: "novanta"}
        tens_word = tens_words.get(tens, str(tens))
        if ones == 0:
            return tens_word
        elif ones == 1 or ones == 8:
            # Drop final vowel: sessanta + uno = sessantuno, ottanta + otto = ottantotto
            return tens_word[:-1] + ITALIAN_NUMBERS[ones]
        else:
            return tens_word + ITALIAN_NUMBERS[ones]

    # Handle years
    if 1900 <= n <= 2100:
        if n == 2000:
            return "duemila"
        elif 2001 <= n <= 2099:
            remainder = n - 2000
            return "duemila" + _number_to_italian(remainder)
        elif 1900 <= n <= 1999:
            remainder = n - 1900
            if remainder == 0:
                return "millenovecento"
            return "millenovecento" + _number_to_italian(remainder)
        elif n == 2100:
            return "duemilacento"

    return str(n)


def date_to_italian_words(date_str: str) -> str:
    """
    Convert a date string (YYYY-MM-DD) to Italian spoken words.

    Args:
        date_str: Date in "YYYY-MM-DD" format (e.g., "2007-04-27")

    Returns:
        Italian spoken form (e.g., "ventisette aprile duemilaesette")

    Examples:
        >>> date_to_italian_words("2007-04-27")
        'ventisette aprile duemilaesette'
        >>> date_to_italian_words("1990-12-15")
        'quindici dicembre millenovecentonovanta'
        >>> date_to_italian_words("2000-01-01")
        'primo gennaio duemila'
    """
    try:
        parts = date_str.strip().split("-")
        if len(parts) != 3:
            return date_str

        year = int(parts[0])
        month = int(parts[1])
        day = int(parts[2])

        # Validate
        if not (1 <= month <= 12) or not (1 <= day <= 31):
            return date_str

        # Day: use "primo" for 1st, otherwise normal number
        if day == 1:
            day_word = "primo"
        else:
            day_word = _number_to_italian(day)

        # Month
        month_word = ITALIAN_MONTHS.get(month, str(month))

        # Year
        year_word = _number_to_italian(year)

        return f"{day_word} {month_word} {year_word}"

    except (ValueError, AttributeError):
        return date_str

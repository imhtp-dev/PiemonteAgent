"""Parse LLM date strings to YYYY-MM-DD format."""

from datetime import datetime


def parse_readable_date(date_str: str) -> str | None:
    """Parse '20 March 2026' or '12 March' → '2026-03-20'. Returns None on failure."""
    formats = ["%d %B %Y", "%d %B", "%Y-%m-%d", "%d/%m/%Y", "%B %d, %Y"]
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            if fmt == "%d %B":  # No year → use current
                dt = dt.replace(year=datetime.now().year)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None

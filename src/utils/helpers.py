"""Helper utilities."""

import hashlib
from datetime import datetime


def generate_id(prefix: str = "TASK") -> str:
    """Generate a unique task/artifact ID."""
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    short_hash = hashlib.md5(ts.encode()).hexdigest()[:6]
    return f"{prefix}-{ts}-{short_hash}"


def truncate(text: str, max_len: int = 500) -> str:
    """Truncate text to max length with ellipsis."""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."

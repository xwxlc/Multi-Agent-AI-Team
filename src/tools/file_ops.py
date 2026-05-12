"""File system operations for agents."""

import os
import json
from typing import Any


def safe_read(path: str) -> str:
    """Read file content safely."""
    try:
        with open(path) as f:
            return f.read()
    except FileNotFoundError:
        return f"[ERROR] File not found: {path}"
    except Exception as e:
        return f"[ERROR] {e}"


def safe_write(path: str, content: str) -> bool:
    """Write content to file, creating directories as needed."""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        return True
    except Exception:
        return False


def safe_write_json(path: str, data: Any) -> bool:
    """Write JSON data to file."""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def list_files(directory: str, pattern: str = "*") -> list[str]:
    """List files in directory matching pattern."""
    import fnmatch
    try:
        return [
            os.path.join(directory, f)
            for f in os.listdir(directory)
            if fnmatch.fnmatch(f, pattern)
        ]
    except FileNotFoundError:
        return []


def ensure_dir(path: str) -> str:
    """Ensure directory exists and return its path."""
    os.makedirs(path, exist_ok=True)
    return path

"""Safe code execution sandbox."""

import subprocess
import tempfile
import os
import sys
from typing import Optional


def run_python(code: str, timeout: int = 30) -> dict:
    """Execute Python code in a subprocess and return stdout/stderr."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(code)
        tmp_path = f.name

    try:
        result = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "stdout": "", "stderr": "执行超时", "returncode": -1}
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def run_shell(command: str, cwd: Optional[str] = None, timeout: int = 60) -> dict:
    """Execute a shell command safely."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd or os.getcwd(),
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"success": False, "stdout": "", "stderr": "命令执行超时", "returncode": -1}

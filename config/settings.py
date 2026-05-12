"""Global configuration — now superseded by config/agents.yaml.

保留用于向后兼容，实际运行时由 Orchestrator 从 agents.yaml 读取。
"""

import os
from dataclasses import dataclass
from typing import Any


@dataclass
class Config:
    model: str = os.getenv("LLM_MODEL", "deepseek-v4-pro")
    api_base: str = os.getenv("LLM_API_BASE", "https://api.deepseek.com/v1")
    api_key: str = os.getenv("LLM_API_KEY", "")
    work_dir: str = os.getenv("WORK_DIR", "workspace")
    max_retry: int = int(os.getenv("MAX_RETRY", "3"))
    temperature: float = float(os.getenv("LLM_TEMPERATURE", "0.3"))
    verbose: bool = os.getenv("VERBOSE", "false").lower() == "true"

    def to_llm_kwargs(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "api_base": self.api_base,
            "api_key": self.api_key,
        }


config = Config()

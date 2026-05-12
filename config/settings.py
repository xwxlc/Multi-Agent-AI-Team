"""Global configuration."""

import os
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Config:
    model: str = os.getenv("LLM_MODEL", "gpt-4o")
    api_base: str = os.getenv("LLM_API_BASE", "https://api.openai.com/v1")
    api_key: str = os.getenv("LLM_API_KEY", "")
    work_dir: str = os.getenv("WORK_DIR", "./project_output")
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

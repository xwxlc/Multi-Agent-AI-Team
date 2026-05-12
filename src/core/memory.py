"""Shared memory module — all agents read/write context through this."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional
import json
from datetime import datetime


class ArtifactType(Enum):
    PRD = "prd"
    TASK_LIST = "task_list"
    SOURCE_CODE = "source_code"
    TEST_CODE = "test_code"
    TEST_REPORT = "test_report"
    DOCUMENTATION = "documentation"
    FEEDBACK = "feedback"


@dataclass
class Artifact:
    type: ArtifactType
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    producer: str = ""

    def to_dict(self) -> dict:
        return {
            "type": self.type.value,
            "content": self.content,
            "metadata": self.metadata,
            "timestamp": self.timestamp,
            "producer": self.producer,
        }


class SharedMemory:
    """Thread-safe shared memory for inter-agent communication."""

    def __init__(self):
        self._artifacts: dict[ArtifactType, list[Artifact]] = {}
        self._context: dict[str, Any] = {}
        self._conversation_log: list[dict] = []

    def store(self, artifact: Artifact) -> None:
        self._artifacts.setdefault(artifact.type, []).append(artifact)
        self._log("store", artifact.producer, f"Stored {artifact.type.value}")

    def get_latest(self, artifact_type: ArtifactType) -> Optional[Artifact]:
        items = self._artifacts.get(artifact_type, [])
        return items[-1] if items else None

    def get_all(self, artifact_type: ArtifactType) -> list[Artifact]:
        return self._artifacts.get(artifact_type, [])

    def set_context(self, key: str, value: Any) -> None:
        self._context[key] = value

    def get_context(self, key: str, default: Any = None) -> Any:
        return self._context.get(key, default)

    def get_full_context(self) -> str:
        """Build a summary of all artifacts for prompt injection."""
        parts = []
        parts.append(f"## Context: {json.dumps(self._context, ensure_ascii=False)}")
        for atype, artifacts in self._artifacts.items():
            if artifacts:
                latest = artifacts[-1]
                parts.append(
                    f"## Latest {atype.value}:\n{latest.content[:2000]}"
                )
        return "\n\n".join(parts)

    def _log(self, action: str, agent: str, detail: str) -> None:
        self._conversation_log.append({
            "action": action,
            "agent": agent,
            "detail": detail,
            "timestamp": datetime.now().isoformat(),
        })

    def dump_log(self) -> str:
        return json.dumps(self._conversation_log, ensure_ascii=False, indent=2)

    def clear(self) -> None:
        self._artifacts.clear()
        self._context.clear()
        self._conversation_log.clear()

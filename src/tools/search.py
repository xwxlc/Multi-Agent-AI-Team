"""知识检索工具 — 可对接向量数据库或搜索引擎."""

import json
from typing import Any


class KnowledgeBase:
    """Simple in-memory knowledge base for agent reference.

    In production, replace with vector DB (Chroma/Weaviate/Pinecone).
    """

    def __init__(self):
        self._store: dict[str, str] = {}

    def add(self, key: str, content: str) -> None:
        self._store[key] = content

    def search(self, query: str) -> list[dict[str, str]]:
        """Simple keyword search (replace with embedding-based search in production)."""
        results = []
        query_lower = query.lower()
        for key, content in self._store.items():
            if query_lower in key.lower() or query_lower in content.lower():
                results.append({"key": key, "content": content[:500]})
        return results[:5]

    def load_from_file(self, path: str) -> None:
        try:
            with open(path) as f:
                data = json.load(f)
                if isinstance(data, dict):
                    self._store.update(data)
        except Exception:
            pass

"""LLM Router — 多模型多 Provider 统一调度层。

职责:
  1. 读取 agents.yaml 配置
  2. 根据 agent role 解析 provider / model
  3. 适配三种 API 格式 → 统一 chat() 接口
     - OpenAI 兼容 (OpenAI / DeepSeek)
     - Anthropic Messages API (Claude)
  4. 管理 httpx 客户端连接池

用法:
    router = LLMRouter("config/agents.yaml")
    reply = await router.chat(
        role="analyst",
        messages=[{"role": "user", "content": "分析需求"}],
        system="你是一个需求分析师",
    )
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any, Optional

import httpx
import yaml

logger = logging.getLogger("router")

# ──────────────────────────────────────────────
#  config
# ──────────────────────────────────────────────


class RouterConfig:
    """YAML 配置的 Python 表示。"""

    def __init__(self, path: str):
        with open(path) as f:
            raw = yaml.safe_load(f)

        self.providers: dict[str, dict] = raw.get("providers", {})
        self.agents: dict[str, dict] = raw.get("agents", {})
        self.pipeline: dict = raw.get("pipeline", {})

    def agent(self, role: str) -> dict:
        cfg = self.agents.get(role)
        if cfg is None:
            raise KeyError(f"未知 agent role: {role}，可用: {list(self.agents)}")
        return cfg

    def provider(self, name: str) -> dict:
        cfg = self.providers.get(name)
        if cfg is None:
            raise KeyError(f"未知 provider: {name}，可用: {list(self.providers)}")
        return cfg

    def resolve(self, role: str) -> tuple[dict, dict]:
        """(agent_cfg, provider_cfg)"""
        ac = self.agent(role)
        pc = self.provider(ac["provider"])
        return ac, pc


# ──────────────────────────────────────────────
#  LLM Router
# ──────────────────────────────────────────────


class LLMRouter:
    """统一 LLM 路由 — 根据 agent role 自动选择 provider + model + API 格式。"""

    def __init__(self, config_path: str = "config/agents.yaml"):
        self.config = RouterConfig(config_path)
        self._clients: dict[str, httpx.AsyncClient] = {}

    # ── public API ─────────────────────────────

    async def chat(
        self,
        *,
        role: str,
        messages: list[dict[str, str]],
        system: Optional[str] = None,
        temperature: Optional[float] = None,
    ) -> str:
        """统一聊天接口 — 根据 role 路由到对应的 provider/model。

        Args:
            role:   agent 角色名（analyst / developer / tester / writer）
            messages: 对话历史 [{"role": "user/assistant", "content": "..."}]
            system:  系统提示词（可覆盖 agent 默认值）
            temperature: 温度参数（可覆盖 agent 默认值）

        Returns:
            模型回复文本。
        """
        ac, pc = self.config.resolve(role)
        api_style = pc["api_style"]
        model = ac["model"]
        temp = temperature if temperature is not None else ac.get("temperature", 0.3)
        max_tok = ac.get("max_tokens", 4096)

        self._log(role, ac, pc)

        if api_style == "anthropic":
            return await self._chat_anthropic(
                provider=pc, model=model,
                messages=messages, system=system or "",
                temperature=temp, max_tokens=max_tok,
            )
        elif api_style == "openai":
            return await self._chat_openai(
                provider=pc, model=model,
                messages=messages, system=system or "",
                temperature=temp, max_tokens=max_tok,
            )
        else:
            raise ValueError(f"不支持的 api_style: {api_style}")

    def get_agent_config(self, role: str) -> dict:
        """返回 agent 在 yaml 中的完整配置（给 orchestrator 用）。"""
        return dict(self.config.agent(role))

    @property
    def pipeline_config(self) -> dict:
        return dict(self.config.pipeline)

    # ── provider implementations ───────────────

    async def _chat_openai(
        self,
        provider: dict,
        model: str,
        messages: list[dict],
        system: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        """OpenAI 兼容 API (OpenAI / DeepSeek)。"""
        client = await self._client(provider)
        url = f"{provider['api_base']}/chat/completions"

        # system prompt 作为 messages 第一条
        full_messages = [{"role": "system", "content": system}] + messages

        payload: dict[str, Any] = {
            "model": model,
            "messages": full_messages,
            "temperature": temperature,
        }
        # max_tokens only for non-reasoning models
        if "o1" not in model and "o3" not in model:
            payload["max_tokens"] = max_tokens

        resp = await client.post(url, json=payload, headers=self._headers(provider))
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    async def _chat_anthropic(
        self,
        provider: dict,
        model: str,
        messages: list[dict],
        system: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        """Anthropic Messages API。

        关键差异: system 是顶层参数，不在 messages 中。
        """
        client = await self._client(provider)
        url = f"{provider['api_base']}/messages"

        headers = self._headers(provider)
        headers["anthropic-version"] = provider.get("default_version", "2023-06-01")

        # Anthropic 的 messages 中不放 system
        anthropic_messages = [
            {"role": m["role"], "content": m["content"]}
            for m in messages
            if m["role"] in ("user", "assistant")
        ]

        payload: dict[str, Any] = {
            "model": model,
            "messages": anthropic_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system:
            payload["system"] = system

        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()

        # content 是 [{type: "text", text: "..."}, ...]
        blocks = data.get("content", [])
        text_blocks = [b["text"] for b in blocks if b.get("type") == "text"]
        return "\n".join(text_blocks)

    # ── client management ──────────────────────

    async def _client(self, provider: dict) -> httpx.AsyncClient:
        key = f"{provider['api_base']}:{provider['api_style']}"
        if key not in self._clients:
            self._clients[key] = httpx.AsyncClient(
                timeout=httpx.Timeout(provider.get("timeout", 120)),
            )
        return self._clients[key]

    def _headers(self, provider: dict) -> dict[str, str]:
        # yaml 中的 key 优先，否则从环境变量读取
        api_key = provider.get("key") or os.getenv(provider.get("env_key", ""), "")
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    async def close(self) -> None:
        for c in self._clients.values():
            await c.aclose()
        self._clients.clear()

    # ── logging ────────────────────────────────

    def _log(self, role: str, agent_cfg: dict, provider_cfg: dict) -> None:
        logger.debug(
            "[router] %s → %s/%s (%s)",
            role, provider_cfg["name"], agent_cfg["model"], provider_cfg["api_style"],
        )

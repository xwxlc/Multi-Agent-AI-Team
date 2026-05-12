"""Base agent abstract class — all role agents inherit from this.

Provides:
  - name / role    from agents.yaml
  - router         shared LLMRouter (multi-model dispatch)
  - run(task)      public async entry — wraps execute() with retry + log + timing
  - think(msg)     → router.chat(role=..., messages=..., system=...)
  - log()          convenience logging with agent identity prefix
  - retry()        decorator / inline utility for exponential-backoff retry
"""

from __future__ import annotations

import asyncio
import functools
import logging
import os
import time
import uuid
from abc import ABC, abstractmethod
from typing import Any, Callable, Coroutine, Optional, TypeVar

from .memory import SharedMemory, Artifact, ArtifactType

T = TypeVar("T")


def _short_id() -> str:
    return uuid.uuid4().hex[:6]


class BaseAgent(ABC):
    """Abstract base agent — delegates LLM calls to LLMRouter.

    Subclass contract
    ─────────────────
      1. Set ``role`` class attribute (str, matches agents.yaml key).
      2. Implement ``system_prompt() -> str``.
      3. Implement ``execute(task) -> Artifact``  (async).

    Usage
    ─────
      agent = MyAgent(memory, router, agent_cfg)
      artifact = await agent.run("分析这段需求并拆解任务")
    """

    role: str = "base"

    @abstractmethod
    def system_prompt(self) -> str:
        ...

    @abstractmethod
    async def execute(self, task: str) -> Artifact:
        ...

    # ── init ──────────────────────────────────

    def __init__(
        self,
        memory: SharedMemory,
        router: Any = None,                     # LLMRouter (可选: 测试 mock 不调 LLM)
        agent_cfg: Optional[dict[str, Any]] = None,
        **kwargs,                               # 向后兼容旧的 model/api_base/api_key 参数
    ):
        self.memory = memory
        self.router = router

        cfg = agent_cfg or {}

        # 从 yaml 配置注入（优先），否则从 kwargs 取（向后兼容）
        self.name: str = cfg.get("name") or kwargs.get("name") or f"{self.role}-{_short_id()}"
        self.model: str = cfg.get("model") or kwargs.get("model") or "deepseek-v4-pro"
        self.provider: str = cfg.get("provider") or kwargs.get("provider") or "deepseek"
        self.max_retries: int = cfg.get("max_retries") or kwargs.get("max_retries", 3)
        self.retry_backoff: float = cfg.get("retry_backoff") or kwargs.get("retry_backoff", 2.0)

        # 内部状态
        self._history: list[dict[str, str]] = []
        self._logger = logging.getLogger(self.name)

    # ── run — public entry point ──────────────

    async def run(self, task: str) -> Artifact:
        """带重试 + 日志 + 计时的统一入口。"""
        self.log(logging.INFO, "▶ 开始  |  %s 🤖 %s/%s",
                 task[:80], self.provider, self.model)

        t_start = time.monotonic()
        last_exc: Optional[Exception] = None

        for attempt in range(1, self.max_retries + 1):
            try:
                result = await self.execute(task)
                elapsed = time.monotonic() - t_start
                self.log(logging.INFO, "✓ 完成  |  %0.1fs  (第 %d 次)",
                         elapsed, attempt)
                return result
            except Exception as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    delay = self.retry_backoff ** (attempt - 1)
                    self.log(
                        logging.WARNING,
                        "✗ 失败 第 %d/%d 次: %s  |  %0.1fs 后重试",
                        attempt, self.max_retries, exc, delay,
                    )
                    await asyncio.sleep(delay)

        elapsed = time.monotonic() - t_start
        self.log(logging.ERROR, "✗ 最终失败 (%0.1fs, %d 次)", elapsed, self.max_retries)
        raise last_exc  # type: ignore[misc]

    # ── log ────────────────────────────────────

    def log(self, level: int, msg: str, *args: Any) -> None:
        self._logger.log(level, f"[{self.name}] {msg}", *args)

    # ── retry — decorator / utility ───────────

    @staticmethod
    def retry(
        max_attempts: int = 3,
        backoff: float = 2.0,
        swallow: tuple[type[Exception], ...] = (),
    ) -> Callable:
        """装饰器 — 对任意 async 方法加指数退避重试。"""
        def decorator(
            func: Callable[..., Coroutine[Any, Any, T]],
        ) -> Callable[..., Coroutine[Any, Any, T]]:
            @functools.wraps(func)
            async def wrapper(self: "BaseAgent", *args: Any, **kwargs: Any) -> T:
                last_exc: Optional[Exception] = None
                for attempt in range(1, max_attempts + 1):
                    try:
                        return await func(self, *args, **kwargs)
                    except Exception as exc:
                        last_exc = exc
                        if attempt < max_attempts:
                            delay = backoff ** (attempt - 1)
                            if not isinstance(exc, swallow):
                                self.log(
                                    logging.WARNING,
                                    "%s 失败 (%d/%d): %s  |  %0.1fs 后重试",
                                    func.__name__, attempt, max_attempts, exc, delay,
                                )
                            await asyncio.sleep(delay)
                raise last_exc  # type: ignore[misc]
            return wrapper
        return decorator

    async def _retry(
        self,
        coro_factory: Callable[[], Coroutine[Any, Any, T]],
        max_attempts: Optional[int] = None,
        backoff: Optional[float] = None,
        label: str = "retry",
    ) -> T:
        """行内重试任意协程。"""
        attempts = max_attempts or self.max_retries
        delay_base = backoff or self.retry_backoff
        last_exc: Optional[Exception] = None

        for attempt in range(1, attempts + 1):
            try:
                return await coro_factory()
            except Exception as exc:
                last_exc = exc
                if attempt < attempts:
                    delay = delay_base ** (attempt - 1)
                    self.log(
                        logging.WARNING,
                        "%s 失败 (%d/%d): %s  |  %0.1fs 后重试",
                        label, attempt, attempts, exc, delay,
                    )
                    await asyncio.sleep(delay)

        raise last_exc  # type: ignore[misc]

    # ── LLM ────────────────────────────────────

    async def think(self, user_message: str) -> str:
        """调用 LLM — 通过 router 自动路由到对应 provider/model。

        如果 router 为 None（如测试 mock），返回占位提示。
        """
        if self.router is None:
            self.log(logging.WARNING, "think() 被调用但未配置 router，返回占位文本")
            return f"[NO_ROUTER] {user_message[:100]}"

        system = self._build_full_system_prompt()
        messages: list[dict[str, str]] = list(self._history[-10:])
        messages.append({"role": "user", "content": user_message})

        reply = await self.router.chat(
            role=self.role,
            messages=messages,
            system=system,
        )

        self._history.append({"role": "user", "content": user_message})
        self._history.append({"role": "assistant", "content": reply})
        return reply

    # ── memory helpers ─────────────────────────

    def _create_artifact(
        self,
        artifact_type: ArtifactType,
        content: str,
        metadata: Optional[dict] = None,
    ) -> Artifact:
        artifact = Artifact(
            type=artifact_type,
            content=content,
            metadata=metadata or {},
            producer=self.name,
        )
        self.memory.store(artifact)
        return artifact

    def _build_full_system_prompt(self) -> str:
        base = self.system_prompt()
        ctx = self.memory.get_full_context()
        return f"{base}\n\n## 当前项目上下文:\n{ctx}"

    # ── file I/O ───────────────────────────────

    def read_file(self, path: str) -> str:
        try:
            with open(path) as f:
                return f.read()
        except Exception as e:
            return f"[读取失败: {e}]"

    def write_file(self, path: str, content: str) -> bool:
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w") as f:
                f.write(content)
            return True
        except Exception:
            return False

    # ── lifecycle ──────────────────────────────

    async def close(self) -> None:
        """Agent 自身无需关闭，连接由 router 统一管理。"""
        # router 由 orchestrator 统一 close，避免重复关闭
        pass

    # ── dunder ─────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__}"
            f" name={self.name!r}"
            f" role={self.role!r}"
            f" model={self.model!r}"
            f" provider={self.provider!r}>"
        )

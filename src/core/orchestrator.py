"""Orchestrator — 多 Agent 多模型协调器。

从 agents.yaml 读取每个 agent 的 provider/model 配置，
通过 LLMRouter 统一调度，按管道顺序执行:
  ANALYST → DEVELOPER → TESTER ⇄ DEVELOPER → WRITER
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Optional, Any

from .memory import SharedMemory, ArtifactType, Artifact
from .task_queue import TaskQueue, Task, TaskStatus
from .router import LLMRouter

logger = logging.getLogger("orchestrator")


class Orchestrator:
    """多模型编排器。

    用法::

        orchestrator = Orchestrator("config/agents.yaml")
        summary = await orchestrator.run("开发一个 React 待办事项应用")
    """

    def __init__(
        self,
        config_path: str = "config/agents.yaml",
        memory: Optional[SharedMemory] = None,
    ):
        self.router = LLMRouter(config_path)
        self.memory = memory or SharedMemory()
        self.task_queue = TaskQueue()

        # 管道参数
        pipeline = self.router.pipeline_config
        self.max_feedback_rounds = pipeline.get("max_feedback_rounds", 3)
        self.workspace = pipeline.get("workspace", "workspace")

        # 创建 4 个 Agent — 每个绑定自己的 model/provider
        self.agents = self._build_agents()

        self._hooks: dict[str, list[Callable]] = {}

    def _build_agents(self) -> dict[str, Any]:
        """根据 agents.yaml 创建 Agent 实例。"""
        from ..agents.analyst import AnalystAgent
        from ..agents.developer import DeveloperAgent
        from ..agents.tester import TesterAgent
        from ..agents.documenter import DocumenterAgent

        _role_to_cls = {
            "analyst":    AnalystAgent,
            "developer":  DeveloperAgent,
            "tester":     TesterAgent,
            "writer":     DocumenterAgent,
        }

        agents: dict[str, Any] = {}
        for role, cls in _role_to_cls.items():
            cfg = self.router.get_agent_config(role)
            agents[role] = cls(
                self.memory,
                router=self.router,
                agent_cfg=cfg,
                workspace=self.workspace,
            )
            logger.info(
                "🤖 %-12s → %s/%-30s (retry=%d)",
                role, cfg["provider"], cfg["model"], cfg.get("max_retries", 2),
            )

        # Wire analyst → task queue
        agents["analyst"].set_task_queue(self.task_queue)
        return agents

    # ── hooks ──────────────────────────────────

    def hook(self, event: str, callback: Callable) -> None:
        self._hooks.setdefault(event, []).append(callback)

    def _fire(self, event: str, **kwargs: Any) -> None:
        for cb in self._hooks.get(event, []):
            cb(**kwargs)

    # ── main entry ─────────────────────────────

    async def run(self, requirement: str, work_dir: str = "") -> dict:
        """执行完整异步管道。"""
        t0 = time.monotonic()
        wd = work_dir or self.workspace
        self.memory.clear()
        self.memory.set_context("requirement", requirement)
        self.memory.set_context("work_dir", wd)

        logger.info("=" * 60)
        logger.info("🚀 管道启动  |  workspace = %s", wd)
        logger.info("需求: %s", requirement[:120])
        logger.info("=" * 60)

        summary: dict = {}

        # ── Stage 1: Analyst ────────────────────
        summary["analyst"] = await self._run_analyst(requirement)

        # ── Stage 2+3: Developer ↔ Tester ──────
        for round_num in range(1, self.max_feedback_rounds + 1):
            logger.info("─" * 40)
            logger.info("🔁 开发↔测试 第 %d/%d 轮", round_num, self.max_feedback_rounds)

            dev_result = await self._run_developer()
            summary["developer"] = dev_result
            test_result = await self._run_tester()
            summary["tester"] = test_result

            if test_result.get("passed", False):
                summary["test_status"] = "PASSED"
                logger.info("✅ 测试全部通过")
                break
            else:
                logger.warning("❌ 测试未通过 → 生成修复反馈...")
                self._store_feedback()
                self.task_queue.reset_for_retry("developer")
                logger.info("   ↳ 已重置开发任务队列")
                summary["test_status"] = f"FAILED (round {round_num})"
        else:
            logger.warning("⚠️  达到最大反馈轮数 %d", self.max_feedback_rounds)
            summary["test_status"] = f"FAILED (max {self.max_feedback_rounds} rounds)"

        # ── Stage 4: Writer ─────────────────────
        summary["writer"] = await self._run_writer()

        # ── Cleanup ─────────────────────────────
        await self.router.close()

        elapsed = time.monotonic() - t0
        summary["elapsed_sec"] = round(elapsed, 1)

        logger.info("=" * 60)
        logger.info("🏁 管道完成  |  耗时 %0.1fs", elapsed)
        self._print_summary(summary)
        return summary

    # ── stage runners ──────────────────────────

    async def _run_analyst(self, requirement: str) -> dict:
        agent = self.agents["analyst"]
        logger.info("📊 [ANALYST] %s (🤖 %s)", agent.name, agent.model)
        self._fire("on_stage_start", stage="analyst")

        try:
            await agent.run(requirement)
        except Exception as exc:
            logger.error("❌ [ANALYST] 失败: %s", exc)
            return {"status": "failed", "error": str(exc)}

        n_tasks = len(self.task_queue._tasks)
        logger.info("📊 [ANALYST] ✓ %d 个任务已入队", n_tasks)
        for tid, t in self.task_queue._tasks.items():
            logger.info("   ↳ %s [%s] %s", tid, t.priority.name, t.title)

        self._fire("on_stage_end", stage="analyst")
        return {"status": "ok", "tasks": n_tasks}

    async def _run_developer(self) -> dict:
        agent = self.agents["developer"]
        completed, failed = 0, 0
        files: list[str] = []

        logger.info("💻 [DEVELOPER] %s (🤖 %s)", agent.name, agent.model)
        self._fire("on_stage_start", stage="developer")

        while True:
            task = self.task_queue.get_next(role_filter="developer")
            if task is None:
                break

            self.task_queue.mark_in_progress(task.id)
            logger.info("   🔨 %s: %s", task.id, task.title)

            try:
                artifact = await agent.run(task.description)
                self.task_queue.mark_completed(task.id, task.description)
                completed += 1
                for f in (artifact.metadata or {}).get("files", []):
                    files.append(f)
                    logger.info("      📄 %s", f)
            except Exception as exc:
                self.task_queue.mark_failed(task.id, str(exc))
                failed += 1
                logger.error("   ❌ %s 失败: %s", task.id, exc)
                self._fire("on_error", task_id=task.id, error=str(exc))

        logger.info("💻 [DEVELOPER] ✓ %d  ✗ %d", completed, failed)
        self._fire("on_stage_end", stage="developer")
        return {"completed": completed, "failed": failed, "files": files}

    async def _run_tester(self) -> dict:
        agent = self.agents["tester"]
        logger.info("🧪 [TESTER] %s (🤖 %s)", agent.name, agent.model)
        self._fire("on_stage_start", stage="tester")

        try:
            await agent.run("")
        except Exception as exc:
            logger.error("❌ [TESTER] 失败: %s", exc)
            return {"status": "failed", "error": str(exc), "passed": False}

        passed = agent.all_passed
        if passed:
            logger.info("🧪 [TESTER] ✅ 全部通过")
        else:
            logger.warning("🧪 [TESTER] ❌ 存在失败用例")

        self._fire("on_stage_end", stage="tester")
        return {"status": "ok", "passed": passed}

    async def _run_writer(self) -> dict:
        agent = self.agents["writer"]
        logger.info("📝 [WRITER] %s (🤖 %s)", agent.name, agent.model)
        self._fire("on_stage_start", stage="writer")

        try:
            artifact = await agent.run("")
        except Exception as exc:
            logger.error("❌ [WRITER] 失败: %s", exc)
            return {"status": "failed", "error": str(exc)}

        files = (artifact.metadata or {}).get("files", [])
        for f in files:
            logger.info("   📄 %s", f)
        logger.info("📝 [WRITER] ✓ %d 个文档", len(files))
        self._fire("on_stage_end", stage="writer")
        return {"status": "ok", "files": files}

    # ── helpers ────────────────────────────────

    def _store_feedback(self) -> None:
        latest = self.memory.get_latest(ArtifactType.TEST_REPORT)
        text = f"测试未通过，请根据以下报告修复代码:\n{latest.content if latest else '无详情'}"
        self.memory.store(Artifact(
            type=ArtifactType.FEEDBACK,
            content=text,
            producer=self.agents["tester"].name,
        ))

    def _print_summary(self, summary: dict) -> None:
        dev = summary.get("developer", {})
        logger.info("─" * 40)
        logger.info("📋 执行摘要")
        logger.info("  需求分析: %s", summary.get("analyst", {}).get("status", "?"))
        logger.info("  开发任务: ✓ %d  /  ✗ %d", dev.get("completed", 0), dev.get("failed", 0))
        logger.info("  测试结果: %s", summary.get("test_status", "?"))
        logger.info("  文档生成: %s", summary.get("writer", {}).get("status", "?"))
        logger.info("  总耗时:   %ss", summary.get("elapsed_sec", "?"))

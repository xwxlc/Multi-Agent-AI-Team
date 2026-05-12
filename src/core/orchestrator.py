"""Orchestrator — multi-agent multi-model coordinator with timeout relay.

Reads agents.yaml per-agent config, dispatches via LLMRouter, runs pipeline:
  ANALYST → DEVELOPER → TESTER ⇄ DEVELOPER → WRITER

With TASKS.md / STATUS.md / checkpoint file-sync layer for observability
and crash recovery via --resume.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Optional, Any

from .memory import SharedMemory, ArtifactType, Artifact
from .task_queue import TaskQueue, Task, TaskStatus
from .router import LLMRouter
from .sync import TasksMd, StatusMd, Checkpoint, Lock

logger = logging.getLogger("orchestrator")


class Orchestrator:
    """Multi-model orchestrator with timeout relay and markdown task board."""

    def __init__(
        self,
        config_path: str = "config/agents.yaml",
        memory: Optional[SharedMemory] = None,
    ):
        self.router = LLMRouter(config_path)
        self.memory = memory or SharedMemory()
        self.task_queue = TaskQueue()

        pipeline = self.router.pipeline_config
        self.max_feedback_rounds = pipeline.get("max_feedback_rounds", 3)
        self.workspace = pipeline.get("workspace", "workspace")

        self.agents = self._build_agents()
        self._hooks: dict[str, list[Callable]] = {}
        self._task_counter = 0

    def _build_agents(self) -> dict[str, Any]:
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
                "🤖 %-12s → %s/%-30s (retry=%d timeout=%s)",
                role, cfg["provider"], cfg["model"],
                cfg.get("max_retries", 2), cfg.get("timeout", "∞"),
            )

        agents["analyst"].set_task_queue(self.task_queue)
        return agents

    def hook(self, event: str, callback: Callable) -> None:
        self._hooks.setdefault(event, []).append(callback)

    def _fire(self, event: str, **kwargs: Any) -> None:
        for cb in self._hooks.get(event, []):
            cb(**kwargs)

    def _next_id(self) -> str:
        self._task_counter += 1
        return f"task{self._task_counter:03d}"

    def _agent_timeout(self, role: str) -> Optional[int]:
        cfg = self.router.get_agent_config(role)
        t = cfg.get("timeout")
        return int(t) if t is not None else None

    async def run(self, requirement: str, work_dir: str = "",
                  resume: bool = False) -> dict:
        """Execute full async pipeline with timeout relay."""
        t0 = time.monotonic()
        wd = work_dir or self.workspace

        if not resume and Lock.is_locked():
            logger.warning("TASKS.md.lock exists — auto-resuming from checkpoint...")
            resume = True

        if not resume:
            Lock.acquire()

        self.memory.clear()
        self.memory.set_context("requirement", requirement)
        self.memory.set_context("work_dir", wd)

        analyst_id = self._next_id()
        self.memory.set_context("analyst_task_id", analyst_id)

        if resume:
            count = TasksMd.rebuild_queue("TASKS.md", self.task_queue)
            logger.info("Resumed %d tasks from TASKS.md", count)
            for cp in Checkpoint.list_all():
                logger.info("   checkpoint: %s/%s — %s",
                            cp.get("agent"), cp.get("task_id"), cp.get("status", "?"))
        else:
            TasksMd.init(initial_task={
                "task": analyst_id, "role": "analyst",
                "title": requirement[:60], "priority": "HIGH",
            })
            self._write_status("analyst", "分析需求", "0/4", t0)

        logger.info("=" * 60)
        logger.info("PIPELINE START  |  workspace = %s", wd)
        logger.info("Requirement: %s", requirement[:120])
        logger.info("=" * 60)

        summary: dict = {}

        try:
            # ── Stage 1: Analyst ────────────────────
            summary["analyst"] = await self._run_analyst(requirement, analyst_id)
            self._write_status("developer", "等待开发", "1/4", t0)

            # ── Stage 2+3: Developer ↔ Tester ──────
            for round_num in range(1, self.max_feedback_rounds + 1):
                logger.info("─" * 40)
                logger.info("Round %d/%d  Developer ↔ Tester",
                            round_num, self.max_feedback_rounds)

                self._write_status("developer", "开发中", "2/4", t0)
                dev_result = await self._run_developer()
                summary["developer"] = dev_result

                self._write_status("tester", "测试中", "2/4", t0)
                test_result = await self._run_tester()
                summary["tester"] = test_result

                if test_result.get("passed", False):
                    summary["test_status"] = "PASSED"
                    logger.info("All tests passed ✓")
                    break
                else:
                    logger.warning("Tests failed → generating fix feedback...")
                    self._store_feedback()
                    self.task_queue.reset_for_retry("developer")
                    logger.info("   Developer task queue reset for retry")
                    summary["test_status"] = f"FAILED (round {round_num})"
            else:
                logger.warning("Max feedback rounds (%d) reached",
                               self.max_feedback_rounds)
                summary["test_status"] = f"FAILED (max {self.max_feedback_rounds} rounds)"

            # ── Stage 4: Writer ─────────────────────
            self._write_status("writer", "文档生成中", "3/4", t0)
            summary["writer"] = await self._run_writer()

            # ── Cleanup ─────────────────────────────
            await self.router.close()

            elapsed = time.monotonic() - t0
            summary["elapsed_sec"] = round(elapsed, 1)

            self._write_status("done", "完成", "4/4", t0,
                               tasks_summary=f"✓ {len(self.task_queue._completed)} completed")
            logger.info("=" * 60)
            logger.info("PIPELINE DONE  |  %0.1fs", elapsed)
            self._print_summary(summary)
            return summary
        finally:
            Lock.release()

    # ── stage runners ──────────────────────────

    async def _run_analyst(self, requirement: str, task_id: str) -> dict:
        agent = self.agents["analyst"]
        timeout = self._agent_timeout("analyst")
        logger.info("[ANALYST] %s (model: %s, timeout: %s)",
                     agent.name, agent.model, timeout)
        self._fire("on_stage_start", stage="analyst")

        TasksMd.move_task("TASKS.md", task_id, "todo", "doing")
        agent._current_task_id = task_id

        try:
            if timeout:
                await asyncio.wait_for(agent.run(requirement), timeout=timeout)
            else:
                await agent.run(requirement)
        except asyncio.TimeoutError:
            logger.error("[ANALYST] Timeout (%ds) → relay", timeout)
            TasksMd.move_task("TASKS.md", task_id, "doing", "failed",
                              error=f"timeout:{timeout}s")
            Checkpoint.save("analyst", task_id,
                            {"status": "timeout", "requirement": requirement[:200]})
            return {"status": "timeout", "error": f"Timeout after {timeout}s"}
        except Exception as exc:
            logger.error("[ANALYST] Failed: %s", exc)
            TasksMd.move_task("TASKS.md", task_id, "doing", "failed",
                              error=str(exc)[:80])
            return {"status": "failed", "error": str(exc)}

        n_tasks = len(self.task_queue._tasks)
        logger.info("[ANALYST] ✓ %d tasks enqueued", n_tasks)
        for tid, t in self.task_queue._tasks.items():
            logger.info("   %s [%s] %s", tid, t.priority.name, t.title)

        TasksMd.move_task("TASKS.md", task_id, "doing", "done")

        # Sync developer tasks from queue to TASKS.md Todo
        for tid, t in self.task_queue._tasks.items():
            if t.assigned_to == "developer" and t.status == TaskStatus.PENDING:
                task_dict = {
                    "task": tid, "role": "developer",
                    "title": t.title,
                    "priority": t.priority.name if hasattr(t.priority, 'name') else "MEDIUM",
                }
                deps = getattr(t, 'dependencies', [])
                if deps:
                    task_dict["depends_on"] = ",".join(deps)
                TasksMd.add_task("TASKS.md", task_dict, "todo")

        self._fire("on_stage_end", stage="analyst")
        return {"status": "ok", "tasks": n_tasks}

    async def _run_developer(self) -> dict:
        agent = self.agents["developer"]
        timeout = self._agent_timeout("developer")
        completed, failed, timed_out = 0, 0, 0
        files: list[str] = []

        logger.info("[DEVELOPER] %s (model: %s, timeout: %s)",
                     agent.name, agent.model, timeout)
        self._fire("on_stage_start", stage="developer")

        stage_start = time.monotonic()

        while True:
            task = self.task_queue.get_next(role_filter="developer")
            if task is None:
                break

            # Stage-level timeout check
            if timeout and (time.monotonic() - stage_start) > timeout:
                logger.warning("[DEVELOPER] Stage timeout (%ds) → relay", timeout)
                Checkpoint.save("developer", task.id, {
                    "status": "timeout",
                    "files_so_far": files,
                    "pending_tasks": [
                        tid for tid, t in self.task_queue._tasks.items()
                        if t.assigned_to == "developer" and t.status == TaskStatus.PENDING
                    ],
                })
                timed_out += 1
                break

            self.task_queue.mark_in_progress(task.id)
            TasksMd.move_task("TASKS.md", task.id, "todo", "doing")
            agent._current_task_id = task.id
            logger.info("   %s: %s", task.id, task.title)

            try:
                artifact = await agent.run(task.description)
                self.task_queue.mark_completed(task.id, task.description)
                TasksMd.move_task("TASKS.md", task.id, "doing", "done")
                completed += 1
                Checkpoint.clear("developer", task.id)
                for f in (artifact.metadata or {}).get("files", []):
                    files.append(f)
                    logger.info("      %s", f)
            except asyncio.TimeoutError:
                logger.error("   %s timeout", task.id)
                self.task_queue._tasks[task.id].status = TaskStatus.TIMEOUT
                TasksMd.move_task("TASKS.md", task.id, "doing", "failed",
                                  error="timeout")
                Checkpoint.save("developer", task.id, {
                    "status": "timeout", "files_so_far": files,
                })
                timed_out += 1
            except Exception as exc:
                self.task_queue.mark_failed(task.id, str(exc))
                TasksMd.move_task("TASKS.md", task.id, "doing", "failed",
                                  error=str(exc)[:80])
                failed += 1
                logger.error("   %s FAIL: %s", task.id, exc)
                self._fire("on_error", task_id=task.id, error=str(exc))

        logger.info("[DEVELOPER] ✓ %d  ✗ %d  ⏰ %d", completed, failed, timed_out)
        self._fire("on_stage_end", stage="developer")
        return {"completed": completed, "failed": failed, "timed_out": timed_out, "files": files}

    async def _run_tester(self) -> dict:
        agent = self.agents["tester"]
        timeout = self._agent_timeout("tester")
        tester_id = self._next_id()

        logger.info("[TESTER] %s (model: %s, timeout: %s)",
                     agent.name, agent.model, timeout)
        self._fire("on_stage_start", stage="tester")

        TasksMd.add_task("TASKS.md", {
            "task": tester_id, "role": "tester",
            "title": "运行前端测试", "priority": "HIGH",
        }, "todo")
        TasksMd.move_task("TASKS.md", tester_id, "todo", "doing")
        agent._current_task_id = tester_id

        try:
            if timeout:
                await asyncio.wait_for(agent.run(""), timeout=timeout)
            else:
                await agent.run("")
        except asyncio.TimeoutError:
            logger.error("[TESTER] Timeout (%ds) → relay", timeout)
            TasksMd.move_task("TASKS.md", tester_id, "doing", "failed",
                              error=f"timeout:{timeout}s")
            Checkpoint.save("tester", tester_id, {"status": "timeout"})
            return {"status": "failed", "error": f"Timeout after {timeout}s", "passed": False}
        except Exception as exc:
            logger.error("[TESTER] Failed: %s", exc)
            TasksMd.move_task("TASKS.md", tester_id, "doing", "failed",
                              error=str(exc)[:80])
            return {"status": "failed", "error": str(exc), "passed": False}

        passed = agent.all_passed
        if passed:
            logger.info("[TESTER] ✓ All passed")
            TasksMd.move_task("TASKS.md", tester_id, "doing", "done")
        else:
            logger.warning("[TESTER] ✗ Some failures")
            TasksMd.move_task("TASKS.md", tester_id, "doing", "failed",
                              error="tests not all passing")

        self._fire("on_stage_end", stage="tester")
        return {"status": "ok", "passed": passed}

    async def _run_writer(self) -> dict:
        agent = self.agents["writer"]
        timeout = self._agent_timeout("writer")
        writer_id = self._next_id()

        logger.info("[WRITER] %s (model: %s, timeout: %s)",
                     agent.name, agent.model, timeout)
        self._fire("on_stage_start", stage="writer")

        TasksMd.add_task("TASKS.md", {
            "task": writer_id, "role": "writer",
            "title": "生成技术文档", "priority": "MEDIUM",
        }, "todo")
        TasksMd.move_task("TASKS.md", writer_id, "todo", "doing")
        agent._current_task_id = writer_id

        try:
            if timeout:
                artifact = await asyncio.wait_for(agent.run(""), timeout=timeout)
            else:
                artifact = await agent.run("")
        except asyncio.TimeoutError:
            logger.error("[WRITER] Timeout (%ds) → relay", timeout)
            TasksMd.move_task("TASKS.md", writer_id, "doing", "failed",
                              error=f"timeout:{timeout}s")
            Checkpoint.save("writer", writer_id, {"status": "timeout"})
            return {"status": "timeout", "files": []}
        except Exception as exc:
            logger.error("[WRITER] Failed: %s", exc)
            TasksMd.move_task("TASKS.md", writer_id, "doing", "failed",
                              error=str(exc)[:80])
            return {"status": "failed", "error": str(exc)}

        files = (artifact.metadata or {}).get("files", [])
        for f in files:
            logger.info("   %s", f)
        logger.info("[WRITER] ✓ %d documents", len(files))

        TasksMd.move_task("TASKS.md", writer_id, "doing", "done")
        Checkpoint.clear("writer", writer_id)

        self._fire("on_stage_end", stage="writer")
        return {"status": "ok", "files": files}

    # ── helpers ────────────────────────────────

    def _store_feedback(self) -> None:
        latest = self.memory.get_latest(ArtifactType.TEST_REPORT)
        text = f"Tests failed. Fix code based on this report:\n{latest.content if latest else 'No details'}"
        self.memory.store(Artifact(
            type=ArtifactType.FEEDBACK,
            content=text,
            producer=self.agents["tester"].name,
        ))

    def _write_status(self, agent: str, task: str, progress: str,
                       t0: float, tasks_summary: str = "") -> None:
        elapsed = time.monotonic() - t0
        StatusMd.write(
            current_agent=agent,
            current_task=task,
            progress=progress,
            elapsed=f"{elapsed:.1f}s",
            tasks_summary=tasks_summary,
        )

    def _print_summary(self, summary: dict) -> None:
        dev = summary.get("developer", {})
        logger.info("─" * 40)
        logger.info("SUMMARY")
        logger.info("  Analyst:  %s", summary.get("analyst", {}).get("status", "?"))
        logger.info("  Dev:      ✓ %d  /  ✗ %d  /  ⏰ %d",
                     dev.get("completed", 0), dev.get("failed", 0), dev.get("timed_out", 0))
        logger.info("  Test:     %s", summary.get("test_status", "?"))
        logger.info("  Writer:   %s", summary.get("writer", {}).get("status", "?"))
        logger.info("  Elapsed:  %ss", summary.get("elapsed_sec", "?"))

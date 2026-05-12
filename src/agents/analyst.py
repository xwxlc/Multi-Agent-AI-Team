"""需求分析师 Agent — 负责需求拆解、技术选型、任务拆分."""

import json
import re
from typing import Optional

from ..core.base_agent import BaseAgent
from ..core.memory import SharedMemory, Artifact, ArtifactType
from ..core.task_queue import Task, TaskQueue, Priority


class AnalystAgent(BaseAgent):
    role = "analyst"

    def __init__(self, memory: SharedMemory, router=None, agent_cfg=None, **kwargs):
        super().__init__(memory, router=router, agent_cfg=agent_cfg, **kwargs)
        self._task_queue: Optional[TaskQueue] = None

    def set_task_queue(self, queue: TaskQueue) -> None:
        self._task_queue = queue

    def system_prompt(self) -> str:
        return """你是一名资深需求分析师和技术架构师。你的职责是将用户的自然语言需求转化为结构化的PRD（产品需求文档）和可执行的开发任务列表。

工作流程:
1. 理解用户需求，识别核心功能和边界条件
2. 确定技术栈和架构方案
3. 将需求拆解为独立的开发任务
4. 为每个任务设定优先级和依赖关系

输出格式要求（必须严格遵循，以便程序解析）:
- PRD部分用【PRD_START】和【PRD_END】包裹
- 任务列表用【TASKS_START】和【TASKS_END】包裹
- 每个任务格式: `- [PRIORITY] task_id | 任务标题 | 描述 | 依赖任务id(逗号分隔)`
- PRIORITY 为 HIGH / MEDIUM / LOW
- 如果没有依赖，写 none"""

    async def execute(self, requirement: str) -> Artifact:
        prompt = f"""请分析以下用户需求，输出PRD和任务列表:

用户需求:
{requirement}

请输出:
1. PRD文档（产品概述、功能需求、非功能需求、技术方案、架构设计）
2. 开发任务列表（每个任务包含优先级、标题、描述、依赖关系）
"""
        response = await self.think(prompt)
        prd_content, tasks = await self._parse_response(response)

        # Store PRD
        artifact = self._create_artifact(
            ArtifactType.PRD,
            prd_content,
            {"requirement": requirement},
        )

        # Populate task queue
        if self._task_queue is not None:
            self._task_queue.add_batch(tasks)

        # Store task list in memory
        task_list_text = "\n".join(
            f"[{t.priority.name}] {t.id}: {t.title} → {t.description}"
            for t in tasks
        )
        self._create_artifact(ArtifactType.TASK_LIST, task_list_text)

        return artifact

    async def _parse_response(self, response: str) -> tuple[str, list[Task]]:
        # Extract PRD
        prd_match = re.search(r"【PRD_START】(.*?)【PRD_END】", response, re.DOTALL)
        prd = prd_match.group(1).strip() if prd_match else response[:2000]

        # Extract tasks
        tasks_match = re.search(r"【TASKS_START】(.*?)【TASKS_END】", response, re.DOTALL)
        tasks_raw = tasks_match.group(1).strip() if tasks_match else ""
        tasks = self._parse_tasks(tasks_raw)

        # Fallback: generate tasks from PRD if none parsed
        if not tasks:
            tasks = self._generate_default_tasks(prd)
            # Ask LLM to format properly
            retry_prompt = f"""基于以下PRD，严格按照格式生成任务列表:

{prd}

格式: 每行一个任务
- [PRIORITY] task_id | 标题 | 描述 | 依赖
用【TASKS_START】和【TASKS_END】包裹"""
            retry = await self.think(retry_prompt)
            tasks_match2 = re.search(r"【TASKS_START】(.*?)【TASKS_END】", retry, re.DOTALL)
            if tasks_match2:
                tasks = self._parse_tasks(tasks_match2.group(1).strip())

        return prd, tasks

    def _parse_tasks(self, text: str) -> list[Task]:
        tasks = []
        priority_map = {"HIGH": Priority.HIGH, "MEDIUM": Priority.MEDIUM, "LOW": Priority.LOW}

        for line in text.strip().split("\n"):
            line = line.strip().lstrip("- ").strip()
            if not line:
                continue
            match = re.match(
                r"\[(HIGH|MEDIUM|LOW)\]\s+(\S+)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+)",
                line,
            )
            if match:
                prio_str, task_id, title, desc, deps_str = match.groups()
                deps = [d.strip() for d in deps_str.split(",") if d.strip() and d.strip() != "none"]
                tasks.append(Task(
                    id=task_id,
                    title=title.strip(),
                    description=desc.strip(),
                    assigned_to="developer",
                    priority=priority_map.get(prio_str, Priority.MEDIUM),
                    dependencies=deps,
                ))
        return tasks

    def _generate_default_tasks(self, prd: str) -> list[Task]:
        """Generate a minimal task list when LLM output can't be parsed."""
        return [
            Task(
                id="TASK-001",
                title="项目初始化",
                description="创建项目结构、配置文件、入口文件",
                assigned_to="developer",
                priority=Priority.HIGH,
            ),
            Task(
                id="TASK-002",
                title="核心功能实现",
                description=f"根据PRD实现核心业务逻辑: {prd[:200]}",
                assigned_to="developer",
                priority=Priority.HIGH,
                dependencies=["TASK-001"],
            ),
            Task(
                id="TASK-003",
                title="单元测试",
                description="为核心功能编写单元测试",
                assigned_to="developer",
                priority=Priority.MEDIUM,
                dependencies=["TASK-002"],
            ),
        ]

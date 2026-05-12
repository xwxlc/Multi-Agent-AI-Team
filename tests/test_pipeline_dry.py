"""Tests for async orchestrator pipeline with BaseAgent (name/role/run/log/retry)."""
import asyncio
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.WARNING)

from src.core.memory import SharedMemory, ArtifactType, Artifact
from src.core.task_queue import TaskQueue, Task, Priority
from src.core.base_agent import BaseAgent
from src.core.orchestrator import Orchestrator
from src.agents.analyst import AnalystAgent
from src.agents.developer import DeveloperAgent
from src.agents.tester import TesterAgent
from src.agents.documenter import DocumenterAgent


# ═══════════════════════════════════════════════
#  Test 1: BaseAgent._retry() inline retry
# ═══════════════════════════════════════════════
def test_base_agent_retry():
    m = SharedMemory()

    class RetryTestAgent(BaseAgent):
        role = "retry-test"
        def system_prompt(self) -> str:
            return "test"
        async def execute(self, task: str) -> Artifact:
            return Artifact(type=ArtifactType.PRD, content="ok")

    agent = RetryTestAgent(m, name="test-retry", max_retries=4, retry_backoff=0.01)

    call_count = [0]

    async def flaky():
        call_count[0] += 1
        if call_count[0] < 3:
            raise RuntimeError(f"fail #{call_count[0]}")
        return "ok"

    result = asyncio.run(agent._retry(lambda: flaky(), label="flaky-test"))
    assert result == "ok"
    assert call_count[0] == 3
    print("✅ Test 1: BaseAgent._retry() — succeeds after 2 failures")


# ═══════════════════════════════════════════════
#  Test 2: BaseAgent.retry() decorator
# ═══════════════════════════════════════════════
def test_base_agent_retry_decorator():
    m = SharedMemory()

    class DecoAgent(BaseAgent):
        role = "deco"

        def system_prompt(self) -> str:
            return "test"

        async def execute(self, task: str) -> Artifact:
            return Artifact(type=ArtifactType.PRD, content="ok")

        @BaseAgent.retry(max_attempts=3, backoff=0.01)
        async def flaky_method(self) -> str:
            self._counter = getattr(self, "_counter", 0) + 1
            if self._counter < 2:
                raise RuntimeError(f"deco fail #{self._counter}")
            return "deco-ok"

    agent = DecoAgent(m, name="deco-agent", max_retries=1)
    result = asyncio.run(agent.flaky_method())
    assert result == "deco-ok"
    assert agent._counter == 2
    print("✅ Test 2: @BaseAgent.retry decorator")


# ═══════════════════════════════════════════════
#  Test 3: BaseAgent properties — name / role / log / repr
# ═══════════════════════════════════════════════
def test_base_agent_properties():
    m = SharedMemory()

    class PropAgent(BaseAgent):
        role = "prop-tester"

        def system_prompt(self) -> str:
            return "prop"

        async def execute(self, task: str) -> Artifact:
            return Artifact(type=ArtifactType.PRD, content="prop-result")

    agent = PropAgent(m, name="属性测试-01")
    assert agent.name == "属性测试-01"
    assert agent.role == "prop-tester"
    assert agent.max_retries == 3
    assert agent.retry_backoff == 2.0
    assert "属性测试-01" in repr(agent)

    # Auto-generated name contains role
    agent2 = PropAgent(m)
    assert agent2.name.startswith("prop-tester-")
    assert len(agent2.name.split("-")[-1]) == 6  # short uuid

    # log() doesn't crash
    agent.log(logging.DEBUG, "这是一条测试日志 %s", "hello")

    print("✅ Test 3: name / role / log / repr properties")


# ═══════════════════════════════════════════════
#  Mock agents for pipeline tests
#  max_retries=1 so orchestrator flow is tested, not agent-level retry
# ═══════════════════════════════════════════════

class MockAnalyst(AnalystAgent):
    role = "analyst"
    async def execute(self, task: str) -> Artifact:
        tq = self._task_queue
        if tq:
            tq.add(Task(id="T1", title="init", description="create project",
                        assigned_to="developer", priority=Priority.HIGH))
            tq.add(Task(id="T2", title="core", description="implement feature",
                        assigned_to="developer", priority=Priority.HIGH, dependencies=["T1"]))
        return self._create_artifact(ArtifactType.PRD, "Mock PRD")


class MockDeveloper(DeveloperAgent):
    role = "developer"
    async def execute(self, task: str) -> Artifact:
        return self._create_artifact(ArtifactType.SOURCE_CODE, "code",
                                     {"files": ["main.py"]})


class MockTester(TesterAgent):
    role = "tester"
    async def execute(self, task: str) -> Artifact:
        self.all_passed = True
        return self._create_artifact(ArtifactType.TEST_REPORT, "all passed")


class MockTesterFailThenPass(TesterAgent):
    role = "tester"
    def __init__(self, memory, **kwargs):
        super().__init__(memory, **kwargs)
        self.fail_first = True

    async def execute(self, task: str) -> Artifact:
        if self.fail_first:
            self.fail_first = False
            self.all_passed = False
            return self._create_artifact(ArtifactType.TEST_REPORT, "2 failed")
        self.all_passed = True
        return self._create_artifact(ArtifactType.TEST_REPORT, "all passed")


class FailingDeveloper(MockDeveloper):
    def __init__(self, memory, **kwargs):
        super().__init__(memory, **kwargs)
        self.call_count = 0

    async def execute(self, task: str) -> Artifact:
        self.call_count += 1
        if self.call_count == 1:
            raise RuntimeError("simulated failure")
        return await super().execute(task)


class MockDocumenter(DocumenterAgent):
    role = "writer"
    async def execute(self, task: str) -> Artifact:
        return self._create_artifact(ArtifactType.DOCUMENTATION, "docs",
                                     {"files": ["README.md"]})


def make_orch(analyst_cls, dev_cls, tester_cls, doc_cls):
    """Build orchestrator with mock agents — max_retries=1 to isolate flow tests."""
    m = SharedMemory()
    orch = Orchestrator(config_path="config/agents.yaml", memory=m)
    orch.max_feedback_rounds = 3
    orch.agents["analyst"] = analyst_cls(m, max_retries=1)
    orch.agents["developer"] = dev_cls(m, max_retries=1)
    orch.agents["tester"] = tester_cls(m, max_retries=1)
    orch.agents["writer"] = doc_cls(m, max_retries=1)
    orch.agents["analyst"].set_task_queue(orch.task_queue)
    return orch


# ═══════════════════════════════════════════════
#  Test 4: Happy path
# ═══════════════════════════════════════════════
def test_happy_path():
    orch = make_orch(MockAnalyst, MockDeveloper, MockTester, MockDocumenter)
    summary = asyncio.run(orch.run("test", work_dir="/tmp/test_dry"))
    assert summary["test_status"] == "PASSED"
    assert summary["developer"]["completed"] == 2
    print("✅ Test 4: Happy path pipeline")


# ═══════════════════════════════════════════════
#  Test 5: Test fail → feedback → retry → pass
# ═══════════════════════════════════════════════
def test_feedback_loop():
    orch = make_orch(MockAnalyst, MockDeveloper, MockTesterFailThenPass, MockDocumenter)
    summary = asyncio.run(orch.run("test", work_dir="/tmp/test_dry2"))
    assert summary["test_status"] == "PASSED"
    # 2 tasks in round-1 + 2 tasks in round-2 (feedback fix)
    assert summary["developer"]["completed"] == 2
    print("✅ Test 5: Test-fail → feedback → retry → pass")


# ═══════════════════════════════════════════════
#  Test 6: Task failure (no retry inside run() since max_retries=1)
# ═══════════════════════════════════════════════
def test_task_failure():
    orch = make_orch(MockAnalyst, FailingDeveloper, MockTester, MockDocumenter)
    summary = asyncio.run(orch.run("test", work_dir="/tmp/test_dry3"))
    assert summary["developer"]["failed"] == 1
    assert summary["developer"]["completed"] == 1
    print("✅ Test 6: Task failure — 1 failed (T1), 1 succeeded (T2)")


# ═══════════════════════════════════════════════
#  Test 7: Agent max_retries — transient recovery
# ═══════════════════════════════════════════════
def test_agent_level_retry():
    fail_counts = [0]

    class FlakyAnalyst(MockAnalyst):
        async def execute(self, task: str) -> Artifact:
            fail_counts[0] += 1
            if fail_counts[0] == 1:
                raise RuntimeError("transient error")
            return await super().execute(task)

    m = SharedMemory()
    orch = Orchestrator(config_path="config/agents.yaml", memory=m)
    orch.max_feedback_rounds = 2
    orch.agents["analyst"] = FlakyAnalyst(m, max_retries=3, retry_backoff=0.01)
    orch.agents["developer"] = MockDeveloper(m, max_retries=1)
    orch.agents["tester"] = MockTester(m, max_retries=1)
    orch.agents["writer"] = MockDocumenter(m, max_retries=1)
    orch.agents["analyst"].set_task_queue(orch.task_queue)

    summary = asyncio.run(orch.run("test", work_dir="/tmp/test_dry7"))
    assert summary["analyst"]["status"] == "ok"
    assert fail_counts[0] == 2  # 1 fail + 1 success via run() retry
    print("✅ Test 7: Agent-level retry recovers from transient error")


if __name__ == "__main__":
    test_base_agent_retry()
    test_base_agent_retry_decorator()
    test_base_agent_properties()
    test_happy_path()
    test_feedback_loop()
    test_task_failure()
    test_agent_level_retry()
    print("\n🎉 ALL TESTS PASSED")

"""前端测试 Agent — 扫描 workspace/ 自动 npm test，失败反馈 developer 修复."""

from __future__ import annotations

import asyncio
import glob
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Optional

from ..core.base_agent import BaseAgent
from ..core.memory import SharedMemory, Artifact, ArtifactType

WORKSPACE = "workspace"
TEST_TIMEOUT = 120  # npm test 超时秒数

TESTER_PROMPT = """你是一名资深前端测试工程师，专注 React + TypeScript 项目测试。你的职责是:

1. 阅读 workspace/src/ 下的组件源代码
2. 生成 vitest + @testing-library/react 测试用例
3. 分析测试结果并给出修复建议

测试策略:
- 组件渲染测试: render → screen.getByText / getByRole 断言 DOM 存在
- 交互测试: fireEvent / userEvent 模拟点击、输入
- 快照测试: 可选的 toMatchSnapshot
- 边界测试: 空 props、空列表、错误状态
- 可访问性测试: getByRole 优先于 getByTestId

输出格式:
1. 测试代码用 [FILE: src/__tests__/Component.test.tsx] 标记

   示例:
   [FILE: src/__tests__/App.test.tsx]
   import { describe, it, expect } from 'vitest';
   import { render, screen } from '@testing-library/react';
   import App from '../App';

   describe('App', () => {
     it('renders headline', () => {
       render(<App />);
       expect(screen.getByRole('heading')).toBeDefined();
     });
   });

2. 测试文件放入 src/__tests__/ 目录
3. 报告部分用自然语言输出: 通过/失败数量和具体原因"""


@dataclass
class TestResult:
    """单次测试运行结果。"""
    passed: int = 0
    failed: int = 0
    total: int = 0
    stdout: str = ""
    stderr: str = ""
    returncode: int = -1
    failures_detail: list[str] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return self.returncode == 0 and self.failed == 0

    @property
    def summary(self) -> str:
        if self.returncode == -1:
            return f"⚠️  测试未执行 ({self.stderr.strip()[:100]})"
        return f"{self.passed} passed, {self.failed} failed  (共 {self.total})"


class TesterAgent(BaseAgent):
    """前端项目测试 Agent。

    流程:
      1. 检测 workspace/package.json → 确认是前端项目
      2. 扫描 src/ 下组件 → 缺失测试则 LLM 补全
      3. npm install（安装测试依赖）
      4. npm test（运行 vitest）
      5. 失败 → 生成 FEEDBACK → orchestrator 通知 developer 修复
    """

    role = "tester"

    def __init__(self, memory: SharedMemory, router=None, agent_cfg=None,
                 *, workspace: str = WORKSPACE, **kwargs):
        super().__init__(memory, router=router, agent_cfg=agent_cfg, **kwargs)
        self.workspace = workspace
        self.all_passed = False
        self.last_result: Optional[TestResult] = None

    # ── abstract ───────────────────────────────

    def system_prompt(self) -> str:
        return TESTER_PROMPT

    async def execute(self, _task: str = "") -> Artifact:
        """主流程：补全测试 → npm install → npm test → 生成报告。"""
        self.log(20, "🔍 扫描 workspace/ ...")

        # 1. 检查是否为前端项目
        if not self._is_frontend_project():
            return self._create_artifact(
                ArtifactType.TEST_REPORT,
                "## 测试报告\n\n⚠️  不是前端项目（无 package.json），跳过测试。",
                {"passed": True, "total": 0},
            )

        # 2. 扫描缺失测试的组件 → LLM 补全
        src_files = self._discover_sources()
        missing_tests = self._find_missing_tests(src_files)
        self.log(20, "源文件 %d 个，缺失测试 %d 个", len(src_files), len(missing_tests))

        if missing_tests:
            test_code = await self._generate_tests(missing_tests)
            test_files = self._write_test_files(test_code)
            self.log(20, "补全 %d 个测试文件", len(test_files))

        # 3. 安装依赖
        await self._install_deps()

        # 4. 运行 npm test
        result = await self._run_npm_test()
        self.all_passed = result.all_passed
        self.last_result = result

        # 5. 生成报告
        report = self._build_report(result)
        artifact = self._create_artifact(
            ArtifactType.TEST_REPORT,
            report,
            {
                "passed": result.all_passed,
                "total": result.total,
                "passed_count": result.passed,
                "failed_count": result.failed,
                "returncode": result.returncode,
            },
        )
        return artifact

    # ── project detection ──────────────────────

    def _is_frontend_project(self) -> bool:
        return os.path.isfile(os.path.join(self.workspace, "package.json"))

    def _discover_sources(self) -> list[str]:
        """扫描 src/ 下的 TSX/TS 源文件。"""
        patterns = ["src/**/*.tsx", "src/**/*.ts"]
        files: list[str] = []
        for pat in patterns:
            full_pat = os.path.join(self.workspace, pat)
            for p in glob.glob(full_pat, recursive=True):
                rel = os.path.relpath(p, self.workspace)
                # 排除测试文件、类型定义
                if "__tests__" not in rel and not rel.endswith(".d.ts"):
                    files.append(rel)
        return sorted(set(files))

    def _find_missing_tests(self, src_files: list[str]) -> list[str]:
        """找出还没有配套测试文件的组件。"""
        missing: list[str] = []
        for f in src_files:
            name = os.path.splitext(os.path.basename(f))[0]
            test_path = os.path.join(
                os.path.dirname(f), "__tests__", f"{name}.test.tsx"
            )
            if not os.path.isfile(os.path.join(self.workspace, test_path)):
                missing.append(f)
        return missing

    # ── test generation ────────────────────────

    async def _generate_tests(self, missing: list[str]) -> str:
        """调用 LLM 为缺失测试的组件生成 vitest 用例。"""
        prd = self.memory.get_latest(ArtifactType.PRD)
        prd_text = prd.content[:3000] if prd else "无 PRD"

        sources_text = self._read_sources_summary(missing, max_chars=6000)

        prompt = f"""请为以下 React 组件生成 vitest 测试用例:

## PRD:
{prd_text}

## 需要补全测试的组件:
{sources_text}

要求:
1. 使用 vitest + @testing-library/react
2. 测试文件放入 src/__tests__/Component.test.tsx
3. 用 [FILE: src/__tests__/xxx.test.tsx] 标记每个测试文件
4. 覆盖渲染、交互、边界情况"""
        return await self.think(prompt)

    def _read_sources_summary(self, files: list[str], max_chars: int = 6000) -> str:
        parts: list[str] = []
        total = 0
        for f in files:
            full = os.path.join(self.workspace, f)
            try:
                with open(full) as fh:
                    content = fh.read()
            except Exception:
                continue
            limit = max(200, max_chars - total)
            parts.append(f"### {f}\n```tsx\n{content[:limit]}\n```")
            total += len(content[:limit])
            if total >= max_chars:
                parts.append("... (内容过长已截断)")
                break
        return "\n\n".join(parts) if parts else "（无源文件）"

    # ── test file writing ──────────────────────

    def _write_test_files(self, response: str) -> list[str]:
        """解析 LLM 输出中的 [FILE: src/__tests__/*.test.tsx] 块。"""
        pattern = r"\[FILE:\s*(.+?\.test\.(tsx?|jsx?))\s*\]\s*\n(.*?)(?=\[FILE:|\Z)"
        matches = re.findall(pattern, response, re.DOTALL)

        written: list[str] = []
        for filepath, _, content in matches:
            filepath = filepath.strip()
            content = self._clean_code(content.strip())
            if not content.strip():
                continue
            full_path = os.path.join(self.workspace, filepath)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            if self.write_file(full_path, content):
                written.append(filepath)
                self.log(20, "📝 %s  (%d bytes)", filepath, len(content))

        return written

    @staticmethod
    def _clean_code(text: str) -> str:
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n```\s*$", "", text)
        return text.strip()

    # ── dependency install ─────────────────────

    async def _install_deps(self) -> None:
        """npm install 安装前端依赖。"""
        pkg = os.path.join(self.workspace, "package.json")
        if not os.path.isfile(pkg):
            return

        self.log(20, "📦 npm install ...")
        try:
            proc = await asyncio.create_subprocess_exec(
                "npm", "install", "--legacy-peer-deps",
                cwd=self.workspace,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=120
            )
            if proc.returncode != 0:
                err_text = stderr.decode()[-500:] if stderr else ""
                self.log(30, "⚠️  npm install 失败: %s", err_text)
            else:
                self.log(20, "✅ npm install 完成")
        except asyncio.TimeoutError:
            self.log(30, "⚠️  npm install 超时")
        except FileNotFoundError:
            self.log(30, "⚠️  npm 未安装，请安装 Node.js")
        except Exception as exc:
            self.log(30, "⚠️  npm install 异常: %s", exc)

    # ── test execution ─────────────────────────

    async def _run_npm_test(self) -> TestResult:
        """运行 npm test（vitest），返回结构化结果。"""
        self.log(20, "🧪 npm test ...")

        try:
            proc = await asyncio.create_subprocess_exec(
                "npx", "vitest", "run", "--reporter=verbose",
                cwd=self.workspace,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=TEST_TIMEOUT
            )
            stdout = stdout_bytes.decode(errors="replace")
            stderr = stderr_bytes.decode(errors="replace")

            return self._parse_vitest_output(stdout, stderr, proc.returncode or 0)
        except asyncio.TimeoutError:
            return TestResult(stderr="测试超时", returncode=-1)
        except FileNotFoundError:
            return TestResult(
                stderr="vitest 未安装，请执行 npm install",
                returncode=-1,
            )
        except Exception as exc:
            return TestResult(stderr=str(exc), returncode=-1)

    def _parse_vitest_output(
        self, stdout: str, stderr: str, returncode: int
    ) -> TestResult:
        """解析 vitest 输出中的通过/失败数量。

        vitest 输出示例:
          ✓ src/__tests__/App.test.tsx > App > renders (10ms)
          × src/__tests__/Counter.test.tsx > Counter > increments

          Test Files  1 passed | 1 failed (2)
               Tests  3 passed | 1 failed (4)
        """
        result = TestResult(returncode=returncode, stdout=stdout, stderr=stderr)

        # 匹配 Tests 行: "Tests  3 passed | 1 failed (4)"
        tests_match = re.search(
            r"Tests\s+(\d+)\s+passed\s*\|\s*(\d+)\s+failed\s*\((\d+)\)",
            stdout,
        )
        if tests_match:
            result.passed = int(tests_match.group(1))
            result.failed = int(tests_match.group(2))
            result.total = int(tests_match.group(3))
        else:
            # 手动统计 ✓ 和 ×
            result.passed = len(re.findall(r"^\s*✓", stdout, re.MULTILINE))
            result.failed = len(re.findall(r"^\s*×", stdout, re.MULTILINE))
            result.total = result.passed + result.failed

        # 提取失败详情
        fail_blocks = re.findall(
            r"^\s*×\s+(.+?)\n(.*?)(?=^\s*[✓×]|\Z)",
            stdout,
            re.MULTILINE | re.DOTALL,
        )
        for name, detail in fail_blocks:
            result.failures_detail.append(f"{name.strip()}: {detail.strip()[:300]}")

        # stderr 中的关键错误
        if stderr.strip() and not stderr.startswith("npm"):
            trace_lines = [
                l for l in stderr.split("\n")
                if "Error" in l or "FAIL" in l or "error" in l
            ]
            if trace_lines:
                result.failures_detail.extend(trace_lines[:5])

        return result

    # ── report ─────────────────────────────────

    def _build_report(self, result: TestResult) -> str:
        """生成结构化测试报告。"""
        icon = "✅" if result.all_passed else "❌"
        lines = [
            f"## 前端测试报告  {icon}",
            "",
            f"**结果**: {result.summary}",
            f"**返回码**: {result.returncode}",
            "",
        ]

        if result.all_passed:
            lines.append("### ✅ 全部通过")
            lines.append("")
            lines.append("所有 vitest 用例通过，组件质量达标。")
        else:
            lines.append("### ❌ 测试失败 — 需要修复")
            lines.append("")
            lines.append("请 Developer 根据以下信息修复组件代码：")
            lines.append("")
            if result.failures_detail:
                lines.append("#### 失败用例")
                for i, detail in enumerate(result.failures_detail, 1):
                    lines.append(f"{i}. `{detail}`")
                    lines.append("")

            if result.stderr.strip():
                lines.append("#### stderr")
                lines.append("```")
                lines.append(result.stderr.strip()[-800:])
                lines.append("```")
                lines.append("")

            lines.append("#### vitest 输出 (最后 2000 字符)")
            lines.append("```")
            lines.append(result.stdout[-2000:])
            lines.append("```")

        return "\n".join(lines)

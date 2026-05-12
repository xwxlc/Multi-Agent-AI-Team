"""前端开发 Agent — 收到任务后生成 React + TypeScript 项目代码，保存到 workspace/."""

from __future__ import annotations

import os
import re
from typing import Optional

from ..core.base_agent import BaseAgent
from ..core.memory import SharedMemory, Artifact, ArtifactType
from ..core.sync import Checkpoint

WORKSPACE = "workspace"

FRONTEND_PROMPT = """你是一名资深前端工程师，精通 React + TypeScript + Vite 技术栈。你的职责是根据 PRD 和任务描述生成完整、可运行的前端项目代码。

项目结构规范:
  workspace/
  ├── package.json          # 依赖与脚本 (react, typescript, vite, vitest)
  ├── tsconfig.json         # TypeScript 配置
  ├── vite.config.ts        # Vite 构建配置
  ├── index.html            # 入口 HTML
  ├── src/
  │   ├── main.tsx          # ReactDOM 入口
  │   ├── App.tsx           # 根组件
  │   ├── components/       # 可复用组件
  │   │   └── *.tsx
  │   ├── hooks/            # 自定义 hooks
  │   │   └── *.ts
  │   ├── types/            # 类型定义
  │   │   └── *.ts
  │   └── __tests__/        # 测试文件
  │       └── *.test.tsx

输出规范:
1. 每个文件用 [FILE: 路径] 标记，输出完整代码。

   示例:
   [FILE: src/App.tsx]
   import { useState } from 'react';
   function App() { ... }
   export default App;

   [FILE: src/__tests__/App.test.tsx]
   import { describe, it, expect } from 'vitest';
   import { render, screen } from '@testing-library/react';
   ...

2. 每个文件代码必须完整可运行，不可省略任何 import 或函数体。
3. package.json 必须包含:
   - scripts: { dev, build, test }
   - dependencies: react, react-dom
   - devDependencies: typescript, vite, @vitejs/plugin-react, vitest, @testing-library/react, @testing-library/jest-dom, jsdom
4. 每个组件必须配套生成测试文件 (__tests__/Component.test.tsx)。
5. 代码规范:
   - 函数组件 + TypeScript 类型
   - CSS Modules 或 Tailwind
   - 错误边界 ErrorBoundary
   - 可访问性 (aria-* 属性)
6. 如上下文中包含 FEEDBACK（测试未通过），优先修复指出的问题。
7. 不要生成多余的说明文字，只输出代码块。"""


class DeveloperAgent(BaseAgent):
    """前端开发 Agent。

    输入: 任务描述（从 orchestrator 的任务队列中获取）
    输出: workspace/ 下的完整 React + TypeScript 项目文件（含配套测试）
    """

    role = "developer"

    def __init__(self, memory: SharedMemory, router=None, agent_cfg=None,
                 *, workspace: str = WORKSPACE, **kwargs):
        super().__init__(memory, router=router, agent_cfg=agent_cfg, **kwargs)
        self.workspace = workspace

    # ── abstract ───────────────────────────────

    def system_prompt(self) -> str:
        return FRONTEND_PROMPT

    async def execute(self, task_description: str) -> Artifact:
        """根据任务生成前端代码 + 测试文件，写入 workspace/。"""
        self._ensure_workspace()

        # Check for previous checkpoint (resume support)
        cp = Checkpoint.load("developer", getattr(self, "_current_task_id", ""))
        if cp and cp.get("files_so_far"):
            self.log(20, "Resuming from checkpoint: %d files already done",
                     len(cp["files_so_far"]))

        prompt = self._build_prompt(task_description)
        response = await self.think(prompt)

        files_written = self._parse_and_write(response)

        # Save checkpoint after each batch of file writes
        if files_written:
            Checkpoint.save("developer", getattr(self, "_current_task_id", "unknown"), {
                "status": "in_progress",
                "files_so_far": files_written,
                "task": task_description[:200],
            })

        summary = self._build_summary(response, files_written)

        return self._create_artifact(
            ArtifactType.SOURCE_CODE,
            summary,
            {"files": files_written, "task": task_description},
        )

    # ── prompt ──────────────────────────────────

    def _build_prompt(self, task_description: str) -> str:
        feedback = self.memory.get_latest(ArtifactType.FEEDBACK)
        prd = self.memory.get_latest(ArtifactType.PRD)
        prd_text = prd.content[:4000] if prd else "无 PRD"

        if feedback:
            return f"""⚠️ 修复模式 — 测试发现以下问题，请修正代码:

## 测试反馈:
{feedback.content}

## PRD:
{prd_text}

## 当前任务:
{task_description}

请只输出修复后的完整文件（用 [FILE: xxx] 标记），不要解释。"""

        return f"""请根据以下信息生成 React + TypeScript 前端项目代码:

## PRD:
{prd_text}

## 当前任务:
{task_description}

请生成完整的项目文件（含测试文件），每个文件用 [FILE: xxx] 标记。"""

    # ── file parsing & writing ──────────────────

    def _parse_and_write(self, response: str) -> list[str]:
        """解析 LLM 输出的 [FILE: path] 块，写入 workspace/。"""
        pattern = r"\[FILE:\s*(.+?)\]\s*\n(.*?)(?=\[FILE:|\Z)"
        matches = re.findall(pattern, response, re.DOTALL)

        written: list[str] = []
        for filepath, content in matches:
            filepath = filepath.strip("/")
            content = self._clean_code(content.strip())
            if not content.strip():
                continue
            full_path = os.path.join(self.workspace, filepath)
            if self.write_file(full_path, content):
                written.append(filepath)
                self.log(20, "📄 %s  (%d bytes)", filepath, len(content))

        # Fallback: no markers → treat as src/App.tsx
        if not written:
            clean = self._clean_code(response)
            if clean.strip():
                path = os.path.join(self.workspace, "src", "App.tsx")
                self.write_file(path, clean)
                written.append("src/App.tsx")
                self.log(20, "📄 src/App.tsx  (fallback, %d bytes)", len(clean))

        return written

    @staticmethod
    def _clean_code(text: str) -> str:
        text = re.sub(r"^```\w*\n?", "", text)
        text = re.sub(r"\n```\s*$", "", text)
        return text.strip()

    # ── workspace ───────────────────────────────

    def _ensure_workspace(self) -> None:
        """创建前端项目标准目录结构。"""
        dirs = [
            self.workspace,
            os.path.join(self.workspace, "src"),
            os.path.join(self.workspace, "src", "components"),
            os.path.join(self.workspace, "src", "hooks"),
            os.path.join(self.workspace, "src", "types"),
            os.path.join(self.workspace, "src", "__tests__"),
        ]
        for d in dirs:
            os.makedirs(d, exist_ok=True)

    # ── summary ─────────────────────────────────

    def _build_summary(self, raw_response: str, files: list[str]) -> str:
        lines = [f"## 前端开发者输出", f"", f"生成文件 ({len(files)}):"]
        for f in sorted(files):
            full = os.path.join(self.workspace, f)
            try:
                size = os.path.getsize(full)
            except OSError:
                size = 0
            lines.append(f"  - {f}  ({size} bytes)")
        lines.append("")
        lines.append(f"<details><summary>LLM 原始输出</summary>\n\n{raw_response[:3000]}\n\n</details>")
        return "\n".join(lines)

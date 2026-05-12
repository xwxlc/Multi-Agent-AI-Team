"""文档工程师 Agent — 生成API文档、使用手册等技术文档."""

import os
import re

from ..core.base_agent import BaseAgent
from ..core.memory import SharedMemory, Artifact, ArtifactType


class DocumenterAgent(BaseAgent):
    role = "writer"

    def system_prompt(self) -> str:
        return """你是一名技术文档工程师。你的职责是根据PRD、源代码和测试报告生成专业的技术文档。

输出内容:
1. README.md — 项目概述、安装说明、快速开始
2. API.md — 接口/函数文档（如有）
3. ARCHITECTURE.md — 架构设计说明

文档规范:
- 使用Markdown格式
- 代码示例要完整可运行
- 中文为主，技术术语保留英文
- 输出时用 [FILE: path/to/file.md] 标记每个文件"""

    async def execute(self, task: str = "") -> Artifact:
        prd = self.memory.get_latest(ArtifactType.PRD)
        source_code = self.memory.get_latest(ArtifactType.SOURCE_CODE)
        test_report = self.memory.get_latest(ArtifactType.TEST_REPORT)

        prompt = f"""请根据以下项目信息生成完整的技术文档:

## PRD:
{prd.content[:3000] if prd else "无"}

## 源代码:
{source_code.content[:3000] if source_code else "无"}

## 测试报告:
{test_report.content[:1000] if test_report else "无"}

请生成以下 3 个文档文件:
1. README.md — 项目简介、安装、使用示例（面向用户）
2. ARCHITECTURE.md — 架构设计、模块说明、数据流（面向开发者）
3. API.md — 接口/函数文档（如有API）"""

        response = await self.think(prompt)
        files_written = self._parse_and_write_docs(response)

        artifact = self._create_artifact(
            ArtifactType.DOCUMENTATION,
            response,
            {"files": files_written},
        )
        return artifact

    def _parse_and_write_docs(self, response: str) -> list[str]:
        work_dir = self.memory.get_context("work_dir", "./project_output")
        pattern = r"\[FILE:\s*(.+?\.md)\]\s*\n(.*?)(?=\[FILE:|\Z)"
        matches = re.findall(pattern, response, re.DOTALL)

        written = []
        for filepath, content in matches:
            content = re.sub(r"^```\w*\n?", "", content.strip())
            content = re.sub(r"\n```$", "", content)
            full_path = os.path.join(work_dir, filepath)
            self.write_file(full_path, content)
            written.append(filepath)

        if not written:
            full_path = os.path.join(work_dir, "README.md")
            content = re.sub(r"^```\w*\n?", "", response)
            content = re.sub(r"\n```$", "", content)
            self.write_file(full_path, content)
            written.append("README.md")

        return written

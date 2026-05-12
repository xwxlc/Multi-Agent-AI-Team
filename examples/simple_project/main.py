"""
示例: 以编程方式使用多Agent团队 (async)

用法:
    python examples/simple_project/main.py
"""

import asyncio
import logging
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from src.core.orchestrator import Orchestrator
from config.settings import config


async def run_example():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )

    requirement = """
开发一个学生成绩管理系统，支持:
1. 添加学生（姓名、学号）
2. 录入成绩（学号、科目、分数）
3. 查询学生所有成绩
4. 计算平均分和排名
5. 数据持久化到JSON文件
"""

    orchestrator = Orchestrator(**config.to_llm_kwargs())

    work_dir = "./output/grade_system"
    summary = await orchestrator.run(requirement, work_dir=work_dir)

    print(f"\n📁 输出目录: {os.path.abspath(work_dir)}")
    if os.path.exists(work_dir):
        for f in sorted(os.listdir(work_dir)):
            fpath = os.path.join(work_dir, f)
            print(f"   - {f} ({os.path.getsize(fpath)} bytes)")


if __name__ == "__main__":
    asyncio.run(run_example())

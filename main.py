"""
多Agent AI开发团队系统 — 主入口 (多模型版)

用法:
    python main.py
    或:  python main.py --config config/agents.yaml
    # 交互式输入需求，自动完成 分析→开发→测试→文档 全流程
    # 4 个 Agent 分别使用不同模型: Claude / DeepSeek / GPT-4.1 / Claude
"""

import asyncio
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.core.orchestrator import Orchestrator


def setup_logging():
    fmt = "%(asctime)s  %(levelname)-7s  %(message)s"
    logging.basicConfig(level=logging.INFO, format=fmt, datefmt="%H:%M:%S")


async def async_main(requirement: str, config_path: str):
    orchestrator = Orchestrator(config_path=config_path)

    summary = await orchestrator.run(requirement)

    wd = orchestrator.workspace
    abspath = os.path.abspath(wd)
    print(f"\n📁 输出目录: {abspath}")
    if os.path.isdir(wd):
        files = sorted(
            os.path.join(dp, f)[len(wd) + 1:]
            for dp, _, fns in os.walk(wd) for f in fns
        )
        print(f"📄 生成文件 ({len(files)}):")
        for f in files:
            fpath = os.path.join(wd, f)
            print(f"   - {f}  ({os.path.getsize(fpath)} bytes)")

    return summary


def main():
    setup_logging()

    config_path = "config/agents.yaml"
    if "--config" in sys.argv:
        idx = sys.argv.index("--config")
        if idx + 1 < len(sys.argv):
            config_path = sys.argv[idx + 1]

    print("=" * 60)
    print("  多 Agent 多模型 开发团队系统")
    print("=" * 60)
    print(f"  配置: {config_path}")
    print()
    print("  Agent 模型分配:")
    print("    analyst   → Claude (Sonnet)")
    print("    developer → DeepSeek")
    print("    tester    → GPT-4.1")
    print("    writer    → Claude (Sonnet)")
    print("=" * 60)

    # Check env vars
    import yaml
    with open(config_path) as f:
        cfg = yaml.safe_load(f)
    providers = cfg.get("providers", {})
    missing = []
    for key, p in providers.items():
        env_key = p.get("env_key", "")
        if env_key and not os.getenv(env_key):
            missing.append(f"  {p['name']:12s} → export {env_key}=your-key")
    if missing:
        print("\n⚠️  缺少 API Key，请设置环境变量:")
        for m in missing:
            print(m)
        print("\n可跳过此检查继续运行。")

    print("\n请输入开发需求（输入完成后按 Ctrl+D 或 /done 结束）:")
    lines = []
    try:
        while True:
            line = input()
            if line.strip() == "/done":
                break
            lines.append(line)
    except EOFError:
        pass

    requirement = "\n".join(lines).strip()
    if not requirement:
        print("未输入需求，使用默认示例。")
        requirement = "开发一个 React 待办事项应用，支持添加/删除/标记完成/过滤，数据存 localStorage"

    print(f"\n📋 需求: {requirement[:100]}...\n")

    try:
        asyncio.run(async_main(requirement, config_path))
    except KeyboardInterrupt:
        print("\n\n⚠️  用户中断")
    except Exception as e:
        print(f"\n❌ 执行出错: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()

"""
Multi-Agent AI Dev Team — main entry (multi-model + timeout relay).

Usage:
    python main.py [--config config/agents.yaml] [--resume]
    # Interactive requirement input → auto ANALYST→DEV→TESTER→WRITER
    # 4 agents use DeepSeek v4: pro / flash / flash / pro
    # --resume: restore from TASKS.md + checkpoints after interruption
"""

import argparse
import asyncio
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.core.orchestrator import Orchestrator
from src.core.sync import Lock, TasksMd, Checkpoint


def setup_logging():
    fmt = "%(asctime)s  %(levelname)-7s  %(message)s"
    logging.basicConfig(level=logging.INFO, format=fmt, datefmt="%H:%M:%S")


async def async_main(requirement: str, config_path: str, resume: bool = False):
    orchestrator = Orchestrator(config_path=config_path)
    summary = await orchestrator.run(requirement, resume=resume)

    wd = orchestrator.workspace
    abspath = os.path.abspath(wd)
    print(f"\n📁 Output: {abspath}")
    if os.path.isdir(wd):
        files = sorted(
            os.path.join(dp, f)[len(wd) + 1:]
            for dp, _, fns in os.walk(wd) for f in fns
        )
        print(f"📄 Generated files ({len(files)}):")
        for f in files:
            fpath = os.path.join(wd, f)
            print(f"   - {f}  ({os.path.getsize(fpath)} bytes)")

    # Display TASKS.md summary
    if os.path.isfile("TASKS.md"):
        sections = TasksMd.parse()
        done = len(sections.get("done", []))
        failed = len(sections.get("failed", []))
        todo = len(sections.get("todo", []))
        print(f"\n📋 TASKS.md: ✓ {done} done  ✗ {failed} failed  ⏳ {todo} pending")
        print(f"   STATUS.md: {os.path.isfile('STATUS.md') and 'written' or 'missing'}")
        cps = Checkpoint.list_all()
        if cps:
            print(f"   Checkpoints: {len(cps)}")

    return summary


def main():
    setup_logging()

    parser = argparse.ArgumentParser(description="Multi-Agent AI Dev Team")
    parser.add_argument("--config", default="config/agents.yaml", help="Path to agents.yaml")
    parser.add_argument("--resume", action="store_true",
                        help="Resume from TASKS.md + checkpoints after interruption")
    args = parser.parse_args()

    print("=" * 60)
    print("  Multi-Agent Multi-Model Dev Team")
    if args.resume:
        print("  [RESUME mode — restoring from TASKS.md]")
    print("=" * 60)
    print(f"  Config: {args.config}")
    print()
    print("  Agent model allocation (DeepSeek):")
    print("    analyst   → deepseek-v4-pro")
    print("    developer → deepseek-v4-flash")
    print("    tester    → deepseek-v4-flash")
    print("    writer    → deepseek-v4-pro")
    print("=" * 60)

    # Check env vars / key
    import yaml
    with open(args.config) as f:
        cfg = yaml.safe_load(f)
    providers = cfg.get("providers", {})
    missing = []
    for key, p in providers.items():
        env_key = p.get("env_key", "")
        if env_key and not os.getenv(env_key) and not p.get("key"):
            missing.append(f"  {p['name']:12s} → export {env_key}=your-key")
    if missing:
        print("\n⚠️  Missing API keys, please set:")
        for m in missing:
            print(m)
        print("\nContinuing anyway...")

    if args.resume:
        if not os.path.isfile("TASKS.md"):
            print("\nNo TASKS.md found — cannot resume. Starting fresh.\n")
            args.resume = False

    if not args.resume:
        print("\nEnter requirement (Ctrl+D or /done to finish):")
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
            print("No input — using default.")
            requirement = "开发一个 React 待办事项应用，支持添加/删除/标记完成/过滤，数据存 localStorage"
    else:
        # Resume mode: extract requirement from memory context or TASKS.md
        requirement = "resume from checkpoint"
        sections = TasksMd.parse()
        done_tasks = [t.get("title", "") for t in sections.get("done", [])]
        if done_tasks:
            requirement = f"Continue: {done_tasks[-1][:100]}" if done_tasks[-1] else requirement
        print(f"\n📋 Resuming: {requirement[:100]}...\n")

    print(f"\n📋 Requirement: {requirement[:100]}...\n")

    try:
        asyncio.run(async_main(requirement, args.config, resume=args.resume))
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted — state saved to TASKS.md / STATUS.md / .checkpoint/")
        print("   Resume with: python main.py --resume")
        Lock.release()
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        Lock.release()


if __name__ == "__main__":
    main()

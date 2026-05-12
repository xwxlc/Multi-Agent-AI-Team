"""直接调用 orchestrator 运行考勤管理系统开发流程"""
import asyncio
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from src.core.orchestrator import Orchestrator

REQUIREMENT = """开发一个员工考勤管理系统 (Employee Attendance Management System)，
使用 React + TypeScript + Vite，数据存 localStorage。

功能需求：
1. 员工管理 — 添加/编辑/删除员工（姓名、工号、部门）
2. 每日签到/签退 — 记录上下班时间，自动计算工作时长
3. 考勤日历视图 — 按月查看每位员工的打卡记录
4. 考勤统计 — 显示正常、迟到、早退、缺勤天数
5. 状态筛选 — 按部门、月份、考勤状态筛选
6. 数据导出 — 导出考勤报表 (JSON/CSV 格式)

非功能需求：
- TypeScript 类型安全
- 组件化架构，hooks 抽离业务逻辑
- Vitest 单元测试覆盖率 > 80%
- 响应式布局"""

async def main():
    orch = Orchestrator(config_path="config/agents.yaml")
    summary = await orch.run(REQUIREMENT)
    wd = orch.workspace
    print(f"\n{'='*60}")
    print(f"📁 输出目录: {os.path.abspath(wd)}")
    if os.path.isdir(wd):
        files = sorted(
            os.path.join(dp, f)[len(wd)+1:]
            for dp, _, fns in os.walk(wd) for f in fns
        )
        print(f"📄 生成文件 ({len(files)}):")
        for f in files:
            fpath = os.path.join(wd, f)
            print(f"   - {f}  ({os.path.getsize(fpath)} bytes)")
    print(f"⏱ 总耗时: {summary.get('elapsed_sec', '?')}s")
    return summary

if __name__ == "__main__":
    asyncio.run(main())

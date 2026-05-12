# 多Agent AI开发团队系统 — 架构文档

## 系统概述

本系统模拟一个完整的软件开发团队，由4个AI Agent协同工作，覆盖软件开发全生命周期。

## 架构图

```
┌──────────────────────────────────────────────────────────┐
│                      Orchestrator                        │
│  ┌─────────┐   ┌──────────┐  ┌────────┐  ┌───────────┐  │
│  │ Analyst │ → │ Developer│ ⇄ │ Tester │ → │Documenter │  │
│  └────┬─────┘  └────┬─────┘  └───┬────┘  └─────┬─────┘  │
│       │              │            │              │        │
│       └──────────────┴────────────┴──────────────┘        │
│                         ↓                                 │
│                   SharedMemory                            │
│               (上下文 / 产物共享)                           │
└──────────────────────────────────────────────────────────┘
```

## 核心模块

### 1. Orchestrator（编排器）
- 控制整体工作流: ANALYZE → DEVELOP ⇄ TEST → DOCUMENT
- 管理Agent生命周期
- 支持生命周期钩子（hooks）用于可观测性

### 2. SharedMemory（共享内存）
- 所有Agent通过SharedMemory交换信息
- 存储Artifact（PRD、源码、测试报告、文档）
- 维护对话日志

### 3. TaskQueue（任务队列）
- 优先级队列（HIGH/MEDIUM/LOW）
- 支持任务依赖（Task A 完成才能执行 Task B）
- 按角色过滤任务

### 4. BaseAgent（Agent基类）
- LLM调用封装（OpenAI兼容API）
- 对话历史管理
- 文件读写能力

## Agent角色

| Agent | 输入 | 输出 | 核心职责 |
|-------|------|------|----------|
| AnalystAgent | 用户需求 | PRD + 任务列表 | 需求拆解、技术选型 |
| DeveloperAgent | PRD + 任务 | 源代码文件 | 编码实现 |
| TesterAgent | 源码 + PRD | 测试代码 + 报告 | 测试验证 |
| DocumenterAgent | 全部产物 | README/API/架构文档 | 文档生成 |

## 数据流

```
用户输入 → Analyst.parse → PRD → TaskQueue
                                    ↓
                              Developer.write → 源代码文件
                                    ↓
                              Tester.test → 测试报告
                               ↙    ↓    ↘
                     ALL PASS    FAIL    ERROR
                        ↓         ↓        ↓
                   Documenter  FEEDBACK  重试
                        ↓         ↓
                     文档输出  → Developer.fix → 重新测试
```

## 反馈循环

开发→测试之间支持最多3轮反馈循环:
1. Developer 编写代码
2. Tester 执行测试
3. 如果失败 → Tester 生成 FEEDBACK artifact
4. Developer 读取 FEEDBACK 并修复代码
5. 重复直到通过或达到最大重试次数

## 扩展点

1. **新Agent**: 继承BaseAgent，实现`system_prompt()`和`execute()`
2. **新工具**: 在`src/tools/`下添加，Agent通过BaseAgent方法调用
3. **新工作流**: 修改Orchestrator的`run()`方法
4. **LLM后端**: 兼容OpenAI API格式的任意模型（GPT-4o、Claude、本地Ollama等）
5. **持久化**: SharedMemory可扩展为数据库存储

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| LLM_API_KEY | - | API密钥（必填） |
| LLM_API_BASE | https://api.openai.com/v1 | API地址 |
| LLM_MODEL | gpt-4o | 模型名称 |
| LLM_TEMPERATURE | 0.3 | 生成温度 |
| WORK_DIR | ./project_output | 输出目录 |
| MAX_RETRY | 3 | 最大重试次数 |

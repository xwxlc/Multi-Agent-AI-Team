"""Prompt templates for Tester Agent."""

TESTER_SYSTEM = """你是一名资深测试工程师，负责编写和执行测试用例。

测试策略:
- 单元测试: 核心函数/方法的独立测试
- 集成测试: 模块间交互验证
- 边界测试: 异常输入、空值、极限值
- 使用 pytest 框架

输出:
1. 测试代码用 [FILE: test_xxx.py] 标记
2. 测试分析报告"""

TESTER_WRITE_TESTS = """请为以下项目编写测试:

## PRD:
{prd}

## 源代码:
{source_code}

请编写pytest测试用例，覆盖核心功能和边界情况。"""

TESTER_ANALYZE_RESULTS = """## 测试执行结果:

{results}

## 源代码:
{source_code}

请分析测试结果，给出改进建议。"""

# RepoPilot Agent

RepoPilot 是一个面向 Python 代码仓库的、证据驱动的研发协作 Agent MVP。它把 Issue 转换为结构化计划，使用 AST 感知的代码检索定位相关符号，并把代码行、工具结果和测试输出保存为 Evidence，后续扩展为可审批、可恢复的 Issue-to-Patch 工作流。

## 当前已实现

- Python AST 函数/类级切分与符号索引
- 关键词 + 符号加权的混合检索骨架
- Plan–Retrieve–Execute 任务状态与结构化 Trace
- EvidenceStore 代码/测试证据模型
- Tool Registry 与高风险工具人工审批门
- pytest 执行与结果证据化
- 分页、空字段、路径穿越三个 Demo Issue
- 3 个核心单元测试

> README 只陈述当前代码已经实现的能力。LangGraph、Embedding、MCP、FastAPI/SSE、SQLite Checkpoint、隔离 worktree 与 Reviewer Agent 属于下一阶段。

## Quickstart

```bash
python -m pip install -e .
repopilot . "list_orders 分页 offset 错误"
python -m unittest discover -s tests -v
```

## 目标流程

```mermaid
flowchart LR
    I[Issue / Error / PR] --> P[Plan]
    P --> R[AST-aware Hybrid RAG]
    R --> T[Search / Read / Test Tools]
    T --> E[EvidenceStore]
    E --> A[Human Approval]
    A --> X[Patch in Isolated Worktree]
    X --> V[Test + Reviewer]
    V --> O[Traceable Report]
```

## Roadmap

- [ ] Embedding + BM25 + AST 邻接扩展
- [ ] LangGraph Developer/Reviewer 工作流
- [ ] SQLite Checkpoint 与任务恢复
- [ ] FastAPI + SSE 执行轨迹
- [ ] MCP 只读代码工具
- [ ] worktree 隔离补丁与 Diff 审批
- [ ] 受控 Bug 仓库及真实开源 PR 案例

## Why this project

普通代码 RAG 的终点是回答问题；RepoPilot 的目标是把检索、工具执行、权限审批、补丁验证和证据报告串成一条可以审计的研发任务链路。

## Limitations

当前版本是可测试的架构 MVP，不是通用 Coding Agent，不会自动推送代码，也不会执行任意 Shell 命令。结果能力取决于后续接入的模型、检索器和受控工具。


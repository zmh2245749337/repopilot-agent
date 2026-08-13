# 架构说明

RepoPilot 采用“应用层与安全内核分离”的结构，借鉴 Agentic-RAG 项目的可运行 API、工具注册和 MCP 展示方式，但针对代码修改保留严格边界。

```text
web/index.html → FastAPI routes → RepoPilot core → CodeIndex / EvidenceStore / TaskStore
                                  ↘ isolated workspace → pytest → reviewer
MCP stdio server → bounded tool adapter → RepoPilot core
```

- `core.py`：领域模型、AST 索引、证据、SQLite 检查点、审批状态机、隔离工作区、Diff 与审查。
- `api.py`：任务创建、恢复、审批、拒绝、验证、报告和 SSE 事件路由。
- `web/index.html`：本地仪表盘，展示计划、证据、补丁建议、Diff 和审查结果。
- `tools.py`：统一注册低风险搜索/读取和中风险测试能力。
- `mcp_server/`：stdio MCP 适配层，只公开受限读取与测试工具。

任务在每次状态改变时写入 `.repopilot/tasks.sqlite3`。补丁批准后优先创建 `git worktree add --detach`；无 Git 元数据的演示目录则复制到相邻 `.repopilot-worktrees/<task-id>`。两种方式都避免直接写入原仓库。

未来接入模型时，模型只能返回结构化计划或补丁建议，不能绕过 Tool Registry、审批状态、路径校验、隔离工作区或 Reviewer。

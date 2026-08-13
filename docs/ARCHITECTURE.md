# 架构说明

RepoPilot 采用“应用层与安全内核分离”的结构，借鉴 Agentic-RAG 项目的可运行 API、工具注册和 MCP 展示方式，但针对代码修改保留严格边界。

```text
web/index.html → FastAPI routes → RepoPilot core → CodeIndex / EvidenceStore / TaskStore
              ↘ Code RAG → Code Tool Registry → Hybrid Retriever → SQLite conversations
                                  ↘ isolated workspace → pytest → reviewer
MCP stdio server → bounded tool adapter → RepoPilot core + Code Tool Registry
```

- `core.py`：领域模型、AST/BM25 索引、证据、SQLite 检查点、审批状态机、隔离工作区、Diff 与审查。
- `planner.py`：离线确定性 Planner 与可选 OpenAI-compatible JSON Planner；不可用时安全回退。
- `retrieval.py`：词法与可选 Embedding 排名的 Reciprocal Rank Fusion；语义服务异常时保留词法结果。
- `chat.py`：按仓库隔离的 SQLite 对话、意图识别、追问改写、6 个只读工具的 Registry、模型/离线回答和流式轨迹。
- `api.py`：任务工作流、会话管理、工具目录、仓库导入和 SSE 回答路由。
- `web/index.html`：本地仪表盘，展示持久化会话、Registered Tools、Agent Execution、代码引用/预览，以及补丁工作流。
- `tools.py`：统一注册低风险搜索/读取和中风险测试能力。
- `mcp_server/`：stdio MCP 适配层，公开受限读取/测试和同源 Code RAG 工具，不公开补丁写入。

任务在每次状态改变时写入 `.repopilot/tasks.sqlite3`，对话与回答引用写入 `.repopilot/conversations.sqlite3`。补丁建议必须先在原仓库记录一次失败的基线测试；审核器会同时检查该失败证据、隔离工作区中同一测试目标的通过结果和补丁路径边界。补丁批准后优先创建 `git worktree add --detach`；仅当选择的目标目录本身就是 Git 仓库根目录时才使用 worktree，嵌套目录或无 Git 元数据的演示目录会复制到相邻 `.repopilot-worktrees/<task-id>`。两种方式都避免直接写入原仓库。

未来接入模型时，模型只能返回结构化计划或补丁建议，不能绕过 Tool Registry、审批状态、路径校验、隔离工作区或 Reviewer。

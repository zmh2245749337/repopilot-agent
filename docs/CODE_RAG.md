# Code RAG Copilot

Code RAG Copilot 是 RepoPilot 的只读对话层。它用于解释仓库代码、定位符号、辅助理解测试和初步识别安全风险；它不能修改文件，也不能调用批准、补丁或隔离工作区能力。

## Agent 路径

```mermaid
flowchart LR
    Q["用户问题"] --> I["意图识别"]
    I --> R["AST + BM25 检索"]
    R --> E["可选 Embedding 语义检索"]
    E --> F["RRF 融合与引用"]
    F --> M{"配置了模型？"}
    M -->|是| A["Grounded LLM Answer"]
    M -->|否/异常| O["Offline Evidence Answer"]
    A --> C["回答 + 文件/行号引用 + 轨迹"]
    O --> C
```

## 支持的对话能力

- **代码问答**：例如“`list_orders` 的分页逻辑在哪里？”
- **代码定位**：例如“路径校验在哪个文件？”
- **仓库概览**：例如“总结订单模块的实现。”
- **测试引导**：例如“应该运行哪个 pytest 目标？”
- **安全讨论**：例如“这里有什么路径穿越风险？”
- **多轮上下文**：在浏览器会话中保留最近对话历史；每一轮仍重新检索当前仓库。

## 证据与模型边界

每次响应包含：

- `citations`：相对文件路径、符号、开始/结束行、检索通道和代码摘录。
- `trace`：意图识别、检索完成和回答生成三个 Agent 事件。
- `provider`：`offline-evidence` 或 `openai-compatible:<model>`。
- `fallback`：模型调用失败时为 `true`，此时答案由离线证据模板生成。

即使配置了模型，系统提示也会约束模型：只能根据传入的仓库证据回答，必须引用文件行号，不得声称修改文件、运行命令或批准补丁。

## 模型配置

Code RAG Copilot 与 Planner 共用以下 OpenAI-compatible 配置：

```powershell
$env:REPOPILOT_MODEL_BASE_URL = "https://your-compatible-endpoint/v1"
$env:REPOPILOT_MODEL_NAME = "your-model"
$env:REPOPILOT_API_KEY = "your-api-key"
```

可选的语义检索配置：

```powershell
$env:REPOPILOT_EMBEDDING_BASE_URL = "https://your-compatible-endpoint/v1"
$env:REPOPILOT_EMBEDDING_MODEL = "your-embedding-model"
$env:REPOPILOT_EMBEDDING_API_KEY = "your-api-key"
```

不要把真实 Key 写入仓库或 `config.py`；请使用环境变量或本地未提交的 `.env` 文件。

## API 示例

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/chat `
  -ContentType 'application/json' `
  -Body '{"message":"list_orders 的分页逻辑在哪里？","top_k":4}'
```

将上一轮响应的 `conversation_id` 原样传给下一轮请求，即可保留对话上下文。

## 安全边界

Code RAG 只读层与修复工作流故意分离。任何写入都仍需走：原仓库失败基线 → 补丁建议 → 人工批准 → 隔离工作区 → 同一目标回归 → Reviewer。这一点不会因为模型已经回答了问题而改变。

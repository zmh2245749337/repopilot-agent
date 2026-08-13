# 简历与面试表述

以下内容基于 RepoPilot 当前真实实现，适合根据岗位要求择一使用。

## 中文简历版本

### 精简版（一行）

- 独立构建 RepoPilot：面向 Python 仓库的证据驱动代码维护 Agent，结合 AST/BM25 检索、人工审批、Git 隔离工作区和 pytest 回归验证，实现可追溯的 Issue-to-Patch 闭环。

### 项目经历版（三条）

- 设计并实现 Python 代码维护 Agent 的状态机：将 Issue 处理为“代码证据 → 原仓库失败基线 → 待审批补丁 → 隔离修复 → 同目标回归 → 审查报告”的受控流程，避免未经复现的自动修改。
- 实现 AST 符号索引、BM25 检索、精确符号优先和可选 Embedding RRF 融合；通过 FastAPI 仪表盘、CLI 与 MCP 提供统一入口，并用 SQLite 按仓库持久化任务、证据、对话引用和审查轨迹。
- 构建只读 Code RAG Copilot 与 6 工具 Registry，支持追问改写、流式回答、代码问答/函数摘要/依赖定位/测试建议/安全扫描路由、文件行号引用、执行耗时和模型故障回退；同时实现安全 GitHub/ZIP 隔离导入与代码预览。
- 构建 GitHub Actions 质量门禁，自动执行 30 个测试、3 个受控 Bug 修复场景和 4 个 RAG Eval 场景；RAG 案例全部通过来源、符号、Top‑1、意图与工具检查。

### 技术关键词

Python、FastAPI、pytest、SQLite、AST、BM25、RAG、MCP、Git worktree、GitHub Actions、Agent Safety

## English resume version

- Built RepoPilot, an evidence-grounded maintenance agent for Python repositories. It enforces an Issue-to-Patch workflow: code evidence, failing baseline reproduction, human approval, isolated patching, same-target regression testing, and reviewer evidence.
- Implemented AST/BM25 retrieval with optional embedding-based RRF ranking, plus FastAPI dashboard, CLI, read-only MCP tools, and SQLite-backed task/evidence audit trails.
- Added a read-only Code RAG Copilot with a six-tool registry, persistent repository-scoped conversations, streaming execution traces, exact-symbol ranking, file-and-line citations, safe repository import, and grounded model responses with offline fallback.
- Added reproducible GitHub Actions verification covering 30 tests, 3 controlled repair scenarios, and 4 RAG evaluation cases with source, symbol, Top-1, intent, and tool assertions.

## STAR 面试故事（约 90 秒）

**Situation：** 代码 Agent 常常直接建议或写入补丁，但无法确认 Bug 是否真实存在，也容易污染原仓库。

**Task：** 我希望做一个可演示的 Agent 项目，把“安全、可验证、可追溯”作为核心，而不只是生成代码。

**Action：** 我先实现 AST/BM25 检索和证据存储；再把任务建模为严格状态机。补丁必须由原仓库中失败的 pytest 基线解锁，并经人工批准后才在 worktree 或副本中写入。最后用同一测试目标做回归，并让 Reviewer 同时读取前后测试和路径范围。

**Result：** 项目可从仪表盘、CLI 或 MCP 接口演示，内置 3 个可复现案例；CI 自动验证完整流程。更重要的是，未知问题不会被伪装成“已修复”。

## 可主动说明的限制

“当前自动补丁只覆盖 3 个受控案例，这是为了先验证安全工作流。下一步会把模型限制在候选补丁生成和排序层，并继续由基线复现、人工审批和隔离回归来决定是否接受修改。”

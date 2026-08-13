# MCP 使用说明

RepoPilot 的 MCP Server 使用 stdio，目标是让其他 Agent 安全地获得仓库上下文，而不是让外部客户端绕过审批直接改代码。

启动前设置目标仓库：

```powershell
$env:REPOPILOT_REPO_PATH = "C:/path/to/python-repo"
python -m repopilot.mcp_server.server
```

可用工具：

- `search_code(query, top_k)`：按 AST 和关键词检索 Python 代码。
- `read_file(path, start_line, end_line)`：读取目标仓库内的文件区间；越界路径会被拒绝。
- `run_tests(target)`：运行相对 pytest 目标；绝对路径和 `..` 越界路径会被拒绝。
- `ask_code(question, conversation_id, top_k)`：自动识别意图并调用 Code RAG 工具，返回回答、文件/行号引用和执行轨迹。
- `summarize_code(symbol, top_k)`：基于仓库证据解释函数或类。
- `locate_code_dependencies(symbol, top_k)`：定位符号相关的导入与依赖。
- `suggest_code_tests(query, top_k)`：根据实现与已有测试推荐 pytest 目标。
- `scan_code_safety(query, top_k)`：执行只读安全模式初筛并返回引用。

后五个工具复用网页端同一套 Code Tool Registry，因此意图、检索、离线回退和引用行为保持一致。MCP 进程内会缓存多轮上下文。

没有 `apply_patch` 工具。任何修改必须从 RepoPilot 的任务 API 或仪表盘进入人工审批，再在隔离工作区中验证。

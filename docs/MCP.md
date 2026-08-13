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

没有 `apply_patch` 工具。任何修改必须从 RepoPilot 的任务 API 或仪表盘进入人工审批，再在隔离工作区中验证。

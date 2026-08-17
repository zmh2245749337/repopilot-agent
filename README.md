# RepoPilot · 代码智能助手

[![Verify RepoPilot](https://github.com/zmh2245749337/repopilot-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/zmh2245749337/repopilot-agent/actions/workflows/ci.yml)

一个面向 Python 仓库的轻量 CodeAgent：导入代码仓库，用自然语言提问，系统检索代码、选择工具、调用 GLM（可选），并返回文件与行号引用。

## 核心流程

    导入仓库 → 代码检索 → Agent 工具路由 → GLM / 本地证据回答 → 代码引用与执行轨迹

- 支持当前仓库、公开 GitHub 仓库和 ZIP 导入。
- 支持代码定位、函数解释、依赖定位、测试建议和安全扫描。
- 使用 Python AST 切分代码，BM25 检索并返回引用。
- 使用 Intent Router、Tool Registry、追问改写和 SSE 流式输出。
- 支持智谱 GLM-4.7-Flash；没有 Key 或模型限流时自动回退到本地证据回答。
- 提供 MCP 工具，供其他 Agent 调用。

## 技术栈

- 后端：FastAPI + Uvicorn
- 代码解析：Python AST
- 检索：BM25 + 精确符号加权
- LLM：智谱 GLM-4.7-Flash（OpenAI-compatible）
- Agent：Intent Router + Tool Registry
- 协议：MCP / stdio
- 前端：HTML + JavaScript 三栏界面
- 测试：pytest + GitHub Actions

## 30 秒启动

要求 Python 3.10+。推荐使用独立 Conda 环境。

    git clone https://github.com/zmh2245749337/repopilot-agent.git
    cd repopilot-agent
    conda create -n repopilot python=3.10 pip -y
    conda activate repopilot
    python -m pip install -e ".[api,mcp,dev]"
    python -m uvicorn repopilot.app:app --app-dir src --host 127.0.0.1 --port 8000

浏览器打开 http://127.0.0.1:8000 。

Windows 也可以运行 scripts/start_repopilot.ps1 一键启动。没有模型 Key 时，代码 RAG 仍可运行。

## 接入智谱 GLM

首次运行 scripts/configure_zhipu.ps1，安全输入你自己的 Key；Key 只保存在本机环境变量，不会进入 Git 仓库。

然后运行 python scripts/run_model_smoke.py 检查模型连通性。

真实问答会发送问题和检索到的代码片段给模型。请只导入你有权发送的代码；公开仓库适合演示。界面状态含义：

- configured：本机已读取模型配置
- online：本轮真实模型调用成功
- fallback：模型限流或异常，已回退到本地证据

## 最快演示

打开首页后依次点击“这个项目做什么？”、“代码检索在哪里？”、“有哪些 Agent 工具？”。点击回答下方的代码引用可以打开源码；右侧会显示意图、工具、检索来源和生成步骤。

## 从哪里开始读

| 文件 | 作用 |
| --- | --- |
| src/repopilot/app.py | 应用入口 |
| src/repopilot/api.py | FastAPI 路由 |
| src/repopilot/chat.py | Code RAG、模型调用、工具路由 |
| src/repopilot/retrieval.py | AST / BM25 检索 |
| src/repopilot/web/index.html | 三栏网页 |

读完这五个文件，再看 MCP 与测试；不需要先理解旧的高级修复工作流。

## MCP 与验证

设置 REPOPILOT_REPO_PATH 后运行 python -m repopilot.mcp_server.server，可使用 search_code、read_file、ask_code、summarize_code、locate_code_dependencies、suggest_code_tests、scan_code_safety。

运行测试：

    python -m pytest -q

项目采用 [MIT License](LICENSE)。

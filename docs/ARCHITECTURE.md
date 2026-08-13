# 架构说明

RepoPilot 使用确定性 Planner 保障本地可复现性。它的接口故意保持简单：`analyze()` 负责计划、AST 索引和证据；`propose_patch()` 只生成已验证规则允许的最小补丁；`apply_proposal()` 只能在审批状态下创建隔离工作区并执行补丁；`review_task()` 根据测试结果、路径边界和证据数量得出结论。

状态与证据会被写入目标仓库的 `.repopilot/tasks.sqlite3`。隔离执行优先使用 `git worktree add --detach`；对不带 Git 元数据的演示目录则完整复制到相邻的 `.repopilot-worktrees/<task-id>`。这两种方式都避免在原仓库直接写入补丁。

未来接入模型时，模型只能输出结构化计划和补丁建议，不能绕过 `ToolRegistry`、审批状态或路径校验。

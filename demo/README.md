# 可复现演示

`01_pagination.md` 是完整的无模型演示路径：RepoPilot 索引代码，给出一行偏移量修复建议，停在审批状态；只有传入 `--approve` 时才写入补丁、运行 pytest，并输出审查结论。

```bash
repopilot ./demo/cases/pagination_off_by_one "第一页漏掉第一条订单，list_orders 的 offset 错误" --propose --approve
```

另外两个 issue 用于检索和风险分析演示。默认 planner 不会为它们猜测补丁，因此不会修改代码。

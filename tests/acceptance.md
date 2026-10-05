# Acceptance Criteria — GitHub CI 修复 wave（weave-mem 补 CI + SQLite 双言修复）

> cap=5  stall=3×

1. weave-mem 补齐 GitHub Actions CI（对齐 note/talk 形态）：scoped lint（E9/F63/F7/F82/F841/F401 归零）+ sqlite 冒烟 5 套件 + PG 全量 12 套件三 job；推送后 run 转 success
2. CI 预验揪出的 2 个 SQLite 双言 bug 修复并有回归覆盖：`run_weight_decay` raw 行日期 str 相减 TypeError（`_as_dt` 归一）、`GET /api/memory/recall_log` 的 `isoformat()` str 崩 500（双言兼容）；sqlite 模式 5 套件全绿、PG 12 套件无回归
3. 全部 GitHub 仓 CI 状态核验：weave-mem 新 run 绿；weave-note/weave-talk 已绿（并发会话修复）不重复动；monorepo 本地留 CI 事实记录

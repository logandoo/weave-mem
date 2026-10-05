# ⏳ chatbot 记忆模块更新 → weave-mem 同步评估（2026-10-05）

## 任务
C4 只读审计：盘点上游 chatbot 自 2026-08-17 同步基线以来的记忆模块多波更新（含 config/config_model），逐项判定哪些可同步至 weave-mem。报告：`docs/AUDIT_2026-10-05_chatbot_memory_sync.md`。

## 关键事实（已实证，tests/audit_poc_20261005.log 10/10 块）
- **weave-mem 现存缺陷 5 项**（上游已修、下游未同步）：F-1 embedding 空键回落全局 LLM key（`memory_embedding_service.py:50-54`，上游 `a207ab59f` no-key 守卫；同类回落 `llm_service.py:19`/`provider_router.py:42`）；F-2 `try_cold_resurrect` 不刷新 `weight_decayed_at`（上游 `3378f9907` A2）；F-3 `apply_reinforcement_signal` RMW 丢更新（上游 B10 原子 UPDATE，`2f4dd26f0`）+ 缺 `answer_cited` 信号；F-4 `memory_clusters.embedding` 全库无写入方但 `_find_nearest_cluster` 按 `<=>` 排序（再平衡合到任意簇）；F-5 `billing_class` 读写分离缺失（`3378f9907` DC1）
- **10-04 记忆能力波**（`0121eb0a7`+`ef3f2ebcd`）：采纳闭环 `memory_adoption_service`（chat 流末触发 `chat.py:1553-1557`）→ weave-mem 需改造成显式 HTTP 端点（ingest/clarify 同款模式）；一致性加权依赖采纳历史；策略路由小纯函数移植（weave-mem `memory_retrieval_service.py:781-782` 已有调参面）；隐式探针是测试 harness 不同步
- **9-14 批量**：W4 recall ledger 默认开（需 SQLite 方言化清理 SQL）；W5 D1/D2/D3 默认关（**D3 必须用 `0d684a16f` 修好的 `config.memory_retrieval` accessor，波次时门不可达**）；W6 E1 可带、E3 低价值、E2/E4 不适用（无 deathmatch/agent worker）
- **config/config_model 分叉是常态不是欠账**：上游 `f33e242ff`/`6f6e32e94` 把 `[memory]` 11 个模型键迁往 `[endpoints]/[routing]`；weave-mem 保持扁平键（`memory_llm_factory._CONFIG_KEY_MAP` 对齐）——盲目同步 config 会静默丢 weave-mem 模型配置；只取 no-key 守卫语义（C-2）
- 独立复核 7/7 CONFIRMED（3 处措辞修正：F-1 追加回落点、F-3 commit=`2f4dd26f0`、F-4 上游写路径先于 A1 存在）

## ⛔/❌ 教训
- ❌ PoC 证据包首版用相对路径 `chatbot/`（不在 weave-family 下）→ 上游对照块全空；证据路径必须绝对化后 `cat` 自查一遍再入 log
- ⛔ 08-19 死代码清理删除的符号（add/remove_concept_to_cluster 等）在同步 F-4 案 (a) 时会重新出现——用户已知悉接受，同步波须防"已删死代码"蔓延
- ⛔ 上游 config_model.toml 与 weave-mem config.toml 是两套模型配置体系，逐文件 copy-同步是错误动作

## 未来注意
- 同步实施建议顺序（报告 §4）：P0 五项缺陷小修（含 SQLite 方言）→ P1 采纳闭环+一致性（新 HTTP 端点）→ P2 门控增强（默认关）→ P3 recall ledger；F-4 与 C-5 取值漂移待用户拍板
- LLM provider 配置后 F-1 风险从"潜在"变"实弹"——配置 embedding_api_base 前先合 P0

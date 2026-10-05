# Review Package — chatbot 同步 wave（P0-P3 + F-4a）2026-10-05

## 形态
- Lane L · Class CODE · COV-8 触发腿：schema/API-surface（5 迁移 + 2 新端点）+ risk-tier（billing/migration）
- 对抗式双审（§V4，fresh-context，假设含缺陷猎杀）+ 1 轮 scoped re-review

## Round 1 — 双审合并（去重后 2 Critical + 5 Important + 6 Minor）
| # | 裁决 | 修复 |
|---|------|------|
| C1 | CONFIRMED（双审一致）D3 快合并缺 `_fast_merge_name_safe`/SAVEPOINT/失败回退 | 三件全补（consolidation）+ 失败对回 LLM 队列 |
| C2 | CONFIRMED（双审一致）台账游标 id 轴与 (created_at,id) 排序不匹配 → 漏行 | 复合 keyset 子查询；re-review 确认 PG+SQLite 行值比较可执行 |
| I1 | CONFIRMED D2 装配死门（mmr/grouping?/contradicts/adaptive 未接线） | adaptive/MMR/contradicts 接入 `_build_injection_context`；grouping 包装未移植（格式特性，门保留键位说明——见遗留） |
| I2 | CONFIRMED `_drop_contradicted` 误用 id 轴恒空 | source_id/target_id 双言过滤 |
| I3 | CONFIRMED 白名单 edge_source 轴混淆 + 未接线 | 语义 relation_type 轴 + stage2 接线；测试改非空双态断言 |
| I4 | CONFIRMED recall_log_* 键节错位（[memory.retrieval] vs 读 [memory]） | 键迁 [memory] |
| B-I4 | CONFIRMED W8 绕过门拒收表 | source=file_link_expansion + 拒收表 |
| B-I5 | CONFIRMED 清理 0=清空语义破坏 | ≤0 禁用 |
| B-I6 | 部分确认（预存在面） | 本 wave 范围内原子化达成（F-3 口径）；余下 RMW 判 deferred |
| B-I7/A-M1 | CONFIRMED 无时预算 + commit 可 500 | 60/时预算 + fail-open commit |
| A-M2/M5/M6/B-M1/B-M2 | CONFIRMED | 全修（含 rho 接线、tier_scores、DDL NOT NULL、空断言） |

## Round 2 — scoped re-review
9/9 fix OK · compileall/import 干净 · 新缺陷仅 `_USER_ADOPT_TS` 列表增长 nit → 已修（append 前剪枝）· **Ship-ready: yes**

## 遗留（deferred，入 memory）
- AGPR 2-hop 未移植（weave-mem 关系扩展仅 1-hop，无消费者）
- D2 assembly_grouping 包装（`[相关记忆]` 分组格式）未移植——键保留说明性
- 预存在 RMW：`_bulk_update_concepts`/`apply_episode_recall_boost`/`run_weight_decay` 写回乐观锁（非本 wave 触碰面）
- ~~C-5 取值漂移~~ **已裁决**：migration_llm_timeout 对齐 120；dreaming/stage4_llm/warn_multiplier/embedding_provider_check/summary_max_tokens 确认保留（服务定位有意裁剪）

# PLAN — chatbot 同步 wave（P0-P3 + F-4a）2026-10-05（C3，Lane L）

零上下文可执行。顺序 T1→T14，每步含验证命令。TDD：先写失败测试（RED，log 留痕）再实现（GREEN）。
基线：PG 套件 8/8 绿；sqlite 套件 2 预存失败隔离（ADR D-4）。

## Consistency Hub（共享实体唯一真值，引用即引此表）

| entity | canonical 值/类型 | source of truth |
|---|---|---|
| `memory_llm_calls.billing_class` | VARCHAR(20) DEFAULT 'write'，取值 `write`/`read` | 迁移键 `mlc_billing_class`（上游 3378f9907 DC1） |
| `memory_clusters.embedding_model` | VARCHAR(100) NULL | 迁移键 `mc_cluster_embedding_model`（上游 DC2） |
| `memory_recall_log` 表 | id·user_id·conversation_id(NULL)·query_hash·candidate_ids·tier_scores·gate_score·budget_chars·injected_chars·truncated·elapsed_ms·cache_hit·created_at | 迁移键 `mrl_create` + `mrl_idx_user_created`（上游 beda68eb4，仅元数据） |
| `concept_relations.edge_source` | VARCHAR(20) DEFAULT 'llm'；确定性边值 `co_occurs` | 迁移键 `cr_edge_source`（上游 f5d2f6401 D1） |
| `memory_episodes.participants/locations` | TEXT NULL（JSON 数组字符串，空串不落库） | 迁移键 `me_participants`/`me_locations` |
| `answer_cited` 信号 | `+0.02`，cap=trust_cap | signal_map（上游 0121eb0a7 P1-①） |
| `POST /api/memory/adoption` | 入 `{injected_ids:[str], answer_text:str}` 出 `{matched:[str], adopted:int, relation_bumped:int}` | 本 wave（审计 F-6，ingest/clarify 同款显式触发） |
| `GET /api/memory/recall_log` | `?before_id&limit≤200` 出 `{items:[…], total:int}`，仅元数据 | 上游 beda68eb4 |
| 门控默认值 | `consistency_enabled=true`；其余全 `false`（strategy_route/expansion_rho/deterministic_edges/edge_read_whitelist/assembly_mmr/assembly_grouping/contradicts_read_downgrade/adaptive_cardinality/text_cross_turn_dedup/merge_fast_path/merge_mst/injection_usage_instruction/concept_link_expansion）；`stage0_hard_ceiling_ms=0`；`recall_log_enabled=true`（键在 `[memory]` 节） | 上游 config 默认 + 审计 §4"先关后开" |
| 语义边类型 | 白名单轴 = `causal/temporal/contradicts/supports/part_of`（+`co_occurs` 当 deterministic_edges_enabled）；`edge_source` 轴 = `llm/co_occurs`（溯源用，勿混） | 上游 f5d2f6401（A4.9 双审 I3 修正） |
| 方言 | PG：`NOW()`/`GREATEST`/`LEAST`/`<=>`/`INTERVAL`/`ANY`；SQLite：`CURRENT_TIMESTAMP`/标量 `max,min`/strftime 秒差/`_ids_in_sql`；一律走 `IS_SQLITE` 分支 | weave-mem 既有方言先例（weight_service:28-39） |

## T1 P0-a · F-1 no-key 守卫（Security）
- **Files**：modify `backend/app/services/memory_embedding_service.py`、`backend/app/services/llm_service.py`、`backend/app/services/provider_router.py`；create `tests/test_sync_p0.py`
- **Interfaces**：consumes `config.memory["embedding_api_base"/"embedding_api_key"]`、`config.api_key`、`config.api_base_url`；produces `_get_embedding_api_key()->str` 新语义：显式 base 且空键→`"no-key"`（绝不回落全局 key）；`llm_service`/`provider_router` 同语义（dummy-key 保留给无 base 场景）
- **Test seam**：tests/test_sync_p0.py 纯函数级（monkeypatch config 三态：空 base+空 key→全局 key；有 base+空 key→"no-key"；有 base+有 key→该 key）
- **Steps**：① RED：写三态断言跑 `./.venv/bin/python tests/test_sync_p0.py` 看 FAIL ② 改 embedding_service ③ 改 llm_service+provider_router ④ GREEN：重跑 + `./.venv/bin/python tests/test_api.py` 无回归

## T2 P0-b · F-2 复活衰减锚
- **Files**：modify `backend/app/services/memory_weight_service.py`；extend `tests/test_sync_p0.py`
- **Interfaces**：`try_cold_resurrect(db,user_message,user_id)->list[str]` 签名不变；两条 UPDATE 增 `weight_decayed_at = CURRENT_TIMESTAMP`（保留 CURRENT_TIMESTAMP）
- **Test seam**：in-process DB（test_clarify_apply 同款活库会话）：植入 cold_forgotten 概念（旧 weight_decayed_at）→ 触发复活 → 断言 weight_decayed_at 已刷新
- **Steps**：① RED ② 两条 UPDATE 补列 ③ GREEN ④ 全量回归

## T3 P0-c · F-3 原子权重 + answer_cited
- **Files**：modify `backend/app/services/memory_weight_service.py`；extend `tests/test_sync_p0.py`
- **Interfaces**：`apply_reinforcement_signal(db,concept_id,signal_type)->None` 不变；内部改双言原子 UPDATE（PG `GREATEST/LEAST`；SQLite 标量 `max/min`）；signal_map 增 `"answer_cited": 0.02`
- **Test seam**：活库会话：连续两次 `recall_reference` 后 weight 恰 +0.06（cap 截断）；`answer_cited` +0.02；`delta==0` 早退
- **Steps**：① RED ② 重写函数体 ③ GREEN ④ 回归

## T4 P0-d · F-5 billing_class 读写分离
- **Files**：modify `backend/app/db/database.py`（ORM 列）、`backend/app/db/migrations.py`（`mlc_billing_class`）、`backend/app/services/memory_cost_governance_service.py`；extend `tests/test_sync_p0.py`
- **Interfaces**：`record_llm_call(db,user_id,kind,model="",prompt_tokens=0,completion_tokens=0,billing_class="write")`；新增 `async record_llm_call_bg(user_id,kind,billing_class="read")`（独立会话静默失败）；4 处 COUNT 增 `AND COALESCE(billing_class,'write')='write'`
- **Test seam**：活库：写 read+write 各一 → degrade 计数只含 write；迁移后列存在（PG psql 抽查）
- **Steps**：① RED ② 迁移+ORM ③ 服务改造+bg 记录器 ④ GREEN ⑤ 回归

## T5 P1-a · F-6 采纳闭环 + 端点
- **Files**：create `backend/app/services/memory_adoption_service.py`；modify `backend/app/api/memory.py`（新端点）；extend `tests/test_adoption.py`
- **Interfaces**：`match_adopted_concepts(concepts,answer_text)->list[str]`（纯）；`adopted_concepts_recent(limit=512)->set[str]`；`record_answer_adoption(db,user_id,injected_ids,answer_text)->dict`；`record_answer_adoption_bg(...)`/`spawn_answer_adoption(...)`；HTTP `POST /api/memory/adoption`（401 无 token/422 缺参/200 `{matched,adopted,relation_bumped}`）
- **Test seam**：HTTP 契约 + 活库写回链（concept weight+0.02、relation 边权封顶、_ADOPTED_RECENT 收录）
- **Steps**：① API doc 更新 ② RED（HTTP 404 即 RED）③ 服务+端点 ④ GREEN ⑤ 回归

## T6 P1-b · F-7 一致性加权
- **Files**：modify `backend/app/services/memory_retrieval_service.py`；extend `tests/test_adoption.py`
- **Interfaces**：`apply_cross_modal_consistency(candidates,verified_ids=None,bonus=0.05,damp=0.03,rank_gap=4)`（纯，改 `metadata["calibrated_score"]`）；stage1 后写 `metadata["lex_rank"]`、stage3 后写 `metadata["dense_rank"]`；`consistency_enabled` 门（默认 true），`verified_ids` 来自 `adopted_concepts_recent()`；无 verified 恒不动
- **Test seam**：纯函数单测（双 rank≤3 → +bonus；|Δ|≥rank_gap → −damp 底 0；镜像 calibrated_score）
- **Steps**：① RED ② 快照+函数+门 ③ GREEN ④ 回归

## T7 P2-a · F-8 策略路由（门默认关）
- **Files**：modify `backend/app/services/memory_retrieval_service.py`、`backend/config.toml`；extend `tests/test_p2_gates.py`
- **Interfaces**：`select_retrieval_profile(query_text,stage0,cfg)->str`（default|entity_dense|narrative；门 `strategy_route_enabled` 默认 false）；`strategy_profile_params(profile,cfg)->dict`；`STRATEGY_PROFILE_DEFAULTS`（entity_dense={stage2_relation_max_new:12,stage2_relation_score_decay:0.65}；narrative={5,0.4}）；`_stage2_description_expansion(...,param_overrides=None)`
- **Test seam**：纯函数（关键词密度≥0.4→entity_dense）+ 门关返回 default
- **Steps**：① RED ② 移植+config 键（false） ③ GREEN ④ 回归

## T8 P2-b · D1 确定性边 + P/L/T
- **Files**：modify `backend/app/db/database.py`（episodes P/L 列、relations edge_source）、`backend/app/db/migrations.py`（`me_participants`/`me_locations`/`cr_edge_source`）、`backend/app/services/memory_cluster_service.py`（`create_relation(edge_source)`、`get_neighbors(allowed_types)`、`edge_read_whitelist`、`build_deterministic_edges`）、`backend/app/services/memory_concept_service.py`（提炼后挂点）、`backend/app/services/memory_episode_service.py`（P/L 参数）、`backend/app/services/memory_subconscious_service.py`（prompt schema）；extend `tests/test_p2_gates.py`
- **Interfaces**：见 Hub 行 `edge_source`/`participants`；`build_deterministic_edges(db,user_id,concept_ids,max_edges=20)` 幂等双向；全挂 `deterministic_edges_enabled` 门
- **Test seam**：门关时 0 行为变化；门开时共现边落库（edge_source=co_occurs）
- **Steps**：① RED ② 迁移+ORM ③ 服务 ④ GREEN ⑤ 回归

## T9 P2-c · D2 装配升级（门默认关）
- **Files**：modify `backend/app/services/memory_retrieval_service.py`；extend `tests/test_p2_gates.py`
- **Interfaces**：纯 `_rho_score`/`_agpr_decay`/`_text_similarity`（bigram Jaccard）/`_mmr_select`/`_drop_contradicted`；装配段挂 `assembly_mmr_enabled(+lambda)`、`assembly_grouping_enabled`、`contradicts_read_downgrade_enabled`、`adaptive_cardinality_enabled(+high_score)`、`text_cross_turn_dedup_enabled`
- **Test seam**：纯函数单测 + 门关断言装配输出与基线一致
- **Steps**：① RED ② 移植 ③ GREEN ④ 回归

## T10 P2-d · D3 快速合并 + MST（**用 HEAD 修好的门**）
- **Files**：modify `backend/app/services/memory_consolidation_service.py`；extend `tests/test_p2_gates.py`
- **Interfaces**：`_d3_fast_path_enabled()/_d3_mst_enabled()/_merge_pair_action(dist,low,thresh)->'fast'|'llm'|'skip'`（NULL dist→skip）；pair SELECT 增 dist 列（SQLite 分支恒 skip）；`_order_pairs_mst`
- **Test seam**：`_merge_pair_action` 三态单测 + 门关行为不变
- **Steps**：① RED ② 移植（禁用波次时坏门）③ GREEN ④ 回归

## T11 P2-e · E1 提示语 / A4c stage0 硬顶 / W8 concept_link
- **Files**：modify `backend/app/services/memory_retrieval_service.py`、`backend/config.toml`；extend `tests/test_p2_gates.py`
- **Interfaces**：E1：预算截断后若 `injection_usage_instruction_enabled` 前置 `injection_usage_instruction_text`（默认"以上历史记忆可能过时或不适用于当前任务，请结合当前对话判断。"）不计预算；A4c：`stage0_hard_ceiling_ms>0` 才 `asyncio.wait_for`；W8：`concept_link_expansion_enabled/max/units/score` 门控扩候选
- **Test seam**：门关输出不变；门开断言提示语出现/超时熔断
- **Steps**：① RED ② 移植 ③ GREEN ④ 回归

## T12 P3 · recall ledger
- **Files**：create `backend/app/services/memory_recall_log_service.py`；modify `backend/app/db/database.py`（MemoryRecallLog）、`backend/app/db/migrations.py`（`mrl_create`/`mrl_idx_user_created`）、`backend/app/api/memory.py`（GET recall_log）、`backend/app/services/memory_retrieval_service.py`（`_emit_recall_log`+`_apply_token_budget_ex` 截断提示）、`backend/app/services/memory_scheduler.py`（清理环）、`backend/config.toml`（recall_log_*）；extend `tests/test_recall_log.py`
- **Interfaces**：见 Hub 表行；`cleanup_statements(days)` 双言（PG INTERVAL / SQLite strftime）；`should_sample(rate)`；写入 fire-and-forget 静默失败；门 `recall_log_enabled` 默认 true
- **Test seam**：HTTP ledger 端点（401/分页/仅元数据）+ 截断提示 + 清理语句双言单测
- **Steps**：① API doc 更新 ② RED ③ 迁移+服务+端点+接线 ④ GREEN ⑤ 回归

## T13 F-4a · 簇 embedding 写路径重移植
- **Files**：modify `backend/app/services/memory_cluster_service.py`（`_update_cluster_embedding`+`add_concept_to_cluster`/`remove_concept_to_cluster` 回归并接线）、`backend/app/services/memory_concept_service.py`（成员变更挂点）、`backend/app/services/memory_embedding_service.py`（`_get_embedding_dim` 端点 extra.dim 优先 + `_get_embedding_model`）、`backend/app/db/migrations.py`（`mc_cluster_embedding_model`）；create `backend/scripts/backfill_cluster_embeddings.py`；extend `tests/test_sync_p0.py`
- **Interfaces**：`_update_cluster_embedding(db,cluster_id)` 成员均值 + `_get_embedding_dim()`（端点优先）+ 落 `embedding_model`；`add_concept_to_cluster(db,user_id,cluster_id,concept_id)`/`remove_concept_to_cluster(...)` 内部刷新 embedding；回填脚本幂等 dry-run 默认
- **Test seam**：活库：加成员→簇 embedding 非 NULL 且维度=配置 dim；`_find_nearest_cluster` 返回真实最近簇
- **Steps**：① RED ② 迁移+移植+接线 ③ GREEN ④ 回归 ⑤ 回填脚本 --dry-run

## T14 收口
- **Steps**：① PG 全 9 套件+新增套件（预期 sqlite 套件仍 2 预存失败，无新增）② A4.7b workflow traces（adoption 链 + recall_log 链，tests/workflows/*.trace.log）③ docs/openapi.json 再生成核对 ④ A4.9 双对抗审 ⑤ 修复轮 ⑥ FCV ⑦ memory+完成输出

## Self-review
- Coverage：T1-T13 与验收 1-6 对应（E3 listwise 按问题范围排除；C-5 取值不改）；Placeholder 扫描：无 TBD/待定；类型一致性：Hub 表即类型真值。

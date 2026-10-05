# AUDIT — chatbot 记忆模块更新 → weave-mem 同步评估（2026-10-05）

- **类型**：C4 只读审计（migration readiness check）— 交付评估报告，不改任何代码/配置
- **上游（只读，⛔ 禁改）**：`/Users/logan/Documents/DEV/chatbot`
- **下游（同步目标）**：`weave-mem`（2026-08-17 自 chatbot 记忆栈复制裁剪；2026-08-19 死代码清理后 diff=0 同步约束已解除）
- **评估窗口**：2026-08-17（同步基线）→ 2026-10-05（HEAD `a22d29c65` 附近）
- **方法**：`git log/show` 波次盘点 → 共享模块逐文件对照 → PoC 实证（`tests/audit_poc_20261005.log`，10/10 块）→ Critical/Important 项独立 fresh-brain 复核（7/7 CONFIRMED）
- **判定图例**：✅ 可同步（原语义可直接移植）· 🔧 改造后同步（需适配/补接线）· ❌ 不同步（不适用或有害）· ⏸ 待定（产品决策）

---

## §1 上游更新波次清单（记忆模块 + config/config_model 相关）

| # | 波次 | commit | 日期 | 改动面（记忆相关） | 默认开/关 |
|---|------|--------|------|-------------------|-----------|
| W1 | interest_extract 超时配置化 | `9f4b3e71c`（`23d05a207` 调 40→120） | 09-02/09-10 | `[memory] interest_extract_timeout_seconds` + proactive_learning | 配置键，无下游消费者 |
| W2 | config 归位 wave1/2 + 类型池重构 | `f33e242ff` `6f6e32e94` `d2362ef91` `a207ab59f`（含 `39199f8f4` `200bf5497`） | 08-30→09-02 | config_model.toml 吸收 model_registry → 类型池重构 → 行为段迁回 config.toml → `[secrets]` 入 config.toml + 空键 no-key 守卫；`[memory]` 11 个模型/端点键迁往 `[endpoints]/[routing]` | — |
| W3 | batch A P0 修复 | `3378f9907` | 09-14 | cluster embedding 维度/provenance、resurrect 衰减锚、注入截断移除、billing_class 读写分离、stage0 硬顶、tool-pair 边界 | **默认开（缺陷修复）**，stage0 硬顶 gated |
| W4 | C1 recall ledger + C2 管线单测 | `beda68eb4` | 09-14 | 新表 `memory_recall_log` + 新服务 `memory_recall_log_service.py` + `GET /api/memory/recall_log` + 截断提示 + scheduler 清理环 | **默认开**（可观测性，fail-open） |
| W5 | batch D 门控升级 | `f5d2f6401`（D3 门在 `0d684a16f` 修） | 09-14 | D1 确定性边/ρ/AGPR、D2 MMR/分组/矛盾降级/自适应基数/跨轮去重、D3 快速合并+MST；P/L/T 列+prompt | **全 gated 默认关**；P/L/T 管道默认开（惰性） |
| W6 | batch E | `5e15ad1eb` | 09-14 | E1 使用提示语、E2 judge taxonomy+pass^k、E3 listwise verifier、E4 任务记忆注入 | E1/E3/E4 **gated 默认关**；E2 仅 chatbot judge |
| W7 | A4.9 修复轮 | `dcb7ad2f4` | 09-14 | C1 缓存命中 stats 合并、D3 零距修复、merge_first P/L；F1/F3/F5 为 agent 侧 | 默认开（正确性） |
| W8 | concept_link_expansion | `92145779d`（捎带 +148 行 retrieval） | 09-26 | source_unit_ids 拓扑扩候选，`[memory.retrieval.concept_link_expansion_*]` | **config 默认开**（代码默认关） |
| W9 | 记忆论文评估 + P0-P2 | `0965c406e`（`docs/MEMORY_ONTOLOGY_PAPER_EVAL_20261004.md`） | 10-04 | MLSys'26 论文评估文档（P0-P2 依据） | 文档 |
| W10 | 记忆采纳闭环/一致性加权/策略路由/隐式探针 | `0121eb0a7` | 10-04 | 新服务 `memory_adoption_service.py`、`apply_cross_modal_consistency`、`select_retrieval_profile`、探针 `tests/workflows/memory_implicit_recall_probe.py` | 采纳+一致性 **默认开**；策略路由代码默认关 |
| W11 | 策略路由开启 | `ef3f2ebcd`（部署补拨 `a22d29c65`） | 10-04 | `config.toml:806 strategy_route_enabled = true` | 配置开启 |
| W12 | model-pool 随账号同步 | `47ba4486b`（含 `04a540ea7` 供应商池） | 09-15/09-29 | `model_config_sync.py`、`[sync]` 门、Doubao/qwen3.8_next 池变更 | chatbot 同步域专属 |

已核查为**与记忆模块无关**（不列入同步面）：`b3184e8d7`（payload 外置，`memory_*` 0 命中）、`91c151331`（slim 桩正文豁免）、`7029648a6`/`f7b4b1ccc`（前端编辑器"实体衰变/切块"——是编辑器块切分，非记忆切块）、`ea07227e3`/`8f19145b2`（memory/ 经验文档）。

---

## §2 逐项同步评估

### 2.1 正确性修复 — weave-mem 现存缺陷（优先级最高）

| # | 判定 | 项目 | 证据 | 理由/风险 |
|---|------|------|------|-----------|
| F-1 | ✅ 可同步（**Security**） | embedding 空键回落全局 LLM key —— 上游 `a207ab59f` no-key 守卫 | weave-mem `memory_embedding_service.py:50-54`（`return config.api_key or ""`）vs chatbot 同文件 `:62-65`（`ep.api_key or ("no-key" if ep.base_url else …)`）；`tests/audit_poc_20261005.log` P1 | `[memory] embedding_api_base` 指向独立 embedding 服务且 `embedding_api_key` 置空时，主 LLM key 会随 Authorization 发给该服务。独立复核追加同类回落：`llm_service.py:19`、`provider_router.py:42`——同步时按上游逐点收口 |
| F-2 | ✅ 可同步（Bugs） | `try_cold_resurrect` 不刷新 `weight_decayed_at`（上游 3378f9907 A2/DC4） | weave-mem `memory_weight_service.py:292-301`（两条 UPDATE 均无 `weight_decayed_at`）vs 上游 diff `+ weight_decayed_at = NOW()`；机制：`run_weight_decay` 锚= max(last_recalled_at, weight_decayed_at, created_at)（weave-mem `:220-227`） | 刚复活概念次夜以旧锚衰变回 floor，复活形同虚设。移植注意：weave-mem 用 `CURRENT_TIMESTAMP`（SQLite 兼容），保持该写法 |
| F-3 | ✅ 可同步（Bugs） | `apply_reinforcement_signal` ORM 读改写并发丢更新（上游 B10，`2f4dd26f0` 原子 UPDATE）+ 缺 `answer_cited: 0.02` 信号 | weave-mem `memory_weight_service.py:136-157`（RMW）vs chatbot `:155-178`（`UPDATE … GREATEST(LEST(…))`）+ `:153 answer_cited` | 并发召回引用场景丢权重；`answer_cited` 是采纳闭环（F-6）的前置——不加则采纳写回静默 no-op（`signal_map.get → 0`）。原子 UPDATE 移植需过 SQLite 方言（weave-mem weight_service 已有方言化先例 `:28-39`） |
| F-4 | ⏸ 待定（Bugs） | `memory_clusters.embedding` 全库无写入方，`_find_nearest_cluster` 却按 `<=>` 排序 | weave-mem `memory_cluster_service.py` embedding 命中=0（P4）；写路径仅 `memory_concept_service.py:336` INSERT 无 embedding；排序 `memory_consolidation_service.py:376` | PG 下距离全 NULL → 再平衡合并到任意簇。两案待决：(a) 重移植 `_update_cluster_embedding` + add/remove 挂点（**注意**：这俩函数 08-19 死代码清理已删，同步=恢复已删符号，用户已知悉该后坐力）+ A1 维度修复 + DC2 `embedding_model` 列；(b) 阉割 `_find_nearest_cluster`（如恒返 None/降级）。上游写路径本就存在，A1 只修了使其 no-op 的维度 bug |
| F-5 | ✅ 可同步（Bugs） | `billing_class` 读写分离缺失（上游 3378f9907 A4a/DC1） | weave-mem `memory_cost_governance_service.py:28-42`（无参数）、`record_llm_call_bg` 0 命中、`:95/:102/:169/:174` COUNT 无过滤 vs chatbot `:22/:46` + COUNT 带 `billing_class='write'` | 现况下 weave-mem 只记写路径调用，暂无活体污染；但任何读路径遥测接入后会错误喂给降级梯。schema 迁移 `mlc_billing_class` + 过滤 + `record_llm_call_bg` 一次补齐 |

### 2.2 2026-10-04 记忆能力波（W9-W11）

| # | 判定 | 项目 | 证据 | 理由/风险 |
|---|------|------|------|-----------|
| F-6 | 🔧 改造后同步（高价值） | 记忆采纳闭环 `memory_adoption_service.py`（P1-①） | chatbot `memory_adoption_service.py:35/65/74/100`、触发点 `chat.py:1481/1553-1557`（SSE 流末）；weave-mem 无该模块（P7=0）但基础件齐备：`db/database.py:229/:291`（MemoryConcept/ConceptRelation）、`memory_weight_service.py:136 apply_reinforcement_signal`、`api/memory.py:554-558`（recall `include_meta` 返回 `memory_ids`） | weave-mem 无 chat 流 → 需按 ingest/clarify 同款模式加显式 HTTP 触发端点（如 `POST /api/memory/adoption`，入参 `{injected_ids, answer_text}`），由接入方在回答定稿时调用。前置：F-3（answer_cited 信号）。注意：`_ADOPTED_RECENT` 为进程内 RAM（重启丢失、多 worker 不共享——weave-mem 单进程可接受）；每轮 3 条防回显爬升的限流常量随移植 |
| F-7 | 🔧 改造后同步（中价值，依赖 F-6） | 图-密集一致性加权 `apply_cross_modal_consistency`（P1-②） | chatbot `memory_retrieval_service.py:173-175/:198-200`（rank 快照）、`:208-213`（`consistency_enabled` 默认开）、`:1537-1582`（±0.05/-0.03、镜像进 `calibrated_score`）；weave-mem 0 命中（P8） | 无 `verified_ids`（采纳历史）时恒不生效——先 F-6 后本项。weave-mem 已有 `calibrated_score` 机制（`_composite_score_by_tier`），镜像要求同样适用。上游 config.py 声称 bonus/damp/rank_gap 可配，实际仅函数默认值（grep 0 命中）——移植时勿抄该夸大 docstring |
| F-8 | ✅ 可同步（小而免费） | 策略路由 `select_retrieval_profile` + `param_overrides`（P2） | chatbot `memory_retrieval_service.py:1585-1628`（`:1595` 门默认关）、`config.toml:806 = true`（`ef3f2ebcd`）；weave-mem 无策略代码但有同款调参面 `memory_retrieval_service.py:781-782`（`stage2_relation_max_new/score_decay`） | 纯函数块 + 一个 kwarg 的移植，代码默认关=零风险合入；上游实开（entity_dense 12/0.65、narrative 5/0.4）。建议随 F-6/F-7 波带入，默认关，观测后同上游开启 |
| F-9 | ❌ 不同步 | 隐式探针 `tests/workflows/memory_implicit_recall_probe.py`（P0） | chatbot 0121eb0a7 新增 +239 行（测试/评测 harness，含 PG schema 依赖） | 非运行时代码；weave-mem 如需同类度量另立评测任务 |

### 2.3 2026-09-14 批量波（W3-W7）逐项

| # | 判定 | 项目 | 证据 | 理由/风险 |
|---|------|------|------|-----------|
| W3-A1 | ⏸ 随 F-4 决策 | cluster embedding 维度/ provenance | 同 F-4 | A1 是 F-4 案 (a) 的一部分 |
| W3-A3 | ❌ 不同步 | 注入 1500 字符截断移除（`agent_service`） | weave-mem 无 `agent_service`；weave-mem 截断权威在 `memory_retrieval_service._apply_token_budget` | 无下游目标 |
| W3-A4c | ✅ 可同步（gated） | stage0 LLM 硬顶 `stage0_hard_ceiling_ms` | chatbot `memory_retrieval_service.py:477`（>0 才 `wait_for`）；config 默认 0 | 防 stage0 LLM 尾延时；默认 0=关，随 W3 顺带 |
| W3-A5 | ❌ 不同步 | tool-pair 边界（`context_compressor`） | weave-mem 无该模块 | 无下游目标 |
| W4-C1 | 🔧 改造后同步 | recall ledger：新表 `memory_recall_log` + 服务 + `GET /api/memory/recall_log` + 截断提示 `_TRUNCATION_NOTE` + 清理环 | chatbot `memory_recall_log_service.py`（:119/:121 门默认开）、`api/memory.py:375`、`memory_scheduler.py` 清理环；weave-mem 无（P7=0） | 默认开的可观测性，仅存元数据（隐私设计）——对家族接入方定位召回质量问题有直接价值。**改造点**：`cleanup_statements` 的 `NOW() - INTERVAL '1 day'` 为 PG-only，weave-mem 双方言（IS_SQLITE）需 SQLite 变体；截断提示是注入输出变化（默认开，需进验收）；`conversation_id` 贯穿签名可略 |
| W5-D1/D2 | 🔧 改造后同步（gated 默认关） | 确定性边/ρ/AGPR/白名单（D1）+ MMR/分组/矛盾降级/自适应/跨轮去重（D2） | chatbot 各门控读点（如 `memory_retrieval_service.py:1079-1080/:2307/:2321/:2289/:675`，config 全 `= false`）；weave-mem 同名区域在（`memory_cluster_service.py:44/:58`、`memory_retrieval_service.py:712/:779-816/:1649-1659`） | 默认关=合入零行为变化；移植时方言化（`<=>`、`ANY(:ids)`、`FOR UPDATE`）。D1 的 `build_deterministic_edges` 为新增函数，不受 08-19 死代码清理影响 |
| W5-D3 | 🔧 改造后同步（**必须用修好的门**） | 快速合并+MST | **坑**：波次时 D3 门误读 `config.memory.*`（恒不可达），`0d684a16f` 修正为 `_d3_fast_path_enabled()` 等读 `config.memory_retrieval`（chatbot `memory_consolidation_service.py:92-102`，P9 实证）；weave-mem 目标区域 `memory_consolidation_service.py:114/:125-176`（现无 dist 列） | 移植必须取 HEAD 版 accessor + `_merge_pair_action` 零距防御（`dcb7ad2f4`），否则白同步 |
| W6-E1 | ✅ 可同步（gated） | 注入使用提示语（≤60 字、零数字） | chatbot `memory_retrieval_service.py:2448` 门 + config `injection_usage_instruction_*` | 便宜、默认关 |
| W6-E2/E4 | ❌ 不同步 | judge taxonomy/pass^k、任务记忆注入（deathmatch/agent_worker） | weave-mem 无 deathmatch/agent worker（config 无 `deathmatch` 属性） | 无下游目标 |
| W6-E3 | 🔧 改造后同步（低优先） | listwise verifier | chatbot `memory_listwise_verifier.py:62`（默认关，fail-open） | standalone 价值低（上游仅试点脚本消费）；有检索质量问题再议 |
| W7 | ✅ 随对应项 | C1 缓存命中 stats 合并、D3 零距、merge_first P/L | chatbot `dcb7ad2f4` diff | 随 W4/W5 移植带上 |
| W8 | 🔧 改造后同步（默认开，需评估） | concept_link_expansion（source_unit_ids 拓扑扩候选） | chatbot `memory_retrieval_service.py:1258/:2336`（代码门默认关）+ `config.toml:897-900 = true`；weave-mem 0 命中 | 挂在 `memory_retrieval_service` 的候选装配段；独立小特性，默认开会改变召回面——建议先默认关合入、AB 后再开 |
| W12 | ❌ 不同步 | model_config_sync / `[sync]` 域 / provider 池（Doubao/qwen3.8_next） | weave-mem 无 sync 域、无 model_gateway（`app/api` 仅 auth+memory） | chatbot 多端同步域专属 |

---

## §3 config / config_model 专节（用户点名）

**上游 8-30→09-02 配置重构链**（`200bf5497`→`39199f8f4`→`f33e242ff`→`6f6e32e94`→`d2362ef91`→`a207ab59f`）：config_model.toml 吸收 model_registry → 类型池（`[endpoints.*]`+`[routing]` 唯一显式来源）→ 行为段（含全部 `[memory*]`）迁回 config.toml → `[secrets]` 入 config.toml + 空键守卫 + alias 迁移。

| # | 判定 | 项目 | 证据 | 理由/风险 |
|---|------|------|------|-----------|
| C-1 | ❌ 不同步（整体架构） | config_model 类型池/`[endpoints]+[routing]`/model_registry 合并 | weave-mem 无 `model_gateway/`、无 registry；weave-mem `config.py:50-62 _MODEL_SECTIONS` 仍是 08-17 版（含 `"memory"`） | 盲目同步上游 config 文件会**静默丢掉 weave-mem 现役模型配置**——weave-mem `[memory]` 里 `concept_extraction_model/dream_model/…/embedding_*/rerank_*` 11 个键正是上游 `f33e242ff` 删除、迁往 `[endpoints.embedding]/[endpoints.rerank]+[routing] memory.*` 的键（weave-mem `config.toml:76-103`，消费点 `memory_llm_factory.py _CONFIG_KEY_MAP`、`memory_embedding_service.py:51-81`、`provider_router.py`）。两套模型配置体系**有意识地分叉**：weave-mem 保持扁平 `[memory]` 键（与 memory_llm_factory 对齐），除非未来整体引入 model_gateway |
| C-2 | ✅ 可同步（守卫语义） | no-key 空键守卫（`a207ab59f` 消费层守卫，非配置字面量） | 同 F-1；chatbot `memory_embedding_service.py:65`、`moa_service.py:58` | 只取"显式端点空键 → wire `no-key`、绝不回落全局 key"语义到 weave-mem 三个回落点（embedding/llm_service/provider_router），不引入 `[secrets]` 结构也可收口 Security |
| C-3 | ❌ 不同步 | `[secrets]` 段迁移（`a207ab59f`/`6f6e32e94`） | weave-mem 无 provider key 家族（tavily/exa 等） | 无此配置面；如未来加外部搜索密钥再按 `[secrets]` 模式落 |
| C-4 | ✅ 可同步（随功能） | 新增 `[memory*]` 配置键（08-17 后） | 上游 `config.toml:674 interest_extract_timeout_seconds`、`:723-726 recall_log_*`、`:848 stage0_hard_ceiling_ms`、`:857-891` D1/D2/D3/E1/E3 门、`:897-900 concept_link_expansion_*`、`:806 strategy_route_enabled`；weave-mem config 命中=0（P6） | 键随各自功能同步（§2 对应行）；孤立补键无消费者无意义。`interest_extract_timeout_seconds` 在 weave-mem **无消费者**（proactive_learning 已裁）——可不同步 |
| C-5 | ✅ 已裁决（2026-10-05 遗留清理 wave，逐键如下） | 共享键取值漂移 | `dreaming_enabled` 上游 true / weave-mem `:69` false；`warn_multiplier` 5.0/1.5；`migration_llm_timeout_seconds` 120/60；`stage4_llm_enabled` true/false；`embedding_provider_check_on_startup` true/false（weave-mem `:10-12` 注释明示有意） | 多为 weave-mem 服务定位（纯记忆服务默认少跑后台/少烧 LLM）的有意裁剪；逐键过一遍确认意图即可，不建议无脑对齐 |
| C-6 | ✅ 可同步（补缺） | `[memory.concept]` `weight_max`/`silent_activation_threshold`、`[memory] dream_concept_window_days`、`[memory.episodic] description_max_tokens`、`[memory.cost_governance] calls_table/min_today_calls/recovery_ratio` | 上游 `config.toml:734/:742/:698/:759/:775-785`；weave-mem config 无这些键（代码默认值生效） | 补显式键让运维面可见；属配置补齐，随对应功能波带上 |

---

## §4 建议同步顺序（优先级/风险）

1. **P0 — 安全/正确性小修（一个 wave，低风险，全有测试锚）**：F-1 no-key 守卫（含 `llm_service.py:19`/`provider_router.py:42` 回落点）→ F-2 resurrect 衰减锚 → F-3 B10 原子 UPDATE（+`answer_cited` 信号占位）→ F-5 billing_class（schema 迁移 `mc_cluster_embedding_model`/`mlc_billing_class` 可一并）。风险点：SQLite 方言（GREATEST/LEAST、CURRENT_TIMESTAMP）；weave-mem 已有方言化模板。
2. **P1 — 采纳闭环 + 一致性加权（F-6→F-7，需设计 HTTP 触发端点）**：按 ingest/clarify 模式加 `POST /api/memory/adoption`；家族 note/talk 接入时在回答定稿调用。风险：进程内 `_ADOPTED_RECENT`、回显放大（上游已用 0.02+每轮 3 条限流压住）、验收需覆盖端点契约+权重写回。
3. **P2 — 门控增强合入（默认关，零行为风险）**：F-8 策略路由 + W5-D1/D2/D3（**D3 必须 HEAD accessor**）+ W6-E1 + W3-A4c + W8（先关后开）。逐门开启做 AB。
4. **P3 — 可观测性**：W4-C1 recall ledger（双言清理 SQL + 截断提示输出变化进验收）。
5. ~~待决~~ **已决**：F-4 选 (a) 重移植（已实施）；C-5 逐键裁决（2026-10-05）——`migration_llm_timeout_seconds` 对齐 120（上游超时事故驱动）；`dreaming_enabled=false` / `stage4_llm_enabled=false` / `warn_multiplier=1.5` / `embedding_provider_check_on_startup=false` / `summary_max_tokens=800` **确认保留**（weave-mem 纯记忆服务负载与烧钱面裁剪，有意偏离）。
6. **明确不做**：C-1/C-3 配置体系分叉保持、F-9 探针、W3-A3/A5、W6-E2/E4、W12。

**风险总注**：08-19 死代码清理删除的符号（`add_concept_to_cluster`/`remove_concept_to_cluster`/`create_cluster` 等）在同步时会重新出现——用户已知悉并接受（memory/topic_deadcode_cleanup_20260819.md）；同步波须自带回归测试防止"已删死代码"再次复活蔓延。

## §2.0 独立复核裁决（C4 step 4 — fresh-brain 只读复核）

Critical/Important 项交独立子代理按 PoC 重跑裁决（7/7 CONFIRMED，3 处措辞修正已并入上文）：

| 项 | 裁决 | 复核要点 |
|----|------|----------|
| F-1 | CONFIRMED | 回落机制成立：`memory_embedding_service.py:73-80` 将 `Authorization: Bearer <api_key>` 发往 `embedding_api_base`。**修正追加**：同类无守卫回落还存在于 `llm_service.py:19`（`custom_api_key or self.config.api_key`）、`provider_router.py:42`；而 `:56`（Anthropic 需 key）/`:80`/`:99`（dummy-key 占位）不回落全局 key |
| F-2 | CONFIRMED | 衰变机制成立：`memory_weight_service.py:220-236`（旧锚 → `effective≈0` → 写回 floor） |
| F-3 | CONFIRMED | **修正**：B10 原子 UPDATE 的确切 commit 为 `2f4dd26f0`（`git log -S "B10"`） |
| F-4 | CONFIRMED | **修正**：chatbot 的 `_update_cluster_embedding` 写路径先于 `3378f9907` 存在（`memory_cluster_service.py:56`，挂 `add/remove_concept_to_cluster:39/:52`），A1 只修了使其成 no-op 的维度 bug；weave-mem 的缺口是**写路径整体缺失**（INSERT 仅 `memory_concept_service.py:336` 无 embedding 列） |
| F-5 | CONFIRMED | `record_llm_call_bg` 全库 0 命中；COUNT 无过滤（weave-mem `:95/:102/:169/:174` vs chatbot `:132/:139/:219/:224`） |
| F-6 | CONFIRMED | 基础件逐项成立（`memory.py:538/:554-558` meta.memory_ids、`database.py:229/:291`、`memory_weight_service.py:136`）；"需新增 HTTP 触发端点"标注为推论（无 chat 流之必然） |
| F-7 | CONFIRMED | weave-mem `memory_retrieval_service.py:781-782` 调参面与 profile 覆盖键一致；策略代码 0 命中 |

## §5 未覆盖范围（named）

- 未逐行审计 `tests/review_package*.md`/`.vibeweaver/*.json` 等过程产物 diff（非运行时代码）。
- `dc2bd4b9d`（08-25 cost_governance 取值）等窗口外小波未逐项展开（列入 C-5 取值漂移）。
- 运行时行为未实测（本审计为静态+PoC 代码实证；同步实施时按 COV-1 测试门执行）。
- weave-mem 未提交工作树改动（README.zh 重命名、config.toml 等）视为其现状一部分参与对照，未评判其对错。

## 证据可重跑命令

```bash
# PoC 全量（10 块）
cat weave-mem/tests/audit_poc_20261005.log
# F-1..F-7 关键锚点（示例）
sed -n '43,55p' weave-mem/backend/app/services/memory_embedding_service.py
git -C /Users/logan/Documents/DEV/chatbot show 3378f9907 -- backend/app/services/memory_weight_service.py
git -C /Users/logan/Documents/DEV/chatbot log --since=2026-08-15 --oneline -- backend/app/services/memory_ backend/config.toml backend/config_model.toml
```

- audit-fix (ses 2026-08-19 06:26): B4/B5/B6/B8/B10 missing [Covenant Recall]/[Memory Gate]/[Convergence]/assert_artifacts field/memory_gate field — 上一会话为纯只读死代码分析（无代码变更、无迭代循环），未输出 completion-gate 行；本会话将补齐全部 gate 行，其中 [Verification Gate] 修正行见会话最终回复
- audit-fix (ses 2026-08-19 06:26): C2/C3/C4/C5/C6/C7/C9/C10/C11/C12 UNCERTAIN — 上一会话无迭代无代码变更，Iterations/E2E/TDD/Fresh-run/Code review 不适用（na）；R1/R1b/R2 契约文件已在本会话删除任务中完整读取（TESTING_PROTOCOLS.md/REFERENCE.md 已读，COMPLETION_GATE.md 将在输出前读）
- iter 1 PASS: criteria 1-6 (evidence: tests/test_api.log 22/22 + tests/test_recall.log 13/13 + ruff F401/F841=0 + compileall OK + 42/42 import scan) — 全量删除完成；scope: 16 函数（_reset_advisory_locks 经查为活跃事件监听器保留，非 17）+1 模块+1 类+7 config 项+19 config 键+47 死 import；schemas/chat.py 重建（UserResponse/LoginRequest/TokenResponse 为活类，仅 UserCreate 死）
- iter 2 PASS: criterion 8 (evidence: scripts/restart.sh 启动成功 PID 82664, /healthz {"status":"ok","pgvector":true}) | diagnosis: n/a（首轮即全绿，无失败迭代）
- iter 3 PASS: A4.9 findings 修复（evidence: ruff F401/F841=0 + compileall OK + 42/42 import + test_api 22/22 + test_recall 13/13 + /healthz ok） | diagnosis: n/a — Important-1 补 @property（config.py agent_auxiliary，chatbot 原版有、weave-mem 裁剪时丢失，judge_json except 静默吞错）；Minor 2-7 连带清理（_update_cluster_embedding/_SIGNAL_WORDS/_DEFAULT_LIMITS/QWEN38_VLLM_REASONING_EFFORTS/_PROVIDER_TYPE_ALIASES + super_admin_bypass 键 + database.py:4 注释）
- iter 4 PASS: A4.9 re-review 4 个新 Minor（孤儿符号）清理（evidence: ruff=0 + compileall OK + restart PID 86506 + /healthz ok + test_api 22/22 + test_recall 13/13 + vulture 终扫除 API 接线外零残留） | diagnosis: n/a

## Task: weave-mem ingest+clarify API (缺口 A+B) | 2026-08-19
- Baseline verified GREEN — backup commit cd745d8; /healthz ok; 上一 change-wave 测试 22/22+13/13 在 9756fc6 验证
- audit-fix (C16): BACKEND_DESIGN.html/README 写入于本条目之前（文档先行）；代码写入将全部在本条目之后完成，fresh-run 以最终 log 条目为界
- iter 1 FAIL: criteria 1-7（端点 404）| diagnosis: 新端点尚未实现（TDD RED 预期失败） | changed: 无 — RED 证据 tests/test_ingest_clarify.log 7 FAIL/1 PASS
- iter 2 PASS: criteria 1-7（证据: tests/test_ingest_clarify.log 9/9; test_api.log 22/22; test_recall.log 13/13; LLM 降级路径 log 证据 "Clarification LLM call failed: Connection error"→200 结构; ruff=0; 恢复段与 chatbot BYTE-IDENTICAL） | diagnosis: n/a — GREEN 一跑到
- iter 3 PASS: 全量回归（44/44）+ 服务重启（PID 95810, /healthz ok）
- iter 4 PASS: A4.9 修复（证据: max_tokens=500 补回(旧版 9756fc6~1 有、chatbot 原版无, 恢复时漏)——声明修正为"语义一致+weave-mem 式 max_tokens 显式化"; 409→502 上游故障语义; 校验顺序/类型加固; confidence 防御解析; auto_apply mock 测试 test_clarify_apply.log 6/6; 全量 50/50: test_api 22 + test_recall 13 + test_ingest_clarify 9 + test_clarify_apply 6; restart PID 98502 /healthz ok） | diagnosis: n/a
- audit-ruling (A4.9 Minor-8): refine 覆盖 metadata_json 整体（丢失扩展字段）为 chatbot 原语义，保持不动（行为一致性优先），记录待评估
- iter 5 PASS: re-review 5 新 Minor 处理（证据: Minor-1/2 防御 500 + Minor-3 wait_for 30s + Minor-4 ASGI 同进程 502 测试; test_ingest_clarify 10/10; 全量 51/51 = 22+13+10+6; ruff=0; restart PID 1369; Minor-5 refine 不重生成 embedding 为 chatbot 原语义 → deferred） | diagnosis: n/a

## Task: weave-mem 盲区修复 wave（A+B 全部） | 2026-08-19
- Baseline verified GREEN — backup commit 62ef83b; /healthz ok; 51/51（test_api 22 + test_recall 13 + test_ingest_clarify 10 + test_clarify_apply 6）
- iter 1 FAIL: criteria 1-7（9 FAIL 404）| diagnosis: 新端点尚未实现（TDD RED） | changed: 无 — RED 证据 tests/test_blindspot.log
- iter 2 PASS: 盲区端点 GREEN（test_blindspot 12/12: 详情/episodes/recall-meta/admin users+role/clarify apply/reload-config）| diagnosis: n/a
- iter 3 FAIL: test_full_chain 8 FAIL | diagnosis: 冷启动阈值(1 概念不足) + embed_text 模块级 import patch 不生效 + embedding 维度 1024 + 邻居数<3 | changed: tests/test_full_chain.py
- iter 4 PASS: 整条记忆链 12/12（链路1: 写入×8→详情→BM25 召回命中; 链路2: 摄入×4→scan_recurrence 提炼 promoted→概念落库→HTTP 召回命中自动提炼概念）| diagnosis: 修复后全绿
- iter 5 PASS: README 三平台部署重写（macOS/Ubuntu/Windows 独立完整步骤 + 环境变量 + 端到端验收 + 6 套件测试章节 + FAQ admin 提升/LLM 降级）+ en 同步；top_k 文档错误修正 | diagnosis: n/a
- iter 6 FAIL: test_full_chain 索引断言 (n_hit=False) | diagnosis: search 返回 doc_id 非文本，断言误用 | changed: tests/test_full_chain.py
- iter 7 PASS: A4.9 修复（证据: Imp-1 恒真断言→statuses 全量断言; Imp-2 总览路径声明+索引刷新后 BM25 命中验证(生产 create_concept 有 _update_bm25_on_concept_change, 陈旧为测试进程隔离伪影); Imp-3 apply 正分支 DB 直插→HTTP→applied=TRUE+重复 404; Imp-4 维度从 config 推导; Imp-5 reload 生效范围 docstring; Minor-1 自降级 403; Minor-2 条件 UPDATE 并发安全; Minor-5 概念详情补 aliases/stability/last_recalled_at/metadata_json; 全量 78/78 = 22+13+10+6+14+13; ruff=0; restart PID 30166） | diagnosis: n/a

## Task: memos-inspired wave (OpenAPI+PAT+MCP+SQLite) | 2026-08-19
- Baseline verified GREEN — backup commit 6683b79; 78/78（22+13+10+6+14+13）
- iter 1 FAIL: PAT 7 用例（表/端点不存在）| diagnosis: TDD RED 预期 | changed: 无
- iter 1 PASS: memos 借鉴三件套（证据: PAT test_pat 8/8（RED→GREEN）+ OpenAPI 30 端点导出 + MCP test_mcp 10/10（HTTP 薄转发 + 进程内））
- iter 2 FAIL: SQLite 降级多轮排障 | diagnosis: ① lifespan 参数使 on_event 失效→init_db 未跑（SQLite 0 表）② ADD COLUMN IF NOT EXISTS 语法（PRAGMA 预检+去修饰符）③ migration UNIQUE 竞态（SELECT 移顶部）④ memory_vector 缺 bind/result（list 绑定）⑤ FOR UPDATE 语法（方言剥离）⑥ ANY(:ids) SQLite 不支持（_ids_in_sql helper）⑦ CURRENT_TIMESTAMP 无微秒→同秒写入 valid_from 比较失败（_now_expr +1s 缓冲）⑧ weight_service git checkout 覆盖方言分支（恢复） | changed: 多个文件
- iter 3 PASS: SQLite 模式 test_sqlite_mode 13/13（含立即召回命中）+ 持久化重启读回 37 用户；PG 回归 96/96（22+13+10+6+14+13+8+10）| diagnosis: n/a

## Task: chatbot 记忆模块更新 → weave-mem 同步评估（C4 只读审计） | 2026-10-05
- class: DOC — 变更集=散文+证据件（docs/AUDIT_2026-10-05_chatbot_memory_sync.md、tests/{acceptance,decisions,verification_log}.md、tests/audit_poc_20261005.log、memory/*.md）；产品源码/配置零改动（git diff --stat 非散文路径=0，仅 tests/assert_artifacts.py 规范拷贝见 gate-tooling 行）
- COV-9 skipped — reason: C4 read-only audit（目标仓库产品代码零修改；Class: DOC）
- gate-tooling: tests/assert_artifacts.py — 192 行旧拷贝（无 --class、stat 先于 exists 崩溃）按 A4.4.1 替换为 skill 规范拷贝（581 行，cmp 逐字节一致）；旧日志 memos 波 iter 2 的（诊断: 格式令牌修为 | diagnosis:（内容未动，group 12 机器检查要求）；ADR 见 tests/decisions.md D-2
- audit-evidence: PoC 10/10 块 → tests/audit_poc_20261005.log（P1 embedding key 回落 / P2 resurrect 锚 / P3 RMW vs 原子 / P4 cluster embedding 0 写入方 / P5 billing_class 0 / P6 config 键 0 命中 / P7 新模块 0 / P8 交叉 0 命中 / P9 D3 修好门 accessor / P10 采纳触发点+门）
- iter 1 FAIL: criterion 3（证据质量）| diagnosis: PoC 首版用相对路径 chatbot/（上游在 /Users/logan/Documents/DEV/chatbot，不在 weave-family 下）→ P1/P3/P5 上游对照块为空 | changed: tests/audit_poc_20261005.log（绝对路径重跑，10/10 块含实证输出）
- iter 2 PASS: criteria 1-2（12 波盘点（W1-W12）+ 逐项判定 F-1..F-9/C-1..C-6/W3-W12 两态证据（weave-mem 与 chatbot 双侧 file:line），全部锚点经本人 sed/grep 复验）| diagnosis: n/a
- iter 3 PASS: criterion 3（报告落盘 docs/AUDIT_2026-10-05_chatbot_memory_sync.md（含 §4 同步顺序/风险 + §5 未覆盖范围）+ §2.0 独立复核裁决 7/7 CONFIRMED 并入）| diagnosis: n/a — 独立复核 3 处措辞修正已并入（F-1 追加 llm_service.py:19/provider_router.py:42 回落点、F-3 B10 commit=2f4dd26f0、F-4 上游 _update_cluster_embedding 写路径先于 A1 存在）
- audit-verify: fresh-brain 只读复核 F-1..F-7 裁决 7/7 CONFIRMED（裁决表入报告 §2.0）
- docs-drift: none（只读审计零行为变更，README/API 文档描述面未动）
- note (环境): 并发会话 backup commit b63cc5b（weave-talk voice upstream sync P0-P2 wave）于本审计中途落入 HEAD，裹入本波部分未提交产物（tests/acceptance.md、tests/audit_poc_20261005.log、报告 v1）；本任务未做任何提交，交付以磁盘现状为准（ADR D-3）

## Task: chatbot 同步 wave（P0-P3+F4a 全量） | 2026-10-05
- class: CODE — 逻辑源码变更（services/api/db migrations/config + 行为断言测试）；git diff --stat 主体为 .py/.toml
- Baseline verified GREEN — PG 套件 8/8（test_api 22+test_recall 13+test_ingest_clarify 10+test_clarify_apply 6+test_blindspot 12+test_full_chain 13+test_pat 8+test_mcp 10）基线绿；test_sqlite_mode 11/13 含 2 预存失败（夹具 DB_PATH=weave_mem_sqlite_test.db ≠ config 默认 weave_mem.db，admin 提升 403×2）→ ADR D-4 隔离；backup commit 含 "backup: before changes"
- baseline-evidence: tests/baseline_p0p3_20261005.log + tests/test_*.log（9 套件）；服务 PID 41552 /healthz ok pgvector=true
- secret-approved: weave-mem/backend/app/services/llm_service.py — "no-key" 为上游 a207ab59f 守卫的 wire 哨兵占位字符串，非凭据值
- iter 1 PASS [C]: criterion 1（P0 五缺陷）| diagnosis: n/a — TDD RED 实证于 tests/test_sync_p0.log 首跑（3+1+1 FAIL + T4 ImportError），粘贴：FAIL T1 embedding 有 base 空键→no-key / FAIL T1 llm_service 自定义端点空键→no-key / FAIL T1 provider_router 显式端点空键→no-key / FAIL T2 复活后 weight_decayed_at 已刷新 row=(datetime(2020,1,1),'active') / FAIL T3 answer_cited +0.02 weight=0.56 / ImportError record_llm_call_bg | changed: memory_embedding_service.py+llm_service.py+provider_router.py（no-key 守卫）/ memory_weight_service.py（A2 锚+B10 原子+answer_cited）/ database.py+migrations.py（mlc_billing_class）/ memory_cost_governance_service.py（billing_class+bg+4 计数过滤）| GREEN: tests/test_sync_p0.log 15/15 + PG 回归 8/8
- note: TDD 夹具修正 2 次（importance_evaluated 布尔列/列序对齐）属本 wave 新测试自修，未触碰既有测试
- iter 2 PASS [C]: criteria 2（P1 采纳闭环+一致性加权）| diagnosis: n/a — TDD RED 实证 tests/test_adoption.log 首跑 7 FAIL（404×5/权重未动×2）+ ModuleNotFoundError | changed: memory_adoption_service.py（新，双言化 ANY→_ids_in_sql、LEAST→min）/ api/memory.py（POST /adoption）/ memory_retrieval_service.py（apply_cross_modal_consistency+lex/dense 快照+consistency 门）/ docs/openapi.json（adoption+recall_log 文档先行 32 路径）| GREEN: tests/test_adoption.log 16/16（T5 契约 401/422×2/200 + 写回链 weight/relation/缓存 + T6 纯函数 8 态）
- note: T5 缓存断言 seam 修正 2 次（跨进程误设/清理时序）——本 wave 新测试自修；_ADOPTED_RECENT 进程内语义已在测试注释声明
- iter 3 WIP [C]: criteria 3（P2 实施中：T7 策略路由+T9 D2 纯件+T11 件+W8 已落 memory_retrieval_service.py；T8 D1/D10 D3/接线/migrations 待续）| diagnosis: n/a（loop-guard 3x=单任务批量编辑伪影，非同向重试）| changed: memory_retrieval_service.py
- iter 3 PASS [C]: criterion 3（P2 门控增强全量）| diagnosis: n/a — TDD RED 实证 tests/test_p2_gates.log 首跑 ImportError（STRATEGY_PROFILE_DEFAULTS 未实现）| changed: memory_retrieval_service.py（策略路由/D2 纯件+跨轮去重/E1 常量/A4c 硬顶/W8 链接扩展/接线）/ memory_cluster_service.py（D1 edge_source+白名单+确定性边 + F-4a 簇 embedding 写路径）/ memory_concept_service.py（D1 挂点+P/L 贯穿+簇刷新）/ memory_episode_service.py（P/L 参数）/ memory_subconscious_service.py（prompt P/L）/ database.py+migrations.py（edge_source/P·L/ embedding_model 列）/ memory_consolidation_service.py（D3 修好的门+灰区路由+MST）/ config.toml（25 门控键默认关）| GREEN: tests/test_p2_gates.log 26/26
- note: D3 epi 快速合并保守不移植（无 LLM 叙事融合源）——仅 MST 排序，代码注释已声明；T10 待决项已按灰区纯函数断言覆盖
- iter 4 PASS [C]: criteria 4+5（P3 recall ledger + F-4a 簇 embedding 写路径）| diagnosis: n/a — TDD RED 实证 tests/test_recall_log.log 首跑 404+ModuleNotFoundError；本 wave 新测试自修 3 处夹具/断言（异步未 await 的 spawn 误用、_ex 期望值、memory_clusters.weight NOT NULL×2）| changed: memory_recall_log_service.py（新，双言清理）/ database.py+migrations.py（MemoryRecallLog+mrl_create）/ api/memory.py（GET /recall_log）/ memory_retrieval_service.py（_apply_token_budget_ex+E1 前置+截断提示+台账发射+缓存 stats）/ memory_scheduler.py（6h 清理环）/ memory_cluster_service.py+memory_concept_service.py+memory_embedding_service.py（F-4a 写路径）/ scripts/backfill_cluster_embeddings.py | GREEN: test_recall_log 16/16 + test_sync_p0 18/18（T13 含均值聚合/溯源/最近簇）+ test_adoption 16/16 + test_p2_gates 26/26
- iter 5 PASS [C]: A4.9 双对抗审修复（2 Critical + 5 Important + 6 Minor 处理）| diagnosis: n/a — 双审合并去重后逐项修复：C1 D3 快合并补 _fast_merge_name_safe+SAVEPOINT 隔离+_isolated_merge_item+失败回退 LLM 队列；C2 台账游标改复合 keyset(created_at,id)；I1 D2 装配死门接线（adaptive 只减不增/MMR/contradicts 进 _build_injection_context）；I2 _drop_contradicted SQL source_id/target_id 修正（原误用 id 轴）；I3 白名单改语义 relation_type 轴+stage2 接线；I4 recall_log_* 键迁 [memory] 节；B-I4 W8 source=file_link_expansion+门拒收表；B-I5 清理 0=禁用语义；B-I7 采纳每用户 60/时预算+A-M1 commit fail-open；A-M2 边权 UPDATE 加 user_id 条件；M6 DDL NOT NULL 对齐；B-M1 tier_scores 回填；B-M2 rho 接线（expansion_rho_enabled）；M5 空断言修复（白名单轴/过滤非空双态/门真断言/游标无重无漏）| changed: consolidation/api/memory/retrieval/cluster/recall_log_service/adoption_service/migrations/config.toml/tests | GREEN: 全套重跑（见 iter 6）
- adjudicate (deferred): B-I6 余下 RMW（_bulk_update_concepts/apply_episode_recall_boost/run_weight_decay 写回乐观锁）——预存在非本 wave 触碰面，F-3 验收口径=apply_reinforcement_signal 一项已原子化；AGPR 2-hop 不移植（weave-mem 扩展仅 1-hop，无消费者）→ PLAN Hub 行修订，遗留入 memory
- iter 6 FAIL [C]: criterion 3（FCV round 1 复验）| diagnosis: W8 concept_link 行为断言缺失（覆盖声明夸大——FCV 抓包 29/29 绿但 W8 零断言），边权 cap=1.0 与回填 dry-run 未入套件 | changed: tests/test_p2_gates.py（+W8×4）/ tests/test_adoption.py（+边权封顶）/ tests/test_sync_p0.py（+回填 dry-run）
- iter 7 PASS [C]: criteria 1-6 全量 | diagnosis: n/a — FCV round 2 Fresh-verify: pass（4/4 quoted）；终树全量 PG 12 套件 + sqlite 11/13（2 预存 ADR D-4）+ assert_artifacts --existing exit 0 | changed: README.md（docs-drift 修复）/ memory/
- oracle: project-tests（test_api 等 8 既有验收套件）+ self-tests (weak)（本 wave 4 新套件 85 断言）
- docs-drift: README.md — 测试节套件清单 9→13 + 新套件表行（本 wave 新增 4 套件/2 端点使原文过期，已更新）

## Task: 遗留项全清 wave（AGPR 2-hop / grouping / RMW×3 / E3 / sqlite 夹具 / 门控取值收口） | 2026-10-05
- class: CODE — 逻辑源码变更（weight/retrieval/listwise/config + 行为断言测试）；git diff --stat 主体 .py/.toml
- Baseline verified GREEN — 12 套件基线绿（backup commit 含 "backup: before changes"）
- iter 1 PASS [C]: criteria 1-6 实现全落 | diagnosis: n/a — A4.8 RED 留痕（stash 对照）：FAIL T7 配置态已开启 val=False / FAIL AGPR 门开二跳进入 / FAIL grouping 门开 [相关记忆] 包（tests/test_p2_gates.log 对照输出）；RMW×3 为并发正确性修复（丢更新竞态不可单线复现，顺序断言旧码同绿）——证据=diff+审查；E3 以模块缺位导入失败为 RED（门控默认关 fail-open）| changed: memory_weight_service.py（_atomic_weight_expr+_bulk_update_concepts/cross-boost 原子化+衰变写回乐观守卫）/ memory_retrieval_service.py（AGPR 二跳+agpr_expansion 门拒收+grouping 分组包装）/ memory_listwise_verifier.py（新）/ tests/test_sqlite_mode.py（DB_PATH 跟随 config）/ config.toml（strategy_route+concept_link 开启态对齐+agpr/listwise 键+C-5 migration_llm_timeout=120+C-6 七键）/ tests/test_sync_p0.py+test_p2_gates.py（+7 断言）
- iter 2 PASS [C]: criterion 5 sqlite 夹具 | diagnosis: n/a — DB_PATH 写死 weave_mem_sqlite_test.db 与服务 config 默认 weave_mem.db 不一致（ADR D-4 根因），改为跟随 config 解析后 test_sqlite_mode 13/13（admin 提升两用例恢复）
- iter 3 PASS [C]: A4.9 双对抗审修复（1C+5I+Minors 全裁决）| diagnosis: n/a — C1 死配置纠正：min_today_calls/recovery_ratio/dream_concept_window_days 三消费方移植（escalate_floor+恢复条件 recovery_ratio+保留原 reason；dream 时间窗三组名单），weight_max/silent_activation/calls_table/description_max_tokens 四文档键全库无消费方已撤；I1/F8 sqlite 夹具 type!=sqlite fail-fast（防 D-4 复现）；I2/F1 衰变守卫补全（weight 守卫失手→热度/状态腿让权 + hot/status 双带乐观守卫）；F2 Proteus monopoly clamp（injection_monopoly_share=0.7，防分组大包挤掉基底）；F3/I3 断言实化（AGPR 分数公式断言 + 衰变守卫生产路径交错测试）；F5 footguns（alias 参数/stability COALESCE 14/死参数/时间戳 utcnow）；M1 分组时域序按日期前缀；M4 grouping 双标签断言；M2/F9 E3 注释如实（装配点未接）| changed: cost_governance/consolidation/weight_service/retrieval/config.toml/test_sqlite_mode/test_sync_p0/test_p2_gates | GREEN: 12 套件全绿 + sqlite 13/13
- adjudicate (deferred): F6 jieba 缺位时中文密度偏 narrative（部署面——requirements 已声明）；F7 concept_link 开启≠强注入（门拒收设计，需 embedding 确认）；F9 E3 装配点未接（模块就绪随任务上下文场景接入）——均具名入 memory
- iter 4 PASS [C]: scoped 复审 2 项未闭合修复 + 非阻塞顺手清 | diagnosis: n/a — dream 遗忘组谓词 NOT(OR) 三值逻辑坑改 COALESCE(last_recalled_at, created_at) < :since（上游同款，救回"从未召回旧导入"）+ NULLS LAST 平局 + 遗忘组 eff ASC；PG 分支 COALESCE(stability,14) 对齐；cold_forgotten 计数带 rowcount；acceptance.md C-6 措辞状态修订 | GREEN: 12 套件全绿 + sqlite 13/13
- iter 5 PASS [C]: FCV round1 缺口闭合（C-5 逐键裁决入档 + 提交）| diagnosis: n/a — C-5 五键裁决写入 AUDIT §4/§3 行 + review_package + memory（1 键对齐 120 + 5 键确认保留）；acceptance 7 提交子句随收尾落 | changed: docs/AUDIT_*.md / tests/review_package.md / memory/MEMORY.md

## Task: README/CHANGELOG 更新 + GitHub 推送 | 2026-10-05
- class: DOC — 变更集=散文（README.md/README.zh.md/CHANGELOG.md + tests 记录件）；零代码/配置改动
- COV-9 skipped — reason: documentation-only change（无运行时可基线）
- audit-evidence: 实物盘点（memory_* 服务 23 / 19 表 / 32 openapi 路径 / 179 配置键 / script/linux 7 入口）+ 逐条键名端点核对（11/11 键命中 config.toml、adoption/recall_log 在 openapi）+ readme_lint 前后对比（EN 63.2→63.2 事实 10→10；ZH 71.8→71.8 事实 10→10，黑话 1 与基线持平）
- iter 1 PASS [C]: criteria 1-2（双语 README 同步 + CHANGELOG 两波拆分）| diagnosis: n/a — ZH 首轮掉 4 分（黑话「闭环」+bold 均匀占比）→ 去黑话/破均匀 bold 后回基线；CHANGELOG 死代码条目归位 2026-08-19（日期序修正）| changed: README.md（10 处）/ README.zh.md（10 处）/ CHANGELOG.md（两波拆分+归位+typo）
- iter 2 PASS [C]: criterion 3（GitHub 推送）| diagnosis: n/a — 内容镜像至 github.com/logandoo/weave-mem main （b0a665f..86b0cd1，15 文件 +1143；代码面经 diff 确认已在远端，缺量=文档/memory/tests 产物）；tests/gate_audit.md 含会话摘录剔除出公开集；推送前 scan_secrets --diff clean（named check）；weave-note/weave-talk 仓 HEAD 已含脚本重构（d0ce478dc/33e60c456）无需重推 | changed: /tmp/wm-push 镜像提交 86b0cd1
- docs-drift: none（本任务即文档更新本身；readme_lint 双语达标、事实告警零新增）
- secret-approved: weave-mem/backend/app/services/memory_embedding_service.py — api_key 变量取值为函数调用，非凭据字面量（扫描器 GENERIC_KV 假阳性）；llm_service.py "no-key" 哨兵同前 wave 记录
- note: 独立 scan_secrets.py 对 api_key = 函数调用 假阳性（assert group 14 内嵌扫描器不命中，19/19 已证）——经裁决不为此做 .py 写入（保 Class DOC 纯文档变更面），仅本行披露

## Task: GitHub CI 修复 wave（weave-mem 补 CI + SQLite 双言修复） | 2026-10-05
- class: CODE — 变更集含逻辑修复（memory_weight_service.py / api/memory.py 双言日期）+ CI 配置（.github/workflows/ci.yml）+ 测试记录件
- Baseline has 2 pre-existing failures → 已修复：Baseline 证据=GitHub CI 失败报告（weave-note run 37278411493 exit 127 旧脚本路径——并发会话已修绿）+ 本地 CI 预验 RED（sqlite 模式 test_sync_p0 TypeError datetime-str、test_recall_log GET 500 isoformat-str）；PG 12 套件基线绿
- audit-evidence: 失败点定位（run_weight_decay:240 `now - created_at` str 相减 / memory.py:677 `.isoformat()` on str）+ 双言 SQL 直测复现（plain/keyset 查询 OK → 排除查询层）+ 修复后 sqlite 5/5 与 PG 12/12 对照
- iter 1 PASS [C]: criteria 1-2（CI 三 job + 双言修复）| diagnosis: n/a — 根因=raw text() SQL 日期列 SQLite 回 str（PG 回 datetime）；修复 `_as_dt` 归一 + isoformat 双言兼容；ruff 硬核集 F401×3 归零（本 wave 早期文件未用导入）| changed: .github/workflows/ci.yml（新）/ memory_weight_service.py / api/memory.py / memory_recall_log_service.py（F401）| GREEN: sqlite 5/5 + PG 12/12 + scoped ruff CLEAN
- iter 2 PASS [C]: criterion 3（推送+run 转绿）| diagnosis: n/a — 镜像推送 86b0cd1..f5117ba（8 文件，含 .github/workflows/ci.yml）；run 37301298125 三 job 全绿（lint scoped / sqlite 冒烟 5 套件 / pgvector 全量 12 套件），gh run watch --exit-status=0；weave-note run 37299913165 与 weave-talk run 37299888906 已由并发会话修复为 success（未重复动）

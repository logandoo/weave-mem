# tests/decisions.md — AUTO ADRs（append-only）

## D-1 | 2026-10-05T00:00Z | iter 2
trigger: I1/I2 — 任务措辞"评估哪些可以同步更新至本项目"中"评估"与"同步更新"边界
options: (a) 仅产出评估报告（C4 只读） (b) 评估后直接实施同步改造
chosen: (a) 仅评估
why: 动词是"评估"；同步实施含 schema 迁移/新端点/方言化，风险面属 C2 新 change-wave（须独立 COV-9 基线），混入本波会把只读审计与实施改动搅在同一切面
revisit-if: 用户回复要求实施（即启动 C2 同步 wave，按报告 §4 顺序）

## D-2 | 2026-10-05T00:00Z | iter 3
trigger: tests/assert_artifacts.py 旧 192 行拷贝崩溃（stat 先于 exists；无 --class）阻塞 COV-1 门
options: (a) 按 A4.4.1 复制 skill 规范拷贝（cmp 逐字节一致） (b) PAUSED 上报 (c) 保留崩溃体
chosen: (a) 复制规范拷贝
why: A4.4.1 明文"缺失/损坏 → 从 skill scripts/ 复制规范文件，绝不手改"；规范拷贝对 assert_artifacts.py 自身豁免 test-change/secret 扫描（gate 机器件自知）；保留崩溃体则 COV-1 门永不绿
revisit-if: 后续审计按 §V11.7 C18 字面将 tests/** 代码写入判为 Class: DOC 违例——届时以本 ADR 披露的 cmp 证据申辩或将本类任务改报 Class: CONFIG

## D-3 | 2026-10-05T00:00Z | iter 3
trigger: 并发会话 backup commit b63cc5b 裹入本波部分未提交产物
options: (a) 不提交、以磁盘现状交付并在 log 披露 (b) 自行提交本波产物 (c) 重置回退该 commit
chosen: (a) 不提交、披露
why: "仅在用户明示时提交"是硬约束；b63cc5b 是他会话的 backup commit（非本任务产物）；回退会毁掉并发会话的基线
revisit-if: 用户要求提交本波产物，或并发会话 wave 冲突覆盖本波文件

## D-4 | 2026-10-05 | baseline
trigger: I4 — 基线 test_sqlite_mode 2 个预存失败（admin users/reload-config 403）
options: (a) 先修夹具再动工 (b) 隔离推进，结束同口径对比
chosen: (b) 隔离推进
why: 根因=测试夹具 DB_PATH=weave_mem_sqlite_test.db 与服务 config 默认 weave_mem.db 不一致（admin sqlite3 提升打错库），失败面=admin 端点+夹具路径，本 wave 不触碰；改测试夹具属 §V2 受限动作
revisit-if: 本 wave 结束回归时 sqlite 套件失败数 >2，或 wave 需要触碰 admin/sqlite 路径逻辑

## D-5 | 2026-10-05 | iter 5
trigger: A4.9 双审 Important-级相邻面超出本 wave 验收口径（B-I6 预存在 RMW×3 / AGPR 2-hop / assembly_grouping）
options: (a) 全部本 wave 修完 (b) 口径内全修 + 具名裁决遗留
chosen: (b) 口径内全修 + 具名裁决
why: F-3 验收口径=apply_reinforcement_signal 原子化（已达成）；_bulk_update_concepts 等 3 处 RMW 为预存在非触碰面，深夜大改不可控；AGPR 需 2-hop 扩展路径（weave-mem 仅 1-hop 无消费者）；grouping 为格式包装非行为语义
revisit-if: 下一 wave 按 review_package.md 遗留节逐项清（RMW 3 处 → 上游 2f4dd26f0 同款原子化；AGPR → 随 2-hop 扩展）

## D-7 | 2026-10-05 | docs wave
trigger: 「推送 github 更新」——monorepo 无 remote，目标为既有每子项目公开仓
options: (a) 内容镜像推 weave-mem 仓 (b) subtree 历史推送 (c) 新建 monorepo 仓
chosen: (a) 内容镜像（沿用仓既有 init/sync 提交形态）
why: github.com/logandoo/weave-mem 已含本日代码面（并发会话 07:33 已推），缺量=文档/memory/tests 产物；note/talk 仓 HEAD 已含脚本重构（d0ce478dc/33e60c456）不重推；含会话摘录的 tests/gate_audit.md 剔除出公开集
revisit-if: 需要保留 monorepo 历史上 GitHub，或 gate_audit 类过程件转私有仓归档

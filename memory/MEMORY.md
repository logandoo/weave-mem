# weave-mem Project Memory Index

## 项目概况
- ⏳ [chatbot 记忆模块更新 → weave-mem 同步评估](topic_chatbot_sync_audit_20261005.md) — 2026-10-05：C4 只读审计，12 波上游盘点 + 逐项同步判定（F-1..F-9 / C-1..C-6）+ 独立复核 7/7；报告 docs/AUDIT_2026-10-05_chatbot_memory_sync.md

## 已验证方法
- [chatbot→weave-mem 同步评估方法](topic_chatbot_sync_audit_20261005.md) — git 波次盘点（commit 锚点+numstat）→ 共享模块 file:line 对照 → PoC 实证包 → fresh-brain 独立复核

## 用户上下文
- ⛔ 绝对禁止修改 /Users/logan/Documents/DEV/chatbot（上游只读）
- 脚本统一入口 script/linux/（start/stop/restart/project_build/install_venv/init_db/export_openapi）；家族编排/安装/冒烟同在各自 script/linux/；无 scripts/ 目录
- 家族级记忆索引见 ../memory/MEMORY.md（三项目波次话题均在该目录）

## 本波教训（A4.9 双审 + FCV）
- ⛔ 死门即假交付：config 键必须有消费者+行为断言（W8 无断言被 FCV 抓包；D2 四门接线前是死配置）
- ⛔ 轴混淆三连：edge_source≠relation_type、relation.id≠concept.id、单 id 游标≠(created_at,id) keyset——SQL 轴错位=恒空查询
- ⛔ 快合并模板记忆守卫（_fast_merge_name_safe）与 SAVEPOINT 隔离（_isolated_merge_item）是上游生产事故换来的一对，移植不可拆
- ❌ 相对路径 PoC 证据包（已在 20261005 审计波记过）：证据路径必须绝对化
- ✅ 遗留全清（2026-10-05 第二波）：AGPR 2-hop/grouping/RMW×3 原子化/E3 模块/sqlite 夹具 13/13/C-5 逐键裁决（1 键对齐+5 键确认保留）——仅 E3 装配点/jieba 部署面/concept_link 门拒收语义三项具名待场景
- ⛔ 镜像 rsync 必须 --exclude='.git/'（--delete 会连克隆仓的 .git 一起清掉）
- ⛔ 公开仓推送剔除含会话文本摘录的过程件（gate_audit.md 类）——秘密扫描 clean ≠ 隐私 clean
- ⛔ raw text() SQL 日期列双言陷阱：SQLite 回 str、PG 回 datetime——比较/做差/isoformat 前必须归一（本次 run_weight_decay 与 recall_log 端点同根双炸）

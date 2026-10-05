# ⏳ chatbot 同步 wave（P0-P3 + F-4a）2026-10-05

## 任务
按审计报告 §4 实施同步（用户裁决：P0-P3 全量 + F-4a 选 (a) 重移植）。Lane L · Class CODE。

## 关键事实
- **P0**：no-key 守卫×3 站点 / 复活锚 weight_decayed_at / B10 原子 UPDATE+answer_cited / billing_class 读写分离（mlc_billing_class+4 计数过滤+bg 记录器）
- **P1**：memory_adoption_service（双言化）+ POST /api/memory/adoption（契约+写回链+60/时预算+fail-open commit）+ apply_cross_modal_consistency（verified-only±权、镜像 calibrated_score）
- **P2 门控默认关**：策略路由/D1 确定性边+白名单(语义轴)+rho/D2 adaptive+MMR+contradicts/（D3 快合并+MST 用 HEAD 修好的门）/E1/A4c/W8 link expansion
- **P3**：memory_recall_log（仅元数据）+ GET 端点（复合 keyset）+ 双言清理 + 6h 清理环 + 截断提示
- **F-4a**：_update_cluster_embedding+add/remove_concept_to_cluster 回归接线+embedding_model 溯源+回填脚本（dry-run 默认）
- 验证：PG 12 套件 175/0（含 4 新套件 85 断言）；sqlite 11/13（2 预存失败 ADR D-4）；A4.7b 双 trace；openapi 32/32；A4.9 双对抗审 2C+5I+6M 全裁决修复（re-review ship-ready）；FCV round2 pass

## 未来注意
- 门控开启前先跑 test_p2_gates 对应断言；D3 快合并依赖 name-safe+SAVEPOINT 双守卫
- 从 chatbot 再同步时：已删符号回坐力 + 轴混淆三连（见 MEMORY.md ⛔ 节）优先自查
- 并发会话互踩已两次发生（backup commit 卷波内文件、scripts 重构改生命周期路径）——波内证据以磁盘现状+log 为准，生命周期入口现为 script/linux/

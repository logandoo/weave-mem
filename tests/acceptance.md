# Acceptance Criteria — weave-mem 死代码清理

> cap=5  stall=3×

1. 删除 `services/memory_backfill_service.py` 整个模块（含 evaluate_all_users）
2. 删除 17 个死函数：detect_signal / process_clarification / create_cluster / add_concept_to_cluster / remove_concept_from_cluster / get_active_concept_count / promote_silent_to_active / invalidate_episode / cosine_similarity(embedding_service) / build_shared_agent_context / pii_hit_labels / migrate_all_users / _local_today / build_thinking_extra_body / get_shared_async_client / get_current_user_from_websocket / _reset_advisory_locks
3. 删除死文件 `schemas/chat.py`（UserCreate）与死 ORM 类 `ConceptClusterMember`（若确认零引用）
4. 删除 6 个死 config 属性（super_admin_bypass / server_host / server_scheme / project_root / default_top_k / agent_auxiliary_coordinator_model）与死方法 `get_provider_config`
5. 清理全部 ~30 处 F401 死 import 与 3 处 F841 死变量
6. 删除 config.toml 18 个零引用键
7. 删除后全库可导入：`compileall` + 模块导入扫描零失败
8. 删除后服务经 `scripts/start.sh` 启动，/healthz 返回 ok + pgvector=true
9. 无回归：tests/test_api.py 22/22 + tests/test_recall.py 13/13 全绿
10. ruff F401/F841 复扫 0 残留；vulture 60% 复扫死函数归零（装饰器接线类除外）

## 本 wave（ingest + clarifications/process 端点）
11. POST /api/memory/ingest：未认证 401 / 缺 content 422 / content<5 422 / unit_kind 白名单 422 / source_ids 类型 422 / 无 provider 503 / provider 故障 502 / 成功 200 {unit_id}
12. POST /api/memory/clarifications/process：未认证 401 / 缺 user_message 422 / 无信号词 detected=false / 信号词 detected=true（clarification 键存在）
13. auto_apply 行为链（mock LLM）：confidence≥0.8 → clarifications 落库 applied=TRUE + 概念更新 + 审计键
14. 恢复的 clarification 服务函数与 chatbot 语义一致（max_tokens=500 显式化除外，A4.9 Important-1 修复）
15. 无回归：test_api 22/22 + test_recall 13/13 + 新套件 9/9 + 6/6

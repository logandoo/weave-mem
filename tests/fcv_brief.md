# FCV Brief — 遗留项全清 wave（round 2 wave）2026-10-05

Fresh-context 复验（只读；命令日志写 tests/ 属预期）。按验收标准逐条 pass/fail，报告观察证据。

## 验收标准（tests/acceptance.md）
1. RMW×3 原子化+衰变写回守卫（含生产路径交错测试）
2. AGPR 二跳（分数=父分×边权×0.5 断言）
3. assembly_grouping（[相关记忆]+双标签+门关不变）
4. E3 listwise（模块+门+解析件 fail-open）
5. sqlite 套件 13/13（夹具 fail-fast）
6. 门控收口（strategy_route/concept_link 开启态+C-5 裁决+C-6 键收口）
7. 全量回归 + 提交

## 验证命令（cwd=/Users/logan/Documents/DEV/weave-family/weave-mem）
```bash
bash script/linux/restart.sh && sleep 2
curl -s -m 5 http://127.0.0.1:8202/healthz
for t in test_api test_recall test_ingest_clarify test_clarify_apply test_blindspot \
         test_full_chain test_pat test_mcp test_sync_p0 test_adoption \
         test_p2_gates test_recall_log; do
  ./.venv/bin/python tests/$t.py > tests/$t.log 2>&1 && echo "$t PASS" || echo "$t FAIL"
done
grep -E "RMW|AGPR|grouping|E3|衰变" tests/test_sync_p0.log tests/test_p2_gates.log
# sqlite 口径（预期 13/13）：
sed -i '' 's/^type = "postgres"/type = "sqlite"/' backend/config.toml
bash script/linux/restart.sh && sleep 2 && ./.venv/bin/python tests/test_sqlite_mode.py
sed -i '' 's/^type = "sqlite"/type = "postgres"/' backend/config.toml && bash script/linux/restart.sh
grep -n "min_today_calls\|recovery_ratio\|dream_concept_window_days" backend/config.toml
grep -rn "weight_max\|calls_table" backend/config.toml backend/app | wc -l   # 期望 0
python3 tests/assert_artifacts.py --existing
```

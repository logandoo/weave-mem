# FCV Report — 遗留项全清 wave
## Round 1（fresh-context）
标准 1-5 PASS（RMW 交错/AGPR 公式 0.192/grouping 双标签/E3 解析 4 态/sqlite 13/13）；
标准 6 PARTIAL（C-5 逐键裁决未入档——仅 migration_llm_timeout 落）；标准 7 PARTIAL（回归过/提交未落）。
→ 修复：C-5 五键裁决入 AUDIT §4/review_package/memory；提交随收尾执行。
## Round 2（scoped）
C-5 五键裁决档 greps + 提交记录复核 → 见执行记录。
Round 2 结果：C-5 裁决档/review_package/提交 a892c47/工作树 clean/migration_llm_timeout=120 — 5/5 PASS。**Fresh-verify: pass**

# FCV — SDK 化 wave（2026-10-06）Fresh-verify: pass
7/7：服务健康 / test_sdk 20+outbound 11 / 32 路径机器对账零缺口（root() 覆盖 GET /）/
空 base fail-closed（无 base_url=None）/ MCP 单源 / 出向仅剩已注记 TEI rerank / ruff CLEAN。

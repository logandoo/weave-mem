# Acceptance Criteria — README/CHANGELOG 更新 + GitHub 推送

> cap=5  stall=3×

1. README.md + README.zh.md 双语同步至两波现状：Core API 补 `POST /api/memory/adoption` 与 `GET /api/memory/recall_log`、配置节补本波门控/台账键、目录结构补三个新服务文件；每处声明实物核对（路径/命令/键名真实存在），readme_lint 总分不低于基线（EN 63.2 / ZH 71.8）且事实类告警不新增，双语结构一致
2. CHANGELOG.md 2026-10-05 条目如实拆分为两波（P0-P3+F-4a 同步波 / 遗留清理波），误归档的死代码清理条目归位 2026-08-19
3. GitHub 推送完成：weave-mem 镜像推送（含清理波 + 本次文档，形态与既有仓一致——子目录内容入仓根）；weave-note/weave-talk 经核验已同步不重复推；推送前项目文件（排除 .venv）秘密扫描干净

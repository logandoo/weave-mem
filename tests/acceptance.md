# Acceptance Criteria — SDK 化 wave（weave-mem-client 发布包 + 出向 provider 归一）

> cap=5  stall=3×

1. 发布型客户端包 `client/`（包名 `weave-mem-client`，pyproject 可安装）：类型化方法覆盖 openapi 全部 32 路径（鉴权/PAT/概念/召回/摄入/采纳/台账/澄清/治理/admin/healthz）；MCP 层 `MemoryClient` 单源复用包内实现（不再自带私有拷贝）；`tests/test_sdk.py` 经 SDK 端到端全绿
2. 出向 provider 归一：embedding 两处（`memory_embedding_service` 主调用 + `provider_router.embedding_available` 探测）改走官方 `AsyncOpenAI` SDK，no-key 哨兵/超时/熔断降级语义逐条保留；`tests/test_outbound_sdk.py` 以 stub embeddings 服务实证（含 no-key 头语义）；rerank（TEI `/rerank` 无标准 SDK）保留 httpx 并注记定性
3. 全量回归（PG 12+N 套件 + sqlite 口径）+ CI 全绿（lint 范围含 client 包、套件列表含新增两套件）+ A4.9 审查 + FCV + 文档（README 双语「调用方式/Client SDK」+ CHANGELOG）

"""Weave Mem 服务入口（全新编写——chatbot main.py 的解耦版）。

启动序列（照 chatbot main.py 的 memory 相关段）：
  JWT 检查 → init_db() → pgvector 探测 kill-switch → embedding 探测
  → test 用户确保存在 → memory_scheduler.start()
关闭：memory_scheduler.stop()。
SIGHUP 配置重载 + memory 环境重探测照搬 chatbot 的 _memory_reprobe_after_reload。
无静态文件 / 无 agent 域服务（agent_scheduler/agent_worker/export_worker 等全部移除）。
"""
import asyncio
import logging
from contextlib import asynccontextmanager
import signal
import sys

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, text

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
)

from app.api import auth as auth_api, memory as memory_api
from app.api.memory import require_admin
from app.db.database import init_db, AsyncSessionLocal, User
from app.core.config import get_config, clear_config_cache

logger = logging.getLogger(__name__)

config = get_config()

@asynccontextmanager
async def _mcp_lifespan(app: FastAPI):
    """宿主生命周期：MCP session_manager + 全部启动/关闭逻辑。

    重要（2026-08-19 排障）：FastAPI 提供 lifespan 参数后 @app.on_event
    会被忽略——init_db/调度器必须在此执行（SQLite 新库 0 表即此因）。
    """
    global _mcp_server
    if _mcp_server is not None:
        try:
            async with _mcp_server.session_manager.run():
                logger.info("MCP session manager active")
                await _startup_tasks()
                yield
        except Exception:
            logger.exception("startup failed (MCP manager or _startup_tasks)")
            raise
    else:
        await _startup_tasks()
        yield
    from app.services.memory_scheduler import memory_scheduler
    await memory_scheduler.stop()


async def _startup_tasks() -> None:
    """启动任务（原 on_event startup 逻辑）：JWT → init_db → probe → 调度器。"""
    if not config.security_jwt_secret_key:
        raise RuntimeError("JWT secret key is not configured")

    def _on_sighup_reload_config(signum, frame):
        clear_config_cache()
        logger.info("Config cache cleared via SIGHUP")
        try:
            loop = asyncio.get_running_loop()
            loop.call_soon_threadsafe(lambda: loop.create_task(_memory_reprobe_after_reload()))
        except RuntimeError:
            pass  # 事件循环未就绪（启动早期），首次启动探测会覆盖

    if sys.platform != "win32":
        signal.signal(signal.SIGHUP, _on_sighup_reload_config)

    await init_db()

    # §9.5：pgvector 缺失时 memory v2 表不存在——kill-switch 禁用 memory 子系统
    from app.db import migrations as _db_migrations
    from app.services.memory_runtime_state import disable_memory as _disable_memory
    if not _db_migrations.PGVECTOR_AVAILABLE:
        if config.memory.get("enabled") or config.memory.get("migration_enabled"):
            logger.error(
                "pgvector 不可用，禁用 memory 子系统；"
                "安装 pgvector 并重启后可重新开启（§9.5）")
        _disable_memory("pgvector 不可用（§9.5）")

    # Memory & Dreaming v2 scheduler（§9.11：先探测 embedding provider，不可用则禁用）
    from app.services.memory_embedding_service import probe_memory_embedding_on_startup
    await probe_memory_embedding_on_startup()

    await _ensure_test_user()
    from app.services.memory_scheduler import memory_scheduler
    await memory_scheduler.start()

    # §8.3 迁移启动自动排队（含 status='running' 崩溃恢复断点续传）
    from app.services.memory_runtime_state import memory_runtime_enabled as _mem_rt_enabled
    if _mem_rt_enabled(config) and config.memory.get("migration_enabled", False):
        from app.services.memory_migration_service import enqueue_pending_migrations
        _mig_task = asyncio.create_task(enqueue_pending_migrations())

        def _log_migration_result(t: asyncio.Task) -> None:
            if t.cancelled():
                return
            if t.exception():
                logger.error("migration queue failed", exc_info=t.exception())
            else:
                logger.info("migration queue done: %s", t.result())

        _mig_task.add_done_callback(_log_migration_result)

    logger.info("Weave Mem 启动完成（port=%s, pgvector=%s）",
                config.server_port, _db_migrations.PGVECTOR_AVAILABLE)


async def _ensure_test_user() -> None:
    """家族约定测试账号 test / 123456（script/linux/smoke_test.sh 依赖）。"""
    from app.services.auth_service import hash_password
    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.username == "test"))).scalar_one_or_none()
        if user is None:
            db.add(User(username="test", password_hash=await hash_password("123456"), role="user"))
            await db.commit()
            logger.info("已创建默认测试账号 test / 123456")


_mcp_server = None
app = FastAPI(
    lifespan=_mcp_lifespan,
    title="Weave Mem API",
    summary="概念/情节/梦境记忆系统后端：双通道召回 + 潜意识摄入 + 澄清处理 + 调度器自动生命周期",
    description=(
        "记忆系统后端服务（概念 CRUD/遗忘、双通道召回（pgvector+BM25+ILIKE）、"
        "潜意识摄入、澄清处理/回退、梦境与巩固调度、成本治理、GDPR 擦除、迁移管理）。\n\n"
        "认证：JWT 登录（POST /api/auth/login）或个人访问令牌（POST /api/auth/tokens）；"
        "均以 `Authorization: Bearer <token>` 携带。\n"
        "健康检查 /healthz 免鉴权。"
    ),
    version="1.1.0",
    contact={"name": "weave-family"},
    license_info={"name": "MIT"},
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.security_cors_allow_origins,
    allow_credentials=config.security_cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_api.router)
app.include_router(memory_api.router)
app.include_router(memory_api.admin_router)
app.include_router(memory_api.admin_users_router)


async def _memory_reprobe_after_reload() -> None:
    """SIGHUP 配置重载后重新探测 memory 运行环境（照搬 chatbot main.py）。

    pgvector 可用性由启动期 migrations 确定（运行期不变）；embedding 探测
    在新 Config 实例上重跑。先测后启：探测期间 kill-switch 保持禁用，
    确认可用才解除——无半开窗口。
    """
    from app.db import migrations as _m
    if not _m.PGVECTOR_AVAILABLE:
        return
    from app.services.memory_runtime_state import (
        memory_disabled_reason as _reason,
        enable_memory as _enable_memory,
    )
    from app.services.memory_embedding_service import probe_memory_embedding_on_startup
    was_disabled = _reason() is not None
    ok = await probe_memory_embedding_on_startup()
    if ok and was_disabled:
        _enable_memory()


@app.get("/healthz")
async def healthz() -> dict:
    payload = {"status": "ok", "service": "weave-mem", "database": "ok", "pgvector": False}
    try:
        from app.db.database import IS_SQLITE
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))
            if not IS_SQLITE:
                vector = (await db.execute(text("SELECT extname FROM pg_extension WHERE extname='vector'"))).scalar()
                payload["pgvector"] = vector == "vector"
    except Exception as exc:
        payload.update({"status": "degraded", "database": "error", "error": str(exc)})
    return payload


@app.get("/api/health")
async def api_health() -> dict:
    return await healthz()


@app.post("/api/admin/reload-config")
async def admin_reload_config(
    _: User = Depends(require_admin),
):
    """配置热重载（B-6）：clear_config_cache + memory 环境重探测，SIGHUP 同语义。

    生效范围：经 get_config() 新鲜读取的配置（embedding base/dim/探测、迁移、
    调度器等）；模块级绑定的检索/提炼阈值（retrieval_enabled 等）不热生效，
    需重启服务（A4.9 review 记录）。"""
    from app.core.config import clear_config_cache as _clr
    _clr()
    await _memory_reprobe_after_reload()
    return {"ok": True, "service": "weave-mem"}


@app.get("/")
async def root() -> dict:
    return {
        "service": "weave-mem",
        "description": "记忆系统后端服务：概念/情节/梦境存储与 pgvector 召回",
        "docs": "见 README.md，接口前缀 /api/memory，健康检查 /healthz",
    }

# MCP server（memos 借鉴）：必须放在所有 @app 路由之后——mount("/") 是兜底
# 匹配，先注册会抢走 /healthz 等全部路径（2026-08-19 排障）。
if config.mcp.get("enabled", True):
    try:
        from app.mcp_server import build_mcp_server_from_config
        _mcp_server = build_mcp_server_from_config()
        # stateless_http：每请求独立 transport、无 session 追踪（薄转发场景无
        # 会话状态；规避 v2 stateful 的 session 校验 400——2026-08-19 排障记录）
        app.mount("/", _mcp_server.streamable_http_app(stateless_http=True, json_response=True))
    except Exception:
        logger.exception("MCP server build failed (service continues without MCP)")

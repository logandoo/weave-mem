"""weave-mem 解耦 shim：仅提供 chatbot app.tools.memory 中被记忆栈引用的路径函数。

chatbot 的 app/tools/memory.py 是 agent 工具（注册 tool registry、prompt 注入扫描、
func.md 等），weave-mem 是独立记忆服务，无 agent 域。记忆栈中仅
memory_migration_service / memory_subconscious_service / api/memory.py(DELETE /all)
引用 `_get_memory_dir()` 读写文件层记忆（AGENT.md / USER.md）。

独立服务下文件层记忆不存在：本 shim 保持与 chatbot 相同的目录解析逻辑
（AGENT_MEMORY_DIR 环境变量优先，否则 backend_root/agent_memories），
目录缺失时各调用方自然降级（迁移跳过 / 水位线为空 / 擦除为 no-op），
业务逻辑零改动。
"""
from pathlib import Path

from app.core.config import get_config

config = get_config()


def _get_memory_dir() -> Path:
    import os as _os
    memory_dir = _os.environ.get("AGENT_MEMORY_DIR")
    if memory_dir:
        return Path(memory_dir)
    backend_root = config.backend_root
    return backend_root / "agent_memories"

# weave-mem 裁剪：从 chatbot app/core/config.py 原样摘取
# 保留 __init__/合并机制 + database/api/security/server/项目根目录/
# defaults/providers/memory/agent(memory+auxiliary) 属性；
# 删除 agent 域专属段（asr/voice/deathmatch/web_search/browser/terminal/
# code_execution/workspace/scheduler/tool_loop/sub_agent/title_generation 等）。
# 每行内容与 chatbot 原文一致（未重写）。

import os
import toml
from pathlib import Path
from typing import Optional
from functools import lru_cache


def _parse_int(value) -> Optional[int]:
    if value == "" or value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None
class Config:
    def __init__(self, config_path: str = None):
        if config_path is None:
            config_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                "config.toml"
            )

        self.config_path = Path(config_path).resolve()
        self._config = toml.load(config_path)
        # Model config split: every model-related setting (LLM / ASR / TTS /
        # embedding / rerank / judge / verifier / subagent / validator /
        # memory / title / providers) lives in config_model.toml, merged
        # OVER the main file so config.toml stays a pure infra file. When the
        # model file is absent (legacy deployments) the main file's sections
        # remain authoritative — every property below is unchanged.
        self.model_config_path = self._resolve_model_config_path(config_path)
        self._config = self._merge_model_config(self._config)

    @staticmethod
    def _resolve_model_config_path(config_path: str) -> Optional[Path]:
        env_override = os.environ.get("CONFIG_MODEL_PATH")
        if env_override:
            return Path(env_override).resolve()
        main = Path(config_path).resolve()
        candidate = main.parent / "config_model.toml"
        return candidate if candidate.exists() else None

    # Sections that belong to config_model.toml. Whole-section moves:
    _MODEL_SECTIONS = {
        "api",               # legacy main LLM endpoint
        "defaults",          # default LLM sampling params
        "default_assistant", # assistant-scoped sampling params
        "asr",               # speech recognition models
        "voice",             # voice LLM / TTS / ASR tuning
        "providers",         # multi-provider LLM routing
        "deathmatch",        # judge / verifier models + goal-loop budgets
        "sub_agent",         # subagent LLM params
        "title_generation",  # title LLM params
        "memory",            # memory LLM / embedding / rerank / cost models
    }
    # [agent] stays in the main file for harness tuning, but its MODEL
    # sub-sections move. Only these keys are taken from the model file.
    _MODEL_AGENT_SUBSECTIONS = {
        "auxiliary",      # per-task auxiliary models (coordinator/classifier/title/…)
        "compression",    # context-compression model params
        "moa",            # mixture-of-agents models
        "memory",         # daily summary / dream models
        "tool_digest",    # subagent tool-result digest model
        "sub_agent",      # subagent model params
    }

    def _merge_model_config(self, base: dict) -> dict:
        if self.model_config_path is None:
            return base
        import logging
        logger = logging.getLogger(__name__)
        try:
            model_cfg = toml.load(self.model_config_path)
        except Exception:
            logger.exception(
                "Failed to load %s — falling back to main config sections. "
                "If this deployment was already split, the server is now "
                "running with EMPTY/default model config and will fail on "
                "the first LLM call.",
                self.model_config_path,
            )
            return base
        merged = dict(base)
        for section in self._MODEL_SECTIONS:
            if section in model_cfg:
                if section in merged:
                    # Section-granular replacement: a partial model file
                    # REPLACES the whole main-file section. Warn so ops can
                    # spot missing keys (e.g. a hand-crafted model file with
                    # only [api].model_name would silently drop base_url/key).
                    logger.warning(
                        "config_model.toml section [%s] REPLACES the main "
                        "config.toml section of the same name (whole-section "
                        "override, not per-key merge)", section,
                    )
                merged[section] = model_cfg[section]
        if "agent" in model_cfg:
            agent = dict(merged.get("agent") or {})
            for key, value in (model_cfg.get("agent") or {}).items():
                if key in self._MODEL_AGENT_SUBSECTIONS:
                    agent[key] = value
            merged["agent"] = agent
        return merged
    @property
    def database_pool_size(self) -> int:
        return int(self._config.get("database", {}).get("pool_size", 20))

    @property
    def database_max_overflow(self) -> int:
        return int(self._config.get("database", {}).get("max_overflow", 30))

    @property
    def database_pool_recycle(self) -> int:
        return int(self._config.get("database", {}).get("pool_recycle", 1800))

    @property
    def database_pool_timeout(self) -> int:
        return int(self._config.get("database", {}).get("pool_timeout", 30))

    @property
    def api_base_url(self) -> str:
        return self._config.get("api", {}).get("base_url", "https://api.openai.com/v1")

    @property
    def api_key(self) -> Optional[str]:
        key = self._config.get("api", {}).get("api_key", "")
        return key if key else None

    @property
    def security(self) -> dict:
        return self._config.get("security", {})

    @property
    def security_jwt_secret_key(self) -> Optional[str]:
        key = self.security.get("jwt_secret_key", "")
        if key:
            return key
        return os.environ.get("JWT_SECRET_KEY") or None

    @property
    def security_cors_allow_origins(self) -> list:
        origins = self.security.get("cors_allow_origins", ["*"])
        if isinstance(origins, str):
            return [origins]
        return list(origins)

    @property
    def security_cors_allow_credentials(self) -> bool:
        return bool(self.security.get("cors_allow_credentials", True))


    @property
    def model_name(self) -> Optional[str]:
        return self._config.get("api", {}).get("model_name") or None


    @property
    def server_port(self) -> int:
        return self._config.get("server", {}).get("port", 8158)



    @property
    def defaults(self) -> dict:
        return self._config.get("defaults", {})

    @property
    def default_temperature(self) -> float:
        return self.defaults.get("temperature", 0.7)

    @property
    def default_top_p(self) -> float:
        return self.defaults.get("top_p", 1.0)


    @property
    def default_presence_penalty(self) -> float:
        return self.defaults.get("presence_penalty", 0.0)

    @property
    def default_frequency_penalty(self) -> float:
        return self.defaults.get("frequency_penalty", 0.0)

    @property
    def default_max_tokens(self) -> Optional[int]:
        val = self.defaults.get("max_tokens")
        return _parse_int(val)

    @property
    def agent(self) -> dict:
        return self._config.get("agent", {})

    @property
    def agent_name(self) -> str:
        return self.agent.get("name", "共享智能体")
    @property
    def agent_memory_max_items(self) -> int:
        return int(self.agent.get("memory_max_items", 12))
    @property
    def agent_memory_refresh_note_limit(self) -> int:
        return int(self.agent.get("memory_refresh_note_limit", 20))

    @property
    def agent_memory_refresh_message_limit(self) -> int:
        return int(self.agent.get("memory_refresh_message_limit", 60))
    @property
    def agent_auxiliary(self) -> dict:
        return self.agent.get("auxiliary", {})

    @property
    def agent_auxiliary_compression_model(self) -> str:
        return str(self.agent_auxiliary.get("compression_model", "") or "")

    @property
    def agent_auxiliary_search_decision_model(self) -> str:
        return str(self.agent_auxiliary.get("search_decision_model", "") or "")

    @property
    def agent_auxiliary_title_model(self) -> str:
        return str(self.agent_auxiliary.get("title_model", "") or "")


    @property
    def agent_auxiliary_classifier_model(self) -> str:
        """Model for agentic judgment calls (error classification, triviality,
        interest extraction, skill assessment, identity facts, citation
        disambiguation, schedule parsing, creative-goal detection, completion
        reconciliation). Empty → main LLM."""
        return str(self.agent_auxiliary.get("classifier_model", "") or "")
    @property
    def agent_memory(self) -> dict:
        return self.agent.get("memory", {})
    # ---- Memory & Dreaming v2 ----

    @property
    def database_type(self) -> str:
        t = str(self._config.get("database", {}).get("type", "postgres")).strip().lower()
        if t not in ("sqlite", "postgres"):
            raise ValueError(f"[database].type 非法值 {t!r}（仅支持 sqlite / postgres）")
        return t

    @property
    def backend_root(self):
        from pathlib import Path
        return Path(self.config_path).resolve().parent

    @property
    def database_url(self) -> str:
        db = self._config.get("database", {})
        if self.database_type == "sqlite":
            # 4 斜杠绝对路径（家族先例：3 斜杠会被解析成相对路径）
            from pathlib import Path
            raw_path = str(db.get("path", "weave_mem.db"))
            p = Path(raw_path)
            if not p.is_absolute():
                base = Path(self.config_path).resolve().parent
                p = base / raw_path
            return "sqlite+aiosqlite:///" + str(p.resolve())
        host = db.get("host", "localhost")
        port = db.get("port", 5432)
        username = db.get("username", "postgres")
        password = db.get("password", "")
        name = db.get("name", "weave_mem")
        if password:
            return f"postgresql+asyncpg://{username}:{password}@{host}:{port}/{name}"
        return f"postgresql+asyncpg://{username}@{host}:{port}/{name}"

    @property
    def mcp(self) -> dict:
        return self._config.get("mcp", {})

    @property
    def memory(self) -> dict:
        return self._config.get("memory", {})

    @property
    def memory_concept(self) -> dict:
        return self.memory.get("concept", {})

    @property
    def memory_retrieval(self) -> dict:
        return self.memory.get("retrieval", {})

    @property
    def memory_subconscious(self) -> dict:
        return self.memory.get("subconscious", {})

    @property
    def memory_episodic(self) -> dict:
        return self.memory.get("episodic", {})

    @property
    def memory_multimodal(self) -> dict:
        return self.memory.get("multimodal", {})

    @property
    def memory_cost_governance(self) -> dict:
        return self.memory.get("cost_governance", {})

    @property
    def memory_fatigue(self) -> dict:
        return self.memory_concept.get("fatigue", {})

    @property
    def memory_timezone(self) -> str:
        return self.memory.get("timezone", "Asia/Shanghai")

    # ---- Providers ----

    @property
    def providers(self) -> dict:
        return self._config.get("providers", {})

    @property
    def provider_configs(self) -> dict:
        """Return provider configs keyed by provider type.
        Example structure from config.toml:
        [providers.deepseek]
        base_url = "https://api.deepseek.com/v1"
        api_key = "sk-..."
        model_name = "deepseek-v4-flash"

        [providers.zhipu]
        base_url = "https://api.zhipu.ai/v1"

        [providers.qwen]
        base_url = "https://api.qwen.ai/v1"
        """
        providers = {}
        raw = self._config.get("providers", {})
        if not isinstance(raw, dict):
            return providers
        for key, value in raw.items():
            if isinstance(value, dict):
                providers[key] = {
                    "base_url": str(value.get("base_url", "")).strip(),
                    "api_key": str(value.get("api_key", "")).strip(),
                    "model_name": str(value.get("model_name", "")).strip(),
                }
        # Always ensure deepseek falls back to [api] section if not explicitly configured
        if "deepseek" not in providers:
            providers["deepseek"] = {
                "base_url": self.api_base_url,
                "api_key": self.api_key or "",
                "model_name": self.model_name or "",
            }
        else:
            pd = providers["deepseek"]
            if not pd["base_url"]:
                pd["base_url"] = self.api_base_url
            if not pd["api_key"]:
                pd["api_key"] = self.api_key or ""
            if not pd["model_name"]:
                pd["model_name"] = self.model_name or ""
        return providers


@lru_cache()
def get_config() -> Config:
    return Config()


def clear_config_cache() -> None:
    get_config.cache_clear()

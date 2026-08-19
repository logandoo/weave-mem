import logging

from app.services.llm_service import LLMService
from app.core.config import get_config

logger = logging.getLogger(__name__)
config = get_config()


class AuxiliaryClient:
    def __init__(self, task: str = "default"):
        self.task = task
        model_override = self._get_model_override(task)
        self.llm = LLMService(
            custom_model_name=model_override or None,
        )

    def _get_model_override(self, task: str) -> str:
        if task == "compression":
            return config.agent_auxiliary_compression_model
        elif task == "search_decision":
            return config.agent_auxiliary_search_decision_model
        elif task == "title":
            return config.agent_auxiliary_title_model
        elif task in (
            "error_classify", "triviality", "interest_extract", "skill_assess",
            "identity_facts", "citation_disambiguate", "schedule_parse",
            "creative_goal", "completion_reconcile", "title_fallback",
        ):
            return config.agent_auxiliary_classifier_model
        return ""

    async def complete(self, messages: list, **kwargs) -> str:
        content, _ = await self.complete_parts(messages, **kwargs)
        return content

    async def complete_parts(self, messages: list, **kwargs) -> tuple[str, str]:
        model = kwargs.pop("model", self.llm.custom_model_name or config.model_name)
        return await self.llm.complete_chat_parts(
            messages,
            model=model,
            **kwargs,
        )

    async def stream(self, messages: list, **kwargs):
        model = kwargs.pop("model", self.llm.custom_model_name or config.model_name)
        async for chunk in self.llm.stream_chat(messages, model=model, **kwargs):
            yield chunk

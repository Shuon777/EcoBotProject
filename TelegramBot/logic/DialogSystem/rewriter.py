# TelegramBot/logic/dialogue_system/rewriter.py
import logging
from .llm_factory import LLMFactory

logger = logging.getLogger(__name__)

class QueryRewriter:
    def __init__(self, provider: str = "qwen"):
        self.llm = LLMFactory.get_model(provider)

    async def rewrite(self, query: str, history: list) -> str:
        if not history:
            return query

        # Формируем компактную историю для модели
        history_text = "\n".join([f"{m['role']}: {m['content']}" for m in history[-3:]])

        prompt = f"""
        Твоя задача — переписать ПОСЛЕДНИЙ запрос пользователя в ПОЛНЫЙ и самостоятельный запрос, используя контекст беседы.
        Если последний запрос уже полный, верни его без изменений.
        НЕ ОТВЕЧАЙ на вопрос, только переписывай!

        История:
        {history_text}

        Последний запрос: {query}

        Переписанный запрос:"""

        try:
            response = await self.llm.ainvoke(prompt)
            return response.content.strip().strip('"')
        except Exception as e:
            logger.error(f"Rewriter error: {e}")
            return query
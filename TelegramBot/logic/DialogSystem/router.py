# TelegramBot/logic/dialogue_system/router.py
import logging
from typing import Literal
from pydantic import BaseModel, Field
from .llm_factory import LLMFactory

logger = logging.getLogger(__name__)

class Route(BaseModel):
    """Классификация намерения пользователя"""
    intent: Literal["BIOLOGY", "INFRASTRUCTURE", "KNOWLEDGE", "CHITCHAT"] = Field(
        description="BIOLOGY - флора/фауна, INFRASTRUCTURE - музеи/памятники, KNOWLEDGE - история/FAQ/почему/кто работает, CHITCHAT - болталка"
    )

class SemanticRouter:
    def __init__(self, provider: str = "qwen"):
        llm = LLMFactory.get_model(provider)
        # Привязываем схему к модели для строгого вывода
        self.runnable = llm.with_structured_output(Route)

    async def get_intent(self, query: str) -> str:
        prompt = f"""
        Ты — строгий диспетчер запросов. Твоя задача — выбрать ОДНУ категорию.

        ПРАВИЛА ПРИОРИТЕТА:
        1. KNOWLEDGE (Справка и Процедуры) — ВЫСШИЙ ПРИОРИТЕТ. 
           Если в запросе есть слова: "разрешение", "оформить", "заказать", "купить", "билеты", "цена", "как добраться", "регистрация", "правила", "онлайн".
           Даже если упомянуто место (парк, музей) — если вопрос о ПРОЦЕДУРЕ, это KNOWLEDGE.

        2. BIOLOGY — Если упомянуты животные, растения или грибы.

        3. INFRASTRUCTURE — Если вопрос о физическом объекте: "где находится скала", "опиши музей", "какие памятники есть". 

        4. CHITCHAT — Приветствия, благодарности, "как дела".

        ПРИМЕР: 
        "Где оформить пропуск в нацпарк?" -> KNOWLEDGE (потому что пропуск — это процедура).
        "Что за скала Шаманка?" -> INFRASTRUCTURE (потому что вопрос об объекте).

        Запрос: {query}
        """
        try:
            result = await self.runnable.ainvoke(prompt)
            return result.intent
        except Exception as e:
            logger.error(f"Router error: {e}")
            return "KNOWLEDGE" # Безопасный fallback
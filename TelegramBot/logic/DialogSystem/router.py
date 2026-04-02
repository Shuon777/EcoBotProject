# TelegramBot/logic/dialogue_system/router.py
import logging
from typing import Literal, Optional
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

    async def get_intent(self, query: str, last_intent: Optional[str] = None) -> str:
        # Добавляем блок контекста в промпт
        context_hint = f"\nКОНТЕКСТ: Предыдущая тема диалога: {last_intent}\n" if last_intent else ""

        prompt = f"""
        {context_hint}
        ЗАДАЧА: Классифицировать запрос.
        
        КРИТЕРИИ:
        1. BIOLOGY: Растения (деревья, цветы, травы, грибы) и животные (рыбы, птицы, насекомые). 
           ПРИМЕРЫ: нерпа, омуль, ЛИСТВЕННИЦА, кедр, эдельвейс.
        2. INFRASTRUCTURE: Рукотворные объекты и места. Здания, музеи, памятники, смотровые площадки, города, поселки. 
           ПРИМЕРЫ: Байкальский музей, Листвянка, Иркутск, памятник Бабру, обсерватория.
        3. KNOWLEDGE: Процедуры, правила, цены, билеты, история.
        
        ЗАПРЕТ: Никогда не относи растения (деревья) к INFRASTRUCTURE.
        
        Запрос: "{query}"
        """
        try:
            result = await self.runnable.ainvoke(prompt)
            return result.intent
        except Exception as e:
            logger.error(f"Router error: {e}")
            return last_intent or "CHITCHAT"
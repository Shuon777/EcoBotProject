# TelegramBot/logic/dialogue_system/workers/knowledge.py
import logging
from typing import Literal, Optional
from pydantic import BaseModel, Field
from ..llm_factory import LLMFactory

logger = logging.getLogger("KnowledgeWorker")

class KnowledgeAnalysis(BaseModel):
    # Нам не нужны сложные типы, только чистая суть запроса для поиска
    search_query: str = Field(description="Очищенный поисковый запрос для базы знаний")
    topic: Literal["History", "Staff", "Rules", "Prices", "General"] = "General"

class KnowledgeWorker:
    def __init__(self, provider: str = "qwen"):
        llm = LLMFactory.get_model(provider)
        self.parser = llm.with_structured_output(KnowledgeAnalysis)

    async def analyze(self, query: str) -> KnowledgeAnalysis:
        prompt = f"""
        Ты — справочный ассистент Байкальского музея. 
        Твоя задача — очистить запрос пользователя от шума для поиска в текстовой базе знаний.
        
        Примеры:
        "Где посмотреть список сотрудников?" -> "список сотрудников музея"
        "Почему обсерваторию построили именно тут?" -> "причина строительства обсерватории в Листвянке"
        
        Запрос: {query}
        """
        logger.info(f"📚 Analyzing knowledge request: '{query}'")
        return await self.parser.ainvoke(prompt)
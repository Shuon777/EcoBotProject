import logging
import re
from .llm_factory import LLMFactory

logger = logging.getLogger("QueryRewriter")

class QueryRewriter:
    def __init__(self, provider: str = "qwen"):
        self.llm = LLMFactory.get_model(provider)
        # Список маркеров, которые указывают на зависимость от контекста
        self.context_markers = [
            "он", "она", "оно", "они", "его", "ее", "их", "им", 
            "там", "тут", "туда", "оттуда", 
            "а ", "еще", "покажи", "где", "кто"
        ]

    def _needs_rewriting(self, query: str) -> bool:
        """
        Простая эвристика: нужно ли вообще трогать запрос?
        """
        q = query.lower()
        
        # 1. Если запрос длинный и в нем есть слова с большой буквы (кроме начала), 
        # скорее всего, там уже есть сущность.
        # 2. Но если есть местоимения или запрос очень короткий ("а в Братске?") - рерайт нужен.
        
        words = q.split()
        has_markers = any(marker in words for marker in self.context_markers)
        is_short = len(words) < 4
        
        return has_markers or is_short

    async def rewrite(self, query: str, history: list) -> str:
        # Если истории нет или запрос выглядит полным - не тратим время
        if not history or not self._needs_rewriting(query):
            logger.info("⏩ Rewriter skipped (query looks standalone or no history)")
            return query

        # Если мы здесь, значит нашли "он", "там" или "а..."
        # Даем модели жесткую наводку
        logger.info("🧠 Rewriter triggered (context markers found)")
        
        history_text = ""
        for m in history[-3:]: # Берем только последние 3 сообщения для экономии
            history_text += f"{m['role'].upper()}: {m['content']}\n"

        prompt = f"""
        Ты — лингвистический модуль. Твоя задача — заменить местоимения на объекты из истории.
        
        ИНСТРУКЦИЯ:
        В запросе пользователя найдены маркеры контекста (он, там, а...). 
        Определи, к какому объекту из истории они относятся, и перепиши запрос так, чтобы он стал понятен без истории.
        
        ПРИМЕР:
        История: USER: Расскажи про омуля. ASSISTANT: Омуль - это рыба...
        Запрос: А где он обитает?
        Результат: Где обитает омуль?

        ИСТОРИЯ БЕСЕДЫ:
        {history_text}
        
        ЗАПРОС ПОЛЬЗОВАТЕЛЯ: {query}
        
        ПЕРЕПИСАННЫЙ ЗАПРОС (только текст):"""

        try:
            response = await self.llm.ainvoke(prompt)
            rewritten = response.content.strip().strip('"')
            logger.info(f"✅ Rewriter result: '{rewritten}'")
            return rewritten
        except Exception as e:
            logger.error(f"Rewriter LLM error: {e}")
            return query
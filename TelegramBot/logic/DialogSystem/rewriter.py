import logging
import re
from .llm_factory import LLMFactory

logger = logging.getLogger("QueryRewriter")

class QueryRewriter:
    def __init__(self, provider: str = "qwen"):
        self.llm = LLMFactory.get_model(provider)
        # Список маркеров, которые указывают на зависимость от контекста
        self.context_markers =[
            "он", "она", "оно", "они", 
            "его", "ее", "её", "их", "им", "ими",
            "него", "нее", "неё", "них", "ним", "ними", "нем", "нём", "ней",
            "этот", "эта", "это", "эти", "этого", "этой", "этим", "этих", "этом",
            "там", "тут", "туда", "оттуда"
        ]

    def _needs_rewriting(self, query: str) -> bool:
        q = query.lower()
        words = q.split()
        
        # Убрали is_short, так как короткие запросы могут быть самостоятельными
        has_markers = any(marker in words for marker in self.context_markers)
        
        # Запросы, начинающиеся с союзов уточнения (А..., И..., Но...)
        starts_with_conjunction = q.startswith(("а ", "и ", "но ", "еще ", "ещё "))
        
        return has_markers or starts_with_conjunction

    async def rewrite(self, query: str, history: list) -> str:
        if not history or not self._needs_rewriting(query):
            logger.info("⏩ Rewriter skipped (query looks standalone or no history)")
            return query

        logger.info("🧠 Rewriter triggered")
        history_text = ""
        for m in history[-3:]: 
            history_text += f"{m['role'].upper()}: {m['content']}\n"

        prompt = f"""
        Ты — лингвистический модуль. Твоя задача — восстановить обрывочный запрос пользователя, используя историю.
        
        СТРОГИЕ ПРАВИЛА:
        1. Если запрос содержит местоимения (он, там) или начинается с "А ...", замени местоимения на объекты из истории.
        2. Если запрос пользователя ПОЛНОЦЕННЫЙ и содержит новый объект (например, "Покажи лиственницу сибирскую"), НЕ ДОБАВЛЯЙ ничего из истории! Просто верни запрос как есть.
        
        ПРИМЕРЫ:
        История: USER: Расскажи про омуля. ASSISTANT: Омуль - это рыба...
        Запрос: А где он обитает? -> Где обитает омуль?
        
        История: USER: Где растет эдельвейс? ASSISTANT: Он растет в горах...
        Запрос: Покажи лиственницу сибирскую -> Покажи лиственницу сибирскую
        
        История: USER: Как выглядит кедр? ASSISTANT: Вот фото...
        Запрос: А осенью? -> Как выглядит кедр осенью?

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
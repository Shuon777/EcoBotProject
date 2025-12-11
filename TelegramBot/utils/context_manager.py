import redis.asyncio as redis 
import json
import logging
from typing import Dict, Any
from config import CONTEXT_TTL_SECONDS

logger = logging.getLogger(__name__)



class RedisContextManager:
    def __init__(self, host='localhost', port=6379):
        try:
            # [# ИЗМЕНЕНО] Используем redis.Redis.from_url для асинхронного клиента
            self.redis_client = redis.Redis(host=host, port=port, db=0, decode_responses=True)
            logger.info(f"Асинхронный клиент Redis инициализирован для {host}:{port}")
        except Exception as e:
            logger.critical(f"Не удалось инициализировать Redis: {e}")
            self.redis_client = None

    async def check_connection(self):
        """Асинхронная проверка соединения."""
        if not self.redis_client:
            return False
        try:
            await self.redis_client.ping()
            logger.info("Подключение к Redis успешно.")
            return True
        except Exception as e:
            logger.critical(f"Не удалось подключиться к Redis: {e}")
            return False

    def _get_key(self, user_id: str) -> str:
        return f"gigachat_context:{user_id}"

    # [# ИЗМЕНЕНО] Все методы теперь асинхронные
    async def get_context(self, user_id: str) -> Dict[str, Any]:
        if not self.redis_client: return {}
        key = self._get_key(user_id)
        try:
            json_data = await self.redis_client.get(key)
            return json.loads(json_data) if json_data else {}
        except Exception as e:
            logger.error(f"Ошибка при получении контекста для user_id {user_id}: {e}")
            return {}

    async def set_context(self, user_id: str, context_data: Dict[str, Any]):
        if not self.redis_client: return
        key = self._get_key(user_id)
        try:
            json_data = json.dumps(context_data, ensure_ascii=False)
            await self.redis_client.set(key, json_data, ex=CONTEXT_TTL_SECONDS)
        except Exception as e:
            logger.error(f"Ошибка при сохранении контекста для user_id {user_id}: {e}")

    async def delete_context(self, user_id: str):
        if not self.redis_client: return
        key = self._get_key(user_id)
        try:
            await self.redis_client.delete(key)
        except Exception as e:
            logger.error(f"Ошибка при удалении контекста для user_id {user_id}: {e}")
            
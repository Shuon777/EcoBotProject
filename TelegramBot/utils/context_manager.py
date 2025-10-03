import redis
import json
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

# Время жизни контекста в секундах (15 минут)
CONTEXT_TTL_SECONDS = 900 

class RedisContextManager:
    def __init__(self, host='localhost', port=6379):
        """
        Инициализирует менеджер контекста и подключается к Redis.
        """
        try:
            # decode_responses=True заставляет redis-py возвращать строки (str), а не байты (bytes).
            self.redis_client = redis.Redis(host=host, port=port, db=0, decode_responses=True)
            self.redis_client.ping()
            logger.info(f"Успешное подключение к Redis по адресу {host}:{port}")
        except redis.exceptions.ConnectionError as e:
            logger.critical(f"Не удалось подключиться к Redis: {e}. Контекст диалогов работать не будет!")
            self.redis_client = None

    def _get_key(self, user_id: str) -> str:
        """Формирует стандартизированный ключ для хранения в Redis."""
        return f"gigachat_context:{user_id}"

    def get_context(self, user_id: str) -> Dict[str, Any]:
        """
        Получает контекст пользователя из Redis.
        Возвращает пустой словарь, если контекст не найден.
        """
        if not self.redis_client: return {}
        key = self._get_key(user_id)
        try:
            json_data = self.redis_client.get(key)
            return json.loads(json_data) if json_data else {}
        except Exception as e:
            logger.error(f"Ошибка при получении контекста для user_id {user_id}: {e}")
            return {}

    def set_context(self, user_id: str, context_data: Dict[str, Any]):
        """
        Сохраняет контекст пользователя в Redis с установленным временем жизни.
        """
        if not self.redis_client: return
        key = self._get_key(user_id)
        try:
            json_data = json.dumps(context_data, ensure_ascii=False)
            self.redis_client.set(key, json_data, ex=CONTEXT_TTL_SECONDS)
            logger.info(f"Контекст для user_id {user_id} сохранен в Redis. TTL: {CONTEXT_TTL_SECONDS} сек.")
        except Exception as e:
            logger.error(f"Ошибка при сохранении контекста для user_id {user_id}: {e}")

    def delete_context(self, user_id: str):
        """
        Удаляет контекст пользователя из Redis.
        """
        if not self.redis_client: return
        key = self._get_key(user_id)
        try:
            self.redis_client.delete(key)
            logger.info(f"Контекст для user_id {user_id} удален из Redis.")
        except Exception as e:
            logger.error(f"Ошибка при удалении контекста для user_id {user_id}: {e}")
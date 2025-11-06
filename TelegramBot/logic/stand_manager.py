import asyncio
import logging
import aiohttp
from aiogram import Bot
from aiogram.dispatcher import Dispatcher

from utils.settings_manager import update_user_settings, get_user_settings
from config import STAND_SESSION_TIMEOUT, API_URLS, STAND_SECRET_KEY

logger = logging.getLogger(__name__)

# Ключ в Redis, который будет служить замком
STAND_LOCK_KEY = "stand_lock:active_user"

# Словарь для хранения активных задач тайм-аута {user_id: asyncio.Task}
active_stand_tasks = {}

async def _send_reset_to_stand(session: aiohttp.ClientSession):
    # ... (эта функция остается без изменений)
    try:
        url = API_URLS['stand_endpoint']
        payload = {"items": [], "secret_key": STAND_SECRET_KEY}
        async with session.post(url, json=payload, timeout=10) as resp:
            if resp.ok:
                logger.info("Команда сброса успешно отправлена на стенд.")
            else:
                logger.warning(f"Стенд вернул ошибку {resp.status} при попытке сброса.")
    except Exception as e:
        logger.error(f"Ошибка при отправке команды сброса на стенд: {e}", exc_info=True)

async def _clear_stand_lock(user_id_to_check: str):
    """Безопасно удаляет замок, только если он принадлежит текущему пользователю."""
    redis_client = Dispatcher.get_current().get('redis_client')
    if await redis_client.get(STAND_LOCK_KEY) == user_id_to_check:
        await redis_client.delete(STAND_LOCK_KEY)
        logger.info(f"[{user_id_to_check}] Глобальная блокировка стенда снята.")

async def _notify_and_end_session(user_id: str, bot: Bot, session: aiohttp.ClientSession):
    """Фоновая задача, которая ждет таймаут и завершает сессию."""
    try:
        await asyncio.sleep(STAND_SESSION_TIMEOUT)
        
        if get_user_settings(user_id).get("on_stand"):
            logger.info(f"[{user_id}] Сессия со стендом истекла по таймауту.")
            update_user_settings(user_id, {"on_stand": False})
            await _send_reset_to_stand(session)
            await _clear_stand_lock(user_id) # <--- Снимаем блокировку
            await bot.send_message(user_id, "⏳ Ваша сессия взаимодействия со стендом завершена по истечении времени.")
            
    except asyncio.CancelledError:
        logger.info(f"[{user_id}] Задача тайм-аута для стенда была отменена.")
    finally:
        active_stand_tasks.pop(user_id, None)

async def start_stand_session(user_id: str, bot: Bot, session: aiohttp.ClientSession) -> bool:
    """
    Пытается запустить сессию для пользователя.
    Возвращает True в случае успеха, False если стенд занят другим пользователем.
    """
    redis_client = Dispatcher.get_current().get('redis_client')
    
    # Проверяем, кем занят стенд
    locked_by_user = await redis_client.get(STAND_LOCK_KEY)

    if locked_by_user and locked_by_user != user_id:
        logger.warning(f"[{user_id}] Попытка занять уже используемый стенд (занят пользователем {locked_by_user}).")
        return False # Стенд занят, отказываем

    # Если уже есть активная задача для ЭТОГО пользователя, отменяем ее
    if user_id in active_stand_tasks:
        active_stand_tasks[user_id].cancel()
    
    # Устанавливаем или обновляем блокировку с TTL
    await redis_client.set(STAND_LOCK_KEY, user_id, ex=STAND_SESSION_TIMEOUT)
    logger.info(f"[{user_id}] Глобальная блокировка стенда установлена.")

    # Запускаем сессию и таймер
    update_user_settings(user_id, {"on_stand": True})
    task = asyncio.create_task(_notify_and_end_session(user_id, bot, session))
    active_stand_tasks[user_id] = task
    logger.info(f"[{user_id}] Сессия со стендом начата/продлена. Таймаут: {STAND_SESSION_TIMEOUT} сек.")
    return True

async def end_stand_session(user_id: str, session: aiohttp.ClientSession):
    """Завершает сессию для пользователя, отменяет задачу и сбрасывает стенд."""
    update_user_settings(user_id, {"on_stand": False})
    if user_id in active_stand_tasks:
        active_stand_tasks[user_id].cancel()
    
    await _send_reset_to_stand(session)
    await _clear_stand_lock(user_id) # <--- Снимаем блокировку
    logger.info(f"[{user_id}] Сессия со стендом завершена вручную.")

def is_stand_session_active(user_id: str) -> bool:
    return get_user_settings(user_id).get("on_stand", False)
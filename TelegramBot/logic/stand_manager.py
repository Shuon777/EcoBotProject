# Файл: TelegramBot/logic/stand_manager.py

import asyncio
import logging
import aiohttp
from aiogram import Bot

from utils.settings_manager import update_user_settings, get_user_settings
from config import STAND_SESSION_TIMEOUT, API_URLS, STAND_SECRET_KEY

logger = logging.getLogger(__name__)

# Словарь для хранения активных задач тайм-аута {user_id: asyncio.Task}
active_stand_tasks = {}

async def _send_reset_to_stand(session: aiohttp.ClientSession):
    """Отправляет пустой список на эндпоинт стенда для сброса его состояния."""
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

async def _notify_and_end_session(user_id: str, bot: Bot, session: aiohttp.ClientSession):
    """Фоновая задача, которая ждет таймаут и завершает сессию."""
    try:
        await asyncio.sleep(STAND_SESSION_TIMEOUT)
        
        if get_user_settings(user_id).get("on_stand"):
            logger.info(f"[{user_id}] Сессия со стендом истекла по таймауту.")
            update_user_settings(user_id, {"on_stand": False})
            await _send_reset_to_stand(session) # <--- СБРОС СТЕНДА
            await bot.send_message(user_id, "⏳ Ваша сессия взаимодействия со стендом завершена по истечении времени.")
            
    except asyncio.CancelledError:
        logger.info(f"[{user_id}] Задача тайм-аута для стенда была отменена.")
    finally:
        active_stand_tasks.pop(user_id, None)

async def start_stand_session(user_id: str, bot: Bot, session: aiohttp.ClientSession):
    """Запускает сессию для пользователя и фоновую задачу тайм-аута."""
    if user_id in active_stand_tasks:
        active_stand_tasks[user_id].cancel()
        
    update_user_settings(user_id, {"on_stand": True})
    # Передаем session в фоновую задачу
    task = asyncio.create_task(_notify_and_end_session(user_id, bot, session))
    active_stand_tasks[user_id] = task
    logger.info(f"[{user_id}] Сессия со стендом начата. Таймаут: {STAND_SESSION_TIMEOUT} сек.")

async def end_stand_session(user_id: str, session: aiohttp.ClientSession):
    """Завершает сессию для пользователя, отменяет задачу и сбрасывает стенд."""
    update_user_settings(user_id, {"on_stand": False})
    if user_id in active_stand_tasks:
        active_stand_tasks[user_id].cancel()
    
    await _send_reset_to_stand(session) # <--- СБРОС СТЕНДА
    logger.info(f"[{user_id}] Сессия со стендом завершена вручную.")

def is_stand_session_active(user_id: str) -> bool:
    """Простая проверка флага в настройках."""
    return get_user_settings(user_id).get("on_stand", False)
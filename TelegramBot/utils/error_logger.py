import logging
import traceback
import aiohttp
from typing import Optional, Any, Dict
from config import API_URLS

logger = logging.getLogger(__name__)

async def send_error_log(
    session: aiohttp.ClientSession,
    user_query: str,
    user_id: str,
    error: Exception,
    context: Optional[Dict[str, Any]] = None,
    additional_info: Optional[Dict[str, Any]] = None
):
    """
    Отправляет информацию об ошибке на внешний сервис логирования.
    
    Args:
        session: aiohttp сессия
        user_query: Текст запроса или callback_data, на котором упало
        user_id: ID пользователя
        error: Объект исключения
        context: JSON-контекст (история диалога, текущий анализ и т.д.)
        additional_info: Дополнительные метаданные (например, место в коде)
    """
    try:
        # Формируем полный traceback
        tb_str = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        
        # Подготавливаем additional_info
        if additional_info is None:
            additional_info = {}
        
        # Добавляем traceback и user_id в additional_info для удобства
        additional_info.update({
            "user_id": user_id,
            "traceback": tb_str,
            "error_type": type(error).__name__
        })

        payload = {
            "user_query": str(user_query) if user_query else "Unknown Query",
            "error_message": str(error),
            "context": context or {},
            "additional_info": additional_info
        }

        url = API_URLS['log_error']
        
        # Отправляем лог "пожарным" запросом (не блокируем основной поток ошибками самого логгера)
        async with session.post(url, json=payload, timeout=5) as resp:
            if resp.status not in [200, 201]:
                logger.warning(f"Сервис логирования ошибок вернул статус {resp.status}: {await resp.text()}")
            else:
                logger.info(f"[{user_id}] Лог ошибки успешно отправлен на сервер.")

    except Exception as e:
        # Логгер не должен ронять приложение
        logger.error(f"[{user_id}] Не удалось отправить лог ошибки на сервер: {e}", exc_info=True)
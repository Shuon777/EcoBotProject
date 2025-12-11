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
    error: Exception | str, # Обновили тайп-хинт, разрешив строки
    context: Optional[Dict[str, Any]] = None,
    additional_info: Optional[Dict[str, Any]] = None
):
    """
    Отправляет информацию об ошибке на внешний сервис логирования.
    """
    try:
        # Если error - это строка (например, кастомное сообщение), оборачиваем в Exception для traceback
        if isinstance(error, str):
            error_msg = error
            error_type = "CustomError"
            tb_str = "No traceback for custom error"
        else:
            error_msg = str(error)
            error_type = type(error).__name__
            tb_str = "".join(traceback.format_exception(type(error), error, error.__traceback__))
        
        if additional_info is None:
            additional_info = {}
        
        additional_info.update({
            "user_id": user_id,
            "traceback": tb_str,
            "error_type": error_type
        })

        payload = {
            "user_query": str(user_query) if user_query else "Unknown Query",
            "error_message": error_msg,
            "context": context or {},
            "additional_info": additional_info
        }

        url = API_URLS['log_error']
        
        async with session.post(url, json=payload, timeout=5) as resp:
            if resp.status not in [200, 201]:
                logger.warning(f"Сервис логирования ошибок вернул статус {resp.status}: {await resp.text()}")
            else:
                logger.info(f"[{user_id}] Лог ошибки успешно отправлен на сервер.")

    except Exception as e:
        logger.error(f"[{user_id}] Не удалось отправить лог ошибки на сервер: {e}", exc_info=True)


async def log_api_error(
    session: aiohttp.ClientSession,
    user_id: str,
    url: str,
    status: int,
    response_text: str,
    user_query: str,
    context: Optional[Dict[str, Any]] = None,
    source: str = "unknown"
):
    """
    Специализированная функция для логирования ответов API (404, 500 и т.д.).
    """
    error_message = f"API Error: {status} at {url}"
    
    # Обрезаем ответ, если он слишком длинный (например, HTML страница ошибки)
    short_response = (response_text[:500] + '...') if len(response_text) > 500 else response_text

    additional_info = {
        "api_url": url,
        "status_code": status,
        "response_body": short_response,
        "source": source,
        "log_category": "API_FAIL" # Метка, чтобы отличать от крэшей бота
    }

    # Вызываем основной логгер, передавая сформированное сообщение как ошибку
    await send_error_log(
        session=session,
        user_query=user_query,
        user_id=user_id,
        error=error_message,
        context=context,
        additional_info=additional_info
    )
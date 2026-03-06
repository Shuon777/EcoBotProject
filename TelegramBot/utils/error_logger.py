import logging
import traceback
import aiohttp
from typing import Optional, Any, Dict
from config import API_URLS

logger = logging.getLogger(__name__)

async def _send_to_backend(
    session: aiohttp.ClientSession,
    level: str,
    user_query: str,
    user_id: str,
    error_message: str,
    context: Optional[Dict[str, Any]] = None,
    additional_info: Optional[Dict[str, Any]] = None
):
    """Приватная базовая функция отправки лога на бэкенд."""
    try:
        if additional_info is None:
            additional_info = {}
        
        additional_info["user_id"] = user_id
        additional_info["log_level"] = level # CRITICAL, API_FAIL, NLU_MISS, ZERO_RESULTS

        payload = {
            "user_query": str(user_query) if user_query else "Unknown Query",
            "error_message": error_message,
            "context": context or {},
            "additional_info": additional_info
        }

        url = API_URLS.get('log_error')
        if not url:
            logger.warning("Не задан URL для логирования (log_error) в config.py")
            return

        async with session.post(url, json=payload, timeout=5) as resp:
            if resp.status not in [200, 201]:
                logger.warning(f"Сервер логов вернул {resp.status}: {await resp.text()}")
            else:
                logger.info(f"[{user_id}] Лог уровня {level} успешно отправлен в БД.")
    except Exception as e:
        logger.error(f"[{user_id}] Ошибка при отправке лога на сервер: {e}", exc_info=True)


async def log_critical(
    session: aiohttp.ClientSession,
    user_query: str,
    user_id: str,
    error: Exception | str,
    context: Optional[Dict[str, Any]] = None,
    additional_info: Optional[Dict[str, Any]] = None
):
    """
    Уровень 1: КРИТИЧЕСКАЯ ОШИБКА. 
    Используется при падении кода (Python Exception), отвале БД или Redis.
    """
    if isinstance(error, str):
        error_msg = error
        error_type = "CustomError"
        tb_str = "No traceback for custom error"
    else:
        error_msg = str(error)
        error_type = type(error).__name__
        tb_str = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    
    info = additional_info or {}
    info.update({"traceback": tb_str, "error_type": error_type})
    
    await _send_to_backend(session, "CRITICAL", user_query, user_id, error_msg, context, info)


async def log_api_fail(
    session: aiohttp.ClientSession,
    user_id: str,
    url: str,
    status: int,
    response_text: str,
    user_query: str,
    context: Optional[Dict[str, Any]] = None,
    payload: Optional[Any] = None
):
    """
    Уровень 2: ОШИБКА API. 
    Используется, когда бэкенд ответил 404, 500 или вернул битый JSON.
    """
    error_message = f"API Request Failed: {status} at {url}"
    short_response = (response_text[:500] + '...') if len(response_text) > 500 else response_text
    
    info = {
        "api_url": url,
        "status_code": status,
        "response_body": short_response,
        "request_payload": payload
    }
    await _send_to_backend(session, "API_FAIL", user_query, user_id, error_message, context, info)


async def log_nlu_miss(
    session: aiohttp.ClientSession,
    user_query: str,
    user_id: str,
    reason: str,
    context: Optional[Dict[str, Any]] = None
):
    """
    Уровень 3: СБОЙ NLU (LLM). 
    Используется, когда LLM не смогла распарсить запрос за 3 попытки, 
    или выдала action="unknown" на явно природный запрос.
    """
    await _send_to_backend(session, "NLU_MISS", user_query, user_id, reason, context, {})


async def log_zero_results(
    session: aiohttp.ClientSession,
    user_query: str,
    user_id: str,
    action: str,
    search_params: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None
):
    """
    Уровень 4: ПУСТОЙ ОТВЕТ (Нет данных). 
    Используется, когда API ответило 200 OK, но данных в базе нет 
    (например, искали то, чего не существует, или нет фото с такими признаками).
    """
    error_message = f"Zero results for action: {action}"
    info = {"search_params": search_params}
    await _send_to_backend(session, "ZERO_RESULTS", user_query, user_id, error_message, context, info)

# --- АЛИАСЫ ДЛЯ ОБРАТНОЙ СОВМЕСТИМОСТИ ---
# Чтобы не сломать текущий код до полного рефакторинга экшенов
send_error_log = log_critical
log_api_error = log_api_fail
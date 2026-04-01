import aiohttp
import logging
from urllib.parse import unquote
from typing import Optional, List, Callable, Awaitable
from config import API_URLS, DEFAULT_TIMEOUT
from utils.error_logger import log_critical, log_api_fail, log_zero_results
from core.model import CoreResponse

logger = logging.getLogger(__name__)

async def handle_describe_service(
    session: aiohttp.ClientSession,
    analysis: dict,
    user_id: str,
    original_query: str,
    debug_mode: bool,
    on_status: Optional[Callable[[str], Awaitable[None]]] = None
) -> List[CoreResponse]:
    # 1. Подготовка запроса
    clean_query = analysis.get("search_query", original_query)
    
    if on_status:
        await on_status("📚 Ищу информацию в базе знаний...")

    # Инициализируем список дебага, если его нет
    if "debug_traces" not in analysis:
        analysis["debug_traces"] = []

    api_data = None
    # Приоритет: оригинальный запрос (он обычно лучше для FAISS)
    queries_to_try = [original_query]
    if clean_query != original_query:
        queries_to_try.append(clean_query)

    responses = []
    base_url = API_URLS['find_geo_special_description']

    # Заголовки как в обычном браузере (на случай блокировок)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    try:
        for i, query_text in enumerate(queries_to_try):
            params = {
                "query": query_text,
                "force_vector_search": "true",
                "debug_mode": str(debug_mode).lower(),
                "in_stoplist": "1"
            }

            # Принудительно ставим JSON-заголовок
            headers = {
                "Content-Type": "application/json"
            }

            # Мы используем GET, но передаем пустой или "фиктивный" JSON-объект
            # Это заставит aiohttp отправить Content-Type: application/json
            async with session.get(
                base_url, 
                params=params, 
                json={"dummy": "fix_415"},
                headers=headers, 
                timeout=DEFAULT_TIMEOUT, 
                ssl=False
            ) as resp:
                
                actual_url = str(resp.url)
                readable_url = unquote(actual_url)
                analysis["debug_traces"].append(f"URL: `{readable_url}`")
                
                if resp.ok:
                    api_data = await resp.json()
                    break 
                
                # Если всё еще ошибка, залогируем её тело (теперь мы увидим детали)
                resp_text = await resp.text()
                logger.error(f"API Error {resp.status} for URL {actual_url}: {resp_text}")
                
                if i == len(queries_to_try) - 1:
                    await log_api_fail(session, user_id, actual_url, resp.status, resp_text, query_text, context=analysis)
                    return [CoreResponse(type="text", content="Извините, информация временно недоступна.")]

        # 2. Разбор ответа
        if api_data and (descriptions := api_data.get("descriptions")):
            main_text = ""
            # Ищем первое непустое описание
            for desc in descriptions:
                if content := desc.get("content"):
                    if content.strip():
                        main_text = content.strip()
                        break
            
            if main_text:
                responses.append(CoreResponse(
                    type="text",
                    content=main_text,
                    used_objects=api_data.get("used_objects", [])
                ))
                
                return responses

        # 3. Если ничего не найдено
        return [CoreResponse(type="text", content="К сожалению, в базе знаний нет точного ответа на этот вопрос.")]

    except Exception as e:
        logger.error(f"Ошибка в handle_describe_service: {e}", exc_info=True)
        # Если в дебаг ещё не успели записать URL, запишем хотя бы базу
        if not any("URL:" in t for t in analysis["debug_traces"]):
            analysis["debug_traces"].append(f"URL (crash): `{base_url}`")
            
        await log_critical(session, original_query, user_id, e, analysis)
        return [CoreResponse(type="text", content=f"Произошла ошибка: {str(e)}")]
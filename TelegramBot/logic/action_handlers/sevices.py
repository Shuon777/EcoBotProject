import os
import aiohttp
import logging
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
    
    clean_query = analysis.get("search_query", original_query)
    
    if on_status:
        await on_status("🗺️ Ищу информацию...")

    api_data = None

    queries_to_try = [original_query]
    if clean_query != original_query: queries_to_try.append(clean_query)

    responses = []
    if debug_mode:
        analysis["debug_traces"].append(f"Query Cleaned: {clean_query}")

    try:
        for i, query_text in enumerate(queries_to_try):
            
            base_url = API_URLS['find_geo_special_description']
            params = f"query={query_text}&force_vector_search=true&debug_mode={str(debug_mode).lower()}"
            
            url = f"{base_url}?{params}"

            if debug_mode:
                # Если в словаре ещё нет ключа, создаем его на всякий случай
                if "debug_traces" not in analysis:
                    analysis["debug_traces"] = []
                analysis["debug_traces"].append(f"URL: `{url}`")

            async with session.get(url, timeout=DEFAULT_TIMEOUT) as resp:
                if resp.ok:
                    api_data = await resp.json()
                    break # Успех, выходим из цикла
                elif i == len(queries_to_try) - 1:
                    # Если это была последняя попытка и она провалилась
                    await log_api_fail(session, user_id, url, resp.status, await resp.text(), query_text, context=analysis)
                    return [CoreResponse(type="text", content="Извините, информация временно недоступна.")]
        
        if descriptions := api_data.get("descriptions"):
            # Если нет GigaChat, ищем описания
            first_valid_index = -1
            text_content = ""

            # Ищем первое непустое описание
            for i, desc in enumerate(descriptions):
                if content := desc.get("content"):
                    if content.strip():
                        text_content = content.strip()
                        first_valid_index = i
                        break
            
            if text_content:
                responses.append(CoreResponse(
                    type="text", 
                    content=text_content,
                    used_objects=api_data.get("used_objects", [])
                ))

            if first_valid_index != -1:
                remaining_titles = []
                for desc in descriptions[first_valid_index + 1:]:
                    if title := desc.get("title"):
                        if cleaned_title := title.strip():
                            remaining_titles.append(cleaned_title)
                    if len(remaining_titles) >= 5:
                        break

                if remaining_titles:
                    title_list_str = "\n".join(f"• {title}" for title in remaining_titles)
                    full_title_message = f"Также могут быть интересны:\n{title_list_str}"
                    responses.append(CoreResponse(type="text", content=full_title_message))


        if not responses or (len(responses) == 1 and responses[0].type == 'debug'):
            await log_zero_results(
                session, original_query, user_id,
                action="describe_geo",
                search_params={"query": query_text},
                context=analysis
            )
            responses.append(CoreResponse(type="text", content="К сожалению, по вашему запросу ничего не найдено."))

        return responses

    except Exception as e:
        logger.error(f"Ошибка geo_request: {e}", exc_info=True)
        await log_critical(session, original_query, user_id, e, analysis)
        return [CoreResponse(type="text", content="Внутренняя ошибка поиска.")]
import logging
import json
from typing import Dict, Any, List, Callable, Awaitable, Optional

# Наши модули
from .schemas import UserRequest, SystemResponse, IntentType
from .router import SemanticRouter
from .rewriter import QueryRewriter
from .workers.biology import BiologyWorker
from .workers.infrastructure import InfrastructureWorker
from .workers.knowledge import KnowledgeWorker

# Старые хендлеры
from core.model import CoreResponse
from logic.action_handlers.biological import handle_get_description, handle_get_picture
from logic.action_handlers.geospatial import (
    handle_draw_locate_map, handle_nearest, handle_objects_in_polygon,
    handle_geo_request, handle_draw_map_of_infrastructure
)
from logic.action_handlers.sevices import handle_describe_service

logger = logging.getLogger("DialogueSystem")
ActionHandler = Callable[..., Awaitable[List[CoreResponse]]]

class DialogueSystem:
    def __init__(self, provider: str = "qwen", session=None, context_manager=None):
        self.session = session
        self.provider = provider
        self.context_manager = context_manager
        
        self.rewriter = QueryRewriter(provider)
        self.router = SemanticRouter(provider)
        
        self.biology_worker = BiologyWorker(provider)
        self.infra_worker = InfrastructureWorker(provider)
        self.knowledge_worker = KnowledgeWorker(provider)
        
        self.action_handlers: Dict[tuple[str, str], ActionHandler] = {
            ("describe", "Biological"): handle_get_description,
            ("describe", "Infrastructure"): handle_geo_request,
            ("describe", "GeoPlace"): handle_geo_request,
            ("describe", "Service"): handle_describe_service,
            ("describe", "Knowledge"): handle_describe_service,
            ("show_image", "Biological"): handle_get_picture,
            ("show_map", "Biological"): handle_draw_locate_map,
            ("show_map", "Infrastructure"): handle_draw_map_of_infrastructure,
            ("find_nearby", "Biological"): handle_nearest,
            ("find_nearby", "ANY"): handle_nearest,
            ("list_items", "Biological"): handle_objects_in_polygon,
            ("list_items", "Infrastructure"): handle_geo_request,
            ("list_items", "GeoPlace"): handle_geo_request,
            ("count_items", "Infrastructure"): handle_geo_request,
        }

    async def process_request(self, request: UserRequest) -> List[SystemResponse]:
        logger.info(f"--- 🚀 START PROCESS: {request.query} ---")
        debug_enabled = request.settings.get("debug_mode", False)
        debug_traces = []
        final_responses = []
        current_intent = "KNOWLEDGE"

        # 1. Перехват технических команд (Кнопки)
        if ":" in request.query or request.query.startswith("clarify"):
            return await self._handle_technical_call(request, debug_traces)

        try:
            # 2. Контекст (Rewriter)
            standalone_query = await self.rewriter.rewrite(request.query, request.context)
            if debug_enabled: debug_traces.append(f"🔍 <b>Rewriter:</b> {standalone_query}")

            # 3. Роутинг (Router)
            current_intent = await self.router.get_intent(standalone_query)
            if debug_enabled: debug_traces.append(f"🎯 <b>Router Intent:</b> {current_intent}")

            # 4. Исполнение веток
            if current_intent == "BIOLOGY":
                final_responses = await self._run_biology_flow(standalone_query, request, current_intent, debug_traces)
            elif current_intent == "INFRASTRUCTURE":
                final_responses = await self._run_infra_flow(standalone_query, request, current_intent, debug_traces)
            elif current_intent == "KNOWLEDGE":
                final_responses = await self._run_knowledge_flow(standalone_query, request, current_intent, debug_traces)
            else:
                final_responses = [SystemResponse(text="Я Эко-бот. Чем могу помочь?", intent="CHITCHAT")]

            # --- ФИНАЛЬНЫЙ СБОР ДЕБАГА ДЛЯ АДМИНКИ ---
            if debug_enabled and debug_traces:
                # Добавляем в конец списка отдельное сообщение с типом debug
                final_responses.append(SystemResponse(
                    text="\n\n".join(debug_traces),
                    intent=current_intent,
                    response_type="debug"
                ))

            return final_responses

        except Exception as e:
            logger.error(f"❌ Pipeline Failure: {e}", exc_info=True)
            return [SystemResponse(text=f"Ошибка: {e}", intent="KNOWLEDGE")]

    # --- ВЕТКИ (FLOWS) ---

    async def _run_biology_flow(self, query: str, request: UserRequest, intent: str, debug_traces: list) -> List[SystemResponse]:
        analysis, nlu_debug = await self.biology_worker.analyze(query)
        if request.settings.get("debug_mode"): debug_traces.append(nlu_debug)
        
        legacy_data = {
            "action": analysis.action,
            "primary_entity": {"name": analysis.species_name, "type": "Biological", "category": analysis.category},
            "secondary_entity": {"name": analysis.location_context, "type": "GeoPlace"} if analysis.location_context else None,
            "attributes": analysis.attributes,
            "debug_traces": []
        }
        return await self._execute_handler(analysis.action, "Biological", legacy_data, query, request, intent, debug_traces)

    async def _run_infra_flow(self, query: str, request: UserRequest, intent: str, debug_traces: list) -> List[SystemResponse]:
        analysis = await self.infra_worker.analyze(query)
        if request.settings.get("debug_mode"): 
            debug_traces.append(f"🏛️ <b>Infra NLU:</b>\n{json.dumps(analysis.model_dump(), ensure_ascii=False)}")
        
        legacy_data = {
            "action": analysis.action,
            "primary_entity": {"name": analysis.object_name, "type": analysis.entity_type, "category": analysis.category, "subcategory": analysis.subcategory},
            "secondary_entity": {"name": analysis.area_name, "type": "GeoPlace"} if analysis.area_name else None,
            "attributes": {},
            "debug_traces": []
        }
        return await self._execute_handler(analysis.action, analysis.entity_type, legacy_data, query, request, intent, debug_traces)

    async def _run_knowledge_flow(self, query: str, request: UserRequest, intent: str, debug_traces: list) -> List[SystemResponse]:
        analysis = await self.knowledge_worker.analyze(query)
        if request.settings.get("debug_mode"):
            debug_traces.append(f"📚 <b>Knowledge NLU:</b> {json.dumps(analysis.model_dump(), ensure_ascii=False)}")
            
        legacy_data = {
            "action": "describe",
            "primary_entity": {"name": analysis.search_query, "type": "Service"},
            "search_query": analysis.search_query,
            "debug_traces": []
        }
        return await self._execute_handler("describe", "Knowledge", legacy_data, query, request, intent, debug_traces)

    # --- СИСТЕМНЫЕ МЕТОДЫ ---

    async def _handle_technical_call(self, request: UserRequest, debug_traces: list) -> List[SystemResponse]:
        if request.query.startswith("clarify_idx:"):
            try:
                idx = int(request.query.split(":")[1])
                data = await self.context_manager.get_context(f"clarify_options:{request.user_id}")
                options = data.get("options", [])
                if options and idx < len(options):
                    selected = options[idx]
                    if request.settings.get("debug_mode"): debug_traces.append(f"✅ <b>Resolved Button:</b> {selected}")
                    legacy_data = {"action": "describe", "primary_entity": {"name": selected, "type": "Biological"}, "debug_traces": []}
                    
                    results = await self._execute_handler("describe", "Biological", legacy_data, selected, request, "BIOLOGY", debug_traces)
                    
                    # Добавляем дебаг в конец технического вызова
                    if request.settings.get("debug_mode"):
                        results.append(SystemResponse(text="\n\n".join(debug_traces), intent="BIOLOGY", response_type="debug"))
                    return results
            except Exception as e: logger.error(f"Technical call error: {e}")
        return [SystemResponse(text="Ошибка обработки выбора.", intent="KNOWLEDGE")]

    async def _execute_handler(self, action, e_type, legacy_data, query, request, intent, debug_traces) -> List[SystemResponse]:
        handler = self.action_handlers.get((action, e_type)) or self.action_handlers.get((action, "ANY"))
        logger.info(f"Choosen handler: {handler}")

        if not handler: return [SystemResponse(text="Обработчик не найден.", intent=intent)]

        core_resps = await handler(
            session=self.session, analysis=legacy_data, user_id=request.user_id,
            original_query=query, debug_mode=request.settings.get("debug_mode", False)
        )
        
        # Собираем трейсы из старого кода (они приходят через список в legacy_data)
        if request.settings.get("debug_mode") and "debug_traces" in legacy_data:
            debug_traces.extend(legacy_data["debug_traces"])
        
        return self._map_to_system_responses(core_resps, intent)

    def _map_to_system_responses(self, core_resps: List[CoreResponse], intent: str) -> List[SystemResponse]:
        if not core_resps: return [SystemResponse(text="Ничего не найдено.", intent=intent)]
        results = []
        for resp in core_resps:
            display_text = resp.content
            media_url = None
            static_map = None
            current_buttons = resp.buttons or []

            if resp.type in ["image", "photo"]:
                media_url = resp.content
                display_text = ""
            elif resp.type in ["map", "clarification_map"]:
                media_url = resp.static_map
                static_map = resp.static_map
                if hasattr(resp, 'interactive_map') and resp.interactive_map:
                    current_buttons.append([{"text": "🌍 Открыть интерактивную карту", "url": resp.interactive_map}])

            results.append(SystemResponse(
                text=display_text, intent=intent, response_type=resp.type,
                buttons=current_buttons, media_url=media_url
            ))
            if static_map: results[-1].media_url = static_map
        return results
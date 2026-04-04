import logging
import json
import time
from typing import Dict, Any, List, Callable, Awaitable, Optional

# Наши модули
from .schemas import UserRequest, SystemResponse, IntentType, DialogueState
from .router import SemanticRouter
from .workers.biology import BiologyWorker
from .workers.infrastructure import InfrastructureWorker
from .workers.knowledge import KnowledgeWorker
from .state_manager import DialogueStateManager

# Старые хендлеры
from core.model import CoreResponse
from logic.action_handlers.biological import handle_get_description, handle_get_picture
from logic.action_handlers.geospatial import (
    handle_draw_locate_map, handle_nearest, handle_objects_in_polygon,
    handle_geo_request, handle_draw_map_of_infrastructure
)
from logic.action_handlers.sevices import handle_describe_service
from .rewriter import QueryRewriter


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
        self.state_manager = DialogueStateManager()
        
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
        debug_traces =[]
        
        state_key = f"state:{request.user_id}"
        state_data = await self.context_manager.get_context(state_key)
        previous_state = DialogueState(**state_data) if state_data else DialogueState()

        # 1. Перехват технических команд (Кнопки)
        if ":" in request.query or request.query.startswith("clarify"):
            return await self._handle_technical_call(request, debug_traces)

        try:
            # 2. РЕРАЙТЕР: Восстанавливаем запрос из истории
            # Передаем оригинальный запрос и отформатированную историю
            rewritten_query = await self.rewriter.rewrite(request.query, request.context)
            if debug_enabled and rewritten_query != request.query:
                debug_traces.append(f"✍️ <b>Rewriter:</b> <code>{request.query}</code> ➡️ <code>{rewritten_query}</code>")
            
            # Заменяем запрос на переписанный для всей дальнейшей логики!
            actual_query = rewritten_query

            # 3. ROUTER (определяет направленность запроса)
            current_intent, routing_source = await self.router.get_intent(actual_query, previous_state.intent)
            if debug_enabled: 
                debug_traces.append(f"🎯 <b>Router Intent:</b> {current_intent} <i>(via {routing_source})</i>")

            # 4. ИСПОЛНЕНИЕ (передаем actual_query воркерам)
            final_responses =[]
            if current_intent == "BIOLOGY":
                final_responses = await self._run_biology_flow(actual_query, request, current_intent, previous_state, debug_traces)
            elif current_intent == "INFRASTRUCTURE":
                final_responses = await self._run_infra_flow(actual_query, request, current_intent, previous_state, debug_traces)
            elif current_intent == "KNOWLEDGE":
                final_responses = await self._run_knowledge_flow(actual_query, request, current_intent, debug_traces)
            else:
                final_responses =[SystemResponse(text="Чем могу помочь?", intent="CHITCHAT")]

            # Добавляем дебаг-сообщение самым первым в списке (чтобы оно было сверху)
            if debug_enabled and debug_traces:
                final_responses.insert(0, SystemResponse(
                    text="\n\n".join(debug_traces), intent=current_intent, response_type="debug"
                ))

            return final_responses

        except Exception as e:
            logger.error(f"❌ Pipeline Failure: {e}", exc_info=True)
            return [SystemResponse(text=f"Произошла ошибка при обработке: {e}", intent="KNOWLEDGE")]

    # --- ВЕТКИ (FLOWS) ---

    async def _run_biology_flow(self, query: str, request: UserRequest, intent: str, previous: DialogueState, debug_traces: list) -> List[SystemResponse]:
        # NLU вытаскивает только то, что в текущей фразе
        current_nlu, nlu_debug = await self.biology_worker.analyze(query)
        if request.settings.get("debug_mode"): debug_traces.append(nlu_debug)
        
        # Слияние состояний
        final_state = self.state_manager.merge_state(current_nlu.model_dump(), previous, intent)
        
        if not final_state.object_name:
            return [SystemResponse(text="О каком растении или животном вы спрашиваете?", intent=intent)]

        if request.settings.get("debug_mode"):
            debug_traces.append(f"🧠 <b>Context Merge:</b> Using object '{final_state.object_name}' with attributes {final_state.attributes}")

        legacy_data = {
            "action": final_state.last_action or "describe",
            "primary_entity": {"name": final_state.object_name, "type": "Biological", "category": final_state.category},
            "secondary_entity": {"name": final_state.location, "type": "GeoPlace"} if final_state.location else None,
            "attributes": final_state.attributes,
            "debug_traces": []
        }
        
        responses = await self._execute_handler(legacy_data["action"], "Biological", legacy_data, query, request, intent, debug_traces)
        
        # Сохраняем состояние только при успехе (если не "ничего не найдено")
        if responses and "ничего не найдено" not in responses[0].text.lower():
            await self.context_manager.set_context(f"state:{request.user_id}", final_state.model_dump())
            
        return responses

    async def _run_infra_flow(self, query: str, request: UserRequest, intent: str, previous: DialogueState, debug_traces: list) -> List[SystemResponse]:
        analysis, infra_debug = await self.infra_worker.analyze(query)
        if request.settings.get("debug_mode"): 
            debug_traces.append(f"🏛️ <b>Infra NLU:</b>\n{json.dumps(analysis.model_dump(), ensure_ascii=False)}")
        
        final_state = self.state_manager.merge_state(analysis.model_dump(), previous, intent)

        legacy_data = {
            "action": final_state.last_action or "describe",
            "primary_entity": {"name": final_state.object_name, "type": "Infrastructure", "category": analysis.category, "subcategory": analysis.subcategory},
            "secondary_entity": {"name": final_state.location, "type": "GeoPlace"} if final_state.location else None,
            "attributes": {},
            "debug_traces": []
        }
        
        responses = await self._execute_handler(legacy_data["action"], "Infrastructure", legacy_data, query, request, intent, debug_traces)
        
        if responses:
            await self.context_manager.set_context(f"state:{request.user_id}", final_state.model_dump())
        return responses

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
        # Ветка знаний (RAG) обычно не хранит состояние объектов, так как это разовые FAQ
        return await self._execute_handler("describe", "Knowledge", legacy_data, query, request, intent, debug_traces)

    # --- СИСТЕМНЫЕ МЕТОДЫ ---

    async def _handle_technical_call(self, request: UserRequest, debug_traces: list) -> List[SystemResponse]:
        # Логика кнопок остается прежней, но может обновлять state при необходимости
        if request.query.startswith("clarify_idx:"):
            try:
                idx = int(request.query.split(":")[1])
                data = await self.context_manager.get_context(f"clarify_options:{request.user_id}")
                options = data.get("options", [])
                if options and idx < len(options):
                    selected = options[idx]
                    legacy_data = {"action": "describe", "primary_entity": {"name": selected, "type": "Biological"}, "debug_traces": []}
                    res = await self._execute_handler("describe", "Biological", legacy_data, selected, request, "BIOLOGY", debug_traces)
                    
                    # Обновляем состояние объекта после выбора из списка
                    new_state = DialogueState(intent="BIOLOGY", object_name=selected, last_action="describe")
                    await self.context_manager.set_context(f"state:{request.user_id}", new_state.model_dump())
                    
                    if request.settings.get("debug_mode"):
                        res.append(SystemResponse(text="\n\n".join(debug_traces), intent="BIOLOGY", response_type="debug"))
                    return res
            except Exception as e: logger.error(f"Technical call error: {e}")
        return [SystemResponse(text="Ошибка обработки выбора.", intent="KNOWLEDGE")]

    async def _execute_handler(self, action, e_type, legacy_data, query, request, intent, debug_traces) -> List[SystemResponse]:
        handler = self.action_handlers.get((action, e_type)) or self.action_handlers.get((action, "ANY"))
        if not handler: return [SystemResponse(text="Обработчик не найден.", intent=intent)]

        core_resps = await handler(
            session=self.session, analysis=legacy_data, user_id=request.user_id,
            original_query=query, debug_mode=request.settings.get("debug_mode", False)
        )
        
        if request.settings.get("debug_mode") and "debug_traces" in legacy_data:
            debug_traces.extend(legacy_data["debug_traces"])
        
        return self._map_to_system_responses(core_resps, intent)

    def _map_to_system_responses(self, core_resps: List[CoreResponse], intent: str) -> List[SystemResponse]:
        if not core_resps: return [SystemResponse(text="Ничего не найдено.", intent=intent)]
        results = []
        for resp in core_resps:
            # Логика маппинга для админки (content vs text)
            display_text = resp.content
            media_url = None
            if resp.type in ["image", "photo"]:
                media_url = resp.content
                display_text = ""
            elif resp.type in ["map", "clarification_map"]:
                media_url = resp.static_map

            sys_resp = SystemResponse(
                text=display_text, intent=intent, response_type=resp.type,
                buttons=resp.buttons or [], media_url=media_url
            )
            
            # Добавляем интерактивную карту если она есть
            if hasattr(resp, 'interactive_map') and resp.interactive_map:
                if not any(btn.get('url') for row in sys_resp.buttons for btn in row):
                    sys_resp.buttons.append([{"text": "🌍 Открыть карту", "url": resp.interactive_map}])
            
            results.append(sys_resp)
        return results
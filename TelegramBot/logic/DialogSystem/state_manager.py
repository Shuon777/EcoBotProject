import logging
from typing import Optional
from .schemas import DialogueState

logger = logging.getLogger("StateManager")

class DialogueStateManager:
    @staticmethod
    def merge_state(current_nlu: dict, previous: DialogueState, intent: str) -> DialogueState:
        """
        Логика слияния текущего анализа и предыдущего состояния.
        """
        # Если юзер назвал новый объект - это СМЕНА темы.
        # Мы сбрасываем старые признаки (осень, берег), но сохраняем действие (show_image).
        new_object = current_nlu.get("species_name") or current_nlu.get("object_name")
        
        if new_object and new_object != previous.object_name:
            logger.info(f"🔄 Change of topic detected: {previous.object_name} -> {new_object}")
            return DialogueState(
                intent=intent,
                object_name=new_object,
                category=current_nlu.get("category"),
                location=current_nlu.get("location_context") or current_nlu.get("area_name"),
                last_action=current_nlu.get("action") or previous.last_action,
                attributes=current_nlu.get("attributes") or {} # Сброс старых атрибутов
            )

        # Если объект НЕ назван (например, "А осенью?"), берем объект из памяти
        # и дополняем/обновляем атрибуты.
        merged_attributes = previous.attributes.copy()
        if current_nlu.get("attributes"):
            merged_attributes.update(current_nlu.get("attributes"))

        return DialogueState(
            intent=intent,
            object_name=previous.object_name,
            category=current_nlu.get("category") or previous.category,
            location=(current_nlu.get("location_context") or current_nlu.get("area_name")) or previous.location,
            last_action=current_nlu.get("action") or previous.last_action,
            attributes=merged_attributes
        )
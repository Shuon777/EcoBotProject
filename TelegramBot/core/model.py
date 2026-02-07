from pydantic import BaseModel, Field
from typing import List, Optional, Any, Dict, Literal

# Типы ответов, которые поддерживает система
ResponseType = Literal[
    "text",           # Просто текст
    "image",          # Картинка (content = url)
    "file",           # Документ/Файл
    "map",            # Карта (static + interactive)
    "clarification",  # Уточнение с кнопками
    "clarification_map", # Карта + кнопки (для objects_in_polygon)
    "debug"           # Техническая инфа
]

class CoreResponse(BaseModel):
    """
    Универсальный ответ от бизнес-логики.
    Не содержит объектов Telegram, только чистые данные.
    """
    type: ResponseType
    content: str = ""  # Текст сообщения или URL картинки/файла
    
    # Кнопки: список строк, где каждая строка — список кнопок (текст + данные)
    # Пример: [[{'text': 'Да', 'callback_data': 'yes'}, {'text': 'Нет', 'data': 'no'}]]
    buttons: List[List[Dict[str, Any]]] = Field(default_factory=list)
    
    # Специфичные поля для карт
    static_map: Optional[str] = None
    interactive_map: Optional[str] = None
    
    # Контекст (used_objects), который нужно сохранить в историю
    used_objects: List[Dict[str, Any]] = Field(default_factory=list)
    
    # Для отладки
    debug_info: Optional[str] = None
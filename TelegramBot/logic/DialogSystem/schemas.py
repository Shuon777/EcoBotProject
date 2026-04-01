# TelegramBot/logic/dialogue_system/schemas.py
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, Literal

# Типы намерений, которые определяет Роутер
IntentType = Literal["BIOLOGY", "GENERAL_KNOWLEDGE", "CHITCHAT", "HELP"]

class UserRequest(BaseModel):
    user_id: str
    query: str
    context: List[Dict[str, str]] = Field(default_factory=list) # Стандартная история сообщений
    settings: Dict[str, Any] = Field(default_factory=dict)

class SystemResponse(BaseModel):
    text: str
    intent: str
    response_type: str = "text"
    buttons: List[List[Dict[str, Any]]] = Field(default_factory=list)
    media_url: Optional[str] = None
    debug_info: Optional[Any] = None # Теперь сюда будем писать трейсы
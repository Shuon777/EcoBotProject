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
    debug_info: Optional[Any] = None

class DialogueState(BaseModel):
    """Снимок состояния диалога для одного пользователя"""
    intent: Optional[str] = None
    object_name: Optional[str] = None
    category: Optional[str] = None
    location: Optional[str] = None
    attributes: Dict[str, str] = Field(default_factory=dict)
    last_action: Optional[str] = None
    timestamp: float = 0.0
# TelegramBot/logic/validator.py
from typing import List, Optional, Literal
from pydantic import BaseModel, Field, field_validator, model_validator

# --- СПИСКИ ДОПУСТИМЫХ ЗНАЧЕНИЙ ---

VALID_ACTIONS = [
    "describe", "show_image", "show_map", "find_nearby", 
    "list_items", "count_items", "unknown"
]

VALID_TYPES = ["Biological", "GeoPlace", "Infrastructure", "Unknown"]

# Категории для Infrastructure
INFRASTRUCTURE_CATEGORIES = ["Природный объект", "Достопримечательности"]
INFRASTRUCTURE_SUBCATEGORIES = [
    "Археологические", "Архитектурные", "Биогеоценотические", "Биологические", "Вершины", 
    "Гидрографические", "Гидрологические", "Гидрометеорологические", "Гидротехнические сооружения", 
    "Гиордологические", "Гляциологические", "Горнолыжные базы", "Горы", "Достопримечательности", 
    "Железнодорожные сооружения", "Заповедники", "Инженерные конструкции", "Инженерные сооружения", 
    "Исторические", "Карстовые рельефы", "Культовые", "Ландшафтные", "Лечебно-оздоровительные", 
    "Лечебные", "Лечебные минеральные источники", "Микроклиматические", "Минеральные источники", 
    "Музеи", "Мысы", "Наука", "Обзорные площадки", "Орнитологические", "Палеонтологические", 
    "Памятники", "Петроглифы", "Пляжи", "Религиозные", "Священные места", "Скалы", 
    "Скальные образования", "Скульптуры", "Смотровые площадки", "Спелеологические", "Сёла", 
    "Термокарстовые рельефы", "Арт-объекты", "Архитектурные памятники", "Галереи", 
    "Железнодорожные вокзалы", "Заказники", "Исторические достопримечательности", 
    "Исторические объекты", "Исторические памятники", "Исторические места", "Культовые объекты", 
    "Муралы", "Оздоровительные комплексы", "Паломнические маршруты", "Паломнические центры", 
    "Парки деревянных скульптур", "Парк-музеи", "Парковые комплексы", "Паровозы", 
    "Познавательные туризмы", "Поминальные памятники", "Произведения искусства", 
    "Промышленные памятники", "Религиозные объекты", "Санатории", "Туристические объекты", 
    "Фестивали", "Этнографические", "Этнографические музеи", "Геоархеологические", "Городища", 
    "Могильники", "Святилища", "Стоянки", "Хуннские могильники", "Арт-пространства", "Города", 
    "Железнодорожные станции", "Курорты", "Туристические базы", "Фермы", "Храмы", "Маяки", 
    "Железобетонные виадуки"
]

# Категории для Biological
BIOLOGICAL_CATEGORIES = ["Flora", "Fauna"]

class Entity(BaseModel):
    name: Optional[str] = None
    type: Literal["Biological", "GeoPlace", "Infrastructure", "Unknown"] = "Unknown"
    category: Optional[str] = None
    # subcategory ВСЕГДА список, даже если пустой
    subcategory: List[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def validate_logic(self):
        # 1. Если Biological, subcategory должна быть пустой
        if self.type == "Biological":
            if self.subcategory:
                self.subcategory = []
            if self.category and self.category not in BIOLOGICAL_CATEGORIES:
                self.category = None

        # 2. Если GeoPlace, subcategory и category должны быть пустыми
        if self.type == "GeoPlace":
            self.subcategory = []
            self.category = None

        # 3. Если Infrastructure
        if self.type == "Infrastructure":
            # Проверяем категорию
            if self.category and self.category not in INFRASTRUCTURE_CATEGORIES:
                self.category = "Достопримечательности"
            
            # Проверяем подкатегории (фильтруем мусор)
            valid_subs = [sub for sub in self.subcategory if sub in INFRASTRUCTURE_SUBCATEGORIES]
            self.subcategory = valid_subs

        return self

class AnalysisResponse(BaseModel):
    reasoning: Optional[str] = Field(None, alias="_reasoning")
    search_query: str = Field(description="Очищенный поисковый запрос без вводных слов")
    action: str
    primary_entity: Optional[Entity] = None
    secondary_entity: Optional[Entity] = None
    attributes: dict = Field(default_factory=dict)

    @field_validator('action')
    def validate_action(cls, v):
        if v not in VALID_ACTIONS:
            # Если действие выдумано, меняем на unknown, чтобы не крашить бота
            return "unknown"
        return v
# --- НАЧАЛО ФАЙЛА: handlers/general.py ---

from aiogram import Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# [# ИСПРАВЛЕНО] Убираем импорт несуществующей функции
from utils.settings_manager import get_user_settings, update_user_settings

main_keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True).add(types.KeyboardButton("⚙ Настройки"))

def create_settings_keyboard(user_id: str) -> InlineKeyboardMarkup:
    user_settings = get_user_settings(user_id)
    current_mode = user_settings.get("mode", "rasa")
    # [# ИСПРАВЛЕНО] Получаем значение fallback из общей функции
    fallback_enabled = user_settings.get("gigachat_fallback", False)

    rasa_button_text = "✅ Режим: Rasa" if current_mode == "rasa" else "Режим: Rasa"
    gigachat_button_text = "✅ Режим: GigaChat" if current_mode == "gigachat" else "Режим: GigaChat"
    fallback_status = "✅ Вкл" if fallback_enabled else "❌ Выкл"
    
    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton(rasa_button_text, callback_data="set_mode_rasa"),
        InlineKeyboardButton(gigachat_button_text, callback_data="set_mode_gigachat")
    )
    keyboard.add(
        InlineKeyboardButton(f"Дополнять GigaChat: {fallback_status}", callback_data="toggle_fallback")
    )
    return keyboard

# --- Регистрация обработчиков ---

def register_general_handlers(dp: Dispatcher):
    """Регистрирует общие обработчики команд и настроек."""

    @dp.message_handler(commands=["start"])
    async def handle_start(message: types.Message):
        user_id = str(message.from_user.id)
        mode_name = "Rasa" if get_user_settings(user_id).get("mode", "rasa") == "rasa" else "GigaChat"
        await message.answer(
            f"Добро пожаловать в эко-ассистент Байкала 🌿\n"
            f"Текущий режим: {mode_name}\n"
            f"Сменить можно в ⚙ Настройки\n\n"
            f"Чем могу помочь?",
            reply_markup=main_keyboard
        )

    @dp.message_handler(lambda message: message.text == "⚙ Настройки")
    async def handle_settings(message: types.Message):
        user_id = str(message.from_user.id)
        keyboard = create_settings_keyboard(user_id)
        await message.answer("Меню настроек:", reply_markup=keyboard)

    @dp.callback_query_handler(lambda c: c.data in ["set_mode_rasa", "set_mode_gigachat", "toggle_fallback"])
    async def process_settings_callback(callback_query: types.CallbackQuery):
        user_id = str(callback_query.from_user.id)
        data = callback_query.data

        if data == "set_mode_rasa":
            update_user_settings(user_id, {"mode": "rasa"})
            await callback_query.answer("Режим переключен на Rasa")
        elif data == "set_mode_gigachat":
            update_user_settings(user_id, {"mode": "gigachat"})
            await callback_query.answer("Режим переключен на GigaChat")
        elif data == "toggle_fallback":
            # [# ИСПРАВЛЕНО] Получаем текущее состояние и меняем его
            current_fallback_state = get_user_settings(user_id).get("gigachat_fallback", False)
            new_state = not current_fallback_state
            update_user_settings(user_id, {"gigachat_fallback": new_state})
            await callback_query.answer(f"Режим дополнения GigaChat {'включен' if new_state else 'выключен'}")

        keyboard = create_settings_keyboard(user_id)
        try:
            # Избегаем ошибки, если сообщение уже не существует или было изменено
            if callback_query.message and callback_query.message.reply_markup != keyboard:
                await callback_query.message.edit_reply_markup(keyboard)
        except Exception:
            pass # Если не удалось обновить, ничего страшного
            
# --- КОНЕЦ ФАЙЛА: handlers/general.py ---
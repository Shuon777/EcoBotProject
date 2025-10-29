# --- НАЧАЛО ФАЙЛА: handlers/general.py ---

from aiogram import Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from utils.settings_manager import get_user_settings, update_user_settings
import logging
import time
# --- Постоянная клавиатура внизу экрана ---
# Теперь она содержит только кнопку "Настройки" для простоты.
# Основные действия будут вызываться через меню команд Telegram (/).
main_keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
main_keyboard.add(types.KeyboardButton("⚙ Настройки"))

logger = logging.getLogger(__name__)

def create_settings_keyboard(user_id: str) -> InlineKeyboardMarkup:
    """
    Создает инлайн-клавиатуру для меню настроек.
    """
    user_settings = get_user_settings(user_id)
    current_mode = user_settings.get("mode", "rasa")
    fallback_enabled = user_settings.get("gigachat_fallback", False)
    # --- [ИЗМЕНЕНИЕ] ---
    # Получаем настройку стоп-листа, по умолчанию он включен (True)
    stoplist_enabled = user_settings.get("stoplist_enabled", True)

    rasa_button_text = "✅ Режим: Rasa" if current_mode == "rasa" else "Режим: Rasa"
    gigachat_button_text = "✅ Режим: GigaChat" if current_mode == "gigachat" else "Режим: GigaChat"
    fallback_status = "✅ Вкл" if fallback_enabled else "❌ Выкл"
    # --- [ИЗМЕНЕНИЕ] ---
    # Формируем текст для новой кнопки
    stoplist_status = "❌ Выкл" if stoplist_enabled else "✅ Вкл"

    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton(rasa_button_text, callback_data="set_mode_rasa"),
        InlineKeyboardButton(gigachat_button_text, callback_data="set_mode_gigachat")
    )
    keyboard.add(
        InlineKeyboardButton(f"Дополнять GigaChat: {fallback_status}", callback_data="toggle_fallback")
    )
    # --- [ИЗМЕНЕНИЕ] ---
    # Добавляем новую кнопку в клавиатуру
    keyboard.add(
        InlineKeyboardButton(f"Стоп-лист: {stoplist_status}", callback_data="toggle_stoplist")
    )
    return keyboard


def register_general_handlers(dp: Dispatcher):
    """
    Регистрирует общие обработчики команд и настроек.
    """

    # --- ОБРАБОТЧИК КОМАНДЫ /start ---
    @dp.message_handler(commands=["start"])
    async def handle_start(message: types.Message):
        user_id = str(message.from_user.id)
        args = message.get_args()

        logger.info(f"[{user_id}] Запущена команда /start. Полученные аргументы (args): '{args}'")

        # --- [ИСПРАВЛЕНИЕ] ---
        # Определяем режим работы ВСЕГДА, в самом начале.
        mode_name = "Rasa" if get_user_settings(user_id).get("mode", "rasa") == "rasa" else "GigaChat"

        # --- Логика для deep link ---
        if args and args.startswith("stand_"):
            update_user_settings(user_id, {
            "on_stand": True,
            "stand_last_active": time.time() 
            })
            logger.info(f"[{user_id}] Начата сессия 'у стенда'. Флаг on_stand=True.")

            # --- ВАРИАНТ 1: Специальное приветствие для стенда ---
            welcome_text = (
                "Добро пожаловать со стенда! Я ваш персональный эко-ассистент по Байкалу. 🌿\n\n"
                "Задайте мне любой вопрос о туристических объектах, и я постараюсь помочь."
            )
            
            # --- ВАРИАНТ 2: Стандартное приветствие (если хотите одинаковое для всех) ---
            # Просто закомментируйте ВАРИАНТ 1 и раскомментируйте этот блок
            # welcome_text = (
            #     f"Здравствуйте! Я ваш эко-ассистент по Байкалу 🌿\n"
            #     f"Текущий режим: *{mode_name}*.\n\n"
            #     f"Для поиска с подсказками используйте команду /search из меню."
            # )

        else:
            # --- Логика для обычного старта ---
            welcome_text = (
                f"Здравствуйте! Я ваш эко-ассистент по Байкалу 🌿\n"
                f"Текущий режим: *{mode_name}*.\n\n"
                f"Для поиска с подсказками используйте команду /search из меню (иконка '/' слева от поля ввода)."
            )

        # Отправляем итоговое сообщение
        await message.answer(
            welcome_text,
            reply_markup=main_keyboard, # Убедитесь, что main_keyboard определена где-то выше
            parse_mode="Markdown"
        )

    # --- ОБРАБОТЧИК КОМАНДЫ /search ---
    @dp.message_handler(commands=["search"])
    async def handle_search_command(message: types.Message):
        # Создаем инлайн-клавиатуру с одной кнопкой-триггером
        inline_keyboard = types.InlineKeyboardMarkup()
        inline_keyboard.add(
            types.InlineKeyboardButton(
                text="Начать поиск с автодополнением",
                switch_inline_query_current_chat=""
            )
        )
        await message.answer(
            "Нажмите на кнопку ниже, чтобы начать поиск с подсказками:",
            reply_markup=inline_keyboard
        )

    # --- ОБРАБОТЧИК КОМАНДЫ /help ---
    @dp.message_handler(commands=["help"])
    async def handle_help(message: types.Message):
        help_text = (
            "Я эко-ассистент по Байкалу. Вот что я умею:\n\n"
            "📖 *Получать описания:* Рассказываю о видах флоры и фауны (`Расскажи про нерпу`).\n"
            "🖼️ *Искать изображения:* Нахожу фото по названию и признакам (`пихта сибирская зимой`).\n"
            "🗺️ *Показывать на карте:* Отображаю ареалы обитания (`где растет эдельвейс`).\n"
            "🐾 *Исследовать местности:* Составляю списки видов для локации (`животные на Ольхоне`).\n\n"
            "Для самого удобного поиска используйте команду /search.\n"
            "Или напечатайте символ @ и нажмите Tab"
        )
        await message.answer(help_text, parse_mode="Markdown")

    # --- ОБРАБОТЧИК КНОПКИ "Настройки" ---
    @dp.message_handler(lambda message: message.text == "⚙ Настройки")
    async def handle_settings(message: types.Message):
        user_id = str(message.from_user.id)
        keyboard = create_settings_keyboard(user_id)
        await message.answer("Меню настроек:", reply_markup=keyboard)

    # --- [ИЗМЕНЕНИЕ] ---
    # Добавляем 'toggle_stoplist' в список обрабатываемых колбэков
    @dp.callback_query_handler(lambda c: c.data in ["set_mode_rasa", "set_mode_gigachat", "toggle_fallback", "toggle_stoplist"])
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
            current_fallback_state = get_user_settings(user_id).get("gigachat_fallback", False)
            new_state = not current_fallback_state
            update_user_settings(user_id, {"gigachat_fallback": new_state})
            await callback_query.answer(f"Режим дополнения GigaChat {'включен' if new_state else 'выключен'}")
        # --- [ИЗМЕНЕНИЕ] ---
        # Добавляем блок для обработки переключения стоп-листа
        elif data == "toggle_stoplist":
            current_stoplist_state = get_user_settings(user_id).get("stoplist_enabled", True)
            new_state = not current_stoplist_state
            update_user_settings(user_id, {"stoplist_enabled": new_state})
            await callback_query.answer(f"Стоп-лист {'включен' if new_state else 'выключен'}")

        keyboard = create_settings_keyboard(user_id)
        try:
            if callback_query.message and callback_query.message.reply_markup != keyboard:
                await callback_query.message.edit_reply_markup(keyboard)
        except Exception:
            pass # Если не удалось обновить, ничего страшного

# --- КОНЕЦ ФАЙЛА: handlers/general.py ---
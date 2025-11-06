import logging
import time

from aiogram import Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from utils.settings_manager import get_user_settings, update_user_settings
from logic.stand_manager import start_stand_session, end_stand_session, is_stand_session_active
from config import STAND_SESSION_TIMEOUT


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
    stoplist_enabled = user_settings.get("stoplist_enabled", True)

    rasa_button_text = "✅ Режим: Rasa" if current_mode == "rasa" else "Режим: Rasa"
    gigachat_button_text = "✅ Режим: GigaChat" if current_mode == "gigachat" else "Режим: GigaChat"
    fallback_status = "✅ Вкл" if fallback_enabled else "❌ Выкл"
    stoplist_status = "❌ Выкл" if stoplist_enabled else "✅ Вкл"

    keyboard = InlineKeyboardMarkup(row_width=2)
    keyboard.add(
        InlineKeyboardButton(rasa_button_text, callback_data="set_mode_rasa"),
        InlineKeyboardButton(gigachat_button_text, callback_data="set_mode_gigachat")
    )
    keyboard.add(
        InlineKeyboardButton(f"Дополнять GigaChat: {fallback_status}", callback_data="toggle_fallback")
    )
    keyboard.add(
        InlineKeyboardButton(f"Стоп-лист: {stoplist_status}", callback_data="toggle_stoplist")
    )
    if is_stand_session_active(user_id):
        keyboard.add(InlineKeyboardButton("❌ Отвязаться от стенда", callback_data="stand_detach"))
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
        current_mode = get_user_settings(user_id).get("mode", "gigachat")
        mode_name = "Rasa" if current_mode == "rasa" else "GigaChat"
        # --- Логика для deep link ---
        if args and args.startswith("stand_"):
            session = Dispatcher.get_current().get('aiohttp_session')
            is_session_started = await start_stand_session(user_id, message.bot, session)
            
            logger.info(f"[{user_id}] Начата сессия 'у стенда'. Флаг on_stand=True.")
            if is_session_started:
                session_minutes = STAND_SESSION_TIMEOUT // 60
                welcome_text = (
                    f"✅ *Вы подключились к интерактивному стенду!* \n\n"
                    f"Сессия продлится *{session_minutes} минут*. В течение этого времени ответы на некоторые ваши запросы будут отображаться прямо на экране стенда.\n\n"
                    "✨ *В чем особенность?*\n"
                    "Просто спросите меня о достопримечательностях, и на большом экране отобразятся найденные объекты.\n\n"
                    "🔍 *Примеры запросов:*\n"
                    " • `Расскажи о музеях на Ольхоне`\n"
                    " • `Какие научные учреждения есть около Байкала?`\n"
                    " • `Покажи на карте музеи Иркутска`\n\n"
                    "Если захотите завершить сессию раньше, нажмите кнопку ниже."
                )
                stand_keyboard = InlineKeyboardMarkup().add(
                    InlineKeyboardButton("❌ Отвязаться от стенда", callback_data="stand_detach")
                )
                await message.answer(
                    welcome_text,
                    reply_markup=stand_keyboard, # <--- Используем локальную переменную
                    parse_mode="Markdown"
                )
            else:
                await message.answer(
                "❗️Извините, в данный момент стенд используется другим посетителем.\n\n"
                "Пожалуйста, попробуйте отсканировать QR-код через несколько минут."
                )
        else:
            welcome_text = (
                f"Здравствуйте! Я ваш эко-ассистент по Байкалу 🌿\n"
                f"Текущий режим: *{mode_name}*.\n\n"
                f"Для поиска с подсказками используйте команду /search из меню (иконка '/' слева от поля ввода)."
            )
            await message.answer(
                welcome_text,
                reply_markup=main_keyboard,
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

    @dp.message_handler(lambda message: message.text == "⚙ Настройки")
    async def handle_settings(message: types.Message):
        user_id = str(message.from_user.id)
        keyboard = create_settings_keyboard(user_id)
        await message.answer("Меню настроек:", reply_markup=keyboard)
    
    @dp.callback_query_handler(lambda c: c.data == 'stand_detach')
    async def handle_stand_detach(callback_query: types.CallbackQuery):
        user_id = str(callback_query.from_user.id)
        # Получаем сессию
        session = Dispatcher.get_current().get('aiohttp_session')
        await end_stand_session(user_id, session)
        await callback_query.answer("Сессия со стендом завершена.")
        await callback_query.message.edit_text(
            "Вы успешно отвязались от стенда. Теперь все ответы будут приходить только сюда."
        )

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
# --- НАЧАЛО ФАЙЛА TelegramBot/test_api.py ---

from flask import Flask, request, jsonify
import asyncio
import json
import os
import logging

# --- Настройка логирования ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - [%(funcName)s] - %(message)s'
)
logger = logging.getLogger(__name__)

# Импортируем нашу функцию с логикой и переменные из bot.py
try:
    from bot import get_bot_response, user_settings, save_settings, load_settings, SETTINGS_FILE
except ImportError as e:
    logger.critical(f"Не удалось импортировать компоненты из bot.py: {e}")
    # Заглушки, чтобы сервер мог запуститься и сообщить об ошибке
    SETTINGS_FILE = "user_settings.json"
    user_settings = {}
    def get_bot_response(q, u, m): raise RuntimeError("Bot logic not available")
    def save_settings(): pass
    def load_settings(): pass

app = Flask(__name__)

@app.route('/test_query', methods=['POST'])
def test_query():
    data = request.get_json()
    logger.info(f"Получен новый запрос: {data}")

    if not data:
        logger.warning("Тело запроса не является JSON.")
        return jsonify({"error": "Request body must be JSON"}), 400

    query = data.get("query")
    user_id = data.get("user_id", "test_user_123")
    request_settings = data.get("settings", {})

    if not query:
        logger.warning("В запросе отсутствует обязательное поле 'query'.")
        return jsonify({"error": "Field 'query' is required"}), 400

    original_settings_content = None
    try:
        # --- Блок управления настройками ---
        logger.info(f"Сохранение исходного состояния user_settings.json для пользователя {user_id}")
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                original_settings_content = f.read()

        load_settings()
        
        user_settings[user_id] = {
            "mode": request_settings.get("mode", "rasa"),
            "gigachat_fallback": request_settings.get("gigachat_fallback", False)
        }
        
        logger.info(f"Временно установлены настройки для {user_id}: {user_settings[user_id]}")
        save_settings()
        logger.debug("Временные настройки сохранены в файл.")
        # --- Конец блока ---

        # --- Вызов основной логики ---
        mode = user_settings[user_id]["mode"]
        logger.info(f"Вызов основной логики бота в режиме '{mode}'")
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(get_bot_response(query, user_id, mode))
        loop.close()
        
        logger.info(f"Логика бота успешно отработала, результат: {result}")
        return jsonify(result)

    except Exception as e:
        logger.error(f"Произошла непредвиденная ошибка при обработке запроса: {e}", exc_info=True)
        return jsonify({"error": f"An unexpected error occurred: {str(e)}"}), 500
    
    finally:
        # --- Блок восстановления настроек ---
        logger.info("Начало восстановления исходного файла user_settings.json")
        if original_settings_content is not None:
            with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
                f.write(original_settings_content)
            logger.info("Файл user_settings.json успешно восстановлен в исходное состояние.")
        else:
            if os.path.exists(SETTINGS_FILE):
                os.remove(SETTINGS_FILE)
            logger.info("Исходный файл user_settings.json не существовал, созданный файл удален.")
        # --- Конец блока ---


if __name__ == '__main__':
    logger.info("Запуск тестового API сервера...")
    app.run(host='0.0.0.0', port=5001, debug=False) 

# --- КОНЕЦ ФАЙЛА TelegramBot/test_api.py ---
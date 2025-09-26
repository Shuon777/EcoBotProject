# --- НАЧАЛО ФАЙЛА TelegramBot/test_api.py ---
from flask import Flask, request, jsonify
import asyncio
import logging
from bot import get_bot_response
from settings_manager import get_user_settings, update_user_settings

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - [%(funcName)s] - %(message)s'
)
logger = logging.getLogger(__name__)
app = Flask(__name__)

@app.route('/test_query', methods=['POST'])
def test_query():
    data = request.get_json()
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    query = data.get("query")
    if not query:
        return jsonify({"error": "Field 'query' is required"}), 400

    user_id = data.get("user_id", "test_user_123")
    request_settings = data.get("settings", {})
    debug_mode = data.get("debug_mode", False)

    logger.info(f"Получен запрос для user_id='{user_id}', debug_mode={debug_mode}: '{query}'")

    original_settings = get_user_settings(user_id)
    try:
        temp_settings = {
            "mode": request_settings.get("mode", "rasa"),
            "gigachat_fallback": request_settings.get("gigachat_fallback", False)
        }
        update_user_settings(user_id, temp_settings)
        logger.info(f"Временно установлены настройки: {temp_settings}")
        
        mode = temp_settings["mode"]
        logger.info(f"Вызов основной логики бота в режиме '{mode}'")
        
        result = asyncio.run(get_bot_response(query, user_id, mode, debug_mode))
        
        logger.info(f"Логика бота успешно отработала, результат: {result}")
        return jsonify(result)

    except Exception as e:
        logger.error(f"Произошла непредвиденная ошибка при обработке запроса: {e}", exc_info=True)
        return jsonify({"error": f"An unexpected error occurred: {str(e)}"}), 500
    
    finally:
        update_user_settings(user_id, original_settings)
        logger.info(f"Настройки для пользователя {user_id} восстановлены в исходное состояние.")

if __name__ == '__main__':
    logger.info("Запуск тестового API сервера...")
    app.run(host='0.0.0.0', port=5001, debug=False)
# --- КОНЕЦ ФАЙЛА TelegramBot/test_api.py ---
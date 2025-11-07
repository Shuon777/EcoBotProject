# --- НАЧАЛО ФАЙЛА GigaChatAPI/giga_api.py ---
from flask import Flask, request, jsonify
from langchain_gigachat import GigaChat
from dotenv import load_dotenv
import os
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

load_dotenv()

app = Flask(__name__)

gigachat_llm = None
try:
    api_key = os.getenv('SBER_KEY_ENTERPRICE')
    if not api_key:
        logger.critical("Ключ SBER_KEY_ENTERPRICE не найден в .env файле!")
    else:
        gigachat_llm = GigaChat(
            credentials=api_key,
            model="GigaChat-2-Max",
            verify_ssl_certs=False,
            scope="GIGACHAT_API_CORP"
        )
        logger.info("GigaChat API сервис успешно инициализирован.")
except Exception as e:
    logger.critical(f"Критическая ошибка при инициализации GigaChat: {e}", exc_info=True)

@app.route('/ask_simple', methods=['POST'])
def ask_simple():
    if not gigachat_llm:
        return jsonify({"error": "GigaChat сервис не инициализирован, проверьте логи."}), 503

    data = request.get_json()
    if not data or "question" not in data:
        return jsonify({"error": "В запросе отсутствует поле 'question'"}), 400

    question = data["question"]
    logger.info(f"Получен запрос для GigaChat: '{question}'")

    try:
        response = gigachat_llm.invoke(question)
        answer = response.content.strip()
        logger.info(f"GigaChat ответил успешно.")
        return jsonify({"answer": answer})
    except Exception as e:
        logger.error(f"Ошибка во время обращения к API GigaChat: {e}", exc_info=True)
        return jsonify({"error": f"Внутренняя ошибка при обращении к GigaChat: {e}"}), 500

if __name__ == '__main__':
    # Запускаем на порту 5556, чтобы не конфликтовать с Rasa
    app.run(host='0.0.0.0', port=5556)

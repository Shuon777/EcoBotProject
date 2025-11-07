# Файл: /var/www/EcoBotProject/RasaProject/Dockerfile

FROM rasa/rasa:3.6.4-full
WORKDIR /app
COPY . .

# --- НАЧАЛО ИЗМЕНЕНИЙ ---
# Временно переключаемся на root, чтобы иметь права на запись для обучения
USER root
# --- КОНЕЦ ИЗМЕНЕНИЙ ---

RUN --network=host pip install rasa[transformers]

RUN rasa train

# --- ВАЖНО: Возвращаемся к стандартному пользователю для запуска сервера ---
USER 1001

CMD ["run", "--debug", "--enable-api", "--cors", "*"]
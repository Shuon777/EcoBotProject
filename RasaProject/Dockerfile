# Правильная версия
FROM python:3.9
WORKDIR /app

# Копируем только то, что нужно для зависимостей
COPY requirements.txt .
RUN --network=host pip install rasa[transformers]

# Копируем остальной код
COPY . .

# УБИРАЕМ "RUN rasa train" ОТСЮДА!

USER 1001
# Запускаем без флага --debug и указываем путь к модели
CMD ["run", "--enable-api", "--cors", "*", "--model", "/app/models"]
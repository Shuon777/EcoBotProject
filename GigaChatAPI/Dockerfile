# Шаг 1: Взять официальный образ Python 3.10
FROM python:3.10

# Шаг 2: Установить рабочую директорию внутри контейнера
WORKDIR /app

# Шаг 3: Скопировать файл с зависимостями
COPY requirements.txt .

# Шаг 4: Установить зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Шаг 5: Скопировать весь остальной код
COPY . .

# Шаг 6: Команда для запуска gunicorn
CMD ["gunicorn", "--workers", "4", "--bind", "0.0.0.0:5556", "giga_api:app"]
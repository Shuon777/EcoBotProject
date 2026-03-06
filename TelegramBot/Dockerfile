FROM python:3.10-slim

WORKDIR /app

# Устанавливаем общие зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь код подмодуля
COPY . .

# Мы НЕ пишем здесь CMD, так как у нас две разные точки входа
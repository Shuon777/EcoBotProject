FROM rasa/rasa:3.6.4-full

USER root

WORKDIR /app

COPY . .

# Скачиваем модели ПРИ СБОРКЕ (используем TF-версии для совместимости с Rasa)

RUN pip install -r requirements.txt
RUN python -m spacy download ru_core_news_md

# Создаем кэш-директории с правильными правами
RUN mkdir -p /app/.rasa/cache && \
    mkdir -p /app/.cache/huggingface && \
    chown -R 1001:1001 /app

USER 1001

ENTRYPOINT ["rasa"]
CMD ["run", "--enable-api", "--cors", "*", "--model", "/app/models"]
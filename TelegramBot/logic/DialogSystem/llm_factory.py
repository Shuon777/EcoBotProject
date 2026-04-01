# TelegramBot/logic/dialogue_system/llm_factory.py
import os
from langchain_openai import ChatOpenAI
from langchain_gigachat import GigaChat

class LLMFactory:
    @staticmethod
    def get_model(provider: str = "qwen"):
        if provider == "qwen":
            return ChatOpenAI(
                base_url=os.getenv("LLM_BASE_URL", "http://localhost:11434/v1"),
                api_key="ollama",
                model="qwen2.5:7b",
                temperature=0
            )
        elif provider == "gigachat":
            return GigaChat(
                credentials=os.getenv("GIGACHAT_CREDENTIALS"),
                model="GigaChat-2-Max",
                verify_ssl_certs=False
            )
        raise ValueError(f"Unknown provider: {provider}")
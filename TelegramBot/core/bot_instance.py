from aiogram import Bot, Dispatcher
from config import BOT_TOKEN

# Создаем экземпляры, которые будут импортироваться в другие части приложения
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)
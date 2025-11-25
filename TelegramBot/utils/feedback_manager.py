# --- НАЧАЛО ФАЙЛА: utils/feedback_manager.py ---

import logging
import asyncio
from typing import Optional
from aiogram import types
from aiogram.utils.exceptions import MessageToDeleteNotFound, MessageCantBeDeleted, MessageNotModified

logger = logging.getLogger(__name__)


class FeedbackManager:
    """
    Менеджер для управления фидбеком пользователю во время обработки запроса.
    Поддерживает:
    - Отправку action-статусов (typing, upload_photo и т.д.)
    - Отправку промежуточных текстовых сообщений
    - Автоматическое удаление промежуточных сообщений
    """
    
    def __init__(self, message: types.Message):
        """
        Инициализирует менеджер фидбека.
        
        Args:
            message: Сообщение пользователя, на которое нужно отправлять фидбек
        """
        self.message = message
        self.user_id = str(message.chat.id)
        self.bot = message.bot
        self.feedback_messages = []  # Список промежуточных сообщений для удаления
        self._action_task = None  # Задача для периодической отправки action
        
    async def send_action(self, action: str = "typing"):
        """
        Отправляет action-статус боту.
        
        Args:
            action: Тип действия ('typing', 'upload_photo', 'upload_document', и т.д.)
        """
        try:
            await self.bot.send_chat_action(chat_id=self.user_id, action=action)
            logger.debug(f"[{self.user_id}] Отправлен action: {action}")
        except Exception as e:
            logger.warning(f"[{self.user_id}] Не удалось отправить action '{action}': {e}")
    
    async def _keep_action_alive(self, action: str, interval: int = 5):
        """
        Периодически отправляет action для поддержания статуса.
        Telegram автоматически убирает action через 5 секунд.
        
        Args:
            action: Тип действия
            interval: Интервал между отправками в секундах
        """
        while True:
            try:
                await self.send_action(action)
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                logger.debug(f"[{self.user_id}] Action loop для '{action}' остановлен")
                break
            except Exception as e:
                logger.error(f"[{self.user_id}] Ошибка в action loop: {e}")
                break
    
    async def start_action(self, action: str = "typing"):
        """
        Запускает непрерывную отправку action-статуса.
        Останавливает предыдущий action, если он был запущен.
        
        Args:
            action: Тип действия
        """
        # Останавливаем предыдущий action, если есть
        await self.stop_action()
        
        # Запускаем новый action loop
        self._action_task = asyncio.create_task(self._keep_action_alive(action))
        logger.debug(f"[{self.user_id}] Запущен непрерывный action: {action}")
    
    async def stop_action(self):
        """
        Останавливает непрерывную отправку action-статуса.
        """
        if self._action_task and not self._action_task.done():
            self._action_task.cancel()
            try:
                await self._action_task
            except asyncio.CancelledError:
                pass
            self._action_task = None
            logger.debug(f"[{self.user_id}] Action остановлен")
    
    async def send_progress_message(
        self, 
        text: str, 
        keep: bool = False,
        parse_mode: Optional[str] = None
    ) -> Optional[types.Message]:
        """
        Отправляет промежуточное сообщение о прогрессе обработки.
        
        Args:
            text: Текст сообщения
            keep: Если True, сообщение не будет автоматически удалено
            parse_mode: Режим парсинга (Markdown, HTML)
            
        Returns:
            Отправленное сообщение или None в случае ошибки
        """
        try:
            sent_message = await self.message.answer(text, parse_mode=parse_mode)
            if not keep:
                self.feedback_messages.append(sent_message)
            logger.debug(f"[{self.user_id}] Отправлено промежуточное сообщение: {text[:50]}...")
            return sent_message
        except Exception as e:
            logger.error(f"[{self.user_id}] Ошибка при отправке промежуточного сообщения: {e}")
            return None
    
    async def update_progress_message(
        self,
        message_to_update: types.Message,
        new_text: str,
        parse_mode: Optional[str] = None
    ):
        """
        Обновляет текст промежуточного сообщения.
        
        Args:
            message_to_update: Сообщение для обновления
            new_text: Новый текст
            parse_mode: Режим парсинга
        """
        try:
            await message_to_update.edit_text(new_text, parse_mode=parse_mode)
            logger.debug(f"[{self.user_id}] Обновлено промежуточное сообщение: {new_text[:50]}...")
        except MessageNotModified:
            logger.debug(f"[{self.user_id}] Сообщение не изменилось, пропускаем обновление")
        except Exception as e:
            logger.error(f"[{self.user_id}] Ошибка при обновлении промежуточного сообщения: {e}")
    
    async def cleanup(self):
        """
        Удаляет все промежуточные сообщения и останавливает action.
        Вызывается после завершения обработки запроса.
        """
        # Останавливаем action
        await self.stop_action()
        
        # Удаляем промежуточные сообщения
        for msg in self.feedback_messages:
            try:
                await msg.delete()
                logger.debug(f"[{self.user_id}] Удалено промежуточное сообщение: {msg.message_id}")
            except (MessageToDeleteNotFound, MessageCantBeDeleted) as e:
                logger.debug(f"[{self.user_id}] Не удалось удалить сообщение {msg.message_id}: {e}")
            except Exception as e:
                logger.error(f"[{self.user_id}] Ошибка при удалении сообщения {msg.message_id}: {e}")
        
        self.feedback_messages.clear()
        logger.debug(f"[{self.user_id}] Cleanup завершен")


class FeedbackContext:
    """
    Контекстный менеджер для упрощенной работы с фидбеком.
    
    Пример использования:
    ```python
    async with FeedbackContext(message, action="typing") as feedback:
        await feedback.send_progress_message("Ищу информацию в базе...")
        # ... выполнение долгой операции ...
        await feedback.send_progress_message("Обрабатываю результаты...")
        # ... ещё операции ...
    # Автоматически вызовется cleanup
    ```
    """
    
    def __init__(
        self, 
        message: types.Message, 
        action: Optional[str] = "typing",
        auto_start_action: bool = True
    ):
        """
        Инициализирует контекстный менеджер.
        
        Args:
            message: Сообщение пользователя
            action: Тип action для запуска (None = не запускать)
            auto_start_action: Автоматически запускать action при входе в контекст
        """
        self.feedback = FeedbackManager(message)
        self.action = action
        self.auto_start_action = auto_start_action
    
    async def __aenter__(self):
        """Вход в контекст - запускаем action"""
        if self.auto_start_action and self.action:
            await self.feedback.start_action(self.action)
        return self.feedback
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Выход из контекста - очищаем все"""
        await self.feedback.cleanup()
        return False

# --- КОНЕЦ ФАЙЛА: utils/feedback_manager.py ---

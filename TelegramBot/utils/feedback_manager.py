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
    
    # Интервал обновления action-статуса (Telegram сбрасывает его через 5 секунд)
    ACTION_REFRESH_INTERVAL = 5
    
    def __init__(self, message: types.Message):
        """
        Инициализирует менеджер фидбека.
        
        Args:
            message: Сообщение пользователя, на которое нужно отправлять фидбек
        """
        self.message = message
        self.user_id = str(message.chat.id)
        self.bot = message.bot
        self.feedback_messages = []
        self._action_task = None
        
    async def send_action(self, action: str = "typing"):
        """
        Отправляет action-статус боту (однократно).
        
        Args:
            action: Тип действия ('typing', 'upload_photo', 'upload_document', и т.д.)
        """
        try:
            await self.bot.send_chat_action(chat_id=self.user_id, action=action)
        except Exception as e:
            logger.warning(f"[{self.user_id}] Не удалось отправить action '{action}': {e}")
    
    async def _keep_action_alive(self, action: str):
        """
        Периодически отправляет action для поддержания статуса.
        Telegram автоматически убирает action через 5 секунд.
        
        Args:
            action: Тип действия
        """
        while True:
            try:
                await self.send_action(action)
                await asyncio.sleep(self.ACTION_REFRESH_INTERVAL)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[{self.user_id}] Ошибка в action loop: {e}")
                break
    
    async def start_action(self, action: str = "typing"):
        """
        Запускает непрерывную отправку action-статуса.
        Автоматически останавливает предыдущий action, если он был запущен.
        
        Args:
            action: Тип действия
        """
        await self.stop_action()
        self._action_task = asyncio.create_task(self._keep_action_alive(action))
    
    async def stop_action(self):
        """Останавливает непрерывную отправку action-статуса."""
        if self._action_task and not self._action_task.done():
            self._action_task.cancel()
            try:
                await self._action_task
            except asyncio.CancelledError:
                pass
            self._action_task = None
    
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
            logger.info(f"[{self.user_id}] Отправлено промежуточное сообщение: {text[:60]}")
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
            logger.info(f"[{self.user_id}] Обновлено промежуточное сообщение: {new_text[:60]}")
        except MessageNotModified:
            pass  # Сообщение не изменилось - это нормально
        except Exception as e:
            logger.error(f"[{self.user_id}] Ошибка при обновлении промежуточного сообщения: {e}")
    
    async def cleanup(self):
        """
        Удаляет все промежуточные сообщения и останавливает action.
        Вызывается после завершения обработки запроса.
        """
        await self.stop_action()
        
        deleted_count = 0
        for msg in self.feedback_messages:
            try:
                await msg.delete()
                deleted_count += 1
            except (MessageToDeleteNotFound, MessageCantBeDeleted):
                pass  # Сообщение уже удалено или не может быть удалено
            except Exception as e:
                logger.error(f"[{self.user_id}] Ошибка при удалении сообщения {msg.message_id}: {e}")
        
        if deleted_count > 0:
            logger.info(f"[{self.user_id}] Удалено промежуточных сообщений: {deleted_count}")
        
        self.feedback_messages.clear()


class FeedbackContext:
    """
    Контекстный менеджер для упрощенной работы с фидбеком.
    
    Пример использования:
    ```python
    async with FeedbackContext(message, action="typing") as feedback:
        await feedback.send_progress_message("Ищу информацию в базе...")
        # ... выполнение долгой операции ...
        await feedback.send_progress_message("Обрабатываю результаты...")
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
        """Вход в контекст - запускаем action."""
        if self.auto_start_action and self.action:
            await self.feedback.start_action(self.action)
        return self.feedback
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Выход из контекста - очищаем все."""
        await self.feedback.cleanup()
        return False

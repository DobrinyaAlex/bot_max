import asyncio
import logging
import os
from maxgram import Bot, Dispatcher, Router
from maxgram.filters import Command
from maxgram.types import BotStarted, Message
from maxgram.utils.keyboard import InlineKeyboardBuilder

# --- Конфигурация ---
BOT_TOKEN = os.getenv("MAX_BOT_TOKEN")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))

if not BOT_TOKEN:
    raise ValueError("Не задана переменная окружения MAX_BOT_TOKEN")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()

# --- Обработчик кнопки "Начать" (bot_started) ---
@router.bot_started()
async def on_start(event: BotStarted):
    print("=== ОТЛАДКА: СРАБОТАЛ ОБРАБОТЧИК КНОПКИ 'НАЧАТЬ' ===")
    
    builder = InlineKeyboardBuilder()
    builder.callback(text="✅ Я человек / Продолжить", payload="captcha_passed")

    rules_text = (
        "Здравствуйте! Я помогу оформить объявление.\n\n"
        "📌 **Правила оформления:**\n"
        "1. Текст объявления — до 500 символов.\n"
        "2. Фотография — 1 шт.\n"
        "3. Имя и телефон для связи.\n\n"
        "Нажмите кнопку ниже, чтобы подтвердить, что вы человек."
    )
    await event.answer(text=rules_text, keyboard=builder)

# --- Обработчик команды /start (на случай ручного ввода) ---
@router.message(Command("start"))
async def cmd_start(message: Message, bot: Bot):
    print("=== ОТЛАДКА: СРАБОТАЛ ОБРАБОТЧИК КОМАНДЫ /start ===")
    builder = InlineKeyboardBuilder()
    builder.callback(text="✅ Я человек / Продолжить", payload="captcha_passed")
    await message.answer("Здравствуйте! Нажмите кнопку ниже:", keyboard=builder)

# --- Обработчик нажатия на inline-кнопку ---
@router.message_callback(lambda call: call.payload == "captcha_passed")
async def process_captcha(callback, bot: Bot):
    print("=== ОТЛАДКА: КНОПКА НАЖАТА ===")
    await callback.answer(notification="Проверка пройдена!")
    await callback.message.answer("✅ Проверка пройдена! Введите текст вашего объявления (до 500 символов):")

# --- Простой сбор данных (для теста) ---
@router.message()
async def handle_any_message(message: Message, bot: Bot):
    # Пока просто эхо-ответ, чтобы проверить, что бот получает сообщения
    await message.answer(f"Я получил ваше сообщение: {message.body.text}")

# --- Запуск ---
async def main():
    dp.include_router(router)
    print("=== БОТ УСПЕШНО ЗАПУЩЕН ===")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

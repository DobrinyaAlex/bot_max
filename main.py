import asyncio
import logging
import os
import aiomax

# --- Конфигурация ---
BOT_TOKEN = os.getenv("MAX_BOT_TOKEN")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))

if not BOT_TOKEN:
    raise ValueError("Не задана переменная окружения MAX_BOT_TOKEN")

logging.basicConfig(level=logging.INFO)

# Инициализируем бота
bot = aiomax.Bot(BOT_TOKEN)

# --- Шаг 1: Обработка нажатия кнопки "Начать" (bot_started) ---
@bot.on_bot_start()
async def on_bot_start(payload: aiomax.BotStartPayload):
    # Создаем inline-клавиатуру с кнопкой
    kb = aiomax.buttons.KeyboardBuilder()
    kb.add(aiomax.buttons.CallbackButton('✅ Я человек / Продолжить', 'captcha_passed'))

    rules_text = (
        "Здравствуйте! Я помогу оформить объявление в канале MAX.\n\n"
        "📌 **Правила оформления:**\n"
        "1. Текст объявления — до 500 символов, без CAPS LOCK.\n"
        "2. Фотографии — до 5 штук (JPG/PNG).\n"
        "3. Обязательно укажите имя и телефон для связи.\n"
        "4. Объявление будет опубликовано после проверки модератором.\n\n"
        "Нажмите кнопку ниже, чтобы подтвердить, что вы человек, и начать."
    )

    await payload.send(rules_text, keyboard=kb)

# --- Шаг 2: Обработка нажатия на inline-кнопку ---
@bot.on_button_callback(lambda data: data.payload == 'captcha_passed')
async def process_callback(callback: aiomax.Callback):
    # Отвечаем на callback, чтобы убрать часики загрузки
    await callback.answer()
    
    # Отправляем следующее сообщение
    await callback.message.answer("✅ Проверка пройдена! Введите текст вашего объявления (до 500 символов):")

# --- Шаг 3: Прием и сохранение данных ---
# (Здесь ваш код для приема текста, фото, имени и телефона.
# Поскольку для теста вам достаточно запуска, я добавляю простой эхо-ответ)
@bot.on_message()
async def handle_message(message: aiomax.Message):
    # В реальном проекте здесь будет логика FSM для сбора данных
    await message.reply("Я получил ваше сообщение! (Логика сбора данных будет добавлена далее)")

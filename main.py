import asyncio
import logging
import os
from maxgram import Bot, Dispatcher, Router
from maxgram.types import BotStarted, Message

BOT_TOKEN = os.getenv("MAX_BOT_TOKEN")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()

# --- Хранилища данных (вместо FSM) ---
# Здесь мы храним, на каком шаге сейчас пользователь
user_states = {} 
# Здесь мы храним собранные данные объявления
user_data = {}

# --- Обработчик кнопки "Начать" ---
@router.bot_started()
async def on_start(event: BotStarted):
    user_id = event.user.id
    user_states[user_id] = "waiting_text"
    user_data[user_id] = {} # Очищаем старые данные

    rules_text = (
        "Здравствуйте! Я помогу оформить объявление.\n\n"
        "📌 **Правила оформления:**\n"
        "1. Текст объявления — до 500 символов.\n"
        "2. Фотография — 1 шт.\n"
        "3. Имя и телефон для связи.\n\n"
        "✅ Проверка пройдена! Введите текст вашего объявления:"
    )
    await event.answer(text=rules_text)

# --- Обработчик всех текстовых сообщений ---
@router.message()
async def handle_message(message: Message):
    user_id = message.sender.user_id
    text = message.body.text if message.body.text else ""
    
    # Если пользователь новый и не нажимал "Начать"
    if user_id not in user_states:
        await message.answer("Пожалуйста, нажмите кнопку 'Начать' в меню бота.")
        return

    state = user_states[user_id]

    # Шаг 1: Ловим текст
    if state == "waiting_text":
        if len(text) > 500:
            await message.answer("❌ Текст слишком длинный. Максимум 500 символов.")
            return
        
        user_data[user_id]['ad_text'] = text
        user_states[user_id] = "waiting_photo"
        await message.answer("Текст принят! Теперь отправьте фотографию (или напишите «Пропустить»).")

    # Шаг 2: Ловим фото
    elif state == "waiting_photo":
        if message.body.attachments:
            # Достаем URL фото
            photo_url = message.body.attachments[0].payload.url
            user_data[user_id]['photo'] = photo_url
            user_states[user_id] = "waiting_name"
            await message.answer("Фото принято. Теперь укажите имя продавца.")
        elif text.lower() == "пропустить":
            user_data[user_id]['photo'] = None
            user_states[user_id] = "waiting_name"
            await message.answer("Фото пропущено. Теперь укажите имя продавца.")
        else:
            await message.answer("Пожалуйста, отправьте фото или напишите «Пропустить».")

    # Шаг 3: Ловим имя
    elif state == "waiting_name":
        user_data[user_id]['name'] = text
        user_states[user_id] = "waiting_phone"
        await message.answer("Имя принято. Теперь укажите контактный телефон.")

    # Шаг 4: Ловим телефон и отправляем
    elif state == "waiting_phone":
        user_data[user_id]['phone'] = text
        
        # Формируем сообщение для админа
        admin_text = (
            f"📥 **Новое объявление на проверку**\n\n"
            f"**Текст:** {user_data[user_id].get('ad_text')}\n"
            f"**Имя:** {user_data[user_id].get('name')}\n"
            f"**Телефон:** {text}\n"
            f"**ID пользователя:** {user_id}"
        )
        if user_data[user_id].get('photo'):
            admin_text += f"\n**Фото:** {user_data[user_id].get('photo')}"

        try:
            await bot.send_message(chat_id=ADMIN_CHAT_ID, text=admin_text)
            await message.answer("✅ Спасибо! Ваше объявление отправлено на проверку.")
        except Exception as e:
            logging.error(f"Ошибка отправки админу: {e}")
            await message.answer("Произошла ошибка при отправке.")

        # Очищаем состояние после завершения
        del user_states[user_id]
        del user_data[user_id]

async def main():
    dp.include_router(router)
    print("=== БОТ ЗАПУЩЕН (БЕЗ FSM) ===")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

import asyncio
import logging
import os
from maxgram import Bot, Dispatcher, Router
from maxgram.types import BotStarted, Message
from maxgram.utils.keyboard import InlineKeyboardBuilder

# --- Конфигурация ---
BOT_TOKEN = os.getenv("MAX_BOT_TOKEN")

try:
    ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))
except ValueError:
    print("!!! ВНИМАНИЕ: ADMIN_CHAT_ID задан неверно. Укажите только цифры !!!")
    ADMIN_CHAT_ID = 0

if not BOT_TOKEN:
    raise ValueError("Не задана переменная окружения MAX_BOT_TOKEN")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()

# --- Хранилища данных ---
user_states = {}
user_data = {}

# --- Шаг 1: Обработчик кнопки "Начать" ---
@router.bot_started()
async def on_start(event: BotStarted):
    user_id = event.user.user_id 
    user_states[user_id] = "waiting_captcha"
    user_data[user_id] = {}

    kb = InlineKeyboardBuilder()
    kb.callback(text="✅ Я человек / Продолжить", payload="captcha_passed")

    rules_text = (
        "Здравствуйте! Я помогу оформить объявление.\n\n"
        "📌 **Правила оформления:**\n"
        "1. Текст объявления — до 500 символов.\n"
        "2. Фотография — 1 шт.\n"
        "3. Имя и телефон для связи.\n\n"
        "Нажмите кнопку ниже, чтобы подтвердить, что вы человек."
    )
    
    await event.answer(text=rules_text, keyboard=kb)

# --- Шаг 2: Универсальный обработчик кнопок (Callback) ---
@router.callback_query()
async def handle_callbacks(callback):
    print(f"=== ОТЛАДКА: НАЖАТА КНОПКА. PAYLOAD: {callback.payload} ===")
    
    # Проверяем, что нажата именно наша кнопка
    if callback.payload == "captcha_passed":
        try:
            # Безопасно достаем ID
            user_id = callback.sender.user_id
            chat_id = callback.message.recipient.chat_id
            
            user_states[user_id] = "waiting_text"
            
            # Отвечаем на колбэк (убираем часики загрузки на кнопке)
            try:
                await callback.answer()
            except Exception:
                pass

            # Отправляем сообщение напрямую
            await bot.send_message(
                chat_id=chat_id, 
                text="✅ Проверка пройдена! Введите текст вашего объявления (до 500 символов):"
            )
            print("=== ОТЛАДКА: ОТВЕТ НА КНОПКУ УСПЕШНО ОТПРАВЛЕН ===")
            
        except Exception as e:
            print(f"!!! ОШИБКА ПРИ ОБРАБОТКЕ КНОПКИ: {e} !!!")

# --- Шаг 3: Обработчик всех текстовых сообщений и фото ---
@router.message()
async def handle_message(message: Message):
    user_id = message.sender.user_id
    text = message.body.text if message.body.text else ""
    
    if user_id not in user_states:
        await message.answer("Пожалуйста, нажмите кнопку 'Начать' в меню бота.")
        return

    state = user_states[user_id]

    # Ждем текст
    if state == "waiting_text":
        if len(text) > 500:
            await message.answer("❌ Текст слишком длинный. Максимум 500 символов.")
            return
        
        user_data[user_id]['ad_text'] = text
        user_states[user_id] = "waiting_photo"
        await message.answer("Текст принят! Теперь отправьте фотографию (или напишите «Пропустить»).")

    # Ждем фото
    elif state == "waiting_photo":
        if message.body.attachments:
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

    # Ждем имя
    elif state == "waiting_name":
        user_data[user_id]['name'] = text
        user_states[user_id] = "waiting_phone"
        await message.answer("Имя принято. Теперь укажите контактный телефон.")

    # Ждем телефон и отправляем
    elif state == "waiting_phone":
        user_data[user_id]['phone'] = text
        
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
            print(f"=== ОТПРАВЛЯЕМ ЗАЯВКУ НА ID: {ADMIN_CHAT_ID} ===")
            await bot.send_message(chat_id=ADMIN_CHAT_ID, text=admin_text)
            await message.answer("✅ Спасибо! Ваше объявление отправлено на проверку.")
        except Exception as e:
            print(f"!!! ОШИБКА ОТПРАВКИ АДМИНУ: {e} !!!")
            await message.answer(f"❌ Ошибка отправки администратору.\n\nТекст ошибки от MAX: {e}")
        
        del user_states[user_id]
        del user_data[user_id]

async def main():
    dp.include_router(router)
    print("=== БОТ УСПЕШНО ЗАПУЩЕН ===")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

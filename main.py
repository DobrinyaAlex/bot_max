import asyncio
import logging
import os
import aiohttp
import io
from maxgram import Bot, Dispatcher, Router
from maxgram.types import BotStarted, Message
from maxgram.utils.keyboard import InlineKeyboardBuilder

# --- Конфигурация ---
BOT_TOKEN = os.getenv("MAX_BOT_TOKEN")

try:
    ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))
except ValueError:
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
    chat_id = event.chat_id
    user_states[chat_id] = "waiting_captcha"
    user_data[chat_id] = {}

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
    
    try:
        await bot.send_message(chat_id=chat_id, text=rules_text, keyboard=kb)
    except Exception as e:
        print(f"!!! ОШИБКА ОТПРАВКИ ПРИВЕТСТВИЯ: {e} !!!")

# --- Шаг 2: Обработчик нажатия на inline-кнопку ---
@router.message_callback()
async def handle_callbacks(callback):
    if getattr(callback, 'payload', None) == "captcha_passed":
        try:
            chat_id = callback.message.recipient.chat_id
            user_states[chat_id] = "waiting_text"
            
            try:
                await callback.answer()
            except Exception:
                pass

            await bot.send_message(
                chat_id=chat_id, 
                text="✅ Проверка пройдена! Введите текст вашего объявления (до 500 символов):"
            )
        except Exception as e:
            print(f"!!! ОШИБКА ПРИ ОБРАБОТКЕ КНОПКИ: {e} !!!")

# --- Шаг 3: Обработчик текстовых сообщений и фото ---
@router.message()
async def handle_message(message: Message):
    try:
        chat_id = message.recipient.chat_id
        text = ""
        if getattr(message, 'body', None) and getattr(message.body, 'text', None):
            text = message.body.text

        if chat_id not in user_states:
            await bot.send_message(chat_id=chat_id, text="Пожалуйста, нажмите кнопку 'Начать' в меню бота.")
            return

        state = user_states[chat_id]

        if state == "waiting_text":
            if len(text) > 500:
                await bot.send_message(chat_id=chat_id, text="❌ Текст слишком длинный. Максимум 500 символов.")
                return
            
            user_data[chat_id]['ad_text'] = text
            user_states[chat_id] = "waiting_photo"
            await bot.send_message(chat_id=chat_id, text="Текст принят! Теперь отправьте фотографию (или напишите «Пропустить»).")

        elif state == "waiting_photo":
            if getattr(message.body, 'attachments', None):
                # Сохраняем ссылку на фото
                photo_url = message.body.attachments[0].payload.url
                user_data[chat_id]['photo'] = photo_url
                user_states[chat_id] = "waiting_name"
                await bot.send_message(chat_id=chat_id, text="Фото принято. Теперь укажите имя продавца.")
            elif text.lower() == "пропустить":
                user_data[chat_id]['photo'] = None
                user_states[chat_id] = "waiting_name"
                await bot.send_message(chat_id=chat_id, text="Фото пропущено. Теперь укажите имя продавца.")
            else:
                await bot.send_message(chat_id=chat_id, text="Пожалуйста, отправьте фото или напишите «Пропустить».")

        elif state == "waiting_name":
            user_data[chat_id]['name'] = text
            user_states[chat_id] = "waiting_phone"
            await bot.send_message(chat_id=chat_id, text="Имя принято. Теперь укажите контактный телефон.")

        elif state == "waiting_phone":
            user_data[chat_id]['phone'] = text
            
            post_text = (
                f"📌 **Объявление**\n\n"
                f"{user_data[chat_id].get('ad_text')}\n\n"
                f"👤 **Продавец:** {user_data[chat_id].get('name')}\n"
                f"📞 **Телефон:** {text}"
            )

            photo_url = user_data[chat_id].get('photo')
            
            if photo_url:
                try:
                    # СКАЧИВАЕМ ФОТО В ПАМЯТЬ
                    async with aiohttp.ClientSession() as session:
                        async with session.get(photo_url) as resp:
                            if resp.status == 200:
                                photo_data = await resp.read()
                                
                                # ОТПРАВЛЯЕМ СКАЧАННОЕ ФОТО В КАНАЛ
                                await bot.send_photo(
                                    chat_id=ADMIN_CHAT_ID, 
                                    photo=photo_data, 
                                    caption=post_text
                                )
                                await bot.send_message(chat_id=chat_id, text="✅ Спасибо! Ваше объявление с фото опубликовано в канале.")
                            else:
                                raise Exception(f"Не удалось скачать фото (HTTP {resp.status})")
                except Exception as e:
                    print(f"!!! ОШИБКА СКАЧИВАНИЯ/ОТПРАВКИ ФОТО: {e} !!!")
                    # Если не получилось с фото, отправляем хотя бы текст
                    await bot.send_message(chat_id=ADMIN_CHAT_ID, text=post_text + "\n\n(Фото не загрузилось)")
                    await bot.send_message(chat_id=chat_id, text="✅ Объявление опубликовано (текстом, т.к. фото не загрузилось).")
            else:
                # Если фото пропущено изначально
                try:
                    await bot.send_message(chat_id=ADMIN_CHAT_ID, text=post_text)
                    await bot.send_message(chat_id=chat_id, text="✅ Спасибо! Ваше объявление опубликовано в канале.")
                except Exception as e:
                    print(f"!!! ОШИБКА ОТПРАВКИ ТЕКСТА: {e} !!!")
            
            del user_states[chat_id]
            del user_data[chat_id]

    except Exception as e:
        print(f"!!! КРИТИЧЕСКАЯ ОШИБКА В ОБРАБОТЧИКЕ: {e} !!!")

async def main():
    dp.include_router(router)
    print("=== БОТ УСПЕШНО ЗАПУЩЕН ===")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

import asyncio
import logging
import os
import aiohttp
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
            attachment_token = None

            if photo_url:
                try:
                    # 1. Скачиваем фото по ссылке
                    async with aiohttp.ClientSession() as session:
                        async with session.get(photo_url) as resp:
                            if resp.status == 200:
                                photo_data = await resp.read()
                            else:
                                raise Exception(f"Не удалось скачать фото (HTTP {resp.status})")

                    # 2. Запрашиваем URL для загрузки на сервер MAX
                    headers = {"Authorization": f"Bearer {BOT_TOKEN}"}
                    async with aiohttp.ClientSession() as session:
                        async with session.post("https://platform-api2.max.ru/uploads?type=image", headers=headers) as resp:
                            if resp.status == 200:
                                upload_data = await resp.json()
                                upload_url = upload_data.get("url")
                                if not upload_url:
                                    raise Exception("Не удалось получить URL для загрузки")
                            else:
                                raise Exception(f"Ошибка запроса URL: HTTP {resp.status}")

                    # 3. Загружаем фото на сервер MAX
                    async with aiohttp.ClientSession() as session:
                        form_data = aiohttp.FormData()
                        form_data.add_field('data', photo_data, filename='photo.jpg')
                        async with session.post(upload_url, data=form_data) as resp:
                            if resp.status == 200:
                                upload_result = await resp.json()
                                # Извлекаем токен (структура ответа может отличаться)
                                attachment_token = upload_result.get("token")
                                if not attachment_token:
                                    # Пробуем другие возможные поля
                                    attachment_token = upload_result.get("photos", {}).get("token")
                                if not attachment_token:
                                    raise Exception(f"Не удалось получить токен вложения. Ответ: {upload_result}")
                            else:
                                raise Exception(f"Ошибка загрузки фото: HTTP {resp.status}")

                except Exception as e:
                    print(f"!!! ОШИБКА ПОДГОТОВКИ ФОТО: {e} !!!")
                    # Если что-то пошло не так, отправляем текст с ссылкой
                    await bot.send_message(chat_id=ADMIN_CHAT_ID, text=post_text + f"\n\n(Ссылка на фото: {photo_url})")
                    await bot.send_message(chat_id=chat_id, text="✅ Объявление опубликовано (текстом, т.к. фото не удалось загрузить).")
                    del user_states[chat_id]
                    del user_data[chat_id]
                    return

            # 4. Отправляем сообщение в канал
            try:
                if attachment_token:
                    # Используем токен для отправки изображения
                    attachments = [{"type": "image", "payload": {"token": attachment_token}}]
                    await bot.send_message(chat_id=ADMIN_CHAT_ID, text=post_text, attachments=attachments)
                else:
                    # Если фото не было
                    await bot.send_message(chat_id=ADMIN_CHAT_ID, text=post_text)
                
                await bot.send_message(chat_id=chat_id, text="✅ Спасибо! Ваше объявление успешно опубликовано в канале.")
            except Exception as e:
                print(f"!!! ОШИБКА ОТПРАВКИ ПОСТА: {e} !!!")
                await bot.send_message(chat_id=chat_id, text=f"❌ Ошибка публикации: {e}")
            
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

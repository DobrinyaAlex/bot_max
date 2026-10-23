import asyncio
import logging
import os
import aiohttp
from maxgram import Bot, Dispatcher, Router
from maxgram.types import BotStarted, Message
from maxgram.utils.keyboard import InlineKeyboardBuilder

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

user_states = {}
user_data = {}

async def upload_photo_to_max(photo_url: str) -> str:
    """Скачивает фото, загружает на сервер MAX и возвращает токен вложения."""
    headers = {"Authorization": f"Bearer {BOT_TOKEN}"}
    
    async with aiohttp.ClientSession() as session:
        # 1. Скачиваем фото
        async with session.get(photo_url) as resp:
            if resp.status != 200:
                raise Exception(f"Не удалось скачать фото (HTTP {resp.status})")
            photo_data = await resp.read()

        # 2. Получаем URL для загрузки (домен platform-api2.max.ru)
        async with session.post("https://platform-api2.max.ru/uploads?type=image", headers=headers) as resp:
            if resp.status != 200:
                raise Exception(f"Ошибка запроса URL для загрузки (HTTP {resp.status})")
            upload_data = await resp.json()
            upload_url = upload_data.get("url")
            if not upload_url:
                raise Exception("Не удалось получить URL для загрузки")

        # 3. Загружаем фото по полученному URL
        async with session.post(upload_url, data={'data': photo_data}, headers=headers) as resp:
            if resp.status != 200:
                raise Exception(f"Ошибка загрузки фото (HTTP {resp.status})")
            upload_result = await resp.json()
            # Токен может быть в корне ответа или в photos (в зависимости от версии API)
            token = upload_result.get("token")
            if not token:
                photos = upload_result.get("photos", {})
                if photos:
                    token = next(iter(photos.values())).get("token")
            if not token:
                raise Exception(f"Не удалось получить токен вложения. Ответ: {upload_result}")
            return token

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
    await bot.send_message(chat_id=chat_id, text=rules_text, keyboard=kb)

@router.message_callback()
async def handle_callbacks(callback):
    if getattr(callback, 'payload', None) == "captcha_passed":
        chat_id = callback.message.recipient.chat_id
        user_states[chat_id] = "waiting_text"
        try:
            await callback.answer()
        except:
            pass
        await bot.send_message(chat_id=chat_id, text="✅ Проверка пройдена! Введите текст вашего объявления (до 500 символов):")

@router.message()
async def handle_message(message: Message):
    chat_id = message.recipient.chat_id
    text = getattr(message.body, 'text', '') or ""
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
        
        if photo_url:
            try:
                attachment_token = await upload_photo_to_max(photo_url)
                attachments = [{"type": "image", "payload": {"token": attachment_token}}]
                
                # Пытаемся отправить сообщение с фото до 3 раз с паузами
                for attempt in range(3):
                    try:
                        await bot.send_message(chat_id=ADMIN_CHAT_ID, text=post_text, attachments=attachments)
                        await bot.send_message(chat_id=chat_id, text="✅ Спасибо! Ваше объявление с фото опубликовано в канале.")
                        break
                    except Exception as e:
                        if "attachment.not.ready" in str(e).lower() and attempt < 2:
                            await asyncio.sleep(2 * (attempt + 1))  # Пауза 2, 4 сек
                            continue
                        else:
                            raise e
            except Exception as e:
                print(f"!!! ОШИБКА ПУБЛИКАЦИИ ФОТО: {e} !!!")
                await bot.send_message(chat_id=ADMIN_CHAT_ID, text=post_text + "\n\n(Фото не удалось загрузить)")
                await bot.send_message(chat_id=chat_id, text="✅ Объявление опубликовано (текстом, т.к. фото не загрузилось).")
        else:
            await bot.send_message(chat_id=ADMIN_CHAT_ID, text=post_text)
            await bot.send_message(chat_id=chat_id, text="✅ Спасибо! Ваше объявление опубликовано в канале.")
        
        del user_states[chat_id]
        del user_data[chat_id]

async def main():
    dp.include_router(router)
    print("=== БОТ УСПЕШНО ЗАПУЩЕН ===")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

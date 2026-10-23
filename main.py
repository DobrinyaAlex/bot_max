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

# --- Вспомогательная функция для загрузки фото ---
async def upload_photo_to_max(photo_url: str) -> str:
    headers = {"Authorization": f"Bearer {BOT_TOKEN}"}
    async with aiohttp.ClientSession() as session:
        # 1. Скачиваем фото
        async with session.get(photo_url) as resp:
            if resp.status != 200:
                raise Exception(f"Скачивание: HTTP {resp.status}")
            photo_data = await resp.read()

        # 2. Получаем URL для загрузки
        async with session.post("https://botapi.max.ru/uploads?type=image", headers=headers) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise Exception(f"Запрос URL: HTTP {resp.status} {text}")
            upload_url = (await resp.json()).get("url")
            if not upload_url:
                raise Exception("Не получен URL для загрузки")

        # 3. Загружаем файл (строго через multipart/form-data)
        form = aiohttp.FormData()
        form.add_field('data', photo_data, filename='photo.jpg')
        async with session.post(upload_url, data=form) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise Exception(f"Загрузка файла: HTTP {resp.status} {text}")
            res = await resp.json()
            
            # Ищем токен
            token = res.get("token")
            if not token:
                photos = res.get("photos", {})
                if photos:
                    token = next(iter(photos.values())).get("token")
            if not token:
                raise Exception(f"Токен не найден в ответе: {res}")
            return token

# --- Обработчики ---
@router.bot_started()
async def on_start(event: BotStarted):
    chat_id = event.chat_id
    user_states[chat_id] = "waiting_captcha"
    user_data[chat_id] = {}
    kb = InlineKeyboardBuilder()
    kb.callback(text="✅ Я человек / Продолжить", payload="captcha_passed")
    await bot.send_message(
        chat_id=chat_id, 
        text="Здравствуйте! Нажмите кнопку ниже, чтобы подтвердить, что вы человек.", 
        keyboard=kb
    )

@router.message_callback()
async def handle_callbacks(callback):
    if getattr(callback, 'payload', None) == "captcha_passed":
        chat_id = callback.message.recipient.chat_id
        user_states[chat_id] = "waiting_text"
        try:
            await callback.answer()
        except:
            pass
        await bot.send_message(chat_id=chat_id, text="✅ Введите текст вашего объявления (до 500 символов):")

@router.message()
async def handle_message(message: Message):
    chat_id = message.recipient.chat_id
    text = getattr(message.body, 'text', '') or ""
    
    if chat_id not in user_states:
        await bot.send_message(chat_id=chat_id, text="Нажмите 'Начать' в меню бота.")
        return

    state = user_states[chat_id]

    if state == "waiting_text":
        user_data[chat_id]['ad_text'] = text
        user_states[chat_id] = "waiting_photo"
        await bot.send_message(chat_id=chat_id, text="Текст принят! Отправьте фото (или напишите «Пропустить»).")

    elif state == "waiting_photo":
        if getattr(message.body, 'attachments', None):
            user_data[chat_id]['photo'] = message.body.attachments[0].payload.url
            user_states[chat_id] = "waiting_name"
            await bot.send_message(chat_id=chat_id, text="Фото принято. Укажите имя продавца.")
        elif text.lower() == "пропустить":
            user_data[chat_id]['photo'] = None
            user_states[chat_id] = "waiting_name"
            await bot.send_message(chat_id=chat_id, text="Фото пропущено. Укажите имя продавца.")
        else:
            await bot.send_message(chat_id=chat_id, text="Отправьте фото или напишите «Пропустить».")

    elif state == "waiting_name":
        user_data[chat_id]['name'] = text
        user_states[chat_id] = "waiting_phone"
        await bot.send_message(chat_id=chat_id, text="Имя принято. Укажите телефон.")

    elif state == "waiting_phone":
        user_data[chat_id]['phone'] = text
        post_text = f"📌 **Объявление**\n\n{user_data[chat_id].get('ad_text')}\n\n👤 **Продавец:** {user_data[chat_id].get('name')}\n📞 **Телефон:** {text}"
        
        photo_url = user_data[chat_id].get('photo')
        
        if photo_url:
            try:
                attachment_token = await upload_photo_to_max(photo_url)
                attachments = [{"type": "image", "payload": {"token": attachment_token}}]
                await bot.send_message(chat_id=ADMIN_CHAT_ID, text=post_text, attachments=attachments)
                await bot.send_message(chat_id=chat_id, text="✅ Объявление с фото успешно опубликовано!")
            except Exception as e:
                # ВЫВОДИМ ТОЧНУЮ ОШИБКУ В ЧАТ
                error_msg = str(e)
                print(f"!!! ОШИБКА ФОТО: {error_msg} !!!")
                await bot.send_message(chat_id=ADMIN_CHAT_ID, text=post_text + f"\n\n(Ошибка фото: {error_msg})")
                await bot.send_message(chat_id=chat_id, text=f"✅ Опубликовано текстом.\n\n⚠️ Ошибка фото: {error_msg}")
        else:
            await bot.send_message(chat_id=ADMIN_CHAT_ID, text=post_text)
            await bot.send_message(chat_id=chat_id, text="✅ Объявление опубликовано!")
        
        del user_states[chat_id]
        del user_data[chat_id]

async def main():
    dp.include_router(router)
    print("=== БОТ УСПЕШНО ЗАПУЩЕН ===")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

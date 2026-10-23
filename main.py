import asyncio
import logging
import os
import aiohttp
from maxgram import Bot, Dispatcher, Router, F
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

# --- Загрузка фото в MAX ---
async def upload_photo_to_max(photo_url: str) -> str:
    # 1. Скачиваем фото
    async with aiohttp.ClientSession() as session:
        async with session.get(photo_url, ssl=False) as resp:
            if resp.status != 200:
                raise Exception(f"Скачивание: HTTP {resp.status}")
            photo_data = await resp.read()

    # 2. Получаем URL для загрузки
    headers = {"Authorization": BOT_TOKEN}
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://platform-api2.max.ru/uploads?type=image",
            headers=headers, ssl=False
        ) as resp:
            if resp.status != 200:
                raise Exception(f"Запрос URL: HTTP {resp.status}")
            upload_url = (await resp.json()).get("url")

    # 3. Загружаем файл
    async with aiohttp.ClientSession() as session:
        form = aiohttp.FormData()
        form.add_field('data', photo_data, filename='photo.jpg')
        async with session.post(upload_url, data=form, ssl=False) as resp:
            if resp.status != 200:
                raise Exception(f"Загрузка: HTTP {resp.status}")
            res = await resp.json()
            token = res.get("token")
            if not token:
                photos = res.get("photos", {})
                if isinstance(photos, dict) and photos:
                    token = next(iter(photos.values())).get("token")
            if not token:
                raise Exception(f"Токен не найден: {res}")
            return token

# --- Обработчики ---
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
        "2. Фотографии — до 5 штук.\n"
        "3. Имя и телефон для связи.\n\n"
        "Нажмите кнопку ниже, чтобы подтвердить, что вы человек."
    )
    await bot.send_message(chat_id=chat_id, text=rules_text, keyboard=kb)

@router.message_callback(F.payload == "captcha_passed")
async def handle_captcha(callback):
    chat_id = callback.message.recipient.chat_id
    user_states[chat_id] = "waiting_text"
    try:
        await callback.answer()
    except Exception:
        pass
    await bot.send_message(chat_id=chat_id, text="✅ Проверка пройдена! Введите текст вашего объявления (до 500 символов):")

@router.message()
async def handle_message(message: Message):
    try:
        chat_id = message.recipient.chat_id
        text = getattr(message.body, 'text', '') or ""

        if chat_id not in user_states:
            await bot.send_message(chat_id=chat_id, text="Пожалуйста, нажмите 'Начать' в меню бота.")
            return

        state = user_states[chat_id]

        # Шаг 1: текст
        if state == "waiting_text":
            if len(text) > 500:
                await bot.send_message(chat_id=chat_id, text="❌ Текст слишком длинный. Максимум 500 символов.")
                return
            user_data[chat_id]['ad_text'] = text
            user_data[chat_id]['photos'] = []
            user_states[chat_id] = "waiting_photos"
            await bot.send_message(chat_id=chat_id, text="Текст принят! Отправьте до 5 фотографий (можно альбомом). После этого напишите «Готово».")

        # Шаг 2: фото
        elif state == "waiting_photos":
            if getattr(message.body, 'attachments', None):
                added = 0
                for att in message.body.attachments:
                    if getattr(att, 'type', 'image') == 'image':
                        user_data[chat_id]['photos'].append(att.payload.url)
                        added += 1
                
                count = len(user_data[chat_id]['photos'])
                if count >= 5:
                    user_states[chat_id] = "waiting_name"
                    await bot.send_message(chat_id=chat_id, text="Принято 5 фотографий (максимум). Теперь укажите имя продавца.")
                elif added > 0:
                    await bot.send_message(chat_id=chat_id, text=f"Фото {count}/5 принято. Отправьте ещё или напишите «Готово».")
            elif text.lower() == "готово":
                user_states[chat_id] = "waiting_name"
                await bot.send_message(chat_id=chat_id, text="Фотографии приняты. Теперь укажите имя продавца.")
            else:
                await bot.send_message(chat_id=chat_id, text="Пожалуйста, отправьте фото или напишите «Готово».")

        # Шаг 3: имя
        elif state == "waiting_name":
            user_data[chat_id]['name'] = text
            user_states[chat_id] = "waiting_phone"
            await bot.send_message(chat_id=chat_id, text="Имя принято. Теперь укажите контактный телефон.")

        # Шаг 4: телефон и публикация
        elif state == "waiting_phone":
            user_data[chat_id]['phone'] = text
            
            # Итоговый текст объявления (без звездочек и с эмодзи)
            post_text = (
                f"Объявление 📌\n\n"
                f"{user_data[chat_id].get('ad_text')}\n\n"
                f"👤 Продавец: {user_data[chat_id].get('name')}\n"
                f"📞 Телефон: {text}"
            )

            photos = user_data[chat_id].get('photos', [])
            attachments = []
            if photos:
                try:
                    for url in photos:
                        token = await upload_photo_to_max(url)
                        attachments.append({"type": "image", "payload": {"token": token}})
                except Exception as e:
                    print(f"⚠️ Ошибка загрузки фото: {e}")
                    await bot.send_message(chat_id=chat_id, text=f"⚠️ Не удалось загрузить некоторые фото: {e}")

            try:
                # Отправляем пост в канал
                await bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=post_text,
                    attachments=attachments if attachments else None
                )
                await bot.send_message(chat_id=chat_id, text="✅ Спасибо! Объявление успешно опубликовано в канале.")
            except Exception as e:
                await bot.send_message(chat_id=chat_id, text=f"❌ Ошибка публикации: {e}")

            # Очищаем состояние
            del user_states[chat_id]
            del user_data[chat_id]

    except Exception as e:
        print(f"!!! КРИТИЧЕСКАЯ ОШИБКА: {e} !!!")

async def main():
    dp.include_router(router)
    print("=== БОТ УСПЕШНО ЗАПУЩЕН ===")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

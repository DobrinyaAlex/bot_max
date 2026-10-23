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

# --- Хранилища ---
user_states = {}
user_data = {}
moderation_messages = {}  # Для хранения ID сообщений, отправленных на модерацию

# --- Загрузка фото в MAX (без изменений) ---
async def upload_photo_to_max(photo_url: str) -> str:
    async with aiohttp.ClientSession() as session:
        async with session.get(photo_url, ssl=False) as resp:
            if resp.status != 200:
                raise Exception(f"Скачивание: HTTP {resp.status}")
            photo_data = await resp.read()

    headers = {"Authorization": BOT_TOKEN}
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "https://platform-api2.max.ru/uploads?type=image",
            headers=headers,
            ssl=False
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise Exception(f"Запрос URL: HTTP {resp.status} {text}")
            upload_data = await resp.json()
            upload_url = upload_data.get("url")
            if not upload_url:
                raise Exception(f"Не получен URL для загрузки: {upload_data}")

    async with aiohttp.ClientSession() as session:
        form = aiohttp.FormData()
        form.add_field('data', photo_data, filename='photo.jpg')
        async with session.post(upload_url, data=form, ssl=False) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise Exception(f"Загрузка файла: HTTP {resp.status} {text}")
            upload_result = await resp.json()
            token = None
            photos = upload_result.get("photos", {})
            if isinstance(photos, dict) and photos:
                token = next(iter(photos.values())).get("token")
            if not token:
                token = upload_result.get("token")
            if not token:
                raise Exception(f"Токен не найден: {upload_result}")
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

# --- Сбор данных ---
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
            await bot.send_message(chat_id=chat_id, text="Текст принят! Отправьте фотографии (до 5 штук). После каждой фотографии пишите «Ещё» или «Готово».")

        # Шаг 2: фото (до 5)
        elif state == "waiting_photos":
            if getattr(message.body, 'attachments', None):
                photo_url = message.body.attachments[0].payload.url
                user_data[chat_id]['photos'].append(photo_url)
                count = len(user_data[chat_id]['photos'])
                if count >= 5:
                    user_states[chat_id] = "waiting_name"
                    await bot.send_message(chat_id=chat_id, text="Принято 5 фотографий. Теперь укажите имя продавца.")
                else:
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
            post_text = (
                f"📌 **Объявление**\n\n"
                f"{user_data[chat_id].get('ad_text')}\n\n"
                f"👤 **Продавец:** {user_data[chat_id].get('name')}\n"
                f"📞 **Телефон:** {text}"
            )

            photos = user_data[chat_id].get('photos', [])
            attachments = []
            if photos:
                try:
                    for url in photos:
                        token = await upload_photo_to_max(url)
                        attachments.append({"type": "image", "payload": {"token": token}})
                except Exception as e:
                    error_msg = str(e)
                    print(f"!!! ОШИБКА ЗАГРУЗКИ ФОТО: {error_msg} !!!")
                    await bot.send_message(chat_id=chat_id, text=f"⚠️ Не удалось загрузить некоторые фото: {error_msg}")
                    # Продолжаем без фото

            # Клавиатура модерации
            kb = InlineKeyboardBuilder()
            kb.callback(text="✅ Опубликовать", payload="approve")
            kb.callback(text="❌ Отклонить", payload="reject")

            try:
                sent_message = await bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=post_text,
                    attachments=attachments if attachments else None,
                    keyboard=kb
                )
                # Сохраняем ID сообщения для модерации
                moderation_messages[sent_message.message_id] = {
                    "chat_id": ADMIN_CHAT_ID,
                    "photos": photos
                }
                await bot.send_message(chat_id=chat_id, text="✅ Спасибо! Ваше объявление отправлено на модерацию.")
            except Exception as e:
                print(f"!!! ОШИБКА ПУБЛИКАЦИИ: {e} !!!")
                await bot.send_message(chat_id=chat_id, text=f"❌ Ошибка публикации: {e}")

            del user_states[chat_id]
            del user_data[chat_id]

    except Exception as e:
        print(f"!!! КРИТИЧЕСКАЯ ОШИБКА: {e} !!!")

# --- Обработчики кнопок модерации ---
@router.message_callback(F.payload == "approve")
async def approve_post(callback):
    try:
        # Убираем клавиатуру (оставляем пост)
        await callback.message.edit(reply_markup=None)
        await callback.answer(notification="Объявление одобрено.")
        # Уведомляем автора? Можно добавить.
    except Exception as e:
        print(f"Ошибка одобрения: {e}")

@router.message_callback(F.payload == "reject")
async def reject_post(callback):
    try:
        # Удаляем сообщение из канала
        await bot.delete_message(chat_id=callback.message.recipient.chat_id, message_id=callback.message.message_id)
        await callback.answer(notification="Объявление отклонено и удалено.")
    except Exception as e:
        print(f"Ошибка отклонения: {e}")

async def main():
    dp.include_router(router)
    print("=== БОТ УСПЕШНО ЗАПУЩЕН ===")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

import asyncio
import logging
import os
import aiohttp
import random
import json
import datetime
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

DATA_DIR = os.getenv("DATA_DIR", "/app/data")
os.makedirs(DATA_DIR, exist_ok=True)
LIMITS_FILE = os.path.join(DATA_DIR, "limits.json")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()
router = Router()

user_states = {}
user_data = {}

# --- Каналы ---
def load_channels():
    raw = os.getenv("CHANNELS_JSON", "[]")
    try:
        channels = json.loads(raw)
        valid = []
        for ch in channels:
            if isinstance(ch, dict) and 'name' in ch and 'id' in ch:
                valid.append({"name": str(ch['name']), "id": int(ch['id'])})
        if valid:
            return valid
    except Exception as e:
        print(f"⚠️ Ошибка парсинга CHANNELS_JSON: {e}")
    return [{"name": "Основной канал", "id": ADMIN_CHAT_ID}]

# --- Лимиты ---
def load_limits():
    if not os.path.exists(LIMITS_FILE):
        return {}
    try:
        with open(LIMITS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_limits(data):
    with open(LIMITS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)

def get_user_limits(user_id: int, max_ads: int = 6) -> dict:
    """Возвращает информацию о лимитах пользователя (использовано, осталось, максимум)"""
    limits = load_limits()
    today = datetime.date.today().isoformat()
    user_key = str(user_id)
    
    if user_key not in limits:
        limits[user_key] = {"date": today, "count": 0}
        
    if limits[user_key]["date"] != today:
        limits[user_key] = {"date": today, "count": 0}
        
    used = limits[user_key]["count"]
    return {"used": used, "left": max_ads - used, "max": max_ads}

def increment_user_limit(user_id: int):
    """Увеличивает счетчик объявлений на 1"""
    limits = load_limits()
    today = datetime.date.today().isoformat()
    user_key = str(user_id)
    
    if user_key not in limits or limits[user_key]["date"] != today:
        limits[user_key] = {"date": today, "count": 1}
    else:
        limits[user_key]["count"] += 1
    save_limits(limits)

# --- Загрузка фото ---
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
            headers=headers, ssl=False
        ) as resp:
            if resp.status != 200:
                raise Exception(f"Запрос URL: HTTP {resp.status}")
            upload_url = (await resp.json()).get("url")

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
    user_id = event.user.user_id
    
    # Проверяем лимит сразу при старте
    limits = get_user_limits(user_id)
    if limits['left'] <= 0:
        await bot.send_message(
            chat_id=chat_id, 
            text="❌ Вы сегодня исчерпали лимит объявлений (6 из 6). Попробуйте завтра."
        )
        return
        
    user_states[chat_id] = "waiting_captcha"
    user_data[chat_id] = {}
    
    a, b = random.randint(1, 9), random.randint(1, 9)
    user_data[chat_id]['captcha_answer'] = str(a + b)
    
    await bot.send_message(
        chat_id=chat_id,
        text=f"Здравствуйте! Я помогу оформить объявление.\n"
             f"Осталось объявлений на сегодня: **{limits['left']}**.\n\n"
             f"Для подтверждения, что вы человек, решите пример: **{a} + {b}**. Введите только цифру ответа."
    )

@router.message()
async def handle_message(message: Message):
    try:
        chat_id = message.recipient.chat_id
        text = getattr(message.body, 'text', '').strip()
        user_id = message.sender.user_id

        if chat_id not in user_states:
            await bot.send_message(chat_id=chat_id, text="Пожалуйста, нажмите 'Начать' или напишите /start.")
            return

        state = user_states[chat_id]

        # Шаг 0: Капча
        if state == "waiting_captcha":
            if text == user_data[chat_id].get('captcha_answer'):
                user_states[chat_id] = "waiting_text"
                await bot.send_message(chat_id=chat_id, text="✅ Проверка пройдена! Введите текст вашего объявления (до 1500 символов):")
            else:
                a, b = random.randint(1, 9), random.randint(1, 9)
                user_data[chat_id]['captcha_answer'] = str(a + b)
                await bot.send_message(chat_id=chat_id, text=f"❌ Неверный ответ. Попробуйте ещё раз: {a} + {b}")

        # Шаг 1: Текст
        elif state == "waiting_text":
            if len(text) > 1500:
                extra = len(text) - 1500
                await bot.send_message(
                    chat_id=chat_id,
                    text=f"❌ Текст слишком длинный ({len(text)} символов). Сократите на {extra} симв. и отправьте снова."
                )
                return
            user_data[chat_id]['ad_text'] = text
            user_data[chat_id]['photos'] = []
            user_states[chat_id] = "waiting_photos"
            await bot.send_message(
                chat_id=chat_id,
                text=f"✅ Текст принят ({len(text)}/1500)! Отправьте до 5 фотографий (можно альбомом) или напишите «Готово»."
            )

        # Шаг 2: Фото
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
                    await bot.send_message(chat_id=chat_id, text="Принято 5 фото (максимум). Теперь укажите имя продавца.")
                elif added > 0:
                    await bot.send_message(chat_id=chat_id, text=f"Фото {count}/5. Отправьте ещё или напишите «Готово».")
            elif text.lower() == "готово":
                user_states[chat_id] = "waiting_name"
                await bot.send_message(chat_id=chat_id, text="Фото приняты. Укажите имя продавца.")
            else:
                await bot.send_message(chat_id=chat_id, text="Отправьте фото или напишите «Готово».")

        # Шаг 3: Имя
        elif state == "waiting_name":
            user_data[chat_id]['name'] = text
            user_states[chat_id] = "waiting_phone"
            await bot.send_message(chat_id=chat_id, text="Имя принято. Укажите контактный телефон.")

        # Шаг 4: Телефон → выбор канала
        elif state == "waiting_phone":
            user_data[chat_id]['phone'] = text
            user_states[chat_id] = "waiting_channel"

            limits = get_user_limits(user_id)
            channels = load_channels()
            
            kb = InlineKeyboardBuilder()
            for i, ch in enumerate(channels):
                kb.callback(text=ch['name'], payload=f"publish_{i}")
            
            # Принудительно ставим кнопки в столбик (по 1 в ряд)
            if hasattr(kb, 'adjust'):
                kb.adjust(1)

            await bot.send_message(
                chat_id=chat_id,
                text=f"📢 В какой канал опубликовать объявление?\n\nУ вас осталось объявлений на сегодня: **{limits['left']}**",
                keyboard=kb
            )

        # Ожидание выбора канала
        elif state == "waiting_channel":
            await bot.send_message(chat_id=chat_id, text="Пожалуйста, выберите канал из списка выше (нажмите кнопку).")

    except Exception as e:
        print(f"!!! КРИТИЧЕСКАЯ ОШИБКА: {e} !!!")

# --- Обработчик выбора канала ---
@router.message_callback()
async def handle_channel_choice(callback):
    payload = getattr(callback, 'payload', None)
    if not payload or not payload.startswith("publish_"):
        return

    try:
        idx = int(payload.split("_")[1])
    except (ValueError, IndexError):
        await callback.answer(notification="Ошибка")
        return

    channels = load_channels()
    if idx >= len(channels):
        await callback.answer(notification="Канал не найден")
        return

    target = channels[idx]
    chat_id = callback.message.recipient.chat_id
    user_id = callback.sender.user_id # Получаем ID пользователя из callback
    data = user_data.get(chat_id)

    if not data or 'phone' not in data:
        await callback.answer(notification="Данные утеряны. Начните заново: /start")
        return

    # Проверяем лимит еще раз (вдруг в двух вкладках открыли)
    limits = get_user_limits(user_id)
    if limits['left'] <= 0:
        await callback.answer(notification="Лимит исчерпан", show_alert=True)
        await bot.send_message(chat_id=chat_id, text="❌ Вы исчерпали лимит. Попробуйте завтра.")
        del user_data[chat_id]
        del user_states[chat_id]
        return

    # Удаляем данные, чтобы повторное нажатие не дублировало публикацию
    del user_data[chat_id]
    del user_states[chat_id]

    try:
        await callback.answer()
    except Exception:
        pass

    # Загрузка фото
    photos = data.get('photos', [])
    attachments = []
    for url in photos:
        try:
            token = await upload_photo_to_max(url)
            attachments.append({"type": "image", "payload": {"token": token}})
        except Exception as e:
            print(f"⚠️ Ошибка загрузки фото: {e}")

    post_text = (
        f"📌 Объявление 📌\n\n"
        f"{data.get('ad_text')}\n\n"
        f"👤 Продавец: {data.get('name')}\n"
        f"📞 Телефон: {data.get('phone')}"
    )

    try:
        # Отправляем пост
        await bot.send_message(
            chat_id=target['id'],
            text=post_text,
            attachments=attachments if attachments else None
        )
        
        # Списываем лимит и показываем остаток
        increment_user_limit(user_id)
        new_limits = get_user_limits(user_id)
        
        await bot.send_message(
            chat_id=chat_id,
            text=f"✅ Объявление опубликовано в канал: **{target['name']}**\n\n"
                 f"Осталось объявлений на сегодня: **{new_limits['left']}** из {new_limits['max']}."
        )
    except Exception as e:
        print(f"!!! ОШИБКА ПУБЛИКАЦИИ В {target['name']}: {e} !!!")
        await bot.send_message(
            chat_id=chat_id,
            text=f"❌ Не удалось опубликовать в «{target['name']}»: {e}\n"
                 f"Попробуйте выбрать другой канал или обратитесь к администратору."
        )
        # Возвращаем данные обратно в память, чтобы он мог выбрать другой канал
        user_data[chat_id] = data
        user_states[chat_id] = "waiting_channel"

async def main():
    dp.include_router(router)
    print("=== БОТ УСПЕШНО ЗАПУЩЕН ===")
    channels = load_channels()
    print(f"📢 Доступные каналы: {[c['name'] for c in channels]}")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

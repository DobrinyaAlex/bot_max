import asyncio
import logging
import os
import aiomax
from aiomax import Bot
from aiomax.buttons import KeyboardBuilder, CallbackButton

# --- Конфигурация ---
BOT_TOKEN = os.getenv("MAX_BOT_TOKEN")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))

if not BOT_TOKEN:
    raise ValueError("Не задана переменная окружения MAX_BOT_TOKEN")

logging.basicConfig(level=logging.INFO)

# Инициализация бота (use_certificate=True обязателен для MAX на Bothost)
bot = Bot(BOT_TOKEN, use_certificate=True)
fsm = aiomax.fsm.FSMManager(aiomax.fsm.MemoryStorage())

class AdForm(aiomax.fsm.StatesGroup):
    waiting_for_text = aiomax.fsm.State()
    waiting_for_photo = aiomax.fsm.State()
    waiting_for_name = aiomax.fsm.State()
    waiting_for_phone = aiomax.fsm.State()

# --- Обработчик кнопки "Начать" (системное событие bot_started) ---
@bot.on_bot_start()
async def on_bot_started(payload):
    print("=== ОТЛАДКА: СРАБОТАЛ ОБРАБОТЧИК КНОПКИ 'НАЧАТЬ' ===")
    
    kb = KeyboardBuilder()
    kb.add(CallbackButton('✅ Я человек / Продолжить', 'captcha_passed'))

    rules_text = (
        "Здравствуйте! Я помогу оформить объявление.\n\n"
        "📌 **Правила оформления:**\n"
        "1. Текст объявления — до 500 символов.\n"
        "2. Фотография — 1 шт.\n"
        "3. Имя и телефон для связи.\n\n"
        "Нажмите кнопку ниже, чтобы подтвердить, что вы человек."
    )
    await payload.send(rules_text, keyboard=kb)

# --- Обработчик команды /start (на случай ручного ввода) ---
@bot.on_message(aiomax.filters.F.command("start"))
async def on_start_command(message):
    print("=== ОТЛАДКА: СРАБОТАЛ ОБРАБОТЧИК КОМАНДЫ /start ===")
    
    kb = KeyboardBuilder()
    kb.add(CallbackButton('✅ Я человек / Продолжить', 'captcha_passed'))

    await message.reply(
        "Здравствуйте! Я помогу оформить объявление.\nНажмите кнопку ниже:",
        keyboard=kb
    )

# --- Обработчик нажатия на inline-кнопку ---
@bot.on_button_callback(lambda data: data.payload == 'captcha_passed')
async def process_callback(callback):
    print("=== ОТЛАДКА: КНОПКА НАЖАТА ===")
    await callback.answer() # Убираем часики загрузки
    
    chat_id = callback.message.recipient.chat_id
    user_id = callback.user_id

    await fsm.set_state(user_id, str(AdForm.waiting_for_text), chat_id)
    await callback.message.answer("✅ Проверка пройдена! Введите текст вашего объявления (до 500 символов):")

# --- Сбор данных (FSM) ---
@bot.on_message()
async def handle_text(message):
    chat_id = message.recipient.chat_id
    user_id = message.sender.user_id
    state = await fsm.get_state(user_id, chat_id)

    if state == str(AdForm.waiting_for_text) and message.body.text:
        await fsm.update_data(user_id, {"ad_text": message.body.text}, chat_id)
        await fsm.set_state(user_id, str(AdForm.waiting_for_photo), chat_id)
        await message.reply("Текст принят. Теперь отправьте фотографию (или напишите «Пропустить»).")

@bot.on_message()
async def handle_photo(message):
    chat_id = message.recipient.chat_id
    user_id = message.sender.user_id
    state = await fsm.get_state(user_id, chat_id)

    if state == str(AdForm.waiting_for_photo):
        if message.body.attachments:
            photo_url = message.body.attachments[0].payload.url
            await fsm.update_data(user_id, {"ad_photo": photo_url}, chat_id)
            await fsm.set_state(user_id, str(AdForm.waiting_for_name), chat_id)
            await message.reply("Фото принято. Теперь укажите имя продавца.")
        elif message.body.text and message.body.text.lower() == "пропустить":
            await fsm.update_data(user_id, {"ad_photo": None}, chat_id)
            await fsm.set_state(user_id, str(AdForm.waiting_for_name), chat_id)
            await message.reply("Фото пропущено. Теперь укажите имя продавца.")
        else:
            await message.reply("Пожалуйста, отправьте фото или напишите «Пропустить».")

@bot.on_message()
async def handle_name(message):
    chat_id = message.recipient.chat_id
    user_id = message.sender.user_id
    state = await fsm.get_state(user_id, chat_id)

    if state == str(AdForm.waiting_for_name) and message.body.text:
        await fsm.update_data(user_id, {"seller_name": message.body.text}, chat_id)
        await fsm.set_state(user_id, str(AdForm.waiting_for_phone), chat_id)
        await message.reply("Имя принято. Теперь укажите контактный телефон.")

@bot.on_message()
async def handle_phone(message):
    chat_id = message.recipient.chat_id
    user_id = message.sender.user_id
    state = await fsm.get_state(user_id, chat_id)

    if state == str(AdForm.waiting_for_phone) and message.body.text:
        phone = message.body.text
        data = await fsm.get_data(user_id, chat_id)
        await fsm.clear_state(user_id, chat_id) 

        ad_text = data.get("ad_text", "—")
        seller_name = data.get("seller_name", "—")
        photo_url = data.get("ad_photo")

        admin_text = (
            f"📥 **Новое объявление**\n\n"
            f"**Текст:** {ad_text}\n"
            f"**Имя:** {seller_name}\n"
            f"**Телефон:** {phone}\n"
            f"**ID пользователя:** {user_id}"
        )
        if photo_url:
            admin_text += f"\n**Фото:** {photo_url}"

        try:
            await bot.send_message(chat_id=ADMIN_CHAT_ID, text=admin_text)
            await message.reply("✅ Спасибо! Ваше объявление отправлено на проверку.")
        except Exception as e:
            logging.error(f"Ошибка отправки админу: {e}")
            await message.reply("Произошла ошибка при отправке. Попробуйте позже.")

# --- Запуск ---
async def main():
    logging.getLogger('aiohttp').setLevel(logging.CRITICAL)
    try:
        await bot.delete_webhook()
    except Exception:
        pass
        
    print("=== БОТ УСПЕШНО ЗАПУЩЕН ===")
    await bot.start_polling()

if __name__ == "__main__":
    asyncio.run(main())

import asyncio
import logging
import os
from aiomax import Bot
from aiomax.filters import F
from aiomax.fsm import State, StatesGroup, MemoryStorage, FSMManager
from aiomax.models.keyboards import InlineKeyboardMarkup, InlineKeyboardButton

# --- Конфигурация ---
BOT_TOKEN = os.getenv("MAX_BOT_TOKEN")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))

if not BOT_TOKEN:
    raise ValueError("Не задана переменная окружения MAX_BOT_TOKEN")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
fsm = FSMManager(MemoryStorage())

class AdForm(StatesGroup):
    waiting_for_text = State()
    waiting_for_photo = State()
    waiting_for_name = State()
    waiting_for_phone = State()

# --- Шаг 1: Старт бота (Нажатие кнопки "Начать") ---
@bot.on_bot_started()
async def cmd_start(update):
    chat_id = update.chat_id
    user_id = update.user_id

    # Создаем inline-кнопку
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Я человек / Продолжить", callback_data="captcha_passed")]
        ]
    )

    rules_text = (
        "Здравствуйте! Я помогу оформить объявление в канале MAX.\n\n"
        "📌 **Правила оформления:**\n"
        "1. Текст объявления — до 500 символов, без CAPS LOCK.\n"
        "2. Фотографии — до 5 штук (JPG/PNG).\n"
        "3. Обязательно укажите имя и телефон для связи.\n"
        "4. Объявление будет опубликовано после проверки модератором.\n\n"
        "Нажмите кнопку ниже, чтобы подтвердить, что вы человек, и начать."
    )

    await bot.send_message(
        chat_id=chat_id,
        text=rules_text,
        reply_markup=keyboard
    )

# --- Шаг 2: Обработка нажатия на inline-кнопку ---
@bot.on_callback_query(F.data == "captcha_passed")
async def process_callback(update):
    chat_id = update.message.recipient.chat_id
    user_id = update.user_id
    
    # Сообщаем MAX, что кнопка нажата (убирает часики загрузки)
    await bot.answer_callback_query(callback_query_id=update.callback_query_id)
    
    # Меняем состояние на ожидание текста
    await fsm.set_state(user_id, str(AdForm.waiting_for_text), chat_id)
    
    # Отправляем следующее сообщение
    await bot.send_message(
        chat_id=chat_id, 
        text="✅ Проверка пройдена! Введите текст вашего объявления (до 500 символов):"
    )

# --- Шаг 3: Сбор текста ---
@bot.on_message()
async def handle_text(update):
    chat_id = update.message.recipient.chat_id
    user_id = update.user_id
    state = await fsm.get_state(user_id, chat_id)

    if state == str(AdForm.waiting_for_text) and update.message.body.text:
        await fsm.update_data(user_id, {"ad_text": update.message.body.text}, chat_id)
        await fsm.set_state(user_id, str(AdForm.waiting_for_photo), chat_id)
        await bot.send_message(chat_id=chat_id, text="Текст принят. Теперь отправьте фотографию (или напишите «Пропустить»).")

# --- Шаг 4: Сбор фото (с возможностью пропуска) ---
@bot.on_message()
async def handle_photo(update):
    chat_id = update.message.recipient.chat_id
    user_id = update.user_id
    state = await fsm.get_state(user_id, chat_id)

    if state == str(AdForm.waiting_for_photo):
        if update.message.body.attachments:
            attachment = update.message.body.attachments[0]
            photo_url = attachment.payload.url if hasattr(attachment.payload, 'url') else None
            await fsm.update_data(user_id, {"ad_photo": photo_url}, chat_id)
            await fsm.set_state(user_id, str(AdForm.waiting_for_name), chat_id)
            await bot.send_message(chat_id=chat_id, text="Фото принято. Теперь укажите имя продавца.")
        elif update.message.body.text and update.message.body.text.lower() == "пропустить":
            await fsm.update_data(user_id, {"ad_photo": None}, chat_id)
            await fsm.set_state(user_id, str(AdForm.waiting_for_name), chat_id)
            await bot.send_message(chat_id=chat_id, text="Фото пропущено. Теперь укажите имя продавца.")
        else:
            await bot.send_message(chat_id=chat_id, text="Пожалуйста, отправьте фото или напишите «Пропустить».")

# --- Шаг 5: Сбор имени ---
@bot.on_message()
async def handle_name(update):
    chat_id = update.message.recipient.chat_id
    user_id = update.user_id
    state = await fsm.get_state(user_id, chat_id)

    if state == str(AdForm.waiting_for_name) and update.message.body.text:
        await fsm.update_data(user_id, {"seller_name": update.message.body.text}, chat_id)
        await fsm.set_state(user_id, str(AdForm.waiting_for_phone), chat_id)
        await bot.send_message(chat_id=chat_id, text="Имя принято. Теперь укажите контактный телефон.")

# --- Шаг 6: Сбор телефона и отправка на модерацию ---
@bot.on_message()
async def handle_phone(update):
    chat_id = update.message.recipient.chat_id
    user_id = update.user_id
    state = await fsm.get_state(user_id, chat_id)

    if state == str(AdForm.waiting_for_phone) and update.message.body.text:
        phone = update.message.body.text
        data = await fsm.get_data(user_id, chat_id)
        await fsm.clear_state(user_id, chat_id) 

        ad_text = data.get("ad_text", "—")
        seller_name = data.get("seller_name", "—")
        photo_url = data.get("ad_photo")

        admin_text = (
            f"📥 **Новое объявление на проверку**\n\n"
            f"**Текст:** {ad_text}\n"
            f"**Имя:** {seller_name}\n"
            f"**Телефон:** {phone}\n"
            f"**ID пользователя:** {user_id}"
        )

        try:
            if photo_url:
                admin_text += f"\n**Фото:** {photo_url}"
                await bot.send_message(chat_id=ADMIN_CHAT_ID, text=admin_text)
            else:
                await bot.send_message(chat_id=ADMIN_CHAT_ID, text=admin_text)

            await bot.send_message(chat_id=chat_id, text="✅ Спасибо! Ваше объявление отправлено на проверку.")
        except Exception as e:
            logging.error(f"Не удалось отправить заявку админу: {e}")
            await bot.send_message(chat_id=chat_id, text="Произошла ошибка при отправке заявки. Попробуйте позже.")

async def main():
    logging.getLogger('aiohttp').setLevel(logging.CRITICAL)
    await bot.delete_webhook() 
    await bot.start_polling()

if __name__ == "__main__":
    asyncio.run(main())

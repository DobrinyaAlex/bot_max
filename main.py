import asyncio
import logging
import os
from maxgram import Bot, Dispatcher, Router, F
from maxgram.fsm import State, StatesGroup, MemoryStorage, FSMContext
from maxgram.types import BotStarted, Message
from maxgram.utils.keyboard import InlineKeyboardBuilder

# --- Конфигурация ---
BOT_TOKEN = os.getenv("MAX_BOT_TOKEN")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))

if not BOT_TOKEN:
    raise ValueError("Не задана переменная окружения MAX_BOT_TOKEN")

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()

# --- Состояния (FSM) для шагов ---
class AdForm(StatesGroup):
    waiting_for_text = State()
    waiting_for_photo = State()
    waiting_for_name = State()
    waiting_for_phone = State()

# --- Шаг 1: Обработчик кнопки "Начать" ---
@router.bot_started()
async def on_start(event: BotStarted):
    builder = InlineKeyboardBuilder()
    builder.callback(text="✅ Я человек / Продолжить", payload="captcha_passed")

    rules_text = (
        "Здравствуйте! Я помогу оформить объявление.\n\n"
        "📌 **Правила оформления:**\n"
        "1. Текст объявления — до 500 символов.\n"
        "2. Фотография — 1 шт.\n"
        "3. Имя и телефон для связи.\n\n"
        "Нажмите кнопку ниже, чтобы подтвердить, что вы человек."
    )
    await event.answer(text=rules_text, keyboard=builder)

# --- Шаг 2: Обработчик нажатия на кнопку ---
@router.message_callback(lambda call: call.payload == "captcha_passed")
async def process_captcha(callback, state: FSMContext):
    await callback.answer(notification="Проверка пройдена!")
    
    # Переводим бота в режим ожидания текста
    await state.set_state(AdForm.waiting_for_text)
    await callback.message.answer("✅ Проверка пройдена! Введите текст вашего объявления (до 500 символов):")

# --- Шаг 3: Ловим текст ---
@router.message(AdForm.waiting_for_text)
async def handle_text(message: Message, state: FSMContext):
    if not message.body.text:
        await message.answer("Пожалуйста, отправьте именно текст.")
        return

    await state.update_data(ad_text=message.body.text)
    await state.set_state(AdForm.waiting_for_photo)
    await message.answer("Текст принят. Теперь отправьте фотографию (или напишите «Пропустить»).")

# --- Шаг 4: Ловим фото (или пропуск) ---
@router.message(AdForm.waiting_for_photo)
async def handle_photo(message: Message, state: FSMContext):
    if message.body.attachments:
        # Берем первое фото
        photo_url = message.body.attachments[0].payload.url
        await state.update_data(ad_photo=photo_url)
        await state.set_state(AdForm.waiting_for_name)
        await message.answer("Фото принято. Теперь укажите имя продавца.")
        
    elif message.body.text and message.body.text.lower() == "пропустить":
        await state.update_data(ad_photo=None)
        await state.set_state(AdForm.waiting_for_name)
        await message.answer("Фото пропущено. Теперь укажите имя продавца.")
        
    else:
        await message.answer("Пожалуйста, отправьте фото или напишите «Пропустить».")

# --- Шаг 5: Ловим имя ---
@router.message(AdForm.waiting_for_name)
async def handle_name(message: Message, state: FSMContext):
    if not message.body.text:
        await message.answer("Пожалуйста, отправьте имя текстом.")
        return

    await state.update_data(seller_name=message.body.text)
    await state.set_state(AdForm.waiting_for_phone)
    await message.answer("Имя принято. Теперь укажите контактный телефон.")

# --- Шаг 6: Ловим телефон и отправляем админу ---
@router.message(AdForm.waiting_for_phone)
async def handle_phone(message: Message, state: FSMContext):
    if not message.body.text:
        await message.answer("Пожалуйста, отправьте телефон текстом.")
        return

    phone = message.body.text
    data = await state.get_data()
    await state.clear() # Сбрасываем состояние

    ad_text = data.get("ad_text", "—")
    seller_name = data.get("seller_name", "—")
    photo_url = data.get("ad_photo")

    # Формируем сообщение для админа/канала
    admin_text = (
        f"📥 **Новое объявление на проверку**\n\n"
        f"**Текст:** {ad_text}\n"
        f"**Имя:** {seller_name}\n"
        f"**Телефон:** {phone}\n"
        f"**ID пользователя:** {message.sender.user_id}"
    )
    if photo_url:
        admin_text += f"\n**Фото:** {photo_url}"

    try:
        # Отправляем на модерацию
        await bot.send_message(chat_id=ADMIN_CHAT_ID, text=admin_text)
        await message.answer("✅ Спасибо! Ваше объявление отправлено на проверку.")
    except Exception as e:
        logging.error(f"Ошибка отправки админу: {e}")
        await message.answer("Произошла ошибка при отправке. Попробуйте позже.")

# --- Запуск ---
async def main():
    dp.include_router(router)
    print("=== БОТ УСПЕШНО ЗАПУЩЕН ===")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

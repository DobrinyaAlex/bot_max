import asyncio
import logging
import os
import aiomax

# --- Конфигурация ---
BOT_TOKEN = os.getenv("MAX_BOT_TOKEN")
# ID чата или канала, куда будут приходить заявки. 
# Для канала ID начинается с минуса (например, -80168518583681).
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))

if not BOT_TOKEN:
    raise ValueError("Не задана переменная окружения MAX_BOT_TOKEN")

logging.basicConfig(level=logging.INFO)

# Инициализируем бота. 
# ВАЖНО: use_certificate=True решает проблему с таймаутами на Bothost (сертификаты Минцифры).
bot = aiomax.Bot(BOT_TOKEN, use_certificate=True)

# Менеджер состояний (FSM) для последовательного сбора данных
fsm = aiomax.fsm.FSMManager(aiomax.fsm.MemoryStorage())

class AdForm(aiomax.fsm.StatesGroup):
    waiting_for_text = aiomax.fsm.State()
    waiting_for_photo = aiomax.fsm.State()
    waiting_for_name = aiomax.fsm.State()
    waiting_for_phone = aiomax.fsm.State()

# --- Шаг 1: Обработка кнопки "Начать" (bot_started) ---
@bot.on_bot_start()
async def cmd_start(payload):
    # Создаем inline-кнопку
    kb = aiomax.buttons.KeyboardBuilder()
    kb.add(aiomax.buttons.CallbackButton('✅ Я человек / Продолжить', 'captcha_passed'))

    rules_text = (
        "Здравствуйте! Я помогу оформить объявление в канале MAX.\n\n"
        "📌 **Правила оформления:**\n"
        "1. Текст объявления — до 500 символов, без CAPS LOCK.\n"
        "2. Фотографии — до 5 штук (JPG/PNG).\n"
        "3. Обязательно укажите имя и телефон для связи.\n"
        "4. Объявление будет опубликовано после проверки модератором.\n\n"
        "Нажмите кнопку ниже, чтобы подтвердить, что вы человек, и начать."
    )

    await payload.send(rules_text, keyboard=kb)

# --- Шаг 2: Обработка нажатия на inline-кнопку ---
@bot.on_button_callback(lambda data: data.payload == 'captcha_passed')
async def process_callback(callback):
    # Отвечаем на callback, чтобы убрать часики загрузки на кнопке
    await callback.answer()
    
    chat_id = callback.message.recipient.chat_id
    user_id = callback.user_id

    # Устанавливаем состояние "ожидание текста"
    await fsm.set_state(user_id, str(AdForm.waiting_for_text), chat_id)
    
    await callback.message.answer("✅ Проверка пройдена! Введите текст вашего объявления (до 500 символов):")

# --- Шаг 3: Сбор текста ---
@bot.on_message()
async def handle_text(message):
    chat_id = message.recipient.chat_id
    user_id = message.sender.user_id
    state = await fsm.get_state(user_id, chat_id)

    if state == str(AdForm.waiting_for_text) and message.body.text:
        await fsm.update_data(user_id, {"ad_text": message.body.text}, chat_id)
        await fsm.set_state(user_id, str(AdForm.waiting_for_photo), chat_id)
        await message.reply("Текст принят. Теперь отправьте фотографию (или напишите «Пропустить»).")

# --- Шаг 4: Сбор фото (с возможностью пропуска) ---
@bot.on_message()
async def handle_photo(message):
    chat_id = message.recipient.chat_id
    user_id = message.sender.user_id
    state = await fsm.get_state(user_id, chat_id)

    if state == str(AdForm.waiting_for_photo):
        if message.body.attachments:
            # Берем первое вложение
            attachment = message.body.attachments[0]
            # Пытаемся получить ссылку на фото (в MAX это payload.url)
            photo_url = getattr(attachment.payload, 'url', None)
            
            await fsm.update_data(user_id, {"ad_photo": photo_url}, chat_id)
            await fsm.set_state(user_id, str(AdForm.waiting_for_name), chat_id)
            await message.reply("Фото принято. Теперь укажите имя продавца.")
            
        elif message.body.text and message.body.text.lower() == "пропустить":
            await fsm.update_data(user_id, {"ad_photo": None}, chat_id)
            await fsm.set_state(user_id, str(AdForm.waiting_for_name), chat_id)
            await message.reply("Фото пропущено. Теперь укажите имя продавца.")
        else:
            await message.reply("Пожалуйста, отправьте фото или напишите «Пропустить».")

# --- Шаг 5: Сбор имени ---
@bot.on_message()
async def handle_name(message):
    chat_id = message.recipient.chat_id
    user_id = message.sender.user_id
    state = await fsm.get_state(user_id, chat_id)

    if state == str(AdForm.waiting_for_name) and message.body.text:
        await fsm.update_data(user_id, {"seller_name": message.body.text}, chat_id)
        await fsm.set_state(user_id, str(AdForm.waiting_for_phone), chat_id)
        await message.reply("Имя принято. Теперь укажите контактный телефон.")

# --- Шаг 6: Сбор телефона и отправка на модерацию ---
@bot.on_message()
async def handle_phone(message):
    chat_id = message.recipient.chat_id
    user_id = message.sender.user_id
    state = await fsm.get_state(user_id, chat_id)

    if state == str(AdForm.waiting_for_phone) and message.body.text:
        phone = message.body.text
        data = await fsm.get_data(user_id, chat_id)
        await fsm.clear_state(user_id, chat_id) # Сбрасываем состояние

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
            # Если есть фото, прикрепляем ссылку к сообщению
            if photo_url:
                admin_text += f"\n**Фото:** {photo_url}"
            
            # Отправляем администратору
            await bot.send_message(chat_id=ADMIN_CHAT_ID, text=admin_text)

            await message.reply("✅ Спасибо! Ваше объявление отправлено на проверку. Мы свяжемся с вами после модерации.")
        except Exception as e:
            logging.error(f"Не удалось отправить заявку админу: {e}")
            await message.reply("Произошла ошибка при отправке заявки. Попробуйте позже.")

# --- Запуск ---
async def main():
    # Отключаем спам таймаутов в логах (это нормальная работа long polling)
    logging.getLogger('aiohttp').setLevel(logging.CRITICAL)
    
    # Принудительно удаляем вебхук, чтобы избежать конфликтов с polling
    try:
        await bot.delete_webhook()
    except Exception as e:
        logging.warning(f"Не удалось удалить вебхук (возможно, его и не было): {e}")
        
    logging.info("Бот успешно запущен и ожидает сообщений!")
    await bot.start_polling()

if __name__ == "__main__":
    asyncio.run(main())

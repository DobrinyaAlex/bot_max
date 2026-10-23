import asyncio
import logging
import os
from aiomax import Bot
from aiomax.filters import F
from aiomax.fsm import State, StatesGroup, MemoryStorage, FSMManager

# --- Конфигурация ---
BOT_TOKEN = os.getenv("MAX_BOT_TOKEN")
# ID администратора (или чата), куда будут приходить заявки на модерацию.
# Узнать свой ID можно через специальных ботов или в веб-версии MAX.
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID", "0"))

if not BOT_TOKEN:
    raise ValueError("Не задана переменная окружения MAX_BOT_TOKEN")

logging.basicConfig(level=logging.INFO)

bot = Bot(token=BOT_TOKEN)
fsm = FSMManager(MemoryStorage())

class AdForm(StatesGroup):
    waiting_for_captcha = State()
    waiting_for_text = State()
    waiting_for_photo = State()
    waiting_for_name = State()
    waiting_for_phone = State()

# --- Шаг 1: Старт и капча ---
@bot.on_message(F.command("start"))
async def cmd_start(update):
    chat_id = update.message.recipient.chat_id
    user_id = update.user_id

    # Отправляем сообщение с inline-кнопкой для проверки
    # В MAX для этого используется объект клавиатуры, но для простоты
    # можно использовать текстовый запрос или специализированный метод.
    # В aiomax можно отправлять кнопки, но для примера используем текст.
    await bot.send_message(
        chat_id=chat_id,
        text="Здравствуйте! Для подачи объявления подтвердите, что вы человек, отправив команду /human"
    )
    await fsm.set_state(user_id, str(AdForm.waiting_for_captcha), chat_id)

@bot.on_message(F.command("human"))
async def captcha_ok(update):
    chat_id = update.message.recipient.chat_id
    user_id = update.user_id
    state = await fsm.get_state(user_id, chat_id)

    if state == str(AdForm.waiting_for_captcha):
        await fsm.set_state(user_id, str(AdForm.waiting_for_text), chat_id)
        await bot.send_message(chat_id=chat_id, text="? Проверка пройдена! Введите текст объявления:")
    else:
        await bot.send_message(chat_id=chat_id, text="Сначала начните с команды /start")

# --- Шаг 2: Сбор текста ---
@bot.on_message()
async def handle_text(update):
    chat_id = update.message.recipient.chat_id
    user_id = update.user_id
    state = await fsm.get_state(user_id, chat_id)

    if state == str(AdForm.waiting_for_text) and update.message.body.text:
        await fsm.update_data(user_id, {"ad_text": update.message.body.text}, chat_id)
        await fsm.set_state(user_id, str(AdForm.waiting_for_photo), chat_id)
        await bot.send_message(chat_id=chat_id, text="Текст принят. Теперь отправьте фотографию (или напишите «Пропустить»).")

# --- Шаг 3: Сбор фото (с возможностью пропуска) ---
@bot.on_message()
async def handle_photo(update):
    chat_id = update.message.recipient.chat_id
    user_id = update.user_id
    state = await fsm.get_state(user_id, chat_id)

    if state == str(AdForm.waiting_for_photo):
        # Проверяем, есть ли в сообщении вложения (фото)
        if update.message.body.attachments:
            # Берем первое вложение (фото) и сохраняем его URL или ID
            # Для простоты сохраняем URL, если он есть.
            # В реальном проекте лучше сохранять file_id или скачивать файл.
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

# --- Шаг 4: Сбор имени ---
@bot.on_message()
async def handle_name(update):
    chat_id = update.message.recipient.chat_id
    user_id = update.user_id
    state = await fsm.get_state(user_id, chat_id)

    if state == str(AdForm.waiting_for_name) and update.message.body.text:
        await fsm.update_data(user_id, {"seller_name": update.message.body.text}, chat_id)
        await fsm.set_state(user_id, str(AdForm.waiting_for_phone), chat_id)
        await bot.send_message(chat_id=chat_id, text="Имя принято. Теперь укажите контактный телефон.")

# --- Шаг 5: Сбор телефона и отправка на модерацию ---
@bot.on_message()
async def handle_phone(update):
    chat_id = update.message.recipient.chat_id
    user_id = update.user_id
    state = await fsm.get_state(user_id, chat_id)

    if state == str(AdForm.waiting_for_phone) and update.message.body.text:
        phone = update.message.body.text
        data = await fsm.get_data(user_id, chat_id)
        await fsm.clear_state(user_id, chat_id) # Сбрасываем состояние

        ad_text = data.get("ad_text", "—")
        seller_name = data.get("seller_name", "—")
        photo_url = data.get("ad_photo")

        # Формируем сообщение для администратора
        admin_text = (
            f"?? **Новое объявление на проверку**\n\n"
            f"**Текст:** {ad_text}\n"
            f"**Имя:** {seller_name}\n"
            f"**Телефон:** {phone}\n"
            f"**ID пользователя:** {user_id}"
        )

        try:
            # Отправляем администратору. Если есть фото, отправляем с медиа.
            if photo_url:
                # В MAX для отправки медиа можно использовать метод send_media или аналогичный.
                # Для простоты отправляем текстовое сообщение с ссылкой на фото.
                admin_text += f"\n**Фото:** {photo_url}"
                await bot.send_message(chat_id=ADMIN_CHAT_ID, text=admin_text)
            else:
                await bot.send_message(chat_id=ADMIN_CHAT_ID, text=admin_text)

            await bot.send_message(chat_id=chat_id, text="? Спасибо! Ваше объявление отправлено на проверку.")
        except Exception as e:
            logging.error(f"Не удалось отправить заявку админу: {e}")
            await bot.send_message(chat_id=chat_id, text="Произошла ошибка при отправке заявки. Попробуйте позже.")

async def main():
    await bot.start()
    await bot.start_polling()
    await bot.close()

if __name__ == "__main__":
    asyncio.run(main())

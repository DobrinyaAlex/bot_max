import asyncio
import logging
from maxapi import Bot, Dispatcher
import os

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("maxbot")

BOT_TOKEN = os.getenv("MAX_BOT_TOKEN", "")

async def main():
    log.info("Проверка запуска...")
    try:
        bot = Bot(token=BOT_TOKEN)
        dp = Dispatcher()
        log.info("✅ Библиотека maxapi успешно импортирована и инициализирована!")
        
        # Просто проверим, что он может пингануть API
        me = await bot.get_me()
        log.info(f"Бот авторизован: {me}")
    except Exception as e:
        log.error(f"❌ Ошибка при инициализации: {e}")

if __name__ == "__main__":
    asyncio.run(main())"""MAX Bot v2 — объявления с пре-модерацией через канал модерации."""
import asyncio
import json
import logging
import random
import time
import traceback
from datetime import datetime, timedelta, timezone

# РАБОЧИЕ ИМПОРТЫ MAXGRAM
from maxgram import Bot, Dispatcher, Router
from maxgram.types import BotStarted, Message
from maxgram.utils.keyboard import InlineKeyboardBuilder

import config
import db
import media
import validators

# ---------- logging ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    handlers=[
        logging.FileHandler(config.LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("maxbot")

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()
router = Router()

_last_msg_ts: dict[int, float] = {}


# ---------- helpers ----------
def get_user_id(obj) -> int | None:
    if hasattr(obj, "user") and hasattr(obj.user, "user_id"):
        return obj.user.user_id
    if hasattr(obj, "sender") and hasattr(obj.sender, "user_id"):
        return obj.sender.user_id
    if hasattr(obj, "message") and hasattr(obj.message, "sender") and hasattr(obj.message.sender, "user_id"):
        return obj.message.sender.user_id
    if hasattr(obj, "from_user") and hasattr(obj.from_user, "user_id"):
        return obj.from_user.user_id
    return None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def rate_ok(user_id: int) -> bool:
    now = time.monotonic()
    if now - _last_msg_ts.get(user_id, 0.0) < config.RATE_LIMIT_SECONDS:
        return False
    _last_msg_ts[user_id] = now
    return True


def is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_USER_IDS


def moderation_target() -> int | None:
    if config.ADMIN_CHAT_ID:
        return config.ADMIN_CHAT_ID
    saved = db.get_setting("admin_chat_id")
    if saved:
        try:
            return int(saved)
        except ValueError:
            pass
    if config.ADMIN_USER_IDS:
        return config.ADMIN_USER_IDS[0]
    return None


async def safe_send(chat_id: int, text: str, keyboard=None, attachments=None) -> bool:
    try:
        await bot.send_message(chat_id=chat_id, text=text, keyboard=keyboard, attachments=attachments)
        return True
    except Exception as e:
        log.error("send_message chat=%s: %s", chat_id, e)
        return False


async def alert_admin(text: str) -> None:
    target = moderation_target()
    if not target:
        log.warning("alert_admin: нет цели, сообщение потеряно:\n%s", text)
        return
    try:
        await bot.send_message(chat_id=target, text=text)
    except Exception:
        log.error("alert_admin failed: %s", traceback.format_exc())


def session_get(chat_id: int) -> tuple[str | None, dict | None]:
    s = db.get_session(chat_id)
    if not s:
        return None, None
    try:
        updated = datetime.fromisoformat(s["updated_at"])
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
    except Exception:
        db.clear_session(chat_id)
        return None, None
    if datetime.now(timezone.utc) - updated > timedelta(minutes=config.SESSION_TIMEOUT_MIN):
        db.clear_session(chat_id)
        return None, None
    return s["state"], json.loads(s["data"])


def session_set(chat_id: int, user_id: int, state: str, data: dict) -> None:
    db.set_session(chat_id, int(user_id), state, json.dumps(data, ensure_ascii=False))


def format_post(data: dict) -> str:
    return (
        "📌 Объявление 📌\n\n"
        f"{data.get('ad_text', '')}\n\n"
        f"👤 Продавец: {data.get('name', '')}\n"
        f"📞 Телефон: {data.get('phone', '')}"
    )


# ---------- start flow ----------
@router.bot_started()
async def on_start(event: BotStarted):
    try:
        chat_id = event.chat_id
        user_id = get_user_id(event) or chat_id
        await start_flow(chat_id, user_id)
    except Exception:
        log.exception("on_start error")
        await alert_admin(f"!! on_start:\n{traceback.format_exc()}")


async def start_flow(chat_id: int, user_id: int) -> None:
    if chat_id < 0:
        return

    db.upsert_user(user_id)
    log.info("start_flow user_id=%s chat_id=%s", user_id, chat_id)

    if is_admin(user_id):
        db.set_setting("admin_chat_id", str(chat_id))
        log.info("admin chat_id сохранён как fallback: %s", chat_id)

    banned = db.is_banned(user_id)
    if banned:
        await safe_send(
            chat_id,
            f"⛔ Вы заблокированы до {banned['banned_until']}.\n"
            f"Причина: {banned['ban_reason'] or '—'}",
        )
        return

    limits = db.get_daily_limits(user_id, config.DAILY_AD_LIMIT)
    if limits["left"] <= 0:
        await safe_send(
            chat_id,
            f"❌ Лимит объявлений на сегодня исчерпан ({limits['used']}/{limits['max']}). "
            f"Попробуйте завтра.",
        )
        return

    a, b = random.randint(1, 9), random.randint(1, 9)
    data = {"captcha_answer": str(a + b), "captcha_attempts": 0, "photos": []}
    session_set(chat_id, user_id, "captcha", data)
    
    await safe_send(
        chat_id,
        f"Здравствуйте! Я помогу оформить объявление.\n"
        f"Осталось объявлений на сегодня: {limits['left']}/{limits['max']}.\n\n"
        f"💡 Доступные команды:\n"
        f"/my — ваши объявления\n"
        f"/cancel — отменить действие\n"
        f"/help — справка\n\n"
        f"Для подтверждения, что вы человек, решите пример: {a} + {b}.\n"
        f"Введите только цифру ответа. Попыток: {config.CAPTCHA_MAX_ATTEMPTS}.",
    )


# ---------- message router ----------
@router.message()
async def handle_message(message: Message):
    try:
        chat_id = message.recipient.chat_id
        if chat_id < 0:
            return
        user_id = get_user_id(message) or chat_id
        text = (getattr(message.body, "text", "") or "").strip()

        if not rate_ok(user_id):
            return

        if text.startswith("/start"):
            await start_flow(chat_id, user_id)
            return
        if text == "/cancel":
            db.clear_session(chat_id)
            await safe_send(chat_id, "Отменено. /start — начать заново.")
            return
        if text == "/help":
            await safe_send(
                chat_id,
                "Доступные команды:\n"
                "/start — начать оформление объявления\n"
                "/my — мои объявления (удалить, повторить)\n"
                "/cancel — отменить текущее действие\n"
                "/help — эта справка",
            )
            return
        if text == "/my":
            await cmd_my_ads(chat_id, user_id)
            return
        if text.startswith("/admin"):
            await cmd_admin(chat_id, user_id, text)
            return

        banned = db.is_banned(user_id)
        if banned:
            await safe_send(chat_id, f"⛔ Вы заблокированы до {banned['banned_until']}.")
            return

        state, data = session_get(chat_id)
        if state is None:
            await safe_send(chat_id, "Нажмите /start, чтобы начать. Или воспользуйтесь /help для списка команд.")
            return

        if state == "captcha":
            await handle_captcha(chat_id, user_id, data, text)
        elif state == "text":
            await handle_text(chat_id, user_id, data, text)
        elif state == "photos":
            await handle_photos(chat_id, user_id, data, message, text)
        elif state == "name":
            await handle_name(chat_id, user_id, data, text)
        elif state == "phone":
            await handle_phone(chat_id, user_id, data, text)
        elif state == "preview":
            await safe_send(chat_id, "Нажмите кнопку под предпросмотром.")
        elif state == "channel":
            await safe_send(chat_id, "Выберите канал кнопкой выше.")
        else:
            db.clear_session(chat_id)
            await safe_send(chat_id, "Сессия сброшена. /start")

    except Exception:
        log.exception("handle_message error")
        await alert_admin(f"!! handle_message:\n{traceback.format_exc()}")


# ---------- state handlers ----------
async def handle_captcha(chat_id, user_id, data, text):
    if not text:
        await safe_send(chat_id, "Введите цифру ответа.")
        return
    if text == data.get("captcha_answer"):
        data["captcha_attempts"] = 0
        session_set(chat_id, user_id, "text", data)
        await safe_send(
            chat_id,
            f"✅ Проверка пройдена!\nВведите текст объявления (до {config.MAX_TEXT_LEN} символов):",
        )
        return

    attempts = data.get("captcha_attempts", 0) + 1
    if attempts >= config.CAPTCHA_MAX_ATTEMPTS:
        until = (datetime.now(timezone.utc) + timedelta(minutes=config.CAPTCHA_BAN_MIN)).isoformat()
        db.ban_user(user_id, until, "3 неверных ответа капчи")
        db.clear_session(chat_id)
        await safe_send(
            chat_id,
            f"⛔ Слишком много неверных ответов. Блокировка на {config.CAPTCHA_BAN_MIN} мин.",
        )
        return

    a, b = random.randint(1, 9), random.randint(1, 9)
    data["captcha_answer"] = str(a + b)
    data["captcha_attempts"] = attempts
    session_set(chat_id, user_id, "captcha", data)
    await safe_send(
        chat_id,
        f"❌ Неверно. Попытка {attempts}/{config.CAPTCHA_MAX_ATTEMPTS}. Новый пример: {a} + {b}.",
    )


async def handle_text(chat_id, user_id, data, text):
    ok, msg, has_url = validators.validate_ad_text(text)
    if not ok:
        await safe_send(chat_id, f"❌ {msg}")
        return

    data["ad_text"] = msg
    data["has_url"] = has_url

    if data.get("from_edit") and data.get("name") and data.get("phone"):
        data.pop("from_edit", None)
        session_set(chat_id, user_id, "preview", data)
        await show_preview(chat_id, user_id, data)
        return

    data.setdefault("photos", [])
    session_set(chat_id, user_id, "photos", data)
    extra = "\n⚠️ Обнаружена ссылка — объявление проверит модератор вручную." if has_url else ""
    await safe_send(
        chat_id,
        f"✅ Текст принят ({len(msg)}/{config.MAX_TEXT_LEN}).{extra}\n"
        f"Отправьте до {config.MAX_PHOTOS} фото (можно альбомом) или напишите «Готово».",
    )


async def handle_photos(chat_id, user_id, data, message, text):
    attachments = getattr(message.body, "attachments", None) or []
    for att in attachments:
        if getattr(att, "type", "image") == "image":
            url = getattr(att.payload, "url", None)
            if url and url not in data["photos"]:
                data["photos"].append(url)

    if len(data["photos"]) >= config.MAX_PHOTOS:
        session_set(chat_id, user_id, "name", data)
        await safe_send(
            chat_id,
            f"✅ Принято {config.MAX_PHOTOS} фото (максимум).\nУкажите имя продавца:",
        )
        return

    if text.lower() in ("готово", "без фото"):
        session_set(chat_id, user_id, "name", data)
        prefix = "" if data["photos"] else "Публикация без фото.\n"
        await safe_send(chat_id, f"{prefix}Укажите имя продавца:")
        return

    if data["photos"]:
        session_set(chat_id, user_id, "photos", data)
        await safe_send(
            chat_id,
            f"✅ Фото {len(data['photos'])}/{config.MAX_PHOTOS}. Ещё или «Готово».",
        )
        return

    await safe_send(chat_id, "Отправьте фото или напишите «Готово» / «Без фото».")


async def handle_name(chat_id, user_id, data, text):
    ok, res = validators.validate_name(text)
    if not ok:
        await safe_send(chat_id, f"❌ {res}")
        return
    data["name"] = res
    session_set(chat_id, user_id, "phone", data)
    await safe_send(chat_id, "✅ Имя принято.\nУкажите контактный телефон.")


async def handle_phone(chat_id, user_id, data, text):
    ok, res = validators.validate_phone(text)
    if not ok:
        await safe_send(chat_id, f"❌ {res}")
        return
    data["phone"] = res
    session_set(chat_id, user_id, "preview", data)
    await show_preview(chat_id, user_id, data)


# ---------- preview / channel ----------
async def show_preview(chat_id, user_id, data):
    kb = InlineKeyboardBuilder()
    kb.callback(text="✅ Опубликовать", payload="confirm_publish")
    kb.callback(text="✏️ Изменить текст", payload="edit_text")
    kb.callback(text="❌ Отмена", payload="cancel_ad")
    if hasattr(kb, "adjust"):
        kb.adjust(1)

    attachments = []
    photos = data.get("photos", [])
    
    if photos:
        await safe_send(chat_id, "⏳ Загружаю фото для предпросмотра, подождите...")
        for url in photos:
            try:
                token = await media.upload_photo_to_max(url)
                attachments.append({"type": "image", "payload": {"token": token}})
            except Exception as e:
                log.warning("Ошибка загрузки фото в предпросмотре: %s", e)

    await safe_send(
        chat_id,
        f"📋 Предпросмотр:\n\n{format_post(data)}\n\nВсё верно?",
        keyboard=kb,
        attachments=attachments or None
    )


async def ask_channel(chat_id, user_id, data):
    channels = config.load_channels()
    limits = db.get_daily_limits(user_id, config.DAILY_AD_LIMIT)
    kb = InlineKeyboardBuilder()
    for i, ch in enumerate(channels):
        kb.callback(text=ch["name"], payload=f"channel_{i}")
    if hasattr(kb, "adjust"):
        kb.adjust(1)
    session_set(chat_id, user_id, "channel", data)
    await safe_send(
        chat_id,
        f"📢 В какой канал опубликовать?\n"
        f"Осталось объявлений: {limits['left']}/{limits['max']}",
        keyboard=kb,
    )


# ---------- callbacks ----------
@router.message_callback()
async def handle_callback(callback):
    try:
        payload = getattr(callback, "payload", None) or ""
        try:
            await callback.answer()
        except Exception:
            pass

        chat_id = callback.message.recipient.chat_id
        user_id = get_user_id(callback) or chat_id

        if payload in ("confirm_publish", "edit_text", "cancel_ad"):
            await cb_preview(chat_id, user_id, payload)
        elif payload.startswith("channel_"):
            await cb_channel(chat_id, user_id, payload)
        elif payload.startswith("approve_"):
            await admin_approve(chat_id, user_id, int(payload.split("_")[1]))
        elif payload.startswith("reject_"):
            parts = payload.split("_")
            await admin_reject(chat_id, user_id, int(parts[1]), parts[2] if len(parts) > 2 else "other")
        elif payload.startswith("myads_delete_"):
            await delete_user_ad(chat_id, user_id, int(payload.split("_")[2]))
        elif payload.startswith("myads_repeat_"):
            await repeat_user_ad(chat_id, user_id, int(payload.split("_")[2]))

    except Exception:
        log.exception("handle_callback error")
        await alert_admin(f"!! handle_callback:\n{traceback.format_exc()}")


async def cb_preview(chat_id, user_id, payload):
    state, data = session_get(chat_id)
    if state != "preview" or not data:
        await safe_send(chat_id, "Сессия истекла. /start")
        return
    if payload == "cancel_ad":
        db.clear_session(chat_id)
        await safe_send(chat_id, "Отменено.")
        return
    if payload == "edit_text":
        data["from_edit"] = True
        data["ad_text"] = ""
        session_set(chat_id, user_id, "text", data)
        await safe_send(chat_id, f"Введите новый текст (до {config.MAX_TEXT_LEN} символов):")
        return
    await ask_channel(chat_id, user_id, data)


async def cb_channel(chat_id, user_id, payload):
    state, data = session_get(chat_id)
    if state != "channel" or not data:
        await safe_send(chat_id, "Сессия истекла. /start")
        return
    try:
        idx = int(payload.split("_")[1])
    except Exception:
        return
    channels = config.load_channels()
    if idx >= len(channels):
        return
    target = channels[idx]
    
    db.clear_session(chat_id)
    await send_ad_to_moderation(chat_id, user_id, data, target)


async def send_ad_to_moderation(chat_id, user_id, data, target):
    limits = db.get_daily_limits(user_id, config.DAILY_AD_LIMIT)
    if limits["left"] <= 0:
        await safe_send(chat_id, "❌ Лимит исчерпан.")
        return

    ad_id = db.create_ad(
        user_id=user_id,
        chat_id=chat_id,
        ad_text=data["ad_text"],
        name=data["name"],
        phone=data["phone"],
        photos=data.get("photos", []),
        channel_id=target["id"],
        channel_name=target["name"],
        status="pending",
    )

    admin_chat = moderation_target()
    if not admin_chat:
        log.error("Нет цели модерации")
        await safe_send(
            chat_id,
            "⚠️ Модерация временно недоступна. Обратитесь к администратору.",
        )
        return

    kb = InlineKeyboardBuilder()
    kb.callback(text="✅ Одобрить", payload=f"approve_{ad_id}")
    kb.callback(text="🚫 Спам", payload=f"reject_{ad_id}_spam")
    kb.callback(text="🚫 Лексика", payload=f"reject_{ad_id}_content")
    kb.callback(text="🚫 Контакты", payload=f"reject_{ad_id}_contact")
    kb.callback(text="🚫 Другое", payload=f"reject_{ad_id}_other")
    if hasattr(kb, "adjust"):
        kb.adjust(1)

    admin_text = (
        f"🆕 Заявка #{ad_id}\n"
        f"Канал: {target['name']}\n"
        f"Пользователь: {user_id}\n"
        f"Ссылка в тексте: {'⚠️ да' if data.get('has_url') else 'нет'}\n\n"
        f"{format_post(data)}"
    )

    photos = data.get("photos", [])
    attachments = []
    for url in photos:
        try:
            token = await media.upload_photo_to_max(url)
            attachments.append({"type": "image", "payload": {"token": token}})
        except Exception as e:
            log.warning("Ошибка загрузки фото для модерации: %s", e)

    try:
        await bot.send_message(
            chat_id=admin_chat, 
            text=admin_text, 
            keyboard=kb, 
            attachments=attachments or None
        )
    except Exception as e:
        log.error("admin send error: %s", e)
        await safe_send(chat_id, "⚠️ Не удалось отправить на модерацию. Попробуйте позже.")
        return

    await safe_send(
        chat_id,
        f"📨 Объявление #{ad_id} отправлено на модерацию.\nПосле проверки придёт уведомление.",
    )


# ---------- admin: approve / reject ----------
REJECT_REASONS = {
    "spam": "объявление расценено как спам",
    "content": "нецензурная лексика или запрещённый контент",
    "contact": "некорректные контактные данные",
    "other": "не соответствует правилам канала",
}


async def admin_approve(chat_id, user_id, ad_id):
    ad = db.get_ad(ad_id)
    if not ad or ad["status"] != "pending":
        await safe_send(chat_id, "❌ Заявка не найдена или уже обработана.")
        return

    photos = json.loads(ad["photos"] or "[]")
    attachments = []
    for url in photos:
        try:
            token = await media.upload_photo_to_max(url)
            attachments.append({"type": "image", "payload": {"token": token}})
        except Exception as e:
            log.warning("photo upload failed ad=%s: %s", ad_id, e)

    data = {"ad_text": ad["ad_text"], "name": ad["name"], "phone": ad["phone"]}
    try:
        res = await bot.send_message(
            chat_id=ad["channel_id"],
            text=format_post(data),
            attachments=attachments or None,
        )
        msg_id = None
        if isinstance(res, dict):
            msg_id = res.get("message_id")
        elif hasattr(res, "message_id"):
            msg_id = res.message_id

        db.update_ad_published(ad_id, msg_id, now_iso())
        db.increment_daily_limit(ad["user_id"])
        limits = db.get_daily_limits(ad["user_id"], config.DAILY_AD_LIMIT)

        await safe_send(
            ad["chat_id"],
            f"✅ Объявление #{ad_id} опубликовано в «{ad['channel_name']}».\n"
            f"Осталось на сегодня: {limits['left']}/{limits['max']}.",
        )
        await safe_send(chat_id, f"✅ #{ad_id} опубликовано.")
    except Exception as e:
        log.exception("publish failed ad=%s", ad_id)
        await safe_send(chat_id, f"❌ Публикация #{ad_id}: {e}")


async def admin_reject(chat_id, user_id, ad_id, reason_code):
    ad = db.get_ad(ad_id)
    if not ad or ad["status"] != "pending":
        await safe_send(chat_id, "❌ Заявка не найдена или уже обработана.")
        return
    reason = REJECT_REASONS.get(reason_code, REJECT_REASONS["other"])
    db.update_ad_rejected(ad_id, reason_code)
    await safe_send(
        ad["chat_id"],
        f"❌ Объявление #{ad_id} отклонено модератором.\nПричина: {reason}.",
    )
    await safe_send(chat_id, f"🚫 #{ad_id} отклонено ({reason_code}).")


# ---------- /my ----------
async def cmd_my_ads(chat_id, user_id):
    ads = db.get_user_ads(user_id, limit=10)
    if not ads:
        await safe_send(chat_id, "У вас пока нет объявлений.")
        return
    status_map = {"pending": "⏳ на модерации", "published": "✅ опубликовано",
                  "rejected": "🚫 отклонено", "deleted": "🗑 удалено"}
    lines = ["Ваши последние объявления:"]
    for ad in ads:
        lines.append(f"#{ad['id']} {status_map.get(ad['status'], ad['status'])} — {ad['channel_name'] or '—'}")

    kb = InlineKeyboardBuilder()
    for ad in ads[:5]:
        if ad["status"] == "published":
            kb.callback(text=f"🗑 Удалить #{ad['id']}", payload=f"myads_delete_{ad['id']}")
        if ad["status"] in ("published", "rejected"):
            kb.callback(text=f"🔁 Повторить #{ad['id']}", payload=f"myads_repeat_{ad['id']}")
    if hasattr(kb, "adjust"):
        kb.adjust(1)
    await safe_send(chat_id, "\n".join(lines), keyboard=kb)


async def delete_user_ad(chat_id, user_id, ad_id):
    ad = db.get_ad(ad_id)
    if not ad or ad["user_id"] != user_id:
        await safe_send(chat_id, "Не найдено.")
        return
    if ad["status"] != "published":
        await safe_send(chat_id, "Удалить можно только опубликованное.")
        return
    if not hasattr(bot, "delete_message"):
        await safe_send(chat_id, "Удаление через API недоступно. Напишите админу.")
        return
    try:
        await bot.delete_message(chat_id=ad["channel_id"], message_id=ad["channel_msg_id"])
        db.update_ad_status(ad_id, "deleted")
        await safe_send(chat_id, f"🗑 #{ad_id} удалено из канала.")
    except Exception as e:
        log.exception("delete failed ad=%s", ad_id)
        await safe_send(chat_id, f"❌ Не удалось удалить: {e}")


async def repeat_user_ad(chat_id, user_id, ad_id):
    ad = db.get_ad(ad_id)
    if not ad or ad["user_id"] != user_id:
        return
    banned = db.is_banned(user_id)
    if banned:
        await safe_send(chat_id, "⛔ Вы заблокированы.")
        return
        
    target = {"id": ad["channel_id"], "name": ad["channel_name"]}
    data = {
        "ad_text": ad["ad_text"],
        "name": ad["name"],
        "phone": ad["phone"],
        "photos": json.loads(ad["photos"] or "[]"),
        "has_url": False,
    }
    
    await safe_send(chat_id, f"⏳ Отправляю повторно на модерацию в канал «{target['name']}»...")
    await send_ad_to_moderation(chat_id, user_id, data, target)


# ---------- /admin ----------
async def cmd_admin(chat_id, user_id, text):
    if not is_admin(user_id):
        await safe_send(chat_id, "⛔ Нет доступа.")
        return
    parts = text.split()
    if len(parts) == 1:
        await safe_send(
            chat_id,
            "Админ-панель:\n"
            "/admin stats — статистика\n"
            "/admin pending — заявки\n"
            "/admin ban <uid> [минут] — блок\n"
            "/admin unban <uid> — разблок\n"
            "/admin limits <uid> — лимиты\n"
            "/admin channels — каналы",
        )
        return
    sub = parts[1].lower()

    if sub == "stats":
        s = db.stats()
        await safe_send(
            chat_id,
            f"📊 Статистика:\n"
            f"Пользователей: {s['users']}\n"
            f"Объявлений: {s['ads_total']}\n"
            f"  ⏳ на модерации: {s['ads_pending']}\n"
            f"  ✅ опубликовано: {s['ads_published']}\n"
            f"  🚫 отклонено: {s['ads_rejected']}\n"
            f"Заблокировано: {s['banned']}",
        )
        return

    if sub == "pending":
        ads = db.get_pending_ads()
        if not ads:
            await safe_send(chat_id, "Заявок нет.")
            return
        lines = [f"⏳ Заявок: {len(ads)}"]
        for a in ads[:10]:
            lines.append(f"#{a['id']} — {a['channel_name']} (user {a['user_id']})")
        await safe_send(chat_id, "\n".join(lines))
        return

    if sub == "ban" and len(parts) >= 3:
        try:
            uid = int(parts[2])
        except ValueError:
            await safe_send(chat_id, "uid должен быть числом.")
            return
        minutes = int(parts[3]) if len(parts) > 3 else 60
        until = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
        db.ban_user(uid, until, "admin ban")
        await safe_send(chat_id, f"⛔ Забанен {uid} до {until}.")
        return

    if sub == "unban" and len(parts) >= 3:
        try:
            uid = int(parts[2])
        except ValueError:
            await safe_send(chat_id, "uid должен быть числом.")
            return
        db.unban_user(uid)
        await safe_send(chat_id, f"✅ Разбанен {uid}.")
        return

    if sub == "limits" and len(parts) >= 3:
        try:
            uid = int(parts[2])
        except ValueError:
            await safe_send(chat_id, "uid должен быть числом.")
            return
        lim = db.get_daily_limits(uid, config.DAILY_AD_LIMIT)
        await safe_send(chat_id, f"Пользователь {uid}: {lim['used']}/{lim['max']} за сегодня.")
        return

    if sub == "channels":
        chs = config.load_channels()
        lines = ["📢 Каналы:"] + [f"• {c['name']} ({c['id']})" for c in chs]
        await safe_send(chat_id, "\n".join(lines))
        return

    await safe_send(chat_id, "Неизвестная команда. /admin — справка.")


# ---------- entrypoint ----------
async def main():
    db.init_db()
    dp.include_router(router)
    channels = config.load_channels()
    log.info("=== БОТ ЗАПУЩЕН ===")
    log.info("Каналы: %s", [c["name"] for c in channels])
    log.info("ADMIN_CHAT_ID=%s ADMIN_USER_IDS=%s", config.ADMIN_CHAT_ID, config.ADMIN_USER_IDS)
    if not config.ADMIN_CHAT_ID:
        log.warning("ADMIN_CHAT_ID не задан — заявки пойдут в личку первого админа или потеряются!")

    try:
        from maxgram.types import BotCommand
        await bot.set_my_commands([
            BotCommand(command="start", description="Начать оформление"),
            BotCommand(command="my", description="Мои объявления"),
            BotCommand(command="cancel", description="Отменить действие"),
            BotCommand(command="help", description="Справка"),
        ])
        log.info("Команды успешно зарегистрированы в меню MAX.")
    except Exception as e:
        log.warning("Не удалось зарегистрировать меню команд: %s", e)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

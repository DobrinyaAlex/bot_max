"""MAX Bot v2 — объявления с пре-модерацией, голосом и FAQ (чистый aiohttp).
Версия от 03.11.2026 (v4): добавлен резервный gTTS и вывод ошибок в чат.
"""
import asyncio
import json
import logging
import random
import time
from datetime import datetime, timedelta, timezone

import aiohttp

import config
import db
import media
import validators
import tts

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    handlers=[
        logging.FileHandler(config.LOG_PATH, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("maxbot")

class MAXBot:
    def __init__(self, token):
        self.token = token
        self.base_url = "https://platform-api2.max.ru"
        self.headers = {"Authorization": token}
        self.session = None

    async def start(self):
        self.session = aiohttp.ClientSession(
            headers=self.headers,
            connector=aiohttp.TCPConnector(ssl=False),
        )

    async def close(self):
        if self.session:
            await self.session.close()

    async def send_message(self, chat_id: int, text: str, keyboard=None, attachments=None):
        params = {"chat_id": chat_id}
        payload = {"text": text}
        final_attachments = list(attachments) if attachments else []
        if keyboard:
            final_attachments.append(keyboard)
        if final_attachments:
            payload["attachments"] = final_attachments

        try:
            async with self.session.post(
                f"{self.base_url}/messages", params=params, json=payload
            ) as r:
                raw = await r.text()
                try:
                    body = json.loads(raw)
                except Exception:
                    body = {"raw": raw}
                if r.status >= 400:
                    log.error("send_message error %s: %s", r.status, body)
                    body["ok"] = False
                    body["_status"] = r.status
                return body
        except Exception as e:
            log.exception("send_message exception: %s", e)
            return {"ok": False, "error": str(e), "_status": 0}

    async def get_updates(self, offset=None):
        params = {"timeout": 30}
        if offset:
            params["offset"] = offset
        try:
            async with self.session.get(
                f"{self.base_url}/updates", params=params
            ) as r:
                if r.status == 200:
                    return (await r.json()).get("updates", [])
        except Exception as e:
            log.error("get_updates error: %s", e)
        return []

    async def answer_callback(self, callback_id):
        try:
            async with self.session.post(
                f"{self.base_url}/answers", json={"callback_id": callback_id}
            ) as r:
                pass
        except Exception:
            pass

    async def delete_message(self, chat_id: int, message_id) -> tuple[bool, dict]:
        if message_id is None:
            return False, {"error": "message_id is None", "_status": 0}

        try:
            async with self.session.delete(
                f"{self.base_url}/messages",
                params={"chat_id": chat_id, "message_id": message_id},
            ) as r:
                raw = await r.text()
                try:
                    body = json.loads(raw)
                except Exception:
                    body = {"raw": raw}
                body["_status"] = r.status
                ok = (
                    r.status == 200
                    and body.get("ok") is not False
                    and not body.get("error")
                )
                if not ok:
                    log.warning(
                        "delete_message failed: chat=%s mid=%s status=%s body=%s",
                        chat_id, message_id, r.status, body,
                    )
                return ok, body
        except Exception as e:
            log.exception("delete_message exception: %s", e)
            return False, {"error": str(e), "_status": 0}

    async def upload_audio(self, audio_bytes: bytes) -> str | None:
        # В MAX API аудио загружается через type=audio
        url = "https://platform-api2.max.ru/uploads?type=audio"
        try:
            async with self.session.post(url, headers=self.headers) as r:
                if r.status != 200:
                    log.error("upload_audio get url failed: %s %s", r.status, await r.text())
                    return None
                payload = await r.json()
                upload_url = payload.get("url")
                if not upload_url:
                    log.error("upload_audio: no url in response: %s", payload)
                    return None

            form = aiohttp.FormData()
            form.add_field("data", audio_bytes, filename="voice.mp3")
            async with self.session.post(upload_url, data=form) as r:
                if r.status != 200:
                    log.error("upload_audio upload failed: %s %s", r.status, await r.text())
                    return None
                res = await r.json()
                token = res.get("token")
                if not token:
                    for v in res.values():
                        if isinstance(v, dict) and "token" in v:
                            token = v["token"]
                            break
                if not token:
                    log.error("upload_audio: no token in response: %s", res)
                return token
        except Exception as e:
            log.exception("upload_audio exception: %s", e)
            return None

bot = MAXBot(config.BOT_TOKEN)
_last_msg_ts: dict[int, float] = {}

def _extract_mid(res) -> int | str | None:
    if not isinstance(res, dict):
        return None
    msg = res.get("message") or {}
    body = msg.get("body") or {}
    candidates = [
        body.get("mid"),
        msg.get("mid"),
        res.get("mid"),
        res.get("message_id"),
    ]
    for v in candidates:
        if v is None:
            continue
        try:
            return int(v)
        except (TypeError, ValueError):
            return v
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

# ---------- Настройки пользователя (озвучка) ----------

def voice_enabled(user_id: int) -> bool:
    val = db.get_setting(f"voice_{user_id}")
    return val == "1"

def set_voice_enabled(user_id: int, enabled: bool) -> None:
    db.set_setting(f"voice_{user_id}", "1" if enabled else "0")

async def send_voice_reply(chat_id: int, user_id: int, text: str) -> bool:
    if not voice_enabled(user_id):
        return False
    try:
        audio_bytes, path = await tts.synthesize_and_cleanup(text)
        token = await bot.upload_audio(audio_bytes)
        try:
            import os
            os.unlink(path)
        except Exception:
            pass
        if not token:
            log.warning("send_voice_reply: no token for text len=%s", len(text))
            raise RuntimeError("MAX API не вернул token для аудио.")
        attachment = {"type": "audio", "payload": {"token": token}}
        res = await bot.send_message(chat_id, "", attachments=[attachment])
        if res.get("error"):
            raise RuntimeError(f"Ошибка отправки аудио: {res.get('error')}")
        return True
    except Exception as e:
        log.exception("send_voice_reply failed: %s", e)
        # Отправляем ошибку пользователю, чтобы он видел, в чем дело
        if "Озвучивание включено" in text:
            await safe_send(chat_id, f"⚠️ Ошибка озвучки: {e}")
        return False

async def safe_send(chat_id: int, text: str, keyboard=None, attachments=None) -> bool:
    return bool(await bot.send_message(chat_id, text, keyboard, attachments))

# ---------- Сессии ----------

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
        f"📌 Объявление 📌\n\n{data.get('ad_text', '')}\n\n"
        f"👤 Продавец: {data.get('name', '')}\n"
        f"📞 Телефон: {data.get('phone', '')}"
    )

def make_keyboard(buttons: list[tuple[str, str]]):
    return {
        "type": "inline_keyboard",
        "payload": {
            "buttons": [
                [{"type": "callback", "text": text, "payload": payload}]
                for text, payload in buttons
            ]
        },
    }

# ---------- Главное меню и приветствие ----------

async def show_main_menu(chat_id: int, user_id: int):
    voice_status = "🔊 Вкл" if voice_enabled(user_id) else "🔇 Выкл"
    kb = make_keyboard([
        ("📝 Подать объявление", "menu_new_ad"),
        ("📋 Мои объявления", "menu_my_ads"),
        ("❓ FAQ", "menu_faq"),
        (f"Озвучивание: {voice_status} (нажмите чтобы изменить)", "menu_toggle_voice"),
    ])
    text = (
        "👋 Добро пожаловать в «Деловую Витрину»!\n\n"
        "Я помогу разместить объявление в тематических каналах.\n"
        "Выберите действие:"
    )
    await safe_send(chat_id, text, kb)

async def show_faq_menu(chat_id: int):
    kb = make_keyboard([
        ("Как подать объявление?", "faq_how"),
        ("Сколько стоит?", "faq_price"),
        ("Как удалить объявление?", "faq_delete"),
        ("Почему объявление не опубликовано?", "faq_moderation"),
        ("⬅️ Назад", "menu_back"),
    ])
    await safe_send(chat_id, "❓ Частые вопросы:", kb)

async def show_faq_answer(chat_id: int, key: str):
    answers = {
        "faq_how": "Чтобы подать объявление, нажмите «📝 Подать объявление» в главном меню. Бот проведёт вас по шагам: капча, текст, фото, имя, телефон и выбор канала.",
        "faq_price": "Размещение объявлений бесплатно. Ограничение — до 6 объявлений в сутки с одного аккаунта.",
        "faq_delete": "Откройте «📋 Мои объявления», найдите нужное объявление и нажмите «🗑 Удалить». Оно будет удалено из канала.",
        "faq_moderation": "Все объявления проходят ручную модерацию. Если объявление отклонено, причина будет указана в уведомлении. Чаще всего это спам, нецензурная лексика или контакты в тексте.",
    }
    text = answers.get(key, "Информация не найдена.")
    kb = make_keyboard([("⬅️ Назад к FAQ", "menu_faq")])
    await safe_send(chat_id, text, kb)

# ---------- Сценарий создания объявления ----------

async def start_flow(chat_id: int, user_id: int) -> None:
    if chat_id < 0:
        return
    db.upsert_user(user_id)
    log.info("start_flow user_id=%s chat_id=%s", user_id, chat_id)
    if is_admin(user_id):
        db.set_setting("admin_chat_id", str(chat_id))

    banned = db.is_banned(user_id)
    if banned:
        await safe_send(chat_id, f"⛔ Вы заблокированы до {banned['banned_until']}.")
        return

    await show_main_menu(chat_id, user_id)
    await send_voice_reply(
        chat_id, user_id,
        "Добро пожаловать в Деловую Витрину. Выберите действие в меню."
    )

async def start_ad_creation(chat_id: int, user_id: int):
    db.upsert_user(user_id)
    banned = db.is_banned(user_id)
    if banned:
        await safe_send(chat_id, f"⛔ Вы заблокированы до {banned['banned_until']}.")
        return

    limits = db.get_daily_limits(user_id, config.DAILY_AD_LIMIT)
    if limits["left"] <= 0:
        await safe_send(chat_id, "❌ Лимит объявлений на сегодня исчерпан.")
        return

    a, b = random.randint(1, 9), random.randint(1, 9)
    data = {"captcha_answer": str(a + b), "captcha_attempts": 0, "photos": []}
    session_set(chat_id, user_id, "captcha", data)

    await safe_send(
        chat_id,
        f"Осталось объявлений на сегодня: {limits['left']}/{limits['max']}.\n\n"
        f"Для подтверждения, что вы человек, решите пример: {a} + {b}.\n"
        f"Введите только цифру ответа. Попыток: {config.CAPTCHA_MAX_ATTEMPTS}.",
    )

async def handle_captcha(chat_id, user_id, data, text):
    if not text:
        return
    if text == data.get("captcha_answer"):
        data["captcha_attempts"] = 0
        session_set(chat_id, user_id, "text", data)
        msg = f"✅ Проверка пройдена!\nВведите текст объявления (до {config.MAX_TEXT_LEN} символов):"
        await safe_send(chat_id, msg)
        await send_voice_reply(chat_id, user_id, "Проверка пройдена. Введите текст объявления.")
        return

    attempts = data.get("captcha_attempts", 0) + 1
    if attempts >= config.CAPTCHA_MAX_ATTEMPTS:
        until = (
            datetime.now(timezone.utc) + timedelta(minutes=config.CAPTCHA_BAN_MIN)
        ).isoformat()
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
    extra = "\n⚠️ Обнаружена ссылка." if has_url else ""
    msg_text = (
        f"✅ Текст принят ({len(msg)}/{config.MAX_TEXT_LEN}).{extra}\n"
        f"Отправьте до {config.MAX_PHOTOS} фото или напишите «Готово»."
    )
    await safe_send(chat_id, msg_text)
    await send_voice_reply(chat_id, user_id, "Текст принят. Отправьте фото или напишите Готово.")

async def handle_photos(chat_id, user_id, data, msg_body, text):
    for att in msg_body.get("attachments", []):
        if att.get("type") == "image":
            url = att.get("payload", {}).get("url")
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

async def show_preview(chat_id, user_id, data):
    kb = make_keyboard(
        [
            ("✅ Опубликовать", "confirm_publish"),
            ("✏️ Изменить текст", "edit_text"),
            ("❌ Отмена", "cancel_ad"),
        ]
    )
    attachments = []
    for url in data.get("photos", []):
        try:
            token = await media.upload_photo_to_max(url)
            attachments.append({"type": "image", "payload": {"token": token}})
        except Exception as e:
            log.warning("Ошибка загрузки фото в предпросмотре: %s", e)

    await safe_send(
        chat_id,
        f"📋 Предпросмотр:\n\n{format_post(data)}\n\nВсё верно?",
        kb,
        attachments or None,
    )

async def ask_channel(chat_id, user_id, data):
    channels = config.load_channels()
    limits = db.get_daily_limits(user_id, config.DAILY_AD_LIMIT)
    kb = make_keyboard(
        [(ch["name"], f"channel_{i}") for i, ch in enumerate(channels)]
    )
    session_set(chat_id, user_id, "channel", data)
    await safe_send(
        chat_id,
        f"📢 В какой канал опубликовать?\nОсталось объявлений: {limits['left']}/{limits['max']}",
        kb,
    )

# ---------- Обработка сообщений ----------

async def handle_message(msg):
    chat_id = msg.get("recipient", {}).get("chat_id")
    if not chat_id or chat_id < 0:
        return
    user_id = msg.get("sender", {}).get("user_id") or chat_id
    text = msg.get("body", {}).get("text", "").strip()

    if not rate_ok(user_id):
        return
    if text.startswith("/start"):
        await start_flow(chat_id, user_id)
        return
    if text == "/menu":
        await show_main_menu(chat_id, user_id)
        return
    if text == "/cancel":
        db.clear_session(chat_id)
        await safe_send(chat_id, "Отменено.")
        return
    if text == "/help":
        await safe_send(chat_id, "Команды:\n/start — главное меню\n/my — мои объявления\n/cancel — отменить\n/help — справка")
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
        await show_main_menu(chat_id, user_id)
        return

    if state == "captcha":
        await handle_captcha(chat_id, user_id, data, text)
    elif state == "text":
        await handle_text(chat_id, user_id, data, text)
    elif state == "photos":
        await handle_photos(chat_id, user_id, data, msg.get("body", {}), text)
    elif state == "name":
        await handle_name(chat_id, user_id, data, text)
    elif state == "phone":
        await handle_phone(chat_id, user_id, data, text)
    elif state == "preview":
        await safe_send(chat_id, "Нажмите кнопку под предпросмотром.")
    elif state == "channel":
        await safe_send(chat_id, "Выберите канал кнопкой выше.")

# ---------- Callback ----------

async def handle_callback(callback, msg):
    payload = callback.get("payload", "")
    chat_id = msg.get("recipient", {}).get("chat_id")
    user_id = callback.get("user", {}).get("user_id") or chat_id

    # Меню
    if payload == "menu_new_ad":
        await start_ad_creation(chat_id, user_id)
        return
    if payload == "menu_my_ads":
        await cmd_my_ads(chat_id, user_id)
        return
    if payload == "menu_faq":
        await show_faq_menu(chat_id)
        return
    if payload == "menu_back":
        await show_main_menu(chat_id, user_id)
        return
    if payload == "menu_toggle_voice":
        new_state = not voice_enabled(user_id)
        set_voice_enabled(user_id, new_state)
        if new_state:
            await safe_send(chat_id, "🔊 Озвучивание включено. Отправляю тестовое сообщение...")
            ok = await send_voice_reply(chat_id, user_id, "Озвучивание включено.")
            if not ok:
                # Функция send_voice_reply сама отправит текст ошибки, если она есть
                pass
        else:
            await safe_send(chat_id, "🔇 Озвучивание выключено.")
        await show_main_menu(chat_id, user_id)
        return

    # FAQ
    if payload.startswith("faq_"):
        await show_faq_answer(chat_id, payload)
        return

    # Остальные сценарии
    if payload in ("confirm_publish", "edit_text", "cancel_ad"):
        state, data = session_get(chat_id)
        if not data:
            return
        if payload == "cancel_ad":
            db.clear_session(chat_id)
            await safe_send(chat_id, "Отменено.")
            await show_main_menu(chat_id, user_id)
        elif payload == "edit_text":
            data["from_edit"] = True
            data["ad_text"] = ""
            session_set(chat_id, user_id, "text", data)
            await safe_send(
                chat_id, f"Введите новый текст (до {config.MAX_TEXT_LEN} символов):"
            )
        else:
            await ask_channel(chat_id, user_id, data)

    elif payload.startswith("channel_"):
        state, data = session_get(chat_id)
        if not data:
            return
        idx = int(payload.split("_")[1])
        channels = config.load_channels()
        if idx < len(channels):
            db.clear_session(chat_id)
            await send_ad_to_moderation(chat_id, user_id, data, channels[idx])

    elif payload.startswith("approve_"):
        await admin_approve(chat_id, int(payload.split("_")[1]))
    elif payload.startswith("reject_"):
        parts = payload.split("_")
        await admin_reject(
            chat_id, int(parts[1]), parts[2] if len(parts) > 2 else "other"
        )
    elif payload.startswith("myads_delete_"):
        await delete_user_ad(chat_id, user_id, int(payload.split("_")[2]))
    elif payload.startswith("myads_repeat_"):
        await repeat_user_ad(chat_id, user_id, int(payload.split("_")[2]))

# ---------- Модерация ----------

async def send_ad_to_moderation(chat_id, user_id, data, target):
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
        log.error("send_ad_to_moderation: no admin_chat configured")
        db.update_ad_rejected(ad_id, "no_admin_chat")
        await safe_send(
            chat_id,
            f"⚠️ Ошибка конфигурации: канал модерации не настроен. "
            f"Объявление #{ad_id} не отправлено.",
        )
        return

    kb = make_keyboard(
        [
            ("✅ Одобрить", f"approve_{ad_id}"),
            ("🚫 Спам", f"reject_{ad_id}_spam"),
            ("🚫 Лексика", f"reject_{ad_id}_content"),
            ("🚫 Контакты", f"reject_{ad_id}_contact"),
            ("🚫 Другое", f"reject_{ad_id}_other"),
        ]
    )
    attachments = []
    for url in data.get("photos", []):
        try:
            token = await media.upload_photo_to_max(url)
            attachments.append({"type": "image", "payload": {"token": token}})
        except Exception as e:
            log.warning("send_ad_to_moderation: photo upload failed: %s", e)

    admin_text = (
        f"🆕 Заявка #{ad_id}\n"
        f"Канал: {target['name']}\n"
        f"Пользователь: {user_id}\n\n"
        f"{format_post(data)}"
    )
    await safe_send(admin_chat, admin_text, kb, attachments or None)
    await safe_send(chat_id, f"📨 Объявление #{ad_id} отправлено на модерацию.")

async def admin_approve(chat_id, ad_id):
    ad = db.get_ad(ad_id)
    if not ad or ad["status"] != "pending":
        return

    attachments = []
    for url in json.loads(ad["photos"] or "[]"):
        try:
            token = await media.upload_photo_to_max(url)
            attachments.append({"type": "image", "payload": {"token": token}})
        except Exception as e:
            log.warning("admin_approve: photo upload failed: %s", e)

    data = {"ad_text": ad["ad_text"], "name": ad["name"], "phone": ad["phone"]}
    res = await bot.send_message(
        ad["channel_id"], format_post(data), attachments=attachments or None
    )
    msg_id = _extract_mid(res)

    if msg_id is None:
        log.error(
            "admin_approve: ad %s not published (no mid in response): %s",
            ad_id, res,
        )
        db.update_ad_rejected(ad_id, "publish_failed")
        await safe_send(
            ad["chat_id"],
            f"⚠️ Объявление #{ad_id} не опубликовано: ошибка MAX API.\n"
            f"Обратитесь к администратору.",
        )
        await safe_send(
            chat_id,
            f"⚠️ #{ad_id}: публикация не удалась.\nОтвет MAX: {res}",
        )
        return

    db.update_ad_published(ad_id, msg_id, now_iso())
    db.increment_daily_limit(ad["user_id"])
    limits = db.get_daily_limits(ad["user_id"], config.DAILY_AD_LIMIT)
    await safe_send(
        ad["chat_id"],
        f"✅ Объявление #{ad_id} опубликовано в «{ad['channel_name']}».\n"
        f"Осталось: {limits['left']}/{limits['max']}.",
    )
    await safe_send(chat_id, f"✅ #{ad_id} опубликовано.")

async def admin_reject(chat_id, ad_id, reason_code):
    ad = db.get_ad(ad_id)
    if not ad or ad["status"] != "pending":
        return
    reason = {
        "spam": "спам",
        "content": "лексика",
        "contact": "контакты",
        "other": "правила",
    }.get(reason_code, "другое")
    db.update_ad_rejected(ad_id, reason_code)
    await safe_send(ad["chat_id"], f"❌ Объявление #{ad_id} отклонено.\nПричина: {reason}.")
    await safe_send(chat_id, f"🚫 #{ad_id} отклонено.")

# ---------- /my ----------

async def cmd_my_ads(chat_id, user_id):
    ads = db.get_user_ads(user_id, limit=10)
    if not ads:
        await safe_send(chat_id, "У вас пока нет объявлений.")
        return

    status_map = {"pending": "⏳", "published": "✅", "rejected": "🚫", "deleted": "🗑"}
    lines = ["Ваши последние объявления:"] + [
        f"#{ad['id']} {status_map.get(ad['status'], '?')} — {ad['channel_name'] or '—'}"
        for ad in ads
    ]

    btns = []
    for ad in ads[:5]:
        if ad["status"] == "published":
            btns.append((f"🗑 Удалить #{ad['id']}", f"myads_delete_{ad['id']}"))
        if ad["status"] in ("published", "rejected"):
            btns.append((f"🔁 Повторить #{ad['id']}", f"myads_repeat_{ad['id']}"))

    btns.append(("⬅️ В меню", "menu_back"))
    await safe_send(chat_id, "\n".join(lines), make_keyboard(btns))

async def delete_user_ad(chat_id, user_id, ad_id):
    ad = db.get_ad(ad_id)
    if not ad or ad["user_id"] != user_id or ad["status"] != "published":
        await safe_send(chat_id, f"⚠️ #{ad_id} нельзя удалить (не ваше или не опубликовано).")
        return

    if not ad["channel_msg_id"]:
        log.error("delete_user_ad: ad %s has NULL channel_msg_id", ad_id)
        db.update_ad_status(ad_id, "deleted")
        await safe_send(
            chat_id,
            f"⚠️ #{ad_id}: ID сообщения не сохранён (опубликовано старой версией бота).\n"
            f"Удалите пост в канале «{ad['channel_name']}» вручную.\n"
            f"Статус в боте обновлён на «удалено».",
        )
        return

    ok, body = await bot.delete_message(ad["channel_id"], ad["channel_msg_id"])
    db.update_ad_status(ad_id, "deleted")

    if ok:
        await safe_send(
            chat_id, f"🗑 #{ad_id} удалено из канала «{ad['channel_name']}»."
        )
        return

    err_text = str(body.get("error") or body.get("message") or body)
    status_code = body.get("_status", 0)
    is_not_found = (
        status_code == 404
        or "not found" in err_text.lower()
        or "message_not_found" in err_text.lower()
    )

    if is_not_found:
        await safe_send(
            chat_id,
            f"🗑 #{ad_id}: сообщение уже было удалено из канала. "
            f"Статус в боте обновлён.",
        )
    else:
        await safe_send(
            chat_id,
            f"⚠️ #{ad_id}: не удалось удалить из канала.\n"
            f"Причина: {err_text}\n"
            f"Статус в боте обновлён, но пост мог остаться. "
            f"Удалите его вручную или обратитесь к администратору.",
        )

async def repeat_user_ad(chat_id, user_id, ad_id):
    ad = db.get_ad(ad_id)
    if not ad or ad["user_id"] != user_id:
        return
    target = {"id": ad["channel_id"], "name": ad["channel_name"]}
    data = {
        "ad_text": ad["ad_text"],
        "name": ad["name"],
        "phone": ad["phone"],
        "photos": json.loads(ad["photos"] or "[]"),
        "has_url": False,
    }
    await safe_send(chat_id, f"⏳ Отправляю на модерацию в «{target['name']}»...")
    await send_ad_to_moderation(chat_id, user_id, data, target)

# ---------- Админ-команды ----------

async def cmd_admin(chat_id, user_id, text):
    if not is_admin(user_id):
        return
    parts = text.split()
    if len(parts) == 1:
        await safe_send(
            chat_id,
            "Админ-панель:\n/admin stats\n/admin stats channels\n/admin pending\n"
            "/admin ban <uid>\n/admin unban <uid>",
        )
        return
    sub = parts[1].lower()

    if sub == "stats" and len(parts) >= 3 and parts[2].lower() == "channels":
        stats_db = {s["channel_name"]: s for s in db.stats_by_channel()}
        channels = config.load_channels()

        lines = ["📊 Статистика по каналам:"]
        for ch in channels:
            s = stats_db.get(
                ch["name"],
                {"total": 0, "published": 0, "pending": 0, "rejected": 0},
            )
            lines.append(
                f"\n📁 {ch['name']}:\n"
                f"  Всего: {s['total']}\n"
                f"  ✅ Опубликовано: {s['published']}\n"
                f"  ⏳ На модерации: {s['pending']}\n"
                f"  🚫 Отклонено: {s['rejected']}"
            )
        await safe_send(chat_id, "\n".join(lines))
        return

    if sub == "stats":
        s = db.stats()
        lines = [
            "📊 Общая статистика:",
            f"Пользователей: {s['users']}",
            f"Объявлений: {s['ads_total']}",
            f"  ⏳ на модерации: {s['ads_pending']}",
            f"  ✅ опубликовано: {s['ads_published']}",
            f"  🚫 отклонено: {s['ads_rejected']}",
            f"Заблокировано: {s['banned']}",
            "",
            "📢 Настроенные каналы:",
        ]
        for ch in config.load_channels():
            lines.append(f"• {ch['name']} (ID: {ch['id']})")
        await safe_send(chat_id, "\n".join(lines))
        return

    if sub == "pending":
        ads = db.get_pending_ads()
        await safe_send(chat_id, f"⏳ Заявок: {len(ads)}" if ads else "Заявок нет.")
        return

    if sub == "ban" and len(parts) >= 3:
        uid = int(parts[2])
        minutes = int(parts[3]) if len(parts) > 3 else 60
        until = (
            datetime.now(timezone.utc) + timedelta(minutes=minutes)
        ).isoformat()
        db.ban_user(uid, until, "admin ban")
        await safe_send(chat_id, f"⛔ Забанен {uid}.")
        return

    if sub == "unban" and len(parts) >= 3:
        db.unban_user(int(parts[2]))
        await safe_send(chat_id, f"✅ Разбанен {parts[2]}.")
        return

# ---------- Точка входа ----------

async def main():
    db.init_db()
    await bot.start()
    
    log.info("=== БОТ ЗАПУЩЕН (pure aiohttp, TTS, FAQ) ===")
    offset = None
    while True:
        try:
            for update in await bot.get_updates(offset):
                offset = update.get("update_id", offset)
                utype = update.get("update_type")
                if utype == "message_created":
                    await handle_message(update.get("message", {}))
                elif utype == "message_callback":
                    cb = update.get("callback", {})
                    await bot.answer_callback(cb.get("callback_id"))
                    await handle_callback(cb, update.get("message", {}))
                elif utype == "bot_started":
                    msg = update.get("message", {})
                    chat_id = (
                        msg.get("recipient", {}).get("chat_id")
                        or update.get("chat_id")
                    )
                    user_id = msg.get("sender", {}).get("user_id") or chat_id
                    await start_flow(chat_id, user_id)
            await asyncio.sleep(1)
        except Exception as e:
            log.error(f"Polling error: {e}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())

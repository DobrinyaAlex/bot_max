"""MAX Bot v2 — объявления с пре-модерацией (чистый aiohttp)."""
import asyncio
import json
import logging
import random
import time
import traceback
import aiohttp
from datetime import datetime, timedelta, timezone

import config
import db
import media
import validators

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s", handlers=[logging.FileHandler(config.LOG_PATH, encoding="utf-8"), logging.StreamHandler()])
log = logging.getLogger("maxbot")

class MAXBot:
    def __init__(self, token):
        self.token = token
        self.base_url = "https://platform-api2.max.ru"
        self.headers = {"Authorization": token}
        self.session = None

    async def start(self):
        self.session = aiohttp.ClientSession(headers=self.headers, connector=aiohttp.TCPConnector(ssl=False))

    async def close(self):
        if self.session: await self.session.close()

    async def send_message(self, chat_id: int, text: str, keyboard=None, attachments=None):
        params = {"chat_id": chat_id}
        payload = {"text": text}
        final_attachments = attachments or []
        
        if keyboard:
            final_attachments.append(keyboard)
            
        if final_attachments:
            payload["attachments"] = final_attachments

        try:
            async with self.session.post(f"{self.base_url}/messages", params=params, json=payload) as r:
                if r.status >= 400: log.error(f"send_message error {r.status}: {await r.text()}")
                return await r.json()
        except Exception as e:
            log.error(f"send_message exception: {e}")
            return {}

    async def get_updates(self, offset=None):
        params = {"timeout": 30}
        if offset: params["offset"] = offset
        try:
            async with self.session.get(f"{self.base_url}/updates", params=params) as r:
                if r.status == 200: return (await r.json()).get("updates", [])
        except Exception as e:
            log.error(f"get_updates error: {e}")
        return []

    async def answer_callback(self, callback_id):
        try:
            async with self.session.post(f"{self.base_url}/answers", json={"callback_id": callback_id}) as r: pass
        except Exception: pass

    async def delete_message(self, chat_id, message_id):
        try:
            async with self.session.delete(f"{self.base_url}/messages", params={"chat_id": chat_id, "message_id": message_id}) as r:
                return r.status == 200
        except Exception: return False

bot = MAXBot(config.BOT_TOKEN)
_last_msg_ts: dict[int, float] = {}

def get_user_id(obj) -> int | None:
    if hasattr(obj, "user") and hasattr(obj.user, "user_id"): return obj.user.user_id
    if hasattr(obj, "sender") and hasattr(obj.sender, "user_id"): return obj.sender.user_id
    if hasattr(obj, "message") and hasattr(obj.message, "sender") and hasattr(obj.message.sender, "user_id"): return obj.message.sender.user_id
    return None

def now_iso() -> str: return datetime.now(timezone.utc).isoformat()

def rate_ok(user_id: int) -> bool:
    now = time.monotonic()
    if now - _last_msg_ts.get(user_id, 0.0) < config.RATE_LIMIT_SECONDS: return False
    _last_msg_ts[user_id] = now
    return True

def is_admin(user_id: int) -> bool: return user_id in config.ADMIN_USER_IDS

def moderation_target() -> int | None:
    if config.ADMIN_CHAT_ID: return config.ADMIN_CHAT_ID
    saved = db.get_setting("admin_chat_id")
    if saved:
        try: return int(saved)
        except ValueError: pass
    if config.ADMIN_USER_IDS: return config.ADMIN_USER_IDS[0]
    return None

async def safe_send(chat_id: int, text: str, keyboard=None, attachments=None) -> bool:
    return bool(await bot.send_message(chat_id, text, keyboard, attachments))

async def alert_admin(text: str) -> None:
    target = moderation_target()
    if target: await bot.send_message(target, text)

def session_get(chat_id: int) -> tuple[str | None, dict | None]:
    s = db.get_session(chat_id)
    if not s: return None, None
    try:
        updated = datetime.fromisoformat(s["updated_at"])
        if updated.tzinfo is None: updated = updated.replace(tzinfo=timezone.utc)
    except Exception:
        db.clear_session(chat_id); return None, None
    if datetime.now(timezone.utc) - updated > timedelta(minutes=config.SESSION_TIMEOUT_MIN):
        db.clear_session(chat_id); return None, None
    return s["state"], json.loads(s["data"])

def session_set(chat_id: int, user_id: int, state: str, data: dict) -> None:
    db.set_session(chat_id, int(user_id), state, json.dumps(data, ensure_ascii=False))

def format_post(data: dict) -> str:
    return f"📌 Объявление 📌\n\n{data.get('ad_text', '')}\n\n👤 Продавец: {data.get('name', '')}\n📞 Телефон: {data.get('phone', '')}"

def make_keyboard(buttons: list[tuple[str, str]]):
    return {
        "type": "inline_keyboard",
        "payload": {
            "buttons": [[{"type": "callback", "text": text, "payload": payload}] for text, payload in buttons]
        }
    }

async def start_flow(chat_id: int, user_id: int) -> None:
    if chat_id < 0: return
    db.upsert_user(user_id)
    log.info("start_flow user_id=%s chat_id=%s", user_id, chat_id)
    if is_admin(user_id): db.set_setting("admin_chat_id", str(chat_id))

    banned = db.is_banned(user_id)
    if banned:
        await safe_send(chat_id, f"⛔ Вы заблокированы до {banned['banned_until']}.")
        return

    limits = db.get_daily_limits(user_id, config.DAILY_AD_LIMIT)
    if limits["left"] <= 0:
        await safe_send(chat_id, f"❌ Лимит объявлений на сегодня исчерпан.")
        return

    a, b = random.randint(1, 9), random.randint(1, 9)
    data = {"captcha_answer": str(a + b), "captcha_attempts": 0, "photos": []}
    session_set(chat_id, user_id, "captcha", data)
    
    await safe_send(chat_id, f"Здравствуйте! Я помогу оформить объявление.\nОсталось объявлений на сегодня: {limits['left']}/{limits['max']}.\n\n💡 Команды:\n/my — ваши объявления\n/cancel — отменить действие\n/help — справка\n\nДля подтверждения, что вы человек, решите пример: {a} + {b}.\nВведите только цифру ответа. Попыток: {config.CAPTCHA_MAX_ATTEMPTS}.")

async def handle_captcha(chat_id, user_id, data, text):
    if not text: return
    if text == data.get("captcha_answer"):
        data["captcha_attempts"] = 0
        session_set(chat_id, user_id, "text", data)
        await safe_send(chat_id, f"✅ Проверка пройдена!\nВведите текст объявления (до {config.MAX_TEXT_LEN} символов):")
        return

    attempts = data.get("captcha_attempts", 0) + 1
    if attempts >= config.CAPTCHA_MAX_ATTEMPTS:
        until = (datetime.now(timezone.utc) + timedelta(minutes=config.CAPTCHA_BAN_MIN)).isoformat()
        db.ban_user(user_id, until, "3 неверных ответа капчи")
        db.clear_session(chat_id)
        await safe_send(chat_id, f"⛔ Слишком много неверных ответов. Блокировка на {config.CAPTCHA_BAN_MIN} мин.")
        return

    a, b = random.randint(1, 9), random.randint(1, 9)
    data["captcha_answer"] = str(a + b)
    data["captcha_attempts"] = attempts
    session_set(chat_id, user_id, "captcha", data)
    await safe_send(chat_id, f"❌ Неверно. Попытка {attempts}/{config.CAPTCHA_MAX_ATTEMPTS}. Новый пример: {a} + {b}.")

async def handle_text(chat_id, user_id, data, text):
    ok, msg, has_url = validators.validate_ad_text(text)
    if not ok:
        await safe_send(chat_id, f"❌ {msg}"); return
    data["ad_text"] = msg
    data["has_url"] = has_url

    if data.get("from_edit") and data.get("name") and data.get("phone"):
        data.pop("from_edit", None)
        session_set(chat_id, user_id, "preview", data)
        await show_preview(chat_id, user_id, data); return

    data.setdefault("photos", [])
    session_set(chat_id, user_id, "photos", data)
    extra = "\n⚠️ Обнаружена ссылка." if has_url else ""
    await safe_send(chat_id, f"✅ Текст принят ({len(msg)}/{config.MAX_TEXT_LEN}).{extra}\nОтправьте до {config.MAX_PHOTOS} фото или напишите «Готово».")

async def handle_photos(chat_id, user_id, data, msg_body, text):
    for att in msg_body.get("attachments", []):
        if att.get("type") == "image":
            url = att.get("payload", {}).get("url")
            if url and url not in data["photos"]: data["photos"].append(url)

    if len(data["photos"]) >= config.MAX_PHOTOS:
        session_set(chat_id, user_id, "name", data)
        await safe_send(chat_id, f"✅ Принято {config.MAX_PHOTOS} фото (максимум).\nУкажите имя продавца:"); return

    if text.lower() in ("готово", "без фото"):
        session_set(chat_id, user_id, "name", data)
        prefix = "" if data["photos"] else "Публикация без фото.\n"
        await safe_send(chat_id, f"{prefix}Укажите имя продавца:"); return

    if data["photos"]:
        session_set(chat_id, user_id, "photos", data)
        await safe_send(chat_id, f"✅ Фото {len(data['photos'])}/{config.MAX_PHOTOS}. Ещё или «Готово»."); return
    await safe_send(chat_id, "Отправьте фото или напишите «Готово» / «Без фото».")

async def handle_name(chat_id, user_id, data, text):
    ok, res = validators.validate_name(text)
    if not ok: await safe_send(chat_id, f"❌ {res}"); return
    data["name"] = res
    session_set(chat_id, user_id, "phone", data)
    await safe_send(chat_id, "✅ Имя принято.\nУкажите контактный телефон.")

async def handle_phone(chat_id, user_id, data, text):
    ok, res = validators.validate_phone(text)
    if not ok: await safe_send(chat_id, f"❌ {res}"); return
    data["phone"] = res
    session_set(chat_id, user_id, "preview", data)
    await show_preview(chat_id, user_id, data)

async def show_preview(chat_id, user_id, data):
    kb = make_keyboard([("✅ Опубликовать", "confirm_publish"), ("✏️ Изменить текст", "edit_text"), ("❌ Отмена", "cancel_ad")])
    attachments = []
    for url in data.get("photos", []):
        try:
            token = await media.upload_photo_to_max(url)
            attachments.append({"type": "image", "payload": {"token": token}})
        except Exception as e: log.warning("Ошибка загрузки фото в предпросмотре: %s", e)

    await safe_send(chat_id, f"📋 Предпросмотр:\n\n{format_post(data)}\n\nВсё верно?", kb, attachments or None)

async def ask_channel(chat_id, user_id, data):
    channels = config.load_channels()
    limits = db.get_daily_limits(user_id, config.DAILY_AD_LIMIT)
    kb = make_keyboard([(ch["name"], f"channel_{i}") for i, ch in enumerate(channels)])
    session_set(chat_id, user_id, "channel", data)
    await safe_send(chat_id, f"📢 В какой канал опубликовать?\nОсталось объявлений: {limits['left']}/{limits['max']}", kb)

async def handle_message(msg):
    chat_id = msg.get("recipient", {}).get("chat_id")
    if not chat_id or chat_id < 0: return
    user_id = msg.get("sender", {}).get("user_id") or chat_id
    text = msg.get("body", {}).get("text", "").strip()

    if not rate_ok(user_id): return
    if text.startswith("/start"): await start_flow(chat_id, user_id); return
    if text == "/cancel":
        db.clear_session(chat_id); await safe_send(chat_id, "Отменено."); return
    if text == "/help":
        await safe_send(chat_id, "Команды:\n/start\n/my\n/cancel\n/help"); return
    if text == "/my": await cmd_my_ads(chat_id, user_id); return
    if text.startswith("/admin"): await cmd_admin(chat_id, user_id, text); return

    banned = db.is_banned(user_id)
    if banned: await safe_send(chat_id, f"⛔ Вы заблокированы до {banned['banned_until']}."); return

    state, data = session_get(chat_id)
    if state is None: await safe_send(chat_id, "Нажмите /start, чтобы начать."); return

    if state == "captcha": await handle_captcha(chat_id, user_id, data, text)
    elif state == "text": await handle_text(chat_id, user_id, data, text)
    elif state == "photos": await handle_photos(chat_id, user_id, data, msg.get("body", {}), text)
    elif state == "name": await handle_name(chat_id, user_id, data, text)
    elif state == "phone": await handle_phone(chat_id, user_id, data, text)
    elif state == "preview": await safe_send(chat_id, "Нажмите кнопку под предпросмотром.")
    elif state == "channel": await safe_send(chat_id, "Выберите канал кнопкой выше.")

async def handle_callback(callback, msg):
    payload = callback.get("payload", "")
    chat_id = msg.get("recipient", {}).get("chat_id")
    user_id = callback.get("user", {}).get("user_id") or chat_id

    if payload in ("confirm_publish", "edit_text", "cancel_ad"):
        state, data = session_get(chat_id)
        if not data: return
        if payload == "cancel_ad": db.clear_session(chat_id); await safe_send(chat_id, "Отменено.")
        elif payload == "edit_text":
            data["from_edit"] = True; data["ad_text"] = ""
            session_set(chat_id, user_id, "text", data)
            await safe_send(chat_id, f"Введите новый текст (до {config.MAX_TEXT_LEN} символов):")
        else: await ask_channel(chat_id, user_id, data)

    elif payload.startswith("channel_"):
        state, data = session_get(chat_id)
        if not data: return
        idx = int(payload.split("_")[1])
        channels = config.load_channels()
        if idx < len(channels):
            db.clear_session(chat_id)
            await send_ad_to_moderation(chat_id, user_id, data, channels[idx])

    elif payload.startswith("approve_"): await admin_approve(chat_id, int(payload.split("_")[1]))
    elif payload.startswith("reject_"):
        parts = payload.split("_")
        await admin_reject(chat_id, int(parts[1]), parts[2] if len(parts) > 2 else "other")
    elif payload.startswith("myads_delete_"): await delete_user_ad(chat_id, user_id, int(payload.split("_")[2]))
    elif payload.startswith("myads_repeat_"): await repeat_user_ad(chat_id, user_id, int(payload.split("_")[2]))

async def send_ad_to_moderation(chat_id, user_id, data, target):
    ad_id = db.create_ad(user_id=user_id, chat_id=chat_id, ad_text=data["ad_text"], name=data["name"], phone=data["phone"], photos=data.get("photos", []), channel_id=target["id"], channel_name=target["name"], status="pending")
    admin_chat = moderation_target()
    if not admin_chat: return

    kb = make_keyboard([("✅ Одобрить", f"approve_{ad_id}"), ("🚫 Спам", f"reject_{ad_id}_spam"), ("🚫 Лексика", f"reject_{ad_id}_content"), ("🚫 Контакты", f"reject_{ad_id}_contact"), ("🚫 Другое", f"reject_{ad_id}_other")])
    attachments = []
    for url in data.get("photos", []):
        try:
            token = await media.upload_photo_to_max(url)
            attachments.append({"type": "image", "payload": {"token": token}})
        except Exception: pass

    admin_text = f"🆕 Заявка #{ad_id}\nКанал: {target['name']}\nПользователь: {user_id}\n\n{format_post(data)}"
    await safe_send(admin_chat, admin_text, kb, attachments or None)
    await safe_send(chat_id, f"📨 Объявление #{ad_id} отправлено на модерацию.")

async def admin_approve(chat_id, ad_id):
    ad = db.get_ad(ad_id)
    if not ad or ad["status"] != "pending": return
    attachments = []
    for url in json.loads(ad["photos"] or "[]"):
        try:
            token = await media.upload_photo_to_max(url)
            attachments.append({"type": "image", "payload": {"token": token}})
        except Exception: pass

    data = {"ad_text": ad["ad_text"], "name": ad["name"], "phone": ad["phone"]}
    res = await bot.send_message(ad["channel_id"], format_post(data), attachments=attachments or None)
    db.update_ad_published(ad_id, res.get("message_id") if isinstance(res, dict) else None, now_iso())
    db.increment_daily_limit(ad["user_id"])
    limits = db.get_daily_limits(ad["user_id"], config.DAILY_AD_LIMIT)
    await safe_send(ad["chat_id"], f"✅ Объявление #{ad_id} опубликовано в «{ad['channel_name']}».\nОсталось: {limits['left']}/{limits['max']}.")
    await safe_send(chat_id, f"✅ #{ad_id} опубликовано.")

async def admin_reject(chat_id, ad_id, reason_code):
    ad = db.get_ad(ad_id)
    if not ad or ad["status"] != "pending": return
    reason = {"spam": "спам", "content": "лексика", "contact": "контакты", "other": "правила"}.get(reason_code, "другое")
    db.update_ad_rejected(ad_id, reason_code)
    await safe_send(ad["chat_id"], f"❌ Объявление #{ad_id} отклонено.\nПричина: {reason}.")
    await safe_send(chat_id, f"🚫 #{ad_id} отклонено.")

async def cmd_my_ads(chat_id, user_id):
    ads = db.get_user_ads(user_id, limit=10)
    if not ads: await safe_send(chat_id, "У вас пока нет объявлений."); return
    status_map = {"pending": "⏳", "published": "✅", "rejected": "🚫", "deleted": "🗑"}
    lines = ["Ваши последние объявления:"] + [f"#{ad['id']} {status_map.get(ad['status'], '?')} — {ad['channel_name'] or '—'}" for ad in ads]
    btns = []
    for ad in ads[:5]:
        if ad["status"] == "published": btns.append((f"🗑 Удалить #{ad['id']}", f"myads_delete_{ad['id']}"))
        if ad["status"] in ("published", "rejected"): btns.append((f"🔁 Повторить #{ad['id']}", f"myads_repeat_{ad['id']}"))
    await safe_send(chat_id, "\n".join(lines), make_keyboard(btns) if btns else None)

async def delete_user_ad(chat_id, user_id, ad_id):
    ad = db.get_ad(ad_id)
    if not ad or ad["user_id"] != user_id or ad["status"] != "published": return
    
    # Мягкое удаление: пытаемся удалить, но в БД меняем статус в любом случае
    success = await bot.delete_message(ad["channel_id"], ad["channel_msg_id"])
    db.update_ad_status(ad_id, "deleted")
    
    if success:
        await safe_send(chat_id, f"🗑 #{ad_id} удалено из канала.")
    else:
        await safe_send(chat_id, f"🗑 #{ad_id} уже было удалено из канала (статус обновлен).")

async def repeat_user_ad(chat_id, user_id, ad_id):
    ad = db.get_ad(ad_id)
    if not ad or ad["user_id"] != user_id: return
    target = {"id": ad["channel_id"], "name": ad["channel_name"]}
    data = {"ad_text": ad["ad_text"], "name": ad["name"], "phone": ad["phone"], "photos": json.loads(ad["photos"] or "[]"), "has_url": False}
    await safe_send(chat_id, f"⏳ Отправляю на модерацию в «{target['name']}»...")
    await send_ad_to_moderation(chat_id, user_id, data, target)

async def cmd_admin(chat_id, user_id, text):
    if not is_admin(user_id): return
    parts = text.split()
    if len(parts) == 1:
        await safe_send(chat_id, "Админ-панель:\n/admin stats\n/admin stats channels\n/admin pending\n/admin ban <uid>\n/admin unban <uid>"); return
    sub = parts[1].lower()

    if sub == "stats" and len(parts) >= 3 and parts[2].lower() == "channels":
        stats_db = {s['channel_name']: s for s in db.stats_by_channel()}
        channels = config.load_channels()
        
        lines = ["📊 Статистика по каналам:"]
        for ch in channels:
            s = stats_db.get(ch['name'], {"total": 0, "published": 0, "pending": 0, "rejected": 0})
            lines.append(
                f"\n📁 {ch['name']}:\n"
                f"  Всего: {s['total']}\n"
                f"  ✅ Опубликовано: {s['published']}\n"
                f"  ⏳ На модерации: {s['pending']}\n"
                f"  🚫 Отклонено: {s['rejected']}"
            )
        await safe_send(chat_id, "\n".join(lines)); return

    if sub == "stats":
        s = db.stats()
        lines = [
            f"📊 Общая статистика:",
            f"Пользователей: {s['users']}",
            f"Объявлений: {s['ads_total']}",
            f"  ⏳ на модерации: {s['ads_pending']}",
            f"  ✅ опубликовано: {s['ads_published']}",
            f"  🚫 отклонено: {s['ads_rejected']}",
            f"Заблокировано: {s['banned']}",
            "",
            "📢 Настроенные каналы:"
        ]
        for ch in config.load_channels():
            lines.append(f"• {ch['name']} (ID: {ch['id']})")
        await safe_send(chat_id, "\n".join(lines)); return

    if sub == "pending":
        ads = db.get_pending_ads()
        await safe_send(chat_id, f"⏳ Заявок: {len(ads)}" if ads else "Заявок нет."); return
    if sub == "ban" and len(parts) >= 3:
        uid = int(parts[2]); minutes = int(parts[3]) if len(parts) > 3 else 60
        until = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
        db.ban_user(uid, until, "admin ban"); await safe_send(chat_id, f"⛔ Забанен {uid}."); return
    if sub == "unban" and len(parts) >= 3:
        db.unban_user(int(parts[2])); await safe_send(chat_id, f"✅ Разбанен {parts[2]}."); return

async def main():
    db.init_db()
    await bot.start()
    log.info("=== БОТ ЗАПУЩЕН (pure aiohttp) ===")
    offset = None
    while True:
        try:
            for update in await bot.get_updates(offset):
                offset = update.get("update_id", offset)
                utype = update.get("update_type")
                if utype == "message_created": await handle_message(update.get("message", {}))
                elif utype == "message_callback":
                    cb = update.get("callback", {})
                    await bot.answer_callback(cb.get("callback_id"))
                    await handle_callback(cb, update.get("message", {}))
                elif utype == "bot_started":
                    msg = update.get("message", {})
                    chat_id = msg.get("recipient", {}).get("chat_id") or update.get("chat_id")
                    user_id = msg.get("sender", {}).get("user_id") or chat_id
                    await start_flow(chat_id, user_id)
            await asyncio.sleep(1)
        except Exception as e:
            log.error(f"Polling error: {e}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())

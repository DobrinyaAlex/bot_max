"""Загрузка фото в MAX: кэш токена по URL, 3 ретрая, лимит размера."""
import asyncio
import logging
import aiohttp

import db
from config import BOT_TOKEN, PHOTO_MAX_BYTES

log = logging.getLogger("maxbot.media")

UPLOAD_ENDPOINT = "https://platform-api2.max.ru/uploads?type=image"
RETRIES = 3

async def upload_photo_to_max(photo_url: str) -> str:
    cached = db.get_photo_token(photo_url)
    if cached:
        return cached

    last_err: Exception | None = None
    for attempt in range(RETRIES):
        try:
            token = await _do_upload(photo_url)
            db.save_photo_token(photo_url, token)
            return token
        except Exception as e:
            last_err = e
            log.warning("upload attempt %s/%s failed: %s", attempt + 1, RETRIES, e)
            if attempt < RETRIES - 1:
                await asyncio.sleep(2 ** attempt)
    raise last_err  # type: ignore[misc]

async def _do_upload(photo_url: str) -> str:
    # 1) скачать
    async with aiohttp.ClientSession() as s:
        async with s.get(photo_url, ssl=False) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Скачивание: HTTP {resp.status}")
            data = await resp.read()
    if len(data) > PHOTO_MAX_BYTES:
        raise RuntimeError(
            f"Фото больше {PHOTO_MAX_BYTES // 1024 // 1024} МБ"
        )

    # 2) получить upload_url
    headers = {"Authorization": BOT_TOKEN}
    async with aiohttp.ClientSession() as s:
        async with s.post(UPLOAD_ENDPOINT, headers=headers, ssl=False) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Запрос URL: HTTP {resp.status}")
            payload = await resp.json()
            upload_url = payload.get("url")
            if not upload_url:
                raise RuntimeError(f"Нет upload_url в ответе: {payload}")

    # 3) залить multipart
    async with aiohttp.ClientSession() as s:
        form = aiohttp.FormData()
        form.add_field("data", data, filename="photo.jpg")
        async with s.post(upload_url, data=form, ssl=False) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Загрузка: HTTP {resp.status}")
            res = await resp.json()
            token = res.get("token")
            if not token:
                photos = res.get("photos", {})
                if isinstance(photos, dict) and photos:
                    token = next(iter(photos.values())).get("token")
            if not token:
                raise RuntimeError(f"Токен не найден: {res}")
            return token

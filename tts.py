"""Синтез речи через edge-tts и отправка голосовых в MAX."""
import asyncio
import logging
import tempfile
import os
from pathlib import Path

import edge_tts

log = logging.getLogger("maxbot.tts")

# Голос для русского языка (мужской, деловой). Можно заменить на ru-RU-SvetlanaNeural (женский).
VOICE = "ru-RU-DmitryNeural"
RATE = "+0%"
VOLUME = "+0%"

async def synthesize_to_mp3(text: str) -> str:
    """Генерирует MP3-файл с озвучкой текста.
    Возвращает путь к временному файлу.
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    tmp_path = tmp.name
    tmp.close()

    communicate = edge_tts.Communicate(text, VOICE, rate=RATE, volume=VOLUME)
    await communicate.save(tmp_path)
    return tmp_path

async def synthesize_and_cleanup(text: str) -> tuple[bytes, str]:
    """Возвращает (bytes_mp3, путь_к_файлу). После отправки файл нужно удалить."""
    path = await synthesize_to_mp3(text)
    with open(path, "rb") as f:
        data = f.read()
    return data, path

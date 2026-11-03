"""Синтез речи через edge-tts (с резервом на gTTS) и отправка голосовых в MAX."""
import asyncio
import logging
import tempfile
from pathlib import Path

import edge_tts
from gtts import gTTS

log = logging.getLogger("maxbot.tts")

VOICE = "ru-RU-DmitryNeural"
RATE = "+0%"
VOLUME = "+0%"

async def synthesize_to_mp3(text: str) -> str:
    """Генерирует MP3-файл с озвучкой текста.
    Пытается использовать edge-tts, при ошибке переключается на gTTS.
    Возвращает путь к временному файлу.
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    tmp_path = tmp.name
    tmp.close()

    # Попытка 1: edge-tts (качественный голос)
    try:
        communicate = edge_tts.Communicate(text, VOICE, rate=RATE, volume=VOLUME)
        await communicate.save(tmp_path)
        return tmp_path
    except Exception as e:
        log.warning(f"edge-tts failed: {e}. Falling back to gTTS...")

    # Попытка 2: gTTS (Google Translate, часто работает там, где блокирован edge-tts)
    try:
        # gTTS синхронный, поэтому запускаем в отдельном потоке
        def _run_gtts():
            tts = gTTS(text=text, lang='ru', slow=False)
            tts.save(tmp_path)
        await asyncio.to_thread(_run_gtts)
        return tmp_path
    except Exception as e:
        log.error(f"gTTS also failed: {e}")
        raise RuntimeError(f"Не удалось озвучить текст. Ошибка edge-tts и gTTS. Детали: {e}")

async def synthesize_and_cleanup(text: str) -> tuple[bytes, str]:
    """Возвращает (bytes_mp3, путь_к_файлу). После отправки файл нужно удалить."""
    path = await synthesize_to_mp3(text)
    with open(path, "rb") as f:
        data = f.read()
    return data, path

"""Валидация пользовательского ввода: телефон, имя, текст объявления."""
import re

from config import MAX_TEXT_LEN, STOPWORDS

PHONE_RE = re.compile(r"^\+?[\d\s\-\(\)]{7,20}$")
URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)
NAME_RE = re.compile(r"^[A-Za-zА-Яа-яЁё\s\-]{2,50}$")

def validate_phone(text: str) -> tuple[bool, str]:
    text = text.strip()
    if not PHONE_RE.match(text):
        return False, "Телефон должен содержать 7–20 символов: цифры, +, пробелы, дефисы, скобки."
    digits = re.sub(r"\D", "", text)
    if len(digits) < 10 or len(digits) > 15:
        return False, "Телефон должен содержать 10–15 цифр."
    return True, text

def validate_name(text: str) -> tuple[bool, str]:
    text = text.strip()
    if not NAME_RE.match(text):
        return False, "Имя: 2–50 символов, только буквы, пробел и дефис."
    return True, text

def validate_ad_text(text: str) -> tuple[bool, str, bool]:
    """Возвращает (ok, сообщение_или_текст, has_url)."""
    text = text.strip()
    if not text:
        return False, "Текст не может быть пустым.", False
    if len(text) > MAX_TEXT_LEN:
        extra = len(text) - MAX_TEXT_LEN
        return False, f"Текст слишком длинный ({len(text)}/{MAX_TEXT_LEN}). Сократите на {extra} симв.", False

    low = text.lower()
    for w in STOPWORDS:
        if w in low:
            return False, "В тексте нецензурная лексика. Уберите и попробуйте снова.", False

    has_url = bool(URL_RE.search(text))
    return True, text, has_url

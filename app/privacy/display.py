"""Ekranda maskalama: koordinator kartında FİN və telefon tam göstərilmir (spec 7-ci bölmə)."""
from app.privacy.crypto import normalize_fin, normalize_phone


def mask_fin(fin: str) -> str:
    value = normalize_fin(fin)
    return f"{value[:3]}***{value[-1:]}" if len(value) >= 4 else "***"


def mask_phone(phone: str) -> str:
    value = normalize_phone(phone)
    return f"{value[:6]}***{value[-4:]}" if len(value) >= 10 else "***"

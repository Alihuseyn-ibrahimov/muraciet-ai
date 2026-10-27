"""Sahə səviyyəsində şifrələmə (spec 9-cu bölmə): FİN, telefon və ünvan bazada açıq mətn kimi saxlanılmır.

- Şifrələmə: Fernet (AES + HMAC). MultiFernet ilə açar rotasiyası mümkündür: birinci açar əsasdır,
  qalanları yalnız köhnə dəyərləri oxumaq üçündür.
- Axtarış: şifrəli mətndə axtarmaq olmur, ona görə ayrıca açarla HMAC-SHA256 "kor indeks" saxlanılır.
  Eyni FİN həmişə eyni indeksi verir, amma açarsız indeksdən FİN-i bərpa etmək mümkün deyil.
"""
import hashlib
import hmac
import re
from functools import lru_cache
from typing import Literal

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.config import ConfigError, settings

IndexKind = Literal["fin", "phone"]


class CryptoError(Exception):
    """Şifrəli dəyər açılmadı: açar uyğun deyil və ya məlumat zədələnib."""


def normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("994") and len(digits) == 12:
        return "+" + digits
    if digits.startswith("0") and len(digits) == 10:
        return "+994" + digits[1:]
    if len(digits) == 9:
        return "+994" + digits
    return "+" + digits


def normalize_fin(raw: str) -> str:
    return raw.strip().upper()


_NORMALIZERS = {"fin": normalize_fin, "phone": normalize_phone}


class Crypto:
    def __init__(self, keys: list[str], hmac_key: str):
        if not keys:
            raise ConfigError("DATA_ENCRYPTION_KEYS təyin olunmayıb")
        if not hmac_key:
            raise ConfigError("HMAC_KEY təyin olunmayıb")
        try:
            self._fernet = MultiFernet([Fernet(key.encode()) for key in keys])
        except (ValueError, TypeError) as e:
            raise ConfigError(f"DATA_ENCRYPTION_KEYS-də yanlış Fernet açarı: {e}") from None
        self._hmac_key = hmac_key.encode()

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken:
            raise CryptoError("şifrəli dəyər açılmadı: açar uyğun deyil və ya məlumat zədələnib") from None

    def encrypt_bytes(self, data: bytes) -> bytes:
        """Fayllar üçün (əlavələr)."""
        return self._fernet.encrypt(data)

    def decrypt_bytes(self, token: bytes) -> bytes:
        try:
            return self._fernet.decrypt(token)
        except InvalidToken:
            raise CryptoError("şifrəli fayl açılmadı: açar uyğun deyil və ya məlumat zədələnib") from None

    def reencrypt(self, token: str) -> str:
        """Köhnə açarla şifrələnmiş dəyəri əsas (yeni) açarla yenidən şifrələyir."""
        try:
            return self._fernet.rotate(token.encode()).decode()
        except InvalidToken:
            raise CryptoError("şifrəli dəyər açılmadı: açar uyğun deyil və ya məlumat zədələnib") from None

    def blind_index(self, kind: IndexKind, value: str) -> str:
        normalized = _NORMALIZERS[kind](value)
        return hmac.new(self._hmac_key, f"{kind}:{normalized}".encode(), hashlib.sha256).hexdigest()


@lru_cache
def get_crypto() -> Crypto:
    settings.require("data_encryption_keys", "hmac_key")
    return Crypto(settings.encryption_keys, settings.hmac_key)

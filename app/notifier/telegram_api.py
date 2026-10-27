"""Telegram Bot API klienti (göndərmə tərəfi).

Update-ləri qəbul edən poller python-telegram-bot ilə işləyir (Faza 6). Göndərmə isə worker-də və
API-dədir, ona görə burada yüngül sinxron httpx klienti kifayətdir.

Təhlükəsizlik: sorğu URL-ində bot tokeni var. Ona görə httpx-in sorğu logları söndürülür, xəta
mesajlarına isə URL düşmür. Əvvəllər token bot loglarında görünmüşdü.
"""
import json
import logging
from typing import Any

import httpx

for _logger in ("httpx", "httpcore"):
    logging.getLogger(_logger).setLevel(logging.WARNING)


class TelegramError(Exception):
    pass


class RetryAfter(TelegramError):
    """429: Telegram göstərilən saniyə qədər gözləməyi tələb edir."""

    def __init__(self, seconds: int, description: str = ""):
        super().__init__(f"rate limit: {seconds} saniyə sonra yenidən cəhd ({description})")
        self.seconds = seconds


class Forbidden(TelegramError):
    """403: istifadəçi botu bloklayıb və ya botla söhbəti başlamayıb. Təkrar cəhdin mənası yoxdur."""


class BadRequest(TelegramError):
    """400: daimi xəta (məs. çat tapılmadı). Təkrar cəhdin mənası yoxdur."""


class TelegramUnavailable(TelegramError):
    """Şəbəkə və ya 5xx xətası: təkrar cəhd etmək lazımdır."""


class TelegramClient:
    def __init__(self, token: str, *, http: httpx.Client | None = None,
                 base_url: str = "https://api.telegram.org", timeout: float = 15):
        if not token:
            raise ValueError("Telegram bot tokeni boşdur")
        self._base = f"{base_url}/bot{token}"
        self._files = f"{base_url}/file/bot{token}"
        self._http = http or httpx.Client(timeout=timeout)

    def _call(self, method: str, payload: dict, files: dict | None = None) -> Any:
        try:
            if files:  # multipart: sahələr form-data kimi
                data = {k: v if isinstance(v, str) else json.dumps(v) for k, v in payload.items()}
                response = self._http.post(f"{self._base}/{method}", data=data, files=files)
            else:
                response = self._http.post(f"{self._base}/{method}", json=payload)
        except httpx.HTTPError as e:
            # Mesaja URL-i (tokeni) daşıya biləcək orijinal xəta mətni yox, yalnız növü yazılır
            raise TelegramUnavailable(f"{method}: şəbəkə xətası ({type(e).__name__})") from None
        try:
            data = response.json()
        except ValueError:
            data = {}
        if data.get("ok"):
            return data.get("result")

        code = int(data.get("error_code", response.status_code))
        description = str(data.get("description", ""))[:300]
        if code == 429:
            raise RetryAfter(int(data.get("parameters", {}).get("retry_after", 5)), description)
        if code == 403:
            raise Forbidden(description)
        if code == 400:
            if "message is not modified" in description:  # eyni mətnlə redaktə: nəticə artıq istənilən kimidir
                return None
            raise BadRequest(description)
        if code >= 500:
            raise TelegramUnavailable(f"{method}: {code} {description}")
        raise TelegramError(f"{method}: {code} {description}")

    def send_message(self, chat_id: int, text: str, *, reply_markup: dict | None = None,
                     reply_to_message_id: int | None = None) -> int:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        if reply_to_message_id is not None:
            payload["reply_parameters"] = {"message_id": reply_to_message_id, "allow_sending_without_reply": True}
        return self._call("sendMessage", payload)["message_id"]

    def edit_message_text(self, chat_id: int, message_id: int, text: str, *,
                          reply_markup: dict | None = None) -> None:
        payload: dict[str, Any] = {"chat_id": chat_id, "message_id": message_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        self._call("editMessageText", payload)

    def answer_callback_query(self, callback_query_id: str, text: str | None = None,
                              show_alert: bool = False) -> None:
        payload: dict[str, Any] = {"callback_query_id": callback_query_id, "show_alert": show_alert}
        if text:
            payload["text"] = text[:200]
        self._call("answerCallbackQuery", payload)

    def download_file(self, file_id: str) -> bytes:
        """Vətəndaşın göndərdiyi faylı yükləyir (getFile + fayl URL-i; Bot API limiti 20 MB)."""
        path = self._call("getFile", {"file_id": file_id})["file_path"]
        try:
            response = self._http.get(f"{self._files}/{path}")
        except httpx.HTTPError as e:
            raise TelegramUnavailable(f"getFile: şəbəkə xətası ({type(e).__name__})") from None
        if response.status_code >= 500:
            raise TelegramUnavailable(f"fayl: {response.status_code}")
        if response.status_code != 200:
            raise BadRequest(f"fayl yüklənmədi: {response.status_code}")
        return response.content

    def send_document(self, chat_id: int, filename: str, content: bytes, *, caption: str | None = None) -> int:
        payload: dict[str, Any] = {"chat_id": str(chat_id)}
        if caption:
            payload["caption"] = caption[:1024]
        return self._call("sendDocument", payload, files={"document": (filename, content)})["message_id"]

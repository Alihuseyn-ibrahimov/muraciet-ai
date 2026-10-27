"""Ümumi bot poller (plan 6.1): python -m bot.runner citizen|staff

Telegram-dan long polling ilə update alır və hər birini olduğu kimi API-yə (POST /bot/{kind}/update,
X-Service-Token ilə) ötürür. Botun bütün məntiqi API-dədir, runner mesaj göndərmir.

- Update-lər ardıcıl ötürülür. API qəbul etməyənə qədər (503, 5xx, şəbəkə, 401) artan intervalla
  təkrar cəhd olunur və offset irəliləmir, yəni update itmir. API-də təkrar emaldan qoruma var.
- API update-i yanlış sayırsa (digər 4xx), o atlanır ki, bot ilişib qalmasın.
- Polling tokeni URL-də daşıdığı üçün httpx logları söndürülür, xətalardan yalnız növü yazılır.
"""
import logging
import signal
import sys
import threading
import time
from collections.abc import Callable

import httpx

log = logging.getLogger("bot.runner")
POLL_TIMEOUT = 30
MAX_BACKOFF = 60
ALLOWED_UPDATES = '["message","callback_query"]'
RETRY_STATUSES = {401, 403, 408, 429}


class Runner:
    def __init__(self, telegram: httpx.Client, api: httpx.Client, *, kind: str,
                 sleep: Callable[[float], None] = time.sleep):
        self.telegram, self.api, self.kind, self._sleep = telegram, api, kind, sleep
        self._backoff = 1

    def _wait(self) -> None:
        self._sleep(self._backoff)
        self._backoff = min(self._backoff * 2, MAX_BACKOFF)

    def poll_once(self, offset: int | None) -> int | None:
        """Bir getUpdates dövrəsi. Qaytarır: növbəti offset (xəta olsa, köhnəsi)."""
        params = {"timeout": POLL_TIMEOUT, "allowed_updates": ALLOWED_UPDATES}
        if offset is not None:
            params["offset"] = offset
        try:
            response = self.telegram.get("/getUpdates", params=params, timeout=POLL_TIMEOUT + 10)
            response.raise_for_status()
            updates = response.json()["result"]
        except (httpx.HTTPError, ValueError, KeyError) as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            log.warning("getUpdates alınmadı (%s%s)", type(e).__name__, f", HTTP {status}" if status else "")
            self._wait()
            return offset
        self._backoff = 1
        for update in updates:
            self.deliver(update)
            offset = update["update_id"] + 1
        return offset

    def deliver(self, update: dict) -> None:
        backoff = 1
        while True:
            try:
                response = self.api.post(f"/bot/{self.kind}/update", json=update, timeout=120)
                status = response.status_code
            except httpx.HTTPError as e:
                status, reason = None, type(e).__name__
            else:
                reason = f"HTTP {status}"
                if status < 300:
                    return
                if 400 <= status < 500 and status not in RETRY_STATUSES:
                    log.error("API update %s-i qəbul etmədi (%s), atlanır", update.get("update_id"), reason)
                    return
            log.warning("update %s API-yə çatmadı (%s), %s s sonra təkrar", update.get("update_id"), reason, backoff)
            self._sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF)


def main(argv: list[str] | None = None) -> None:
    from app.config import settings

    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1 or args[0] not in ("citizen", "staff"):
        raise SystemExit("İstifadə: python -m bot.runner citizen|staff")
    kind = args[0]
    token_name = f"{kind}_bot_token"
    settings.require(token_name, "service_token")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpcore"):  # sorğu URL-ində bot tokeni var
        logging.getLogger(noisy).setLevel(logging.WARNING)

    telegram = httpx.Client(base_url=f"https://api.telegram.org/bot{getattr(settings, token_name)}")
    api = httpx.Client(base_url=settings.api_base_url, headers={"X-Service-Token": settings.service_token})
    runner = Runner(telegram, api, kind=kind)
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    log.info("%s botu üçün runner işə düşdü", kind)
    offset = None
    while not stop.is_set():
        offset = runner.poll_once(offset)


if __name__ == "__main__":
    main()

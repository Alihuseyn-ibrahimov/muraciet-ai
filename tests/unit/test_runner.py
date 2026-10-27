"""Bot runner: Telegram-dan update alır, ardıcıl API-yə ötürür, update itirmir, tokeni loga yazmır."""
import logging

import httpx
import pytest

from bot.runner import Runner

pytestmark = pytest.mark.unit
TOKEN = "123456:SECRET-bot-token"


class Fake:
    def __init__(self, updates: list[list[dict]], api_statuses: list[int | Exception]):
        self.updates, self.api_statuses = list(updates), list(api_statuses)
        self.offsets, self.delivered, self.sleeps = [], [], []

    def telegram(self, request: httpx.Request) -> httpx.Response:
        self.offsets.append(request.url.params.get("offset"))
        batch = self.updates.pop(0) if self.updates else []
        if isinstance(batch, Exception):
            raise batch
        return httpx.Response(200, json={"ok": True, "result": batch})

    def api(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Service-Token"] == "svc"
        status = self.api_statuses.pop(0) if self.api_statuses else 200
        if isinstance(status, Exception):
            raise status
        if status == 200:
            self.delivered.append(request.read())
        return httpx.Response(status, json={})

    def runner(self) -> Runner:
        telegram = httpx.Client(transport=httpx.MockTransport(self.telegram),
                                base_url=f"https://api.telegram.org/bot{TOKEN}")
        api = httpx.Client(transport=httpx.MockTransport(self.api), base_url="http://api",
                           headers={"X-Service-Token": "svc"})
        return Runner(telegram, api, kind="citizen", sleep=self.sleeps.append)


def update(n: int) -> dict:
    return {"update_id": n, "message": {"text": f"m{n}"}}


def test_updates_are_delivered_in_order_and_offset_advances():
    fake = Fake([[update(10), update(11)], []], [])
    runner = fake.runner()
    offset = runner.poll_once(None)
    assert offset == 12 and [b'"update_id":10' in d for d in fake.delivered] == [True, False]
    assert runner.poll_once(offset) == 12 and fake.offsets[-1] == "12"


def test_delivery_is_retried_until_the_api_accepts_it():
    fake = Fake([[update(5)]], [503, httpx.ConnectError("api yoxdur"), 500, 200])
    assert fake.runner().poll_once(None) == 6
    assert len(fake.delivered) == 1 and fake.sleeps == [1, 2, 4]


def test_wrong_service_token_is_retried_not_dropped():
    fake = Fake([[update(5)]], [401, 200])
    fake.runner().poll_once(None)
    assert len(fake.delivered) == 1


def test_an_update_the_api_rejects_as_invalid_is_skipped():
    fake = Fake([[update(5), update(6)]], [422])
    assert fake.runner().poll_once(None) == 7 and len(fake.delivered) == 1


def test_telegram_outage_keeps_the_offset_and_never_logs_the_token(caplog):
    fake = Fake([httpx.ConnectError(f"https://api.telegram.org/bot{TOKEN}/getUpdates")], [])
    with caplog.at_level(logging.DEBUG):
        assert fake.runner().poll_once(41) == 41
    assert fake.sleeps and TOKEN not in caplog.text and "SECRET" not in caplog.text

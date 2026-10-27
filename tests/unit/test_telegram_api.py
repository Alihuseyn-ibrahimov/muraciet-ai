import json
import logging

import httpx
import pytest

from app.notifier.telegram_api import BadRequest, Forbidden, RetryAfter, TelegramClient, TelegramUnavailable

pytestmark = pytest.mark.unit

TOKEN = "123456:SECRET-token-value"


def client_with(handler) -> TelegramClient:
    return TelegramClient(TOKEN, http=httpx.Client(transport=httpx.MockTransport(handler)))


def reply(status: int, body: dict):
    return lambda request: httpx.Response(status, json=body)


def test_send_message_returns_message_id_and_sends_payload():
    seen = {}

    def handler(request: httpx.Request):
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})

    keyboard = {"inline_keyboard": [[{"text": "OK", "callback_data": "ap:1:2"}]]}
    assert client_with(handler).send_message(777, "Salam", reply_markup=keyboard) == 42
    assert seen["path"].endswith("/sendMessage")
    assert seen["body"] == {"chat_id": 777, "text": "Salam", "reply_markup": keyboard}


def test_rate_limit_raises_retry_after_with_seconds():
    body = {"ok": False, "error_code": 429, "description": "Too Many Requests", "parameters": {"retry_after": 7}}
    with pytest.raises(RetryAfter) as exc:
        client_with(reply(429, body)).send_message(1, "x")
    assert exc.value.seconds == 7


def test_blocked_bot_raises_forbidden():
    body = {"ok": False, "error_code": 403, "description": "Forbidden: bot was blocked by the user"}
    with pytest.raises(Forbidden):
        client_with(reply(403, body)).send_message(1, "x")


def test_message_not_modified_is_treated_as_success():
    body = {"ok": False, "error_code": 400, "description": "Bad Request: message is not modified"}
    client_with(reply(400, body)).edit_message_text(1, 5, "eyni mətn")  # xəta yoxdur


def test_other_bad_request():
    body = {"ok": False, "error_code": 400, "description": "Bad Request: chat not found"}
    with pytest.raises(BadRequest, match="chat not found"):
        client_with(reply(400, body)).send_message(1, "x")


def test_server_error_and_network_error_are_retryable():
    with pytest.raises(TelegramUnavailable):
        client_with(reply(502, {"ok": False, "error_code": 502, "description": "Bad Gateway"})).send_message(1, "x")

    def broken(request):
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(TelegramUnavailable):
        client_with(broken).send_message(1, "x")


def test_token_never_leaks_into_errors_or_logs(caplog):
    def broken(request):
        raise httpx.ConnectError(f"cannot connect to {request.url}", request=request)

    responses = [
        reply(429, {"ok": False, "error_code": 429, "description": "slow", "parameters": {"retry_after": 1}}),
        reply(403, {"ok": False, "error_code": 403, "description": "blocked"}),
        reply(400, {"ok": False, "error_code": 400, "description": "bad"}),
        reply(500, {"ok": False, "error_code": 500, "description": "oops"}),
        broken,
    ]
    with caplog.at_level(logging.DEBUG):
        for handler in responses:
            with pytest.raises(Exception) as exc:
                client_with(handler).send_message(1, "x")
            assert TOKEN not in str(exc.value) and TOKEN not in repr(exc.value)
    assert TOKEN not in caplog.text


def test_answer_callback_query():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": True})

    client_with(handler).answer_callback_query("cb1", "Köhnəlib")
    assert seen["body"] == {"callback_query_id": "cb1", "text": "Köhnəlib", "show_alert": False}


# ---------- fayllar (əlavələr) ----------
def test_download_file_gets_path_then_bytes():
    seen = []

    def handler(request: httpx.Request):
        seen.append(request.url.path)
        if request.url.path.endswith("/getFile"):
            assert json.loads(request.content) == {"file_id": "F1"}
            return httpx.Response(200, json={"ok": True, "result": {"file_path": "photos/file_1.jpg",
                                                                     "file_size": 4}})
        return httpx.Response(200, content=b"\xff\xd8ab")

    assert client_with(handler).download_file("F1") == b"\xff\xd8ab"
    assert seen[1] == f"/file/bot{TOKEN}/photos/file_1.jpg"


def test_download_failure_does_not_leak_the_token():
    def handler(request: httpx.Request):
        if request.url.path.endswith("/getFile"):
            return httpx.Response(200, json={"ok": True, "result": {"file_path": "x.pdf"}})
        return httpx.Response(404)

    with pytest.raises(BadRequest) as exc:
        client_with(handler).download_file("F1")
    assert TOKEN not in str(exc.value)


def test_send_document_uploads_multipart():
    seen = {}

    def handler(request: httpx.Request):
        seen["path"], seen["body"] = request.url.path, request.content
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 9}})

    assert client_with(handler).send_document(777, "arayış.pdf", b"%PDF-1.4", caption="MR-2026-000001") == 9
    assert seen["path"].endswith("/sendDocument")
    assert b"%PDF-1.4" in seen["body"] and b"MR-2026-000001" in seen["body"] and b'name="chat_id"' in seen["body"]

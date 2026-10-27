"""Production-a çıxmazdan əvvəl yoxlama: sirlər dolu və düzgün formatdadır; nəticədə dəyərlər görünmür."""
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from app.preflight import check

pytestmark = pytest.mark.unit

FERNET = Fernet.generate_key().decode()
GOOD = dict(
    org_name="Əmək və Əhalinin Sosial Müdafiəsi Nazirliyi", hotline_text="Qaynar xətt: 142",
    anthropic_api_key="sk-ant-" + "a" * 40, data_encryption_keys=FERNET, hmac_key="h" * 40,
    app_database_url="postgresql+psycopg://muraciet_app:p@postgres:5432/muraciet_v2",
    migration_database_url="postgresql+psycopg://muraciet:p@postgres:5432/muraciet_v2",
    app_db_password="a" * 24, n8n_db_password="n" * 24,
    citizen_bot_token="123456:" + "C" * 35, staff_bot_token="654321:" + "S" * 35,
    citizen_bot_username="muraciet_bot", staff_bot_username="muraciet_staff_bot",
    service_token="t" * 32, admin_api_key="k" * 32, retention_years=5, langsmith_tracing=False,
)


def settings(**overrides):
    values = GOOD | overrides
    return SimpleNamespace(**values, encryption_keys=[k for k in values["data_encryption_keys"].split(",") if k])


def errors(**overrides) -> list[str]:
    return [p.message for p in check(settings(**overrides)) if p.level == "error"]


def test_complete_production_settings_pass():
    assert check(settings()) == []


@pytest.mark.parametrize("overrides, fragment", [
    ({"anthropic_api_key": ""}, "anthropic_api_key"),
    ({"data_encryption_keys": "qisa-acar"}, "data_encryption_keys"),
    ({"hmac_key": "qisa"}, "hmac_key"),
    ({"citizen_bot_token": "token"}, "citizen_bot_token"),
    ({"staff_bot_token": GOOD["citizen_bot_token"]}, "eyni"),  # iki bot bir tokenlə işləyə bilməz
    ({"service_token": "abc"}, "service_token"),
    ({"admin_api_key": GOOD["service_token"]}, "eyni"),
    ({"app_database_url": GOOD["migration_database_url"]}, "app_database_url"),  # tətbiq sahib rolu ilə yox
    ({"staff_bot_username": ""}, "staff_bot_username"),
])
def test_problems_are_reported_by_name(overrides, fragment):
    assert any(fragment in message for message in errors(**overrides))


def test_messages_never_contain_secret_values():
    bad = check(settings(anthropic_api_key="sk-ant-gizli", service_token="gizli-token", hmac_key="gizli"))
    text = " ".join(p.message for p in bad)
    assert bad and "gizli" not in text


def test_warnings_do_not_block():
    problems = check(settings(retention_years=None, langsmith_tracing=True, hotline_text=""))
    assert {p.level for p in problems} == {"warning"} and len(problems) == 3

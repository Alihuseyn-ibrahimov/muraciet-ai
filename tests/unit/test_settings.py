import pytest

from app.config import ConfigError, Settings

pytestmark = pytest.mark.unit

ENV_NAMES = [
    "CITIZEN_BOT_TOKEN", "TELEGRAM_BOT_TOKEN", "SERVICE_TOKEN", "HMAC_KEY",
    "DATA_ENCRYPTION_KEYS", "DATABASE_URL", "MIGRATION_DATABASE_URL", "RETENTION_YEARS",
    "APP_DATABASE_URL", "APP_DB_ROLE", "N8N_DB_ROLE",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def make(**values) -> Settings:
    return Settings(_env_file=None, **values)


def test_old_telegram_token_name_is_still_accepted(monkeypatch):
    # Mövcud .env-lərdə vətəndaş botunun tokeni TELEGRAM_BOT_TOKEN adı ilə durur
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    assert Settings(_env_file=None).citizen_bot_token == "123:abc"


def test_citizen_token_from_new_env_name(monkeypatch):
    monkeypatch.setenv("CITIZEN_BOT_TOKEN", "1:x")
    assert Settings(_env_file=None).citizen_bot_token == "1:x"


def test_require_names_every_missing_env_variable():
    with pytest.raises(ConfigError) as exc:
        make().require("service_token", "hmac_key")
    message = str(exc.value)
    assert "SERVICE_TOKEN" in message and "HMAC_KEY" in message and ".env" in message


def test_require_passes_when_values_are_set():
    make(service_token="t", hmac_key="k").require("service_token", "hmac_key")


def test_encryption_keys_are_split_and_trimmed():
    assert make(data_encryption_keys=" key1 , key2 ,").encryption_keys == ["key1", "key2"]


def test_migration_url_has_no_fallback():
    # Köhnə DATABASE_URL köhnə bazadır: migrasiya səhvən ora getməməlidir
    s = make(database_url="postgresql+psycopg://a:b@h:1/kohne")
    assert s.migration_database_url == ""
    with pytest.raises(ConfigError, match="MIGRATION_DATABASE_URL"):
        s.require("migration_database_url")


def test_new_system_database_settings_defaults():
    s = make()
    assert s.app_database_url == ""
    assert (s.app_db_role, s.n8n_db_role) == ("muraciet_app", "muraciet_n8n")


def test_retention_is_disabled_by_default():
    assert make().retention_years is None


def test_secrets_are_read_from_docker_secret_files(tmp_path):
    # docker-compose.prod.yml sirləri /run/secrets/<ad> fayllarına qoyur
    (tmp_path / "anthropic_api_key").write_text("sk-test-secret\n")
    (tmp_path / "staff_bot_token").write_text("1:staff")
    s = Settings(_env_file=None, _secrets_dir=tmp_path)
    assert (s.anthropic_api_key, s.staff_bot_token) == ("sk-test-secret", "1:staff")


def test_default_secrets_dir_is_used_only_when_it_exists():
    from app.config import SECRETS_DIR, Settings as S
    assert S.model_config.get("secrets_dir") in (None, SECRETS_DIR)


def test_empty_env_variable_does_not_hide_the_secret_file(tmp_path, monkeypatch):
    # prod compose bazanın URL-ini boş mühit dəyişəni ilə "silir", URL sirr faylından gəlməlidir
    (tmp_path / "app_database_url").write_text("postgresql+psycopg://app:gizli@postgres:5432/muraciet_v2")
    monkeypatch.setenv("APP_DATABASE_URL", "")
    assert Settings(_env_file=None, _secrets_dir=tmp_path).app_database_url.endswith("/muraciet_v2")

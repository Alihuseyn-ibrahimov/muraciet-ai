"""Mühit konfiqurasiyası (.env / Docker secrets).

Qayda: məcburi dəyişənlər import zamanı yoxlanmır, çünki hər komponentə (API, worker, botlar, manage)
fərqli dəyişənlər lazımdır. Hər komponent işə düşəndə özünə lazım olanları `settings.require(...)`
ilə yoxlayır və çatışmayan dəyişənin adı aydın xəta mesajında göstərilir.
"""
import os
from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


SECRETS_DIR = "/run/secrets"  # docker-compose.prod.yml: sirlər fayl kimi (/run/secrets/anthropic_api_key və s.)


class ConfigError(RuntimeError):
    """Konfiqurasiya çatışmır və ya yanlışdır."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore", env_ignore_empty=True,
                                      secrets_dir=SECRETS_DIR if Path(SECRETS_DIR).is_dir() else None)

    # Təşkilat
    org_name: str = "Əmək və Əhalinin Sosial Müdafiəsi Nazirliyi"
    config_dir: str = "config"
    hotline_text: str = ""  # boşdursa bot qaynar xətti göstərmir

    # LLM provayderlərinin açarları (hər agentin modeli config/agents.yaml-dadır)
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    openai_base_url: str | None = None  # vLLM üçün: http://gpu-server:8001/v1
    ollama_base_url: str = "http://ollama:11434"

    # Embeddings: ollama | openai | huggingface
    embedding_provider: str = "ollama"
    embedding_model: str = "bge-m3"
    # Yeni sistemin embedding ünvanı (hostda 127.0.0.1:11435, konteynerdə http://ollama:11434).
    # Boşdursa ollama_base_url istifadə olunur. Hibrid rejimdə embedding lokal olmalıdır.
    embeddings_url: str = ""
    rag_top_k: int = 4
    rag_max_distance: float = 0.65  # cosine distance; böyükdürsə sənəd "əlaqəsiz" sayılır

    # Verilənlər bazası
    # Tətbiq məhdud rolla (APP_DATABASE_URL), migrasiyalar sahib rolu ilə (MIGRATION_DATABASE_URL) qoşulur.
    # Ehtiyat keçidi bilərəkdən yoxdur: migrasiya səhvən başqa bazaya getməməlidir.
    app_database_url: str = ""
    migration_database_url: str = ""
    app_db_role: str = "muraciet_app"
    n8n_db_role: str = "muraciet_n8n"
    app_db_password: str = ""
    n8n_db_password: str = ""
    redis_url: str = "redis://redis:6379/0"
    admin_api_key: str = ""  # admin API və MCP (yalnız oxuma); boşdursa admin API bağlıdır
    service_token: str = ""  # botlar və n8n API-yə bu açarla müraciət edir

    # Şəxsi məlumatların şifrələnməsi
    data_encryption_keys: str = ""  # Fernet açarları, vergüllə; birincisi əsasdır
    hmac_key: str = ""
    retention_years: int | None = None  # təyin olunmayıbsa təmizlik işləmir

    # Telegram botları
    citizen_bot_token: str = Field(
        default="",
        validation_alias=AliasChoices("citizen_bot_token", "CITIZEN_BOT_TOKEN", "TELEGRAM_BOT_TOKEN"),
    )
    citizen_bot_username: str = ""
    staff_bot_token: str = ""
    staff_bot_username: str = ""
    api_base_url: str = "http://api:8000"

    # Təhlükəsizlik / performans
    use_nemo_guardrails: bool = False
    rate_limit_per_minute: int = 20
    faq_cache_hours: int = 24  # eyni ümumi sualın cavabı; 0 keşi söndürür

    # LangSmith: yalnız maskalanmış LLM çağırışları izlənir (app/observability.py)
    langsmith_tracing: bool = False
    langsmith_api_key: str = ""
    langsmith_project: str = "muraciet-ai"

    @property
    def encryption_keys(self) -> list[str]:
        return [k.strip() for k in self.data_encryption_keys.split(",") if k.strip()]

    def require(self, *names: str) -> None:
        missing = [name.upper() for name in names if not getattr(self, name)]
        if missing:
            raise ConfigError(
                f"Məcburi konfiqurasiya təyin olunmayıb: {', '.join(missing)}. "
                "Bu dəyişənləri .env faylına (production-da Docker secrets-ə) əlavə edin."
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

AUTO_TRACING_VARS = ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2", "LANGCHAIN_TRACING")


def disable_auto_tracing() -> None:
    """LangChain-in avtomatik izləməsi qrafın xam vəziyyətini göndərərdi: izləmə yalnız app/observability.py ilə.
    Hər şeydən əvvəl import olunan modul burada olduğu üçün LangChain dəyişəni köhnə qiymətlə oxuya bilmir."""
    for var in AUTO_TRACING_VARS:
        os.environ[var] = "false"


disable_auto_tracing()  # settings yuxarıda oxunub: LANGSMITH_TRACING-in əsl qiyməti orada qalır

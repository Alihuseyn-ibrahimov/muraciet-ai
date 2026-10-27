"""Production-a çıxmazdan əvvəl konfiqurasiya yoxlaması: `python -m app.manage preflight` (scripts/deploy.sh çağırır).

Konteynerin içində işləyir və sirləri /run/secrets-dən oxuyur. Nəticədə yalnız sahələrin adları var, dəyərlər
heç vaxt çap olunmur. Xəta (error) deploy-u dayandırır, xəbərdarlıq (warning) yox.
"""
import re
from dataclasses import dataclass

from cryptography.fernet import Fernet

_BOT_TOKEN = re.compile(r"^\d{5,}:[\w-]{30,}$")
_BOT_USERNAME = re.compile(r"^\w{5,32}$")
MIN_SECRET = 24


@dataclass(frozen=True)
class Problem:
    level: str  # error | warning
    message: str


def check(settings) -> list[Problem]:
    problems: list[Problem] = []

    def error(message: str) -> None:
        problems.append(Problem("error", message))

    def warning(message: str) -> None:
        problems.append(Problem("warning", message))

    for name in ("org_name", "anthropic_api_key", "app_database_url", "migration_database_url"):
        if not getattr(settings, name):
            error(f"{name} təyin olunmayıb")

    if not settings.encryption_keys:
        error("data_encryption_keys təyin olunmayıb")
    for number, key in enumerate(settings.encryption_keys, 1):
        try:
            Fernet(key.encode())
        except (ValueError, TypeError):
            error(f"data_encryption_keys: {number}-ci açar düzgün Fernet açarı deyil")

    for name in ("hmac_key", "service_token", "admin_api_key", "app_db_password", "n8n_db_password"):
        if len(getattr(settings, name) or "") < MIN_SECRET:
            error(f"{name} boşdur və ya {MIN_SECRET} simvoldan qısadır")
    if settings.service_token and settings.service_token == settings.admin_api_key:
        error("service_token və admin_api_key eyni olmamalıdır")

    for name in ("citizen_bot_token", "staff_bot_token"):
        if not _BOT_TOKEN.match(getattr(settings, name) or ""):
            error(f"{name} Telegram bot tokeni formatında deyil (@BotFather)")
    if settings.citizen_bot_token and settings.citizen_bot_token == settings.staff_bot_token:
        error("citizen_bot_token və staff_bot_token eyni olmamalıdır: iki ayrı bot lazımdır")
    for name in ("citizen_bot_username", "staff_bot_username"):
        if not _BOT_USERNAME.match((getattr(settings, name) or "").lstrip("@")):
            error(f"{name} təyin olunmayıb və ya düzgün deyil")

    app_user = re.search(r"//([^:@/]+)", settings.app_database_url or "")
    owner_user = re.search(r"//([^:@/]+)", settings.migration_database_url or "")
    if app_user and owner_user and app_user.group(1) == owner_user.group(1):
        error("app_database_url sahib rolu ilə qoşulur: tətbiq məhdud rolla (muraciet_app) işləməlidir")

    if settings.retention_years is None:
        warning("retention_years təyin olunmayıb: saxlama müddəti təmizliyi işləməyəcək (hüquqşünasın qərarı)")
    if settings.langsmith_tracing:
        warning("langsmith_tracing açıqdır: maskalanmış məlumat xaricə gedir, hüquqi razılıq lazımdır")
    if not settings.hotline_text:
        warning("hotline_text boşdur: bot qaynar xətti göstərməyəcək")
    return problems

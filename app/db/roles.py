"""DB rolları və icazələr (spec 8-ci və 11-ci bölmələr). Hər çağırışda eyni nəticə verir (idempotent).

- Tətbiq rolu: bütün cədvəllərdə SELECT/INSERT/UPDATE/DELETE; `appeal_events`-də yalnız SELECT/INSERT
  (audit jurnalı dəyişdirilə və silinə bilməz).
- n8n rolu: yalnız şəxsi məlumatsız `v_daily_stats` görünüşünü oxuyur.

Rollar Alembic-də deyil, burada idarə olunur: rollar bütün klaster üçündür, parollar isə mühitə görə dəyişir.
PostgreSQL ALTER ROLE ... PASSWORD əmrində parametr qəbul etmir, ona görə adlar və parollar ciddi
formatla yoxlanılır (SQL injection mümkün olmasın).
"""
import re

from sqlalchemy import Connection, text

from app.config import ConfigError

_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
_PASSWORD = re.compile(r"^[A-Za-z0-9_\-]{16,128}$")


def _ident(name: str) -> str:
    if not _IDENT.match(name):
        raise ConfigError(f"yanlış DB identifikatoru: {name!r}")
    return name


def _password(value: str, env_name: str) -> str:
    if not _PASSWORD.match(value or ""):
        raise ConfigError(f"{env_name} ən azı 16 simvol olmalı və yalnız A-Z, a-z, 0-9, _ və - ehtiva etməlidir")
    return value


def ensure_role(conn: Connection, role: str, password: str, env_name: str) -> None:
    role, password = _ident(role), _password(password, env_name)
    exists = conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}).scalar()
    if not exists:
        conn.execute(text(f"CREATE ROLE {role} LOGIN"))
    conn.execute(text(f"ALTER ROLE {role} WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD '{password}'"))


def apply_grants(conn: Connection, database: str, app_role: str, n8n_role: str) -> None:
    database, app_role, n8n_role = _ident(database), _ident(app_role), _ident(n8n_role)
    for statement in [
        f"GRANT CONNECT ON DATABASE {database} TO {app_role}, {n8n_role}",
        f"GRANT USAGE ON SCHEMA public TO {app_role}, {n8n_role}",
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {app_role}",
        f"REVOKE UPDATE, DELETE, TRUNCATE ON appeal_events FROM {app_role}",
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {app_role}",
        f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {n8n_role}",
        f"GRANT SELECT ON v_daily_stats TO {n8n_role}",
    ]:
        conn.execute(text(statement))

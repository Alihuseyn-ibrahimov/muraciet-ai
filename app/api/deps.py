"""API-nin asılılıqları: servis tokeni və botların istehsal komponentləri (ilk sorğuda bir dəfə qurulur).

Testlər `app.dependency_overrides` ilə kontrolleri və dialoq funksiyasını əvəz edir.
"""
import hmac
from functools import lru_cache, partial

from fastapi import Header, HTTPException

from app.config import settings


def require_service_token(x_service_token: str = Header(default="")) -> None:
    expected = settings.service_token
    if not expected or not hmac.compare_digest(x_service_token.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="servis tokeni yanlışdır")


@lru_cache
def _citizen_graph():
    from app.agents.citizen_graph import CitizenAgents, build_citizen_graph, postgres_checkpointer
    from app.agents.faq import FaqAgent, FaqCache
    from app.knowledge.kb import get_knowledge_base
    from app.yaml_config import get_config

    agents = CitizenAgents.default()
    if settings.faq_cache_hours > 0:
        from redis import Redis

        agents.faq = FaqAgent(cache=FaqCache(Redis.from_url(settings.redis_url),
                                             ttl_seconds=settings.faq_cache_hours * 3600))
    return build_citizen_graph(agents, search=lambda query: get_knowledge_base().search(query),
                               templates=get_config().templates, org_name=settings.org_name,
                               hotline=settings.hotline_text, checkpointer=postgres_checkpointer())


def get_web_turn():
    from app.agents.citizen_graph import run_turn

    return partial(run_turn, _citizen_graph())


@lru_cache
def get_citizen_controller():
    from redis import Redis

    from app.citizen.controller import CitizenController
    from app.citizen.session import RedisLimiter, RedisSessionStore
    from app.db.engine import get_session_factory
    from app.notifier.telegram_api import TelegramClient
    from app.privacy.crypto import get_crypto
    from app.workflow.calendar import get_calendar
    from app.yaml_config import get_config

    settings.require("app_database_url", "citizen_bot_token", "service_token")
    config = get_config()
    redis = Redis.from_url(settings.redis_url)
    return CitizenController(
        get_session_factory(),
        store=RedisSessionStore(redis, ttl_seconds=config.sla.sessions.citizen_ttl_hours * 3600),
        limiter=RedisLimiter(redis), turn=get_web_turn(), client=TelegramClient(settings.citizen_bot_token),
        config=config, calendar=get_calendar(), crypto=get_crypto(), org_name=settings.org_name,
        hotline=settings.hotline_text, rate_limit=settings.rate_limit_per_minute,
    )


@lru_cache
def get_staff_controller():
    from redis import Redis

    from app.agents.qa import QaAgent
    from app.citizen.session import RedisLimiter, RedisSessionStore
    from app.db.engine import get_session_factory
    from app.knowledge.kb import get_knowledge_base
    from app.notifier.telegram_api import TelegramClient
    from app.privacy.crypto import get_crypto
    from app.staff.controller import StaffController
    from app.staff.session import StaffSession
    from app.workflow.calendar import get_calendar
    from app.yaml_config import get_config

    settings.require("app_database_url", "staff_bot_token", "staff_bot_username", "service_token")
    config = get_config()
    redis = Redis.from_url(settings.redis_url)
    agent = QaAgent()

    def qa(**kwargs):
        return agent.review(**kwargs, search=lambda query: get_knowledge_base().search(query))

    return StaffController(
        get_session_factory(),
        store=RedisSessionStore(redis, ttl_seconds=config.sla.sessions.staff_ttl_minutes * 60, prefix="staff:session",
                                cls=StaffSession),
        limiter=RedisLimiter(redis, prefix="staff"), client=TelegramClient(settings.staff_bot_token), config=config,
        calendar=get_calendar(), crypto=get_crypto(), org_name=settings.org_name, qa=qa,
        bot_username=settings.staff_bot_username,
        kb_add=lambda entry_id, question, answer: get_knowledge_base().add_entry(entry_id, question, answer),
        hotline=settings.hotline_text,
    )


class Internal:
    """n8n-in çağırdığı işlər (SLA, bildiriş, saxlama müddəti). Testlərdə saxtası ilə əvəz olunur."""

    def __init__(self):
        from app.db.engine import get_engine, get_session_factory
        from app.privacy.crypto import get_crypto
        from app.workflow.calendar import get_calendar
        from app.workflow.sla import SlaMonitor
        from app.yaml_config import get_config

        settings.require("app_database_url", "service_token")
        self.factory, self.engine = get_session_factory(), get_engine()
        self.monitor = SlaMonitor(self.factory, config=get_config(), calendar=get_calendar(), crypto=get_crypto(),
                                  org_name=settings.org_name)

    @staticmethod
    def _now():
        from datetime import UTC, datetime

        return datetime.now(UTC)

    def sla_check(self) -> dict:
        from app.livechat import LiveChats
        from app.yaml_config import get_config

        result = self.monitor.check(self._now())
        with self.factory() as s, s.begin():  # qəbul edilməyən və yazışmasız qalan canlı çatlar
            result |= LiveChats(s, now=self._now(), templates=get_config().templates,
                                hotline=settings.hotline_text).sweep()
        return result

    def notify(self, target: str, text: str, only_workday: bool = False) -> int:
        from app.notifier.recipients import broadcast
        from app.workflow.calendar import get_calendar

        with self.factory() as s, s.begin():
            return broadcast(s, target, text, now=self._now(), calendar=get_calendar(), only_workday=only_workday)

    def weekly_report(self, days: int = 7) -> dict:
        """Rəhbərə həftəlik hesabat (rəqəmlər + AI təhlili). Bayram günü olsa da göndərilir."""
        from app.notifier.recipients import broadcast

        text, _, ai = weekly_report(self.factory, now=self._now(), days=days)
        with self.factory() as s, s.begin():
            return {"sent": broadcast(s, "head", text, now=self._now()), "ai": ai}

    def retention(self) -> dict:
        from app.workflow.retention import retention_cleanup

        return retention_cleanup(self.engine, now=self._now(), years=settings.retention_years)


def weekly_report(sessions, *, now, days: int):
    """(mətn, göstəricilər, AI təhlili var): n8n-in həftəlik göndərişi və admin API eyni qurucunu işlədir."""
    from app.agents.analyst import AnalystAgent
    from app.reports.weekly import build_report
    from app.workflow.calendar import get_calendar
    from app.yaml_config import get_config

    agent = AnalystAgent()
    return build_report(sessions, now=now, today=now.astimezone(get_calendar().tz).date(), days=days,
                        no_answer_text=get_config().templates.render("no_answer_offer_appeal"),
                        analyse=agent.analyse)


@lru_cache
def get_internal() -> Internal:
    return Internal()


def require_admin_key(x_api_key: str = Header(default="")) -> None:
    expected = settings.admin_api_key
    if not expected or not hmac.compare_digest(x_api_key.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="admin açarı yanlışdır")


def get_session_factory_dep():
    from app.db.engine import get_session_factory

    settings.require("app_database_url")
    return get_session_factory()


def get_crypto_dep():
    from app.privacy.crypto import get_crypto

    return get_crypto()


@lru_cache
def get_web_limiter():
    from redis import Redis

    from app.citizen.session import RedisLimiter

    return RedisLimiter(Redis.from_url(settings.redis_url), prefix="web")

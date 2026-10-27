"""Worker: outbox (Telegram mesajları) və jobs (agent işləri) növbələrini emal edir.

İşə salmaq:  python -m app.worker
Növbə boş olanda saniyədə bir dəfə yoxlayır. SIGINT və SIGTERM cari dövrə bitəndən sonra dayandırır.
"""
import logging
import signal
import threading

from app.config import settings
from app.db.engine import get_session_factory
from app.jobs import JobHandler, JobRunner
from app.notifier.outbox import OutboxWorker
from app.notifier.telegram_api import TelegramClient
from app.privacy.crypto import get_crypto
from app.workflow.states import BotKind
from app.yaml_config import get_config

log = logging.getLogger("worker")
IDLE_SECONDS = 1.0


def job_handlers(factory, config) -> dict[str, JobHandler]:
    """İş növü → handler: triaj (müraciət yarananda) və cavab layihəsi (təyinatdan sonra)."""
    from app.agent_jobs import AgentJobs
    from app.knowledge.kb import get_knowledge_base
    from app.privacy.crypto import get_crypto
    from app.workflow.calendar import get_calendar

    if any(config.agents.model_for(agent)[0] == "anthropic" for agent in ("triage", "routing", "drafter")):
        settings.require("anthropic_api_key")
    jobs = AgentJobs(factory, config=config, calendar=get_calendar(), crypto=get_crypto(),
                     org_name=settings.org_name, search=lambda query: get_knowledge_base().search(query))
    return jobs.handlers()


def build() -> tuple[OutboxWorker, JobRunner]:
    settings.require("app_database_url")
    clients = {}
    if settings.citizen_bot_token:
        clients[BotKind.citizen] = TelegramClient(settings.citizen_bot_token)
    if settings.staff_bot_token:
        clients[BotKind.staff] = TelegramClient(settings.staff_bot_token)
    missing = [bot.value for bot in BotKind if bot not in clients]
    if missing:  # çatışmayan botun mesajları növbədə qalır və token əlavə olunanda göndərilir
        log.warning("bot tokeni təyin olunmayıb: %s", ", ".join(missing))

    config = get_config()
    factory = get_session_factory()
    outbox = OutboxWorker(factory, clients, config=config.sla.outbox, crypto=get_crypto())
    jobs = JobRunner(factory, job_handlers(factory, config), retry_delays=config.sla.outbox.retry_delays_seconds,
                     stale_minutes=config.sla.outbox.stale_sending_minutes)
    return outbox, jobs


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    outbox, jobs = build()
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    log.info("worker işə düşdü")
    while not stop.is_set():
        try:
            busy = outbox.process_batch() + jobs.process_batch()
        except Exception:  # DB qısa müddətə əlçatmaz ola bilər: worker dayanmasın, bir az sonra yenidən cəhd etsin
            log.exception("worker dövrəsində xəta")
            busy = 0
        if not busy:
            stop.wait(IDLE_SECONDS)
    log.info("worker dayandı")


if __name__ == "__main__":
    main()

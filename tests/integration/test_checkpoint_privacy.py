"""Checkpoint bazada şifrəsiz saxlanılır: vətəndaşın şifrəsi açılmış profili oraya heç vaxt düşməməlidir."""
import psycopg
import pytest
from langgraph.checkpoint.postgres import PostgresSaver
from sqlalchemy import text

from app.agents.citizen_graph import CitizenAgents, build_citizen_graph, run_turn
from app.agents.faq import FaqAgent
from app.agents.guard import GuardAgent
from app.agents.intake import IntakeAgent
from app.agents.router import RouterAgent
from app.privacy.llm import PrivateLLM
from app.privacy.masking import Pseudonymizer
from tests.fakes import FakeChatModel
from tests.integration.helpers import CFG

pytestmark = pytest.mark.integration

PROFILE = {"FIN": ["5ABC12D"], "SOYAD": ["Məmmədov"], "UNVAN": ["Nizami küç. 5, m. 12"],
           "TELEFON": ["+994501234567"]}


def test_checkpoint_never_stores_decrypted_profile(app_engine, owner_engine):
    conninfo = app_engine.url.render_as_string(hide_password=False).replace("postgresql+psycopg://", "postgresql://")
    agents = CitizenAgents(GuardAgent(PrivateLLM("guard", model=FakeChatModel())),
                           RouterAgent(PrivateLLM("router", model=FakeChatModel(
                               script=[{"intent": "greeting", "reason": "salam"}]))),
                           FaqAgent(PrivateLLM("faq", model=FakeChatModel())),
                           IntakeAgent(PrivateLLM("intake", model=FakeChatModel())))
    with psycopg.connect(conninfo, autocommit=True, prepare_threshold=0) as conn:
        graph = build_citizen_graph(agents, search=lambda q: [], templates=CFG.templates, org_name="Q", hotline="",
                                    checkpointer=PostgresSaver(conn))
        run_turn(graph, citizen_key=777, text="Salam", step="idle", has_open_question=False,
                 pseudo=Pseudonymizer(PROFILE))

    with owner_engine.connect() as c:
        dump = b"".join([
            *[row[0].encode() for row in c.execute(text("SELECT checkpoint::text || metadata::text FROM checkpoints"))],
            *[bytes(row[0] or b"") for row in c.execute(text("SELECT blob FROM checkpoint_blobs"))],
            *[bytes(row[0] or b"") for row in c.execute(text("SELECT blob FROM checkpoint_writes"))],
        ])
    assert b"Salam" in dump  # düzgün yerə baxırıq: dialoq yaddaşı orada saxlanılıb
    for secret in ("5ABC12D", "Məmmədov", "Nizami küç", "+994501234567"):
        assert secret.encode() not in dump, secret

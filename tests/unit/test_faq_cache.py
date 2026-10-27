"""Məlumat agentinin keşi: eyni müstəqil sual ikinci dəfə LLM-ə getmir, şəxsi sual isə heç vaxt keşlənmir."""
import pytest

from app.agents.faq import FaqAgent, FaqCache, MemoryKV
from app.knowledge.kb import KnowledgeHit
from app.privacy.llm import PrivateLLM
from app.privacy.masking import Pseudonymizer
from tests.fakes import FakeChatModel

pytestmark = pytest.mark.unit

HIT = KnowledgeHit(1, "Müraciətlərin qəbulu və baxılması qaydası", "01.md", "Müraciətlərə 15 iş günü ərzində baxılır.", 0.2)
ANSWER = {"answerable": True, "answer": "Müraciətə 15 iş günü ərzində baxılır.", "source_ids": [1]}


def agent(*script, store=None):
    spy = FakeChatModel(script=list(script))
    cache = FaqCache(store if store is not None else MemoryKV(), ttl_seconds=3600)
    return FaqAgent(PrivateLLM("faq", model=spy), cache=cache), spy


def pseudo():
    return Pseudonymizer({"AD": ["Əli"], "SOYAD": ["Məmmədov"]})


def ask(faq, question, hits=(HIT,), dialogue=()):
    return faq.answer(question, pseudo(), search=lambda q: list(hits), dialogue=list(dialogue))


def test_same_question_is_answered_from_cache():
    faq, spy = agent(ANSWER)
    first = ask(faq, "Müraciətə neçə günə baxılır?")
    second = ask(faq, "  müraciətə neçə   günə baxılır ")  # hərf ölçüsü, boşluq və sual işarəsi fərq etmir
    assert len(spy.received) == 1
    assert second == first and second.answer.answer == ANSWER["answer"] and second.sources == [HIT]


def test_changed_knowledge_base_invalidates_the_cache():
    faq, spy = agent(ANSWER, ANSWER)
    ask(faq, "Müraciətə neçə günə baxılır?")
    updated = KnowledgeHit(1, HIT.title, HIT.source, "Müraciətlərə 10 iş günü ərzində baxılır.", 0.2)
    ask(faq, "Müraciətə neçə günə baxılır?", hits=[updated])
    assert len(spy.received) == 2


def test_personal_questions_are_never_cached():
    faq, spy = agent(ANSWER, ANSWER)
    ask(faq, "Mən Əli Məmmədov, müraciətimə neçə günə baxılır?")
    ask(faq, "Mən Əli Məmmədov, müraciətimə neçə günə baxılır?")
    assert len(spy.received) == 2


def test_follow_up_questions_with_dialogue_are_not_cached():
    faq, spy = agent(ANSWER, ANSWER, ANSWER)
    dialogue = [("citizen", "Pensiya haqqında"), ("ai", "Nə bilmək istəyirsiniz?")]
    ask(faq, "Bəs neçə günə?", dialogue=dialogue)
    ask(faq, "Bəs neçə günə?", dialogue=dialogue)
    ask(faq, "Bəs neçə günə?")  # dialoqlu cavab keşə yazılmayıb
    assert len(spy.received) == 3


def test_unanswerable_and_failed_results_are_not_cached():
    faq, spy = agent({"answerable": False}, RuntimeError("API"), ANSWER)
    assert not ask(faq, "Müraciətə neçə günə baxılır?").answer.answerable
    assert ask(faq, "Müraciətə neçə günə baxılır?") is None
    assert ask(faq, "Müraciətə neçə günə baxılır?").answer.answerable
    assert len(spy.received) == 3


def test_answer_with_profile_data_is_not_cached():
    faq, spy = agent({**ANSWER, "answer": "[AD_1], müraciətə 15 iş günü ərzində baxılır."}, ANSWER)
    ask(faq, "Müraciətə neçə günə baxılır?")
    ask(faq, "Müraciətə neçə günə baxılır?")
    assert len(spy.received) == 2


class BrokenKV:
    def get(self, key):
        raise ConnectionError("redis yoxdur")

    def set(self, key, value, ex=None):
        raise ConnectionError("redis yoxdur")


def test_cache_outage_does_not_break_answers():
    faq, spy = agent(ANSWER, ANSWER, store=BrokenKV())
    assert ask(faq, "Müraciətə neçə günə baxılır?").answer.answerable
    assert ask(faq, "Müraciətə neçə günə baxılır?").answer.answerable
    assert len(spy.received) == 2


def test_cache_stores_no_question_text():
    store = MemoryKV()
    faq, _ = agent(ANSWER, store=store)
    ask(faq, "Müraciətə neçə günə baxılır?")
    assert len(store.data) == 1
    key, value = next(iter(store.data.items()))
    assert "neçə" not in key and "neçə" not in value

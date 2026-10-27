"""9. Kurator agenti: 👍 alan cavabdan bilik bazası üçün ümumi sual-cavab təklif edir (insan təsdiqləyir)."""
import pytest

from app.agents.curator import CuratorAgent
from app.knowledge.kb import KnowledgeHit
from app.privacy.llm import PrivateLLM
from app.privacy.masking import Pseudonymizer
from tests.fakes import FakeChatModel

pytestmark = pytest.mark.unit

PROFILE = {"AD": ["Kamran"], "SOYAD": ["Həsənov"], "FIN": ["7AB12CD"], "UNVAN": ["Azadlıq pr. 10, m. 5"]}
GOOD = {"suitable": True, "question": "Pensiya kartı itəndə pensiyanı necə almaq olar?",
        "answer": "Bankın filialına şəxsiyyət vəsiqəsi ilə müraciət edib kartı bərpa etdirmək lazımdır.",
        "reason": "ümumi prosedurdur"}
APPEAL = dict(subject="Pensiya kartı itib", summary="Kartım itib, pensiyanı ala bilmirəm",
              answer="Hörmətli Kamran Həsənov, bankın filialına şəxsiyyət vəsiqəsi ilə müraciət edib kartı bərpa "
                     "etdirməlisiniz.")
HIT = KnowledgeHit(1, "Pensiya", "02.md", "Pensiya hər ayın 8-15-də köçürülür.", 0.3)


def curator(*script):
    spy = FakeChatModel(script=list(script))
    return CuratorAgent(PrivateLLM("curator", model=spy)), spy


def propose(agent, **kw):
    return agent.propose(**(APPEAL | kw), pseudo=Pseudonymizer(PROFILE), search=lambda q: [HIT])


def test_general_answer_becomes_a_proposal_and_llm_sees_only_masked_data():
    agent, spy = curator(GOOD)
    result = propose(agent)
    assert (result.question, result.answer) == (GOOD["question"], GOOD["answer"])
    sent = spy.all_text()
    assert "Kamran" not in sent and "Həsənov" not in sent and "[AD_1]" in sent
    assert "Pensiya hər ayın 8-15-də" in sent  # mövcud bilik bazası: dublikatı tanımaq üçün


def test_case_specific_answer_is_not_proposed():
    agent, _ = curator({"suitable": False, "question": "", "answer": "", "reason": "fərdi qərar"})
    assert propose(agent) is None


@pytest.mark.parametrize("leak", [
    "[AD_1], bankın filialına müraciət edin.",  # əvəzedici qalıb
    "Kamran Həsənov bankın filialına müraciət etməlidir.",  # profil adı (bərpadan sonra)
    "Bank filialına zəng edin: 050 123 45 67.",  # telefon
    "Hüseynova Nigar sizə kömək edəcək.",  # başqa şəxsin adı (NER)
    "Ödəniş [...] tarixində bərpa olunacaq.",  # yarımçıq
])
def test_proposals_with_personal_or_unfinished_data_are_rejected(leak):
    agent, _ = curator(GOOD | {"answer": leak})
    assert propose(agent) is None


def test_llm_failure_gives_none():
    agent, _ = curator(RuntimeError("API"))
    assert propose(agent) is None

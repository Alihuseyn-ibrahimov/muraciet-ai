"""Agent qiymətləndirməsi: məlumat dəstlərinin düzgünlüyü, metriklərin hesablanması və saxta modellə tam dövrə."""
import json
from typing import get_args

import pytest

from app.agents.faq import FaqAgent
from app.agents.qa import QaAgent
from app.agents.router import RouterAgent
from app.agents.routing import RoutingAgent
from app.agents.schemas import IntentName, QaCode
from app.agents.triage import TriageAgent
from app.knowledge.kb import KnowledgeHit
from app.privacy.llm import PrivateLLM
from app.workflow.states import Category, Outcome, Priority
from app.yaml_config import load_config
from eval.run_agent_eval import AGENTS, accepted, evaluate, keyword_hit, load_cases, make_predictor, score
from tests.fakes import FakeChatModel

pytestmark = pytest.mark.unit

HIT = KnowledgeHit(1, "Müraciətlərin qəbulu və baxılması qaydası", "01.md", "Müraciətlərə 15 iş günü ərzində baxılır.", 0.2)


def fake(agent_cls, *script):
    return agent_cls(PrivateLLM(agent_cls.name, model=FakeChatModel(script=list(script))))


def rows(*pairs) -> list[dict]:
    return [{"case": case, "pred": pred, "latency_ms": 100} for case, pred in pairs]


# ---------- məlumat dəstləri ----------
@pytest.mark.parametrize("agent", AGENTS)
def test_dataset_is_large_enough_and_multilingual(agent):
    cases = load_cases(agent)
    assert len(cases) >= 20
    if agent != "qa":  # rəsmi cavablar yalnız dövlət dilində yazılır
        assert {"az", "translit", "ru"} <= {case.get("lang", "az") for case in cases}


def test_datasets_use_only_valid_labels_and_cover_each_of_them():
    assert {case["intent"] for case in load_cases("router")} == set(get_args(IntentName))

    faq = load_cases("faq")
    assert {case["answerable"] for case in faq} == {True, False}
    assert all(case.get("must") or case.get("any") for case in faq if case["answerable"])

    triage = load_cases("triage")
    assert set().union(*(accepted(case["category"]) for case in triage)) == set(Category)
    assert set().union(*(accepted(case["priority"]) for case in triage)) == set(Priority)
    assert {case["legal_issue"] for case in triage} == {True, False}

    departments = {d.code for d in load_config().departments}
    assert set().union(*(accepted(case["department"]) for case in load_cases("routing"))) == departments

    qa = load_cases("qa")
    assert {code for case in qa for code in case["expected"]} == set(get_args(QaCode))
    assert any(not case["expected"] for case in qa)
    assert {Outcome(case["outcome"]) for case in qa} == set(Outcome)


# ---------- metriklər ----------
def test_keyword_hit_needs_all_must_and_one_of_any_case_insensitively():
    assert keyword_hit("ŞƏXSİYYƏT VƏSİQƏSİ ilə", must=["şəxsiyyət vəsiqəsi"])
    assert keyword_hit("2 iş günü", any_=["iki iş", "2 iş"])
    assert not keyword_hit("09:00-dan", must=["9:00", "18:00"])
    assert keyword_hit("istənilən mətn")


def test_score_router_counts_failures_as_errors_and_breaks_down_by_language():
    result = score("router", rows(
        ({"text": "Salam", "intent": "greeting"}, {"intent": "greeting"}),
        ({"text": "isiqlar yanmir", "intent": "new_appeal", "lang": "translit"}, {"intent": "question"}),
        ({"text": "Спасибо", "intent": "greeting", "lang": "ru"}, None),
    ))
    assert result["accuracy"] == 0.333 and result["failures"] == 1
    assert result["by_lang"]["translit"] == {"n": 1, "accuracy": 0.0}
    assert result["by_label"]["greeting"] == {"n": 2, "accuracy": 0.5}
    assert result["errors"] == [{"input": "isiqlar yanmir", "expected": "new_appeal", "got": "question"},
                                {"input": "Спасибо", "expected": "greeting", "got": None}]


def test_score_faq_separates_coverage_keywords_and_hallucinations():
    result = score("faq", rows(
        ({"question": "a", "answerable": True, "any": ["15 iş"]}, {"answerable": True, "answer": "15 İŞ günü"}),
        ({"question": "b", "answerable": True, "must": ["13:00"]}, {"answerable": True, "answer": "Saat 14:00"}),
        ({"question": "c", "answerable": True, "must": ["112"]}, {"answerable": False, "answer": ""}),
        ({"question": "d", "answerable": False}, {"answerable": True, "answer": "uydurma"}),
        ({"question": "e", "answerable": False}, None),  # xəta səhv sayılır, amma uydurma deyil
    ))
    assert result["answerable_accuracy"] == 0.4
    assert result["coverage"] == 0.667 and result["keyword_hit_rate"] == 0.5
    assert result["hallucination_rate"] == 0.5 and result["failures"] == 1
    assert [error["input"] for error in result["errors"]] == ["b", "c", "d", "e"]


def test_score_triage_accepts_alternatives_and_counts_under_prioritized():
    result = score("triage", rows(
        ({"subject": "Qaz", "summary": "s", "category": ["complaint", "request"], "priority": "urgent",
          "legal_issue": False}, {"category": "request", "priority": "high", "legal_issue": False}),
        ({"subject": "Çuxur", "summary": "s", "category": "complaint", "priority": ["high", "urgent"],
          "legal_issue": False}, {"category": "complaint", "priority": "urgent", "legal_issue": False}),
        ({"subject": "Arayış", "summary": "s", "category": "request", "priority": "normal",
          "legal_issue": True}, {"category": "request", "priority": "high", "legal_issue": False}),
        ({"subject": "X", "summary": "s", "category": "request", "priority": "normal", "legal_issue": False}, None),
        ({"subject": "Y", "summary": "s", "category": "information", "priority": ["normal", "high"],
          "legal_issue": False}, {"category": "information", "priority": "normal", "legal_issue": False}),
    ))
    assert (result["category_accuracy"], result["priority_accuracy"]) == (0.8, 0.4)
    assert (result["legal_accuracy"], result["all_correct"]) == (0.6, 0.4)
    assert result["under_prioritized"] == 1  # yüksək qiymətləndirmə təhlükəsizdir, aşağı qiymətləndirmə yox
    assert result["urgent_recall"] == 0.0 and result["failures"] == 1  # yalnız birmənalı təcili hallar


def test_score_routing():
    result = score("routing", rows(
        ({"subject": "a", "summary": "b", "department": "kommunal"}, {"department": "kommunal"}),
        ({"subject": "a", "summary": "b", "department": "huquq"}, {"department": "general"}),
        ({"subject": "a", "summary": "b", "department": "general", "lang": "ru"}, None),
    ))
    assert result["accuracy"] == 0.333 and result["failures"] == 1
    assert result["by_label"]["huquq"] == {"n": 1, "accuracy": 0.0}


def test_score_qa_per_code_precision_recall_and_clean_false_positives():
    case = {"summary": "s", "outcome": "satisfied", "answer": "a"}
    result = score("qa", rows(
        ({**case, "expected": []}, {"codes": [], "complete": True}),
        ({**case, "expected": []}, {"codes": ["spelling"], "complete": True}),
        ({**case, "expected": ["tone"]}, {"codes": ["spelling", "tone"], "complete": True}),
        ({**case, "outcome": "rejected", "expected": ["missing_appeal_procedure", "tone"]},
         {"codes": ["missing_appeal_procedure"], "complete": False}),
    ))
    assert result["clean_false_positive_rate"] == 0.5 and result["exact_match"] == 0.25
    assert result["incomplete"] == 1
    assert result["by_code"]["tone"] == {"tp": 1, "fp": 0, "fn": 1, "precision": 1.0, "recall": 0.5}
    assert result["by_code"]["spelling"] == {"tp": 0, "fp": 2, "fn": 0, "precision": 0.0, "recall": None}
    assert result["by_code"]["missing_appeal_procedure"]["recall"] == 1.0


# ---------- saxta modellə tam dövrə ----------
def test_evaluate_runs_agent_and_writes_results(tmp_path):
    cases = [{"text": "Salam", "intent": "greeting"}, {"text": "İşıqlar yanmır", "intent": "new_appeal"}]
    router = fake(RouterAgent, {"intent": "greeting", "reason": "salam"}, {"intent": "question", "reason": "?"})
    summary = evaluate("router", cases=cases, predict=make_predictor("router", agent=router), out_dir=tmp_path,
                       model="fake")
    saved = json.loads((tmp_path / "router.json").read_text("utf-8"))
    assert summary["metrics"]["accuracy"] == 0.5 and saved["metrics"] == summary["metrics"]
    assert saved["model"] == "fake" and saved["n"] == 2 and set(saved["latency_ms"]) == {"p50", "p95"}
    assert saved["rows"][1]["pred"] == {"intent": "question"}


def test_predictors_return_plain_json_values():
    faq = make_predictor("faq", agent=fake(FaqAgent, {"answerable": True, "answer": "15 iş günü.", "source_ids": [1]}),
                         search=lambda q: [HIT])
    assert faq({"question": "Neçə günə baxılır?"}) == {"answerable": True, "answer": "15 iş günü.",
                                                       "sources": [HIT.title]}
    triage = make_predictor("triage", agent=fake(TriageAgent, {"category": "complaint", "priority": "urgent",
                                                               "legal_issue": False, "reason": "qaz"}))
    assert json.dumps(triage({"subject": "Qaz", "summary": "Qaz qoxusu"})) == \
        '{"category": "complaint", "priority": "urgent", "legal_issue": false}'
    routing = make_predictor("routing", agent=fake(RoutingAgent, {"department_code": "kosmos", "reason": "?"}))
    assert routing({"subject": "X", "summary": "Y"}) is None  # naməlum şöbə: servis Ümumi şöbəyə göndərir


def test_qa_predictor_does_not_flag_the_eval_citizens_own_phone():
    qa = make_predictor("qa", agent=fake(QaAgent, {"issues": []}, {"issues": []}), search=lambda q: [])
    case = {"summary": "Su yoxdur", "outcome": "satisfied"}
    assert qa({**case, "answer": "Sizinlə +994501234567 nömrəsi ilə əlaqə saxlanılacaq."})["codes"] == []
    assert qa({**case, "answer": "Qonşunuzun nömrəsi: 0552223344."})["codes"] == ["pii_other_person"]


def test_intake_dataset_labels():
    cases = load_cases("intake")
    assert {case["complete"] for case in cases} == {True, False, None}  # None: hər iki cavab qəbul olunur
    assert all(isinstance(group, list) and group for case in cases for group in case["keep"])
    assert all(turn[0] in ("citizen", "bot") for case in cases for turn in case["dialogue"])


def test_score_intake_checks_completion_kept_terms_and_question_safety():
    rows_ = rows(
        ({"dialogue": [], "complete": True, "keep": [["pensiya"], ["3 ay"]], "forbid_question": []},
         {"complete": True, "next_question": None, "subject": "Pensiya", "summary": "Pensiya 3 AYDIR köçmür"}),
        ({"dialogue": [], "complete": True, "keep": [["əlillik"]], "forbid_question": []},
         {"complete": True, "next_question": None, "subject": "Müavinət", "summary": "Müavinət kəsilib"}),
        ({"dialogue": [], "complete": False, "keep": [], "forbid_question": []},
         {"complete": False, "next_question": "FİN kodunuzu yazın", "subject": "", "summary": ""}),
        ({"dialogue": [], "complete": None, "keep": [], "forbid_question": ["əmək haqqı"]},
         {"complete": False, "next_question": "Hansı ödəniş: əmək haqqı, təqaüd?", "subject": "", "summary": ""}),
        ({"dialogue": [], "complete": True, "keep": [], "forbid_question": []}, None),
    )
    result = score("intake", rows_)
    assert result["complete_accuracy"] == 0.75  # gözlənti None olan hal sayılmır, xəta səhvdir
    assert result["keep_rate"] == 0.5  # yalnız tamamlanmış xülasələr: vətəndaşın terminləri itməməlidir
    assert (result["personal_data_questions"], result["forbidden_questions"], result["failures"]) == (1, 1, 1)
    assert len(result["errors"]) == 4


def test_curator_scoring_counts_false_proposals_and_leaks():
    from eval.run_agent_eval import score_curator

    def row(kind, suitable, pred, must=()):
        return {"case": {"kind": kind, "suitable": suitable, "must": list(must), "answer": "x"}, "pred": pred}

    metrics = score_curator([
        row("general", True, {"suitable": True, "question": "Kart itəndə?", "answer": "Banka müraciət edin."}, ["bank"]),
        row("general", True, {"suitable": False}),
        row("individual", False, {"suitable": True, "question": "S", "answer": "Əli Məmmədov üçün"}),
        row("duplicate", False, {"suitable": False}),
    ])
    assert (metrics["accuracy"], metrics["false_proposal_rate"], metrics["coverage"]) == (0.5, 0.5, 0.5)
    assert metrics["keyword_hit_rate"] == 1.0 and metrics["pii_leaks"] == 1


def test_curator_dataset_is_well_formed():
    from eval.run_agent_eval import load_cases

    cases = load_cases("curator")
    assert len(cases) >= 20 and {c["kind"] for c in cases} == {"general", "pii", "individual", "duplicate"}
    assert all(c["suitable"] == (c["kind"] in ("general", "pii")) for c in cases)

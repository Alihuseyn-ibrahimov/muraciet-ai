"""Agentlərin keyfiyyət ölçümü (#33): hər agent öz məlumat dəstində (eval/datasets/<agent>.jsonl) real modellə.

İstifadə:
    python -m eval.run_agent_eval --agent router          # nəticə: eval/results/router.json
    python -m eval.run_agent_eval --agent all --limit 3   # sınaq: hər dəstdən ilk 3 hal

Real API çağırır və xərc yaradır. faq və qa bilik bazasından istifadə edir: APP_DATABASE_URL (muraciet_v2,
`manage.py ingest` edilmiş) və Ollama işləməlidir. Məlumat dəstləri uydurmadır, real vətəndaş məlumatı yoxdur.

Metriklər:
- router, routing: accuracy, hər etiket və dil üzrə (az / translit / ru)
- faq: answerable_accuracy, coverage (cavablanmalı suallardan cavablananlar), keyword_hit_rate
  (cavabda mənbədəki fakt var), hallucination_rate (mənbədə olmayan suala cavab verib)
- triage: kateqoriya, prioritet, hüquqi məsələ; under_prioritized (təhlükəli səhv: prioritet aşağı
  qiymətləndirilib), urgent_recall (m. 10.4: 24 saatlıq hallar)
- qa: hər kod üzrə precision/recall, clean_false_positive_rate (qüsursuz cavaba irad)
- intake: complete_accuracy, keep_rate (xülasədə vətəndaşın açar terminləri qalıb), personal_data_questions
  (şəxsi məlumat soruşan suallar, 0 olmalıdır), forbidden_questions (məsələni təhrif edən təkliflər)
- curator: accuracy, false_proposal_rate (fərdi qərar və ya bilik bazasında artıq olan üçün təklif, 0-a yaxın
  olmalıdır), coverage (ümumi cavablardan təklif edilənlər), keyword_hit_rate (təklifdə cavabın faktı qalıb),
  pii_leaks (təklifdə vətəndaşın adı/telefonu, 0 olmalıdır). Agent None qaytarırsa "təklif yoxdur" sayılır.
Agent xəta verib None qaytarırsa (failures), nəticə səhv sayılır.
"""
import argparse
import json
import re
import statistics
import sys
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.privacy.masking import Pseudonymizer, az_lower
from app.workflow.states import Outcome

HERE = Path(__file__).resolve().parent
DATASETS = HERE / "datasets"
RESULTS = HERE / "results"
AGENTS = ("router", "faq", "triage", "routing", "qa", "intake", "curator")
# Saxta vətəndaşın profili: qa agenti onun öz telefonunu "başqa şəxsin məlumatı" saymamalıdır
EVAL_PROFILE = {"AD": ["Əli"], "SOYAD": ["Məmmədov"], "TELEFON": ["+994501234567"]}
PRIORITY_RANK = {"normal": 0, "high": 1, "urgent": 2}
# Qəbul agenti bunları soruşmamalıdır: forma sistem tərəfindən ayrıca, təhlükəsiz toplanır
PERSONAL_DATA = re.compile(r"\bfin\b|telefon|ünvan|adınız|soyad|şəxsiyyət vəsiqə|doğum tarix")

Predictor = Callable[[dict], dict | None]


def load_cases(agent: str, folder: Path = DATASETS) -> list[dict]:
    lines = (folder / f"{agent}.jsonl").read_text("utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def accepted(value: str | list[str]) -> set[str]:
    """Etiket bir dəyər və ya qəbul olunan dəyərlərin siyahısı ola bilər (həqiqətən birmənalı olmayan hallar)."""
    return {value} if isinstance(value, str) else set(value)


def keyword_hit(answer: str, *, must: list[str] = (), any_: list[str] = ()) -> bool:
    text = az_lower(answer)
    return all(az_lower(k) in text for k in must) and (not any_ or any(az_lower(k) in text for k in any_))


def _ratio(hits: int, total: int) -> float | None:
    return round(hits / total, 3) if total else None


def _breakdown(rows: list[dict], key: Callable[[dict], str], ok: list[bool]) -> dict:
    groups: dict[str, list[bool]] = {}
    for row, good in zip(rows, ok):
        groups.setdefault(key(row["case"]), []).append(good)
    return {name: {"n": len(flags), "accuracy": _ratio(sum(flags), len(flags))} for name, flags in sorted(groups.items())}


def _label_metrics(rows: list[dict], label: str, field: str, input_key: str) -> dict:
    """router və routing: bir etiketli təsnifat."""
    got = [row["pred"][field] if row["pred"] else None for row in rows]
    ok = [value in accepted(row["case"][label]) for value, row in zip(got, rows)]
    return {
        "accuracy": _ratio(sum(ok), len(rows)),
        "failures": sum(row["pred"] is None for row in rows),
        "by_label": _breakdown(rows, lambda c: "/".join(sorted(accepted(c[label]))), ok),
        "by_lang": _breakdown(rows, lambda c: c.get("lang", "az"), ok),
        "errors": [{"input": row["case"][input_key], "expected": row["case"][label], "got": value}
                   for row, value, good in zip(rows, got, ok) if not good],
    }


def score_router(rows: list[dict]) -> dict:
    return _label_metrics(rows, "intent", "intent", "text")


def score_routing(rows: list[dict]) -> dict:
    return _label_metrics(rows, "department", "department", "summary")


def score_faq(rows: list[dict]) -> dict:
    answered = [bool(row["pred"] and row["pred"]["answerable"]) for row in rows]
    should = [row["case"]["answerable"] for row in rows]
    hit = [a and keyword_hit(row["pred"]["answer"], must=row["case"].get("must", ()), any_=row["case"].get("any", ()))
           for row, a in zip(rows, answered)]
    ok = [row["pred"] is not None and a == s and (not s or h) for row, a, s, h in zip(rows, answered, should, hit)]
    answered_should = [h for a, s, h in zip(answered, should, hit) if a and s]
    return {
        "answerable_accuracy": _ratio(sum(row["pred"] is not None and a == s
                                          for row, a, s in zip(rows, answered, should)), len(rows)),
        "coverage": _ratio(len(answered_should), sum(should)),
        "keyword_hit_rate": _ratio(sum(answered_should), len(answered_should)),
        "hallucination_rate": _ratio(sum(a for a, s in zip(answered, should) if not s), should.count(False)),
        "failures": sum(row["pred"] is None for row in rows),
        "by_lang": _breakdown(rows, lambda c: c.get("lang", "az"), ok),
        "errors": [{"input": row["case"]["question"], "expected_answerable": s,
                    "got": row["pred"]["answer"] if row["pred"] and a else None}
                   for row, a, s, good in zip(rows, answered, should, ok) if not good],
    }


def score_triage(rows: list[dict]) -> dict:
    def correct(row: dict, field: str) -> bool:
        if row["pred"] is None:
            return False
        if field == "legal_issue":
            return row["pred"][field] == row["case"][field]
        return row["pred"][field] in accepted(row["case"][field])

    checks = {field: [correct(row, field) for row in rows] for field in ("category", "priority", "legal_issue")}
    predicted = [row for row in rows if row["pred"]]
    urgent = [row for row in rows if accepted(row["case"]["priority"]) == {"urgent"}]
    all_ok = [all(flags) for flags in zip(*checks.values())]
    return {
        "category_accuracy": _ratio(sum(checks["category"]), len(rows)),
        "priority_accuracy": _ratio(sum(checks["priority"]), len(rows)),
        "legal_accuracy": _ratio(sum(checks["legal_issue"]), len(rows)),
        "all_correct": _ratio(sum(all_ok), len(rows)),
        # xəta halında servis prioriteti insan yoxlamasına buraxır, ona görə yalnız real proqnozlar sayılır
        "under_prioritized": sum(PRIORITY_RANK[row["pred"]["priority"]]
                                 < min(PRIORITY_RANK[p] for p in accepted(row["case"]["priority"]))
                                 for row in predicted),
        "urgent_recall": _ratio(sum(bool(row["pred"]) and row["pred"]["priority"] == "urgent" for row in urgent),
                                len(urgent)),
        "failures": len(rows) - len(predicted),
        "by_lang": _breakdown(rows, lambda c: c.get("lang", "az"), all_ok),
        "errors": [{"input": row["case"]["summary"],
                    "expected": {f: row["case"][f] for f in checks}, "got": row["pred"]}
                   for row, good in zip(rows, all_ok) if not good],
    }


def score_qa(rows: list[dict]) -> dict:
    expected = [set(row["case"]["expected"]) for row in rows]
    found = [set(row["pred"]["codes"]) if row["pred"] else set() for row in rows]
    by_code = {}
    for code in sorted(set().union(*expected, *found)):
        tp = sum(code in e and code in f for e, f in zip(expected, found))
        fp = sum(code not in e and code in f for e, f in zip(expected, found))
        fn = sum(code in e and code not in f for e, f in zip(expected, found))
        by_code[code] = {"tp": tp, "fp": fp, "fn": fn, "precision": _ratio(tp, tp + fp), "recall": _ratio(tp, tp + fn)}
    clean = [f for e, f in zip(expected, found) if not e]
    return {
        "exact_match": _ratio(sum(e == f for e, f in zip(expected, found)), len(rows)),
        "clean_false_positive_rate": _ratio(sum(bool(f) for f in clean), len(clean)),
        "incomplete": sum(not (row["pred"] and row["pred"]["complete"]) for row in rows),
        "by_code": by_code,
        "errors": [{"input": row["case"]["answer"][:80], "expected": sorted(e), "got": sorted(f)}
                   for row, e, f in zip(rows, expected, found) if e != f],
    }


def score_intake(rows: list[dict]) -> dict:
    def kept(row: dict) -> bool:
        text = az_lower(f"{row['pred']['subject']} {row['pred']['summary']}")
        return all(any(az_lower(term) in text for term in group) for group in row["case"]["keep"])

    def question(row: dict) -> str:
        return az_lower(row["pred"]["next_question"] or "") if row["pred"] and not row["pred"]["complete"] else ""

    judged = [row for row in rows if row["case"]["complete"] is not None]
    complete_ok = [row["pred"] is not None and row["pred"]["complete"] == row["case"]["complete"] for row in judged]
    completed = [row for row in rows if row["pred"] and row["pred"]["complete"]]
    personal = [row for row in rows if PERSONAL_DATA.search(question(row))]
    forbidden = [row for row in rows if any(az_lower(w) in question(row) for w in row["case"]["forbid_question"])]
    bad = {id(r) for r, ok in zip(judged, complete_ok) if not ok} | {id(r) for r in completed if not kept(r)}         | {id(r) for r in personal + forbidden} | {id(r) for r in rows if r["pred"] is None}
    return {
        "complete_accuracy": _ratio(sum(complete_ok), len(judged)),
        "keep_rate": _ratio(sum(kept(r) for r in completed), len(completed)),
        "personal_data_questions": len(personal),
        "forbidden_questions": len(forbidden),
        "failures": sum(row["pred"] is None for row in rows),
        "by_lang": _breakdown(rows, lambda c: c.get("lang", "az"), [id(r) not in bad for r in rows]),
        "errors": [{"input": " / ".join(text for _, text in row["case"]["dialogue"])[:120],
                    "expected_complete": row["case"]["complete"], "got": row["pred"]}
                   for row in rows if id(row) in bad],
    }


def score_curator(rows: list[dict]) -> dict:
    proposed = [bool(row["pred"] and row["pred"]["suitable"]) for row in rows]
    should = [row["case"]["suitable"] for row in rows]
    ok = [p == s for p, s in zip(proposed, should)]
    good = [row for row, p, s in zip(rows, proposed, should) if p and s]
    texts = [f"{row['pred']['question']} {row['pred']['answer']}" for row, p in zip(rows, proposed) if p]
    return {
        "accuracy": _ratio(sum(ok), len(rows)),
        "false_proposal_rate": _ratio(sum(p for p, s in zip(proposed, should) if not s), should.count(False)),
        "coverage": _ratio(len(good), sum(should)),
        "keyword_hit_rate": _ratio(sum(keyword_hit(f"{r['pred']['question']} {r['pred']['answer']}",
                                                   must=r["case"].get("must", ()), any_=r["case"].get("any", ()))
                                       for r in good), len(good)),
        "pii_leaks": sum(any(v in text for v in ("Məmmədov", "Əli ", "501234567")) for text in texts),
        "by_label": _breakdown(rows, lambda c: c["kind"], ok),
        "errors": [{"input": row["case"]["answer"][:80], "expected_suitable": s, "got": row["pred"]}
                   for row, s, good_ in zip(rows, should, ok) if not good_],
    }


SCORERS = {"router": score_router, "faq": score_faq, "triage": score_triage, "routing": score_routing,
           "qa": score_qa, "intake": score_intake, "curator": score_curator}


def score(agent: str, rows: list[dict]) -> dict:
    return SCORERS[agent](rows)


# ---------- agentlərin işə salınması ----------
def _pseudo() -> Pseudonymizer:
    return Pseudonymizer(EVAL_PROFILE)


def make_predictor(agent_name: str, *, agent=None, search=None, departments=None) -> Predictor:
    """Agenti bir halın üzərində işlədib JSON-a yazıla bilən proqnoz qaytaran funksiya. Verilməyən asılılıqlar
    realdır: agentlər agents.yaml-dakı modeli, axtarış muraciet_v2-dəki bilik bazasını istifadə edir."""
    from app.agents.faq import FaqAgent
    from app.agents.intake import IntakeAgent
    from app.agents.qa import QaAgent
    from app.agents.router import RouterAgent, RouterContext
    from app.agents.routing import RoutingAgent
    from app.agents.triage import TriageAgent

    if agent_name in ("faq", "qa", "curator") and search is None:
        from app.knowledge.kb import get_knowledge_base
        search = get_knowledge_base().search
    if agent_name == "routing" and departments is None:
        from app.yaml_config import get_config
        departments = get_config().departments

    if agent_name == "router":
        router = agent or RouterAgent()

        def predict(case: dict) -> dict | None:
            found = router.classify(case["text"], dialogue=[tuple(turn) for turn in case.get("dialogue", [])],
                                    context=RouterContext(case.get("has_open_question", False)), pseudo=_pseudo())
            return {"intent": found.intent} if found else None
    elif agent_name == "faq":
        faq = agent or FaqAgent()

        def predict(case: dict) -> dict | None:
            result = faq.answer(case["question"], _pseudo(), search=search)
            if result is None:
                return None
            return {"answerable": result.answer.answerable, "answer": result.answer.answer,
                    "sources": sorted({hit.title for hit in result.sources})}
    elif agent_name == "triage":
        triage = agent or TriageAgent()

        def predict(case: dict) -> dict | None:
            found = triage.classify(case["subject"], case["summary"], case.get("description", case["summary"]),
                                    _pseudo())
            return {"category": found.category.value, "priority": found.priority.value,
                    "legal_issue": found.legal_issue} if found else None
    elif agent_name == "routing":
        routing = agent or RoutingAgent()

        def predict(case: dict) -> dict | None:
            found = routing.route(case["subject"], case["summary"], departments, _pseudo())
            return {"department": found.department_code} if found else None
    elif agent_name == "qa":
        qa = agent or QaAgent()

        def predict(case: dict) -> dict | None:
            report = qa.review(appeal_summary=case["summary"], answer=case["answer"], outcome=Outcome(case["outcome"]),
                               pseudo=_pseudo(), search=search)
            return {"codes": sorted({issue.code for issue in report.issues}), "complete": report.complete,
                    "issues": [issue.model_dump() for issue in report.issues]}
    elif agent_name == "intake":
        intake = agent or IntakeAgent()

        def predict(case: dict) -> dict | None:
            dialogue = [(role, text) for role, text in case["dialogue"]]
            asked = sum(1 for role, _ in dialogue if role == "bot")
            result = intake.step(dialogue, questions_asked=asked, pseudo=_pseudo())
            return {"complete": result.complete, "next_question": result.next_question, "subject": result.subject,
                    "summary": result.summary}
    elif agent_name == "curator":
        from app.agents.curator import CuratorAgent
        curator = agent or CuratorAgent()

        def predict(case: dict) -> dict | None:
            found = curator.propose(subject=case["subject"], summary=case["summary"], answer=case["answer"],
                                    pseudo=_pseudo(), search=search)
            return {"suitable": True, "question": found.question, "answer": found.answer} if found else \
                {"suitable": False}
    else:
        raise ValueError(f"naməlum agent: {agent_name}")
    return predict


def _percentile(values: list[int], q: float) -> int:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(len(ordered) * q))] if ordered else 0


def evaluate(agent: str, *, cases: list[dict], predict: Predictor, out_dir: Path = RESULTS, model: str = "",
             echo: Callable[[str], None] = lambda line: None) -> dict:
    rows = []
    for number, case in enumerate(cases, 1):
        started = time.perf_counter()
        pred = predict(case)
        rows.append({"case": case, "pred": pred, "latency_ms": round((time.perf_counter() - started) * 1000)})
        echo(f"  {number}/{len(cases)} {json.dumps(pred, ensure_ascii=False)[:100]}")
    latencies = [row["latency_ms"] for row in rows]
    summary = {
        "agent": agent, "model": model, "n": len(rows),
        "run_at": datetime.now(ZoneInfo("Asia/Baku")).isoformat(timespec="seconds"),
        "metrics": score(agent, rows),
        "latency_ms": {"p50": round(statistics.median(latencies)) if latencies else 0,
                       "p95": _percentile(latencies, 0.95)},
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{agent}.json").write_text(json.dumps({**summary, "rows": rows}, ensure_ascii=False, indent=2), "utf-8")
    return summary


def main(argv: list[str] | None = None) -> None:
    from app.config import settings
    from app.yaml_config import get_config

    parser = argparse.ArgumentParser(description="Agentlərin real modellə qiymətləndirilməsi (API xərci var)")
    parser.add_argument("--agent", choices=[*AGENTS, "all"], required=True)
    parser.add_argument("--limit", type=int, help="hər dəstdən yalnız ilk N hal")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):  # Windows konsolu (cp1252) Azərbaycan hərflərini yaza bilmir
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    names = AGENTS if args.agent == "all" else (args.agent,)
    config = get_config()
    if any(config.agents.model_for(name)[0] == "anthropic" for name in names):
        settings.require("anthropic_api_key")
    for name in names:
        provider, model = config.agents.model_for(name)
        effort = config.agents.effort_for(name)
        label = f"{provider}/{model}" + (f" effort={effort}" if effort else "")
        cases = load_cases(name)[:args.limit]
        print(f"{name}: {len(cases)} hal, {label}")
        summary = evaluate(name, cases=cases, predict=make_predictor(name), model=label, echo=print)
        metrics = {k: v for k, v in summary["metrics"].items() if k not in ("errors", "by_label", "by_code")}
        print(json.dumps({**metrics, "latency_ms": summary["latency_ms"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

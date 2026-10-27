"""Seçmə təsdiq (spec 7-ci bölmə): cavab koordinator təsdiqinə düşürmü və niyə?

Qaydalar config/approval_rules.yaml-dadır. Şərtlərdən biri ödənəndə cavab təsdiqə düşür.
Səbəblərin hamısı qaytarılır ki, koordinator kartda nəyə diqqət etməli olduğunu görsün.
"""
from dataclasses import dataclass, field
from typing import Protocol

from app.workflow.authz import Actor
from app.workflow.states import CATEGORY_LABELS, OUTCOME_LABELS, PRIORITY_LABELS, Category, Outcome, Priority
from app.yaml_config import ApprovalRules


class AppealFacts(Protocol):
    category: Category | None
    priority: Priority
    legal_issue: bool
    triage_failed: bool


@dataclass(frozen=True)
class ApprovalDecision:
    required: bool
    reasons: list[str] = field(default_factory=list)


def needs_approval(
    appeal: AppealFacts,
    *,
    outcome: Outcome,
    qa_critical: bool | None,  # None: Keyfiyyət agenti yoxlama apara bilmədi
    actor: Actor,
    rules: ApprovalRules,
) -> ApprovalDecision:
    if actor.is_coordinator:  # koordinatorun öz cavabı təsdiqə düşmür
        return ApprovalDecision(False)

    reasons = []
    if appeal.category in rules.categories:
        reasons.append(f"Kateqoriya: {CATEGORY_LABELS[appeal.category]}")
    if appeal.priority in rules.priorities:
        reasons.append(f"Prioritet: {PRIORITY_LABELS[appeal.priority]}")
    if outcome in rules.outcomes:
        reasons.append(f"Nəticə: {OUTCOME_LABELS[outcome]}")
    if rules.legal_issue and appeal.legal_issue:
        reasons.append("Hüquqi məsələ")
    if rules.qa_critical and qa_critical:
        reasons.append("Keyfiyyət yoxlamasında kritik qeyd var")
    if rules.agent_failure and appeal.triage_failed:
        reasons.append("AI triajı alınmadı")
    if rules.agent_failure and qa_critical is None:
        reasons.append("AI keyfiyyət yoxlaması aparılmadı")
    return ApprovalDecision(bool(reasons), reasons)

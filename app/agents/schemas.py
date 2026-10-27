"""Agentlərin çıxış sxemləri (spec 5-ci bölmə). Təsvirlər LLM üçün yazılıb: model hər sahəni bu mətnlərə görə doldurur."""
from typing import Literal

from pydantic import BaseModel, Field

from app.workflow.states import Category, Priority

IntentName = Literal["question", "new_appeal", "status", "clarification_reply", "human", "greeting", "off_topic"]
QaCode = Literal[
    "unanswered_question", "missing_measures", "missing_appeal_procedure", "pii_other_person",
    "contradicts_kb", "tone", "spelling",
]
Severity = Literal["info", "warning", "critical"]


class GuardResult(BaseModel):
    allowed: bool
    reason: str = ""


class Intent(BaseModel):
    intent: IntentName = Field(description="Mesajın niyyəti")
    reason: str = Field(description="Qısa əsaslandırma (1 cümlə)")


class FaqAnswer(BaseModel):
    answerable: bool = Field(description="Cavab verilən mənbələrdə varsa true, yoxdursa və ya şəxsi işdirsə false")
    answer: str = Field(default="", description="Azərbaycan dilində qısa, dəqiq cavab (answerable=false olduqda boş)")
    source_ids: list[int] = Field(default_factory=list, description="Cavabın əsaslandığı mənbələrin nömrələri")


class IntakeResult(BaseModel):
    complete: bool = Field(description="Problem müraciət üçün kifayət qədər aydındırsa true")
    next_question: str | None = Field(default=None, description="complete=false olduqda vətəndaşa veriləcək bir sual")
    subject: str = Field(default="", description="Müraciətin qısa mövzusu (ən çoxu 10 söz)")
    summary: str = Field(default="", description="Problemin 1-3 cümləlik xülasəsi: nə, harada, nə vaxtdan, nə tələb olunur")


class Triage(BaseModel):
    category: Category = Field(description="complaint, request, suggestion və ya information")
    priority: Priority = Field(description="normal; high (sağlamlıq/təhlükəsizlik riski); urgent (24 saatda həll olunmasa məna itirir)")
    legal_issue: bool = Field(description="Hüquqi qiymətləndirmə tələb edirsə true")
    reason: str = Field(description="Qısa əsaslandırma")


class Routing(BaseModel):
    department_code: str = Field(description="Siyahıdakı şöbə kodlarından biri")
    reason: str = Field(description="Niyə bu şöbə (1 cümlə)")


class Draft(BaseModel):
    text: str = Field(description="Vətəndaşa rəsmi cavabın layihəsi, Azərbaycan dilində")
    source_ids: list[int] = Field(default_factory=list, description="İstifadə olunan mənbələrin nömrələri")
    missing_info: list[str] = Field(default_factory=list, description="Əməkdaşın dəqiqləşdirməli olduğu məlumatlar")


class QaIssue(BaseModel):
    severity: Severity
    code: QaCode
    message: str = Field(description="Əməkdaş üçün qısa izah")


class QaReport(BaseModel):
    issues: list[QaIssue] = Field(default_factory=list)
    complete: bool = Field(default=True, description="LLM yoxlaması tam aparıldısa true (sistem doldurur)")

    @property
    def has_critical(self) -> bool:
        return any(issue.severity == "critical" for issue in self.issues)


class KbProposal(BaseModel):
    suitable: bool = Field(description="Cavab başqa vətəndaşlar üçün də doğru olan ümumi məlumatdırsa true, "
                                       "fərdi qərar, konkret işin vəziyyəti və ya bilik bazasında artıq varsa false")
    question: str = Field(default="", description="Ümumiləşdirilmiş sual, Azərbaycan dilində (şəxsi məlumatsız)")
    answer: str = Field(default="", description="Ümumiləşdirilmiş cavab, yalnız əməkdaşın cavabındakı faktlarla")
    reason: str = Field(default="", description="Qərarın qısa əsaslandırması")


class Analysis(BaseModel):
    summary: str = Field(description="Həftənin 1-2 cümləlik xülasəsi, Azərbaycan dilində")
    highlights: list[str] = Field(default_factory=list, description="Diqqət çəkən məqamlar (ən çoxu 4)")
    recommendations: list[str] = Field(default_factory=list, description="Rəhbər üçün konkret tövsiyələr (ən çoxu 3)")

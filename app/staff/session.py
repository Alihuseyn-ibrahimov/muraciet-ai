"""Əməkdaşın yarımçıq əməliyyatı (Redis, 30 dəqiqə; spec 7-ci bölmə).

Məsələn: [✍️ Cavab yaz] basılıb, nəticə seçilib, mətn hələ yazılmayıb. Saxlama Redis-dədir
(app/citizen/session.py-dakı ümumi anbar, `cls=StaffSession`).
"""
from dataclasses import asdict, dataclass, field


@dataclass
class StaffSession:
    action: str | None = None  # gözlənilən mətn: answer_text, ask, return, send_back, redirect_target, ...
    appeal_id: int | None = None
    version: int | None = None
    outcome: str | None = None
    text: str | None = None  # önizləmədən sonra göndəriləcək cavab
    qa_critical: bool | None = None  # None: keyfiyyət yoxlaması aparılmadı
    extra: dict = field(default_factory=dict)  # draft_text, target, reason, workdays, role

    def reset(self) -> None:
        self.__dict__.update(asdict(StaffSession()))

"""Bilik bazasının boşluqları: AI-nin "rəsmi məlumatım yoxdur" dediyi suallar (kurator üçün).

Bilik bazasına nəyin əlavə olunmalı olduğunu göstərir: eyni sualı neçə vətəndaş verib. Mətnlər
maskalanır (regex + NER), çünki cavab admin API ilə MCP-yə, oradan LLM-ə gedə bilər. Vətəndaşın
profili burada istifadə olunmur: tək adı və ünvanı maskalanmaya bilər, ona görə nümunə ilk görünən
formadır və yalnız sual (bot cavabı yox) qaytarılır.
"""
import re
from collections import Counter
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.tables import ChatLog
from app.privacy.masking import Pseudonymizer, az_lower


def _normalize(text: str) -> str:
    return " ".join(re.sub(r"[?!.,;:]+", " ", az_lower(text)).split())


def knowledge_gaps(s: Session, *, since: datetime, no_answer_text: str, limit: int = 50) -> list[dict]:
    rows = s.execute(select(ChatLog.citizen_telegram_id, ChatLog.role, ChatLog.content, ChatLog.intent)
                     .where(ChatLog.created_at >= since)
                     .order_by(ChatLog.citizen_telegram_id, ChatLog.id)).all()
    counts: Counter[str] = Counter()
    example: dict[str, str] = {}
    previous = None
    for row in rows:
        if (row.role == "bot" and row.content == no_answer_text and previous is not None
                and previous.citizen_telegram_id == row.citizen_telegram_id
                and previous.role == "citizen" and previous.intent == "question"):
            key = _normalize(previous.content)
            counts[key] += 1
            example.setdefault(key, previous.content)
        previous = row
    pseudo = Pseudonymizer()
    return [{"question": pseudo.mask(example[key]), "count": count} for key, count in counts.most_common(limit)]

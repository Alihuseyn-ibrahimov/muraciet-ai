"""Telegram mesajlarının mətni və düymələri (spec 6-cı və 7-ci bölmələr).

Burada yalnız sadə funksiyalar var: verilən məlumatdan mətn və düymə qurulur, bazaya və Telegram-a
müraciət olunmur. Əməkdaş kartlarının mətni koddadır. Vətəndaşa gedən mətnlər isə
config/templates.yaml-dadır, qurum onları kodu dəyişmədən redaktə edə bilir.

callback_data formatı: "kod:müraciət[:versiya[:əlavə]]", ən çoxu 64 bayt (Telegram limiti).
Versiya köhnə düyməni tanımaq üçündür: müraciət dəyişəndə köhnə kartın düyməsi StaleAppeal verir.
"""
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.privacy.display import mask_fin, mask_phone
from app.workflow.states import (
    CATEGORY_LABELS,
    OUTCOME_LABELS,
    STAFF_STATUS_LABELS,
    Outcome,
    Priority,
)
from app.yaml_config import Templates

TELEGRAM_LIMIT = 4096
CALLBACK_LIMIT = 64
DESCRIPTION_LIMIT = 2500  # kartda; tam mətn /tap ilə
BAKU = ZoneInfo("Asia/Baku")

# callback kodları
ASSIGN, OTHER, REDIRECT, NOT_REVIEW, EXTEND = "as", "ot", "rd", "nr", "ex"
ANSWER, USE_DRAFT, ASK, RETURN, FIN = "an", "ud", "aq", "rt", "fi"
APPROVE, SEND_BACK = "ap", "sb"
FEEDBACK_POS, FEEDBACK_NEG = "fp", "fn"
ATTACHMENTS = "at"  # vətəndaşın əlavələri: vəziyyəti dəyişmir, versiyasızdır
KB_APPROVE, KB_REJECT = "ka", "kr"  # bilik bazası təklifi: ikinci sahə müraciət yox, təklifin id-sidir
KB_PROPOSAL = "kb_proposal"  # təklif kartının outbox/telegram_messages məqsədi
LIVE_CHAT_ACCEPT = "lc"  # canlı çatı qəbul etmək: ikinci sahə çatın id-sidir

CLOSED = {"inline_keyboard": []}


@dataclass(frozen=True)
class Rendered:
    text: str
    reply_markup: dict | None = None


@dataclass(frozen=True)
class Callback:
    code: str
    appeal_id: int
    version: int | None = None
    extra: str | None = None


@dataclass(frozen=True)
class CitizenView:
    full_name: str
    short_name: str
    phone: str
    address: str
    fin: str


@dataclass(frozen=True)
class Suggestion:
    staff_id: int
    name: str
    open_count: int


# ---------- callback ----------
def callback(code: str, appeal_id: int, version: int | None = None, extra: object = None) -> str:
    parts = [code, str(appeal_id)]
    if version is not None:
        parts.append(str(version))
        if extra is not None:
            parts.append(str(extra))
    data = ":".join(parts)
    if len(data.encode()) > CALLBACK_LIMIT:
        raise ValueError(f"callback_data {CALLBACK_LIMIT} baytdan uzundur: {data!r}")
    return data


def parse_callback(data: str) -> Callback:
    parts = (data or "").split(":")
    if len(parts) < 2 or not parts[1].isdigit():
        raise ValueError(f"tanınmayan callback_data: {data!r}")
    version = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
    extra = parts[3] if len(parts) > 3 else None
    return Callback(parts[0], int(parts[1]), version, extra)


def keyboard(*rows: list[tuple[str, str]]) -> dict:
    return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in rows if row]}


# ---------- mətn köməkçiləri ----------
def split_text(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Uzun mətni paraqraf sərhədində hissələrə bölür. Heç nə kəsilib atılmır."""
    if len(text) <= limit:
        return [text]
    parts, current = [], ""
    for paragraph in text.split("\n\n"):
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            parts.append(current)
        while len(paragraph) > limit:
            parts.append(paragraph[:limit])
            paragraph = paragraph[limit:]
        current = paragraph
    if current:
        parts.append(current)
    return parts


def split_rendered(rendered: Rendered) -> list[Rendered]:
    """Düymələr yalnız son hissədə olur."""
    chunks = split_text(rendered.text)
    return [Rendered(chunk, rendered.reply_markup if i == len(chunks) - 1 else None) for i, chunk in enumerate(chunks)]


def _date(value: date) -> str:
    return value.strftime("%d.%m.%Y")


def _datetime(value: datetime) -> str:
    return value.astimezone(BAKU).strftime("%d.%m.%Y %H:%M")


def _priority(priority: Priority) -> str:
    return {Priority.high: " · 🔴 Yüksək", Priority.urgent: " · 🚨 Təcili"}.get(priority, "")


def _category(appeal) -> str:
    return CATEGORY_LABELS.get(appeal.category, "Kateqoriya təyin olunmayıb")


def _shorten(text: str, limit: int = DESCRIPTION_LIMIT) -> str:
    return text if len(text) <= limit else f"{text[:limit]}… (tam mətn: /tap)"


def _status_footer(appeal, assignee_name: str | None) -> str:
    who = f" · {assignee_name}" if assignee_name else ""
    return f"— Status: {STAFF_STATUS_LABELS[appeal.status]}{who}"


def _with_header(header: str | None, body: list[str]) -> str:
    lines = [header, ""] if header else []
    return "\n".join(lines + [line for line in body if line])


# ---------- koordinator ----------
def _attachment_line(count: int) -> str:
    return f"📎 Əlavələr: {count}" if count else ""


def _attachment_row(appeal, count: int) -> list[tuple[str, str]]:
    return [("📎 Əlavələri göstər", callback(ATTACHMENTS, appeal.id))] if count else []


def coordinator_card(appeal, *, citizen: CitizenView, department: str | None, suggestion: Suggestion | None,
                     header: str | None = None, active: bool = True, assignee_name: str | None = None,
                     attachments: int = 0) -> Rendered:
    body = [
        f"📥 {appeal.number} · {_category(appeal)}{_priority(appeal.priority)} · son tarix {_date(appeal.due_date)}",
        f"Mövzu: {appeal.subject}",
        f"Xülasə: {_shorten(appeal.summary)}",
        f"Vətəndaş: {citizen.short_name} · FİN {mask_fin(citizen.fin)} · {mask_phone(citizen.phone)}",
        f"Şöbə (AI): {department}: {appeal.ai_department_reason}" if department else "",
        "⚠️ AI triajı alınmadı: kateqoriyanı və şöbəni yoxlayın" if appeal.triage_failed else "",
        f"🚨 Təcili: ən gec {_datetime(appeal.urgent_deadline)} (m. 10.4)" if appeal.urgent_deadline else "",
        f"🔁 Yenidən açılıb: {appeal.reopen_count} dəfə" if appeal.reopen_count else "",
        _attachment_line(attachments),
    ]
    if not active:
        return Rendered(_with_header(header, body + ["", _status_footer(appeal, assignee_name)]), CLOSED)
    if suggestion:
        body.append(f"Təklif olunan icraçı: {suggestion.name} (açıq işləri: {suggestion.open_count})")
    v = appeal.version
    rows = [
        [(f"✅ Təyin et: {suggestion.name} (açıq: {suggestion.open_count})",
          callback(ASSIGN, appeal.id, v, suggestion.staff_id))] if suggestion else [],
        [("👥 Başqası", callback(OTHER, appeal.id, v)), ("↪️ Aidiyyəti üzrə", callback(REDIRECT, appeal.id, v))],
        [("⏸ Baxılmamış saxla", callback(NOT_REVIEW, appeal.id, v)), ("⏳ Müddəti uzat", callback(EXTEND, appeal.id, v))],
        _attachment_row(appeal, attachments),
    ]
    return Rendered(_with_header(header, body), keyboard(*rows))


def approval_card(appeal, *, staff_name: str, outcome: Outcome, reasons: list[str], active: bool = True,
                  assignee_name: str | None = None) -> Rendered:
    body = [
        f"🟡 Təsdiq gözləyir: {appeal.number} · {_category(appeal)}{_priority(appeal.priority)}",
        f"İcraçı: {staff_name} · Nəticə: {OUTCOME_LABELS[outcome]}",
        "Təsdiq səbəbləri:",
        *[f" • {reason}" for reason in reasons],
        "Cavabın mətni yuxarıdakı mesajdadır.",
    ]
    if not active:
        return Rendered("\n".join(body + ["", _status_footer(appeal, assignee_name)]), CLOSED)
    v = appeal.version
    return Rendered("\n".join(body), keyboard(
        [("✅ Təsdiqlə və göndər", callback(APPROVE, appeal.id, v)), ("↩️ Geri qaytar", callback(SEND_BACK, appeal.id, v))]
    ))


# ---------- icraçı ----------
def staff_card(appeal, *, citizen: CitizenView, header: str | None = None, active: bool = True,
               assignee_name: str | None = None, attachments: int = 0) -> Rendered:
    body = [
        f"📌 {appeal.number} · son tarix {_date(appeal.due_date)}",
        f"{_category(appeal)}{_priority(appeal.priority)}",
        f"🚨 Təcili: ən gec {_datetime(appeal.urgent_deadline)}" if appeal.urgent_deadline else "",
        f"Mövzu: {appeal.subject}",
        f"Təsvir: {_shorten(appeal.description)}",
        f"Vətəndaş: {citizen.full_name}",
        f"Telefon: {citizen.phone}",
        f"Ünvan: {citizen.address}",
        f"FİN: {mask_fin(citizen.fin)}",
        _attachment_line(attachments),
    ]
    if not active:
        return Rendered(_with_header(header, body + ["", _status_footer(appeal, assignee_name)]), CLOSED)
    v = appeal.version
    return Rendered(_with_header(header, body), keyboard(
        [("✍️ Cavab yaz", callback(ANSWER, appeal.id, v)), ("❓ Vətəndaşa sual", callback(ASK, appeal.id, v))],
        [("↩️ Koordinatora qaytar", callback(RETURN, appeal.id, v)), ("👁 FİN-i göstər", callback(FIN, appeal.id, v))],
        _attachment_row(appeal, attachments),
    ))


def draft_message(appeal, *, text: str, missing_info: list[str], draft_message_id: int) -> Rendered:
    """Cavab layihəsi agentinin layihəsi. Düymə vəziyyəti dəyişmir, ona görə Faza 7-də versiya yoxlanılmır."""
    lines = [f"🤖 {appeal.number}: AI cavab layihəsi (yoxlayın və uyğunlaşdırın):", "", text]
    if missing_info:
        lines += ["", "Dəqiqləşdirilməlidir:", *[f" • {item}" for item in missing_info]]
    return Rendered("\n".join(lines), keyboard(
        [("📋 Layihəni cavab kimi istifadə et", callback(USE_DRAFT, appeal.id, appeal.version, draft_message_id))]
    ))


def staff_info(text: str) -> Rendered:
    return Rendered(text)


def citizen_message_for_staff(appeal, text: str) -> Rendered:
    return Rendered(f"💬 {appeal.number}: vətəndaş yazdı:\n{text}")


def delivery_failed_alert(number: str | None, reason: str, *, to_citizen: bool) -> Rendered:
    prefix = f"⚠️ {number}: " if number else "⚠️ "
    if to_citizen:
        return Rendered(f"{prefix}vətəndaşa mesaj çatdırılmadı ({reason}).\nVətəndaşla telefonla əlaqə saxlayın.")
    return Rendered(f"{prefix}əməkdaşa mesaj çatdırılmadı ({reason}).\n"
                    "Əməkdaşın əməkdaş botunu bloklamadığını yoxlayın.")


# ---------- vətəndaş (mətnlər templates.yaml-dan) ----------
def citizen_answer(appeal, *, answer: str, outcome: Outcome, templates: Templates, org_name: str) -> list[Rendered]:
    body = answer.strip()
    procedure = templates.texts["appeal_procedure"]
    if outcome in (Outcome.rejected, Outcome.partial) and procedure not in body:
        body = f"{body}\n\n{procedure}"  # m. 7.8: icraçı unutsa da şikayət qaydası izah olunur
    text = templates.render(
        "official_answer", number=appeal.number, answer=body,
        signature=templates.render("answer_signature", org_name=org_name),
    )
    buttons = keyboard([("👍 Qane oldum", callback(FEEDBACK_POS, appeal.id)),
                        ("👎 Qane olmadım", callback(FEEDBACK_NEG, appeal.id))])
    return split_rendered(Rendered(text, buttons))


def citizen_question(appeal, *, question: str, templates: Templates) -> Rendered:
    return Rendered(templates.render("staff_question", number=appeal.number, question=question))


def citizen_redirected(appeal, *, target: str, letter_ref: str | None, sent_date: date,
                       templates: Templates) -> Rendered:
    return Rendered(templates.render(
        "redirect_notice", number=appeal.number, target=target, sent_date=_date(sent_date),
        letter_ref=f" (məktub № {letter_ref})" if letter_ref else "",
    ))


def citizen_not_reviewed(appeal, *, reason: str, templates: Templates) -> Rendered:
    return Rendered(templates.render(
        "not_reviewed_notice", number=appeal.number, reason=reason,
        appeal_procedure=templates.texts["appeal_procedure"],
    ))


def citizen_extended(appeal, *, due_date: date, reason: str, templates: Templates) -> Rendered:
    return Rendered(templates.render("extension_notice", number=appeal.number, due_date=_date(due_date),
                                     reason=reason))


def citizen_reopened(appeal, *, templates: Templates) -> Rendered:
    return Rendered(templates.render("reopened", number=appeal.number))


# ---------- bilik bazası təklifi (kurator agenti, adminlərə) ----------
def kb_proposal(entry, *, number: str | None, decision: str | None = None) -> Rendered:
    body = [
        f"📚 Bilik bazasına təklif{f' ({number} üzrə 👍 cavabdan)' if number else ''}",
        f"Sual: {entry.question}",
        f"Cavab: {entry.answer}",
        "",
        "Təsdiqləsəniz, AI bu məlumatla vətəndaşların suallarına cavab verəcək. Mətni diqqətlə yoxlayın.",
    ]
    if decision:
        return Rendered("\n".join(body + ["", decision]), CLOSED)
    return Rendered("\n".join(body), keyboard(
        [("✅ Bilik bazasına əlavə et", callback(KB_APPROVE, entry.id)), ("❌ Rədd et", callback(KB_REJECT, entry.id))]))


# ---------- MCP-dən təyinat təklifi (koordinatorlara) ----------
def assignment_proposal(appeal, *, staff_id: int, staff_name: str, open_count: int, reason: str) -> Rendered:
    body = [
        f"🤖 Təyinat təklifi (admin, MCP): {appeal.number} · {_category(appeal)}{_priority(appeal.priority)} · "
        f"son tarix {_date(appeal.due_date)}",
        f"Mövzu: {appeal.subject}",
        f"Təklif olunan icraçı: {staff_name} (açıq işləri: {open_count})",
        f"Səbəb: {reason}" if reason else "",
        "Qərar sizindir. Müraciət o vaxtadək dəyişibsə, düymə işləməyəcək.",
    ]
    return Rendered("\n".join(line for line in body if line), keyboard(
        [(f"✅ Təyin et: {staff_name}", callback(ASSIGN, appeal.id, appeal.version, staff_id))]))

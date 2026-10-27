from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.notifier import cards
from app.notifier.cards import CitizenView, Suggestion
from app.privacy.display import mask_fin, mask_phone
from app.workflow.states import AppealStatus, Category, Outcome, Priority
from app.yaml_config import load_config

pytestmark = pytest.mark.unit

TEMPLATES = load_config().templates
PROCEDURE = TEMPLATES.texts["appeal_procedure"]
CITIZEN = CitizenView(
    full_name="Məmmədov Əli Vəli oğlu", short_name="Məmmədov Əli", phone="+994501234567",
    address="Nizami küç. 5, m. 12", fin="5ABC12D",
)


def appeal(**fields):
    base = dict(
        id=123, number="MR-2026-000123", version=3, status=AppealStatus.new, category=Category.complaint,
        priority=Priority.high, subject="Küçə işıqlandırması", summary="3 gündür işıqlar yanmır",
        description="Nizami küçəsi 5 yaxınlığında 3 gündür işıqlar yanmır", due_date=date(2026, 11, 11),
        urgent_deadline=None, triage_failed=False, reopen_count=0, ai_department_reason="işıqlandırma",
    )
    return SimpleNamespace(**(base | fields))


def buttons(rendered) -> list[tuple[str, str]]:
    return [(b["text"], b["callback_data"]) for row in rendered.reply_markup["inline_keyboard"] for b in row]


# ---------- maskalama ----------
def test_masks():
    assert mask_fin("5abc12d") == "5AB***D"
    assert mask_phone("050 123 45 67") == "+99450***4567"


# ---------- callback_data ----------
def test_callback_data_fits_telegram_limit():
    data = cards.callback(cards.ASSIGN, 9_999_999_999, 99_999, 9_999_999)
    assert len(data.encode()) <= 64
    parsed = cards.parse_callback(data)
    assert (parsed.code, parsed.appeal_id, parsed.version, parsed.extra) == (cards.ASSIGN, 9_999_999_999, 99_999, "9999999")


def test_parse_callback_rejects_garbage():
    with pytest.raises(ValueError):
        cards.parse_callback("salam")


# ---------- koordinator kartı ----------
def test_coordinator_card_shows_masked_identity_and_actions():
    card = cards.coordinator_card(appeal(), citizen=CITIZEN, department="Kommunal şöbə",
                                  suggestion=Suggestion(7, "Rəşad H.", 3))
    assert "MR-2026-000123" in card.text and "Şikayət" in card.text and "Yüksək" in card.text
    assert "11.11.2026" in card.text and "5AB***D" in card.text and "+99450***4567" in card.text
    assert "5ABC12D" not in card.text and "+994501234567" not in card.text  # koordinator tam görmür
    # Ada şəkilçi qoşulmur ("Rəşad H.-a" / "Nigar Q.-ya"): neytral forma
    assert ("✅ Təyin et: Rəşad H. (açıq: 3)", "as:123:3:7") in buttons(card)
    codes = {data.split(":")[0] for _, data in buttons(card)}
    assert {"ot", "rd", "nr", "ex"} <= codes


def test_coordinator_card_warnings():
    urgent = datetime(2026, 10, 20, 10, 0, tzinfo=ZoneInfo("Asia/Baku"))
    card = cards.coordinator_card(appeal(triage_failed=True, priority=Priority.urgent, urgent_deadline=urgent,
                                         reopen_count=1),
                                  citizen=CITIZEN, department="Ümumi şöbə", suggestion=None,
                                  header="🔁 Vətəndaş qane olmadı: işıqlar yenə yanmır")
    assert "AI triajı alınmadı" in card.text and "20.10.2026 10:00" in card.text
    assert card.text.startswith("🔁 Vətəndaş qane olmadı")
    assert not any(data.startswith("as:") for _, data in buttons(card))  # təklif yoxdur


def test_closed_card_has_no_buttons_and_shows_status():
    card = cards.coordinator_card(appeal(status=AppealStatus.assigned), citizen=CITIZEN, department="Kommunal",
                                  suggestion=None, active=False, assignee_name="Rəşad Hüseynov")
    assert card.reply_markup == cards.CLOSED
    assert "İcrada" in card.text and "Rəşad Hüseynov" in card.text


# ---------- icraçı kartı ----------
def test_staff_card_shows_full_contact_but_masked_fin():
    card = cards.staff_card(appeal(status=AppealStatus.assigned), citizen=CITIZEN)
    assert "+994501234567" in card.text and "Nizami küç. 5, m. 12" in card.text
    assert "5AB***D" in card.text and "5ABC12D" not in card.text
    assert {data for _, data in buttons(card)} == {"an:123:3", "aq:123:3", "rt:123:3", "fi:123:3"}


def test_approval_card_lists_reasons():
    card = cards.approval_card(appeal(status=AppealStatus.pending_approval), staff_name="Rəşad H.",
                               outcome=Outcome.rejected, reasons=["Kateqoriya: Şikayət", "Nəticə: Rədd edildi"])
    assert "Rəşad H." in card.text and "Kateqoriya: Şikayət" in card.text and "Rədd edildi" in card.text
    assert {data for _, data in buttons(card)} == {"ap:123:3", "sb:123:3"}


# ---------- vətəndaş mesajları ----------
@pytest.mark.parametrize("outcome", [Outcome.rejected, Outcome.partial])
def test_rejection_gets_appeal_procedure_automatically(outcome):
    parts = cards.citizen_answer(appeal(), answer="Müraciətiniz təmin edilmədi.", outcome=outcome,
                                 templates=TEMPLATES, org_name="Test Qurumu")
    assert PROCEDURE in "".join(p.text for p in parts)  # m. 7.8


def test_procedure_is_not_duplicated_or_added_to_satisfied():
    with_procedure = cards.citizen_answer(appeal(), answer=f"Rədd edildi.\n\n{PROCEDURE}", outcome=Outcome.rejected,
                                          templates=TEMPLATES, org_name="Q")
    assert "".join(p.text for p in with_procedure).count(PROCEDURE) == 1
    satisfied = cards.citizen_answer(appeal(), answer="Bərpa olundu.", outcome=Outcome.satisfied,
                                     templates=TEMPLATES, org_name="Q")
    assert PROCEDURE not in satisfied[0].text


def test_long_answer_is_split_and_feedback_buttons_are_on_last_part():
    answer = "\n\n".join(["Paraqraf " + "ə" * 900] * 5)  # ~4550 simvol
    parts = cards.citizen_answer(appeal(), answer=answer, outcome=Outcome.satisfied, templates=TEMPLATES,
                                 org_name="Q")
    assert len(parts) > 1 and all(len(p.text) <= 4096 for p in parts)
    assert all(p.reply_markup is None for p in parts[:-1])
    assert {data for _, data in buttons(parts[-1])} == {"fp:123", "fn:123"}
    assert "".join(p.text for p in parts).count("Paraqraf") == 5  # heç nə itmir


def test_split_text_hard_cuts_single_huge_paragraph():
    chunks = cards.split_text("ə" * 9000, limit=4096)
    assert [len(c) for c in chunks] == [4096, 4096, 808]


def test_not_reviewed_notice_includes_reason_and_procedure():
    text = cards.citizen_not_reviewed(appeal(), reason="Məhkəmə qərarı var (m. 8.1.3)", templates=TEMPLATES).text
    assert "8.1.3" in text and PROCEDURE in text


def test_redirect_notice_with_and_without_letter():
    with_letter = cards.citizen_redirected(appeal(), target="Su Kanal İdarəsi", letter_ref="12/345",
                                           sent_date=date(2026, 10, 21), templates=TEMPLATES).text
    assert "Su Kanal İdarəsi" in with_letter and "12/345" in with_letter and "21.10.2026" in with_letter
    without = cards.citizen_redirected(appeal(), target="X", letter_ref=None, sent_date=date(2026, 10, 21),
                                       templates=TEMPLATES).text
    assert "№" not in without

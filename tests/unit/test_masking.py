import pytest
from pydantic import BaseModel

from app.privacy.masking import Pseudonymizer

pytestmark = pytest.mark.unit

PROFILE = {
    "SOYAD": ["Məmmədov"], "AD": ["Əli"], "ATA": ["Vəli oğlu"],
    "UNVAN": ["Nizami küç. 5, m. 12"], "TELEFON": ["+994501234567"], "FIN": ["5ABC12D"],
}


def masked(text: str, known=None) -> str:
    return Pseudonymizer(known).mask(text)


# ---------- regex ilə tapılanlar (profilsiz, başqa şəxslərin məlumatı da) ----------
@pytest.mark.parametrize("phone", ["+994 50 123 45 67", "0501234567", "(050) 123-45-67", "994501234567"])
def test_phone_formats(phone):
    assert masked(f"Nömrəm {phone}, zəng edin") == "Nömrəm [TELEFON_1], zəng edin"


def test_same_number_in_two_formats_gets_same_placeholder():
    assert masked("0501234567 və ya +994 50 123 45 67") == "[TELEFON_1] və ya [TELEFON_1]"


@pytest.mark.parametrize("fin", ["5ABC12D", "5abc12d"])
def test_fin(fin):
    assert masked(f"FİN kodum {fin}.") == "FİN kodum [FIN_1]."


@pytest.mark.parametrize("text", ["MR-2026-000123 statusu", "abcdefg sözü", "2026-cı il", "15 iş günü"])
def test_things_that_are_not_pii_stay(text):
    assert masked(text) == text


def test_email_and_card():
    assert masked("ali@mail.az, kart 4169 7388 1234 5678") == "[EMAIL_1], kart [KART_1]"


# ---------- profil dəyərləri ----------
def test_profile_values_with_azerbaijani_suffixes():
    text = "Əlinin atası Vəli oğlu, Məmmədovlar ailəsi, ünvan Nizami küç. 5, m. 12"
    assert masked(text, PROFILE) == "[AD_1]nin atası [ATA_1], [SOYAD_1]lar ailəsi, ünvan [UNVAN_1]"


def test_translit_and_uppercase_forms():
    assert masked("MEMMEDOV ELI, Eliye zeng edin", PROFILE) == "[SOYAD_1] [AD_1], [AD_1]ye zeng edin"


def test_name_inside_other_word_is_not_masked():
    assert masked("Vəliyev küçəsi", {"AD": ["Əli"]}) == "Vəliyev küçəsi"


def test_short_values_are_ignored():
    assert masked("Ok, ağ ev", {"AD": ["Ok"], "SOYAD": ["Ağ"]}) == "Ok, ağ ev"


def test_profile_phone_and_fin_share_placeholders_with_regex_matches():
    p = Pseudonymizer(PROFILE)
    assert p.mask("050 123 45 67 / 5abc12d") == "[TELEFON_1] / [FIN_1]"


# ---------- bərpa ----------
def test_roundtrip():
    p = Pseudonymizer(PROFILE)
    original = "Əli Məmmədov, tel 0501234567, Nizami küç. 5, m. 12"
    assert p.unmask(p.mask(original)) == original


def test_placeholders_are_not_masked_again():
    p = Pseudonymizer(PROFILE)
    once = p.mask("Əli 5ABC12D")
    assert p.mask(once) == once


def test_unknown_placeholder_is_left_untouched():
    assert Pseudonymizer().unmask("[AD_7] gəldi") == "[AD_7] gəldi"


class Reply(BaseModel):
    answer: str
    sources: list[int]
    notes: dict[str, list[str]]


def test_unmask_nested_model():
    p = Pseudonymizer(PROFILE)
    p.mask("Əli Məmmədov")
    reply = Reply(answer="Hörmətli [AD_1] [SOYAD_1]", sources=[1], notes={"x": ["[AD_1]"]})
    restored = p.unmask_obj(reply)
    assert restored.answer == "Hörmətli Əli Məmmədov" and restored.notes == {"x": ["Əli"]}
    assert restored.sources == [1]


def test_public_contacts_are_neither_masked_nor_foreign():
    # Qurumun rəsmi telefonu və e-poçtu şəxsi məlumat deyil: cavabda onlara görə kritik irad olmamalıdır
    p = Pseudonymizer(PROFILE, public=["(012) 525 41 30", "sxa@sosial.gov.az"])
    text = "SXA: (012) 525-41-30, SXA@sosial.gov.az. Qonşu: 0551112233"
    masked = p.mask(text)
    assert "(012) 525-41-30" in masked and "SXA@sosial.gov.az" in masked and "0551112233" not in masked
    assert len(p.foreign_values(text)) == 1


def test_knowledge_base_contacts_are_public_by_default():
    assert Pseudonymizer().mask("Telefon: (012) 434-55-86, demx@sosial.gov.az") == \
        "Telefon: (012) 434-55-86, demx@sosial.gov.az"
    assert Pseudonymizer().mask("Mənim nömrəm 0551112233") == "Mənim nömrəm [TELEFON_1]"


def test_unmask_keeps_enum_fields_as_enums():
    # StrEnum həm də str-dir: bərpa onu adi mətnə çevirsəydi, triaj nəticəsində .value işləməzdi
    from app.agents.schemas import Triage
    from app.workflow.states import Category, Priority

    restored = Pseudonymizer(PROFILE).unmask_obj(
        Triage(category="complaint", priority="urgent", legal_issue=False, reason="[AD_1] yazır"))
    assert type(restored.category) is Category and type(restored.priority) is Priority

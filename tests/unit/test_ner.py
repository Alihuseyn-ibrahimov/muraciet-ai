"""Lokal NER: profildə olmayan başqa şəxslərin adları da LLM-ə getməzdən əvvəl maskalanır."""
import pytest

from app.privacy.masking import KNOWLEDGE_DIR, Pseudonymizer
from app.privacy.ner import person_spans

pytestmark = pytest.mark.unit


def names(text: str) -> list[str]:
    return [text[a:b] for a, b in person_spans(text)]


@pytest.mark.parametrize("text, expected", [
    ("Qonşum Əhmədova Leyla bu barədə məlumat verdi", ["Əhmədova Leyla"]),
    ("Rəşad Hüseynov bizə kömək etmədi", ["Rəşad Hüseynov"]),
    ("Elçin Rəhim oğlu ilə danışdım", ["Elçin Rəhim oğlu"]),
    ("Məmmədov Əli Vəli oğluna zəng etdim", ["Məmmədov Əli Vəli oğluna"]),
    ("Əhmədovanın evi yanındadır", ["Əhmədovanın"]),
    ("Leyla xanım dedi ki, sənəd hazırdır", ["Leyla xanım"]),
    ("Bunu Kamran müəllim yazıb", ["Kamran müəllim"]),
    ("cənab Quliyev cavab vermədi", ["cənab Quliyev"]),
    ("Иванов Иван Петрович не отвечает", ["Иванов Иван Петрович"]),
    ("Memmedov Eli yazdi", ["Memmedov Eli"]),  # translit
    ("Rüstəmzadə Səbinə gəlmədi", ["Rüstəmzadə Səbinə"]),
])
def test_person_names_are_found(text, expected):
    assert names(text) == expected


@pytest.mark.parametrize("text", [
    "Nizami küçəsi 5, mənzil 12",  # tək ad (küçə) maskalanmır
    "Sosial Xidmətlər Agentliyinə müraciət etdim",
    "Nazirlər Kabinetinin 187 nömrəli qərarı",
    "Azərbaycan Respublikasının Dövlət İmtahan Mərkəzi",
    "Pensiyam 3 aydır köçmür, ÜDSY imtina verib",
    "Əli dünən zəng etdi",  # tək ad: kontekstsiz insan adı kimi qəbul olunmur
    "Vəliyev küçəsi 3",  # şəxsin adını daşıyan küçə, fond, prospekt toponimdir
    "Heydər Əliyev Fonduna yazdım",
    "Ziya Bünyadov prospekti",
    "Bakı, Ziya Bünyadov pr. 1965",
])
def test_ordinary_text_is_left_alone(text):
    assert names(text) == []


def test_knowledge_base_has_no_false_person_names():
    found = set()
    for path in KNOWLEDGE_DIR.glob("*.md"):
        found.update(names(path.read_text(encoding="utf-8")))
    assert found == set()


def test_pseudonymizer_masks_other_persons_and_restores_them():
    p = Pseudonymizer({"AD": ["Əli"], "SOYAD": ["Məmmədov"]})
    masked = p.mask("Mən Əli Məmmədov, qonşum Hüseynova Nigar şikayətimi təsdiqləyir")
    assert "Hüseynova" not in masked and "Nigar" not in masked and "[SEXS_1]" in masked
    assert "[AD_1] [SOYAD_1]" in masked  # vətəndaşın öz adı profil ilə
    assert p.unmask(masked) == "Mən Əli Məmmədov, qonşum Hüseynova Nigar şikayətimi təsdiqləyir"


def test_same_person_gets_the_same_placeholder():
    p = Pseudonymizer()
    masked = p.mask("Hüseynova Nigar gəldi. Sonra Hüseynova Nigar getdi.")
    assert masked.count("[SEXS_1]") == 2


def test_other_person_names_do_not_count_as_foreign_contact_data():
    # QA-nın kritik qaydası telefon/FİN/e-poçt/kart üçündür; ad (məs. əməkdaşın adı) cavabı bloklamamalıdır
    assert Pseudonymizer().foreign_values("İcraçı: Rəşad Hüseynov") == []

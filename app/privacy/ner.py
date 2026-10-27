"""Lokal, qaydalara əsaslanan şəxs adı tanıyıcısı (NER): mətn heç yerə göndərilmir, model yüklənmir.

Profildə olmayan başqa şəxslərin (qonşu, əməkdaş, işəgötürən) adlarını tapır ki, `Pseudonymizer` onları da
maskalasın. Tanınan formalar:
- soyad (-ov/-ova, -yev/-yeva, -ev/-eva, -zadə; rusca -ов/-ев/-ин), şəkilçili də ("Əhmədovanın"), yanındakı
  ad və ata adı ilə birlikdə ("Rəşad Hüseynov", "Иванов Иван Петрович");
- ad + ata adı + oğlu/qızı ("Elçin Rəhim oğlu");
- müraciət formaları: "Leyla xanım", "Kamran müəllim", "cənab Quliyev".

Tək ad maskalanmır: "Nizami küçəsi", "Əli dedi" kimi kontekstsiz adlar yer adı və ya adi söz ola bilər.
Qalıq risk: -li/-lı soyadları ("Hüseynli") və siyahıda olmayan adın soyadsız işlənməsi tanınmır. Gələcəkdə
eyni `person_spans` interfeysi arxasında ML modeli qoşmaq olar.
"""
import re


def az_lower(text: str) -> str:
    """Azərbaycan dilinə uyğun kiçik hərf: I → ı, İ → i (Python-un lower() funksiyası bunu bilmir)."""
    return text.replace("İ", "i").replace("I", "ı").lower()

_WORD = re.compile(r"[^\W\d_]+")
_CASE = r"(?:n?[ıiuü]n?|y?[aəıiuü]|n?[dt][aə]n?|y?l[aə](?:r[^\W\d_]*)?|gil[^\W\d_]*)?"
_SURNAME = re.compile(r"[A-ZÇƏĞIİÖŞÜ][a-zçəğıöşü]{2,}?(?:y?ev|ov)a?" + _CASE + r"|"
                      r"[A-ZÇƏĞIİÖŞÜ][a-zçəğıöşü]{2,}?zad[əe]" + _CASE)
_RU_SURNAME = re.compile(r"[А-ЯЁ][а-яё]{2,}(?:ов|ев|ёв|ин)(?:а|у|ым|ой|е|ой)?")
_RU_PATRONYMIC = re.compile(r"[А-ЯЁ][а-яё]+(?:ович|евич|ич|овна|евна|ична|инична)[а-яё]*")
_CYRILLIC_CAP = re.compile(r"[А-ЯЁ][а-яё]+")
_PATRONYMIC = re.compile(r"(?:oğl|ogl|qız|qiz|giz)[a-zçəğıöşü]*", re.IGNORECASE)
_HONORIFIC_AFTER = {"xanım", "xanim", "müəllim", "muellim", "müəllimə", "bəy", "bey", "həkim"}
_HONORIFIC_BEFORE = {"cənab", "cenab"}
# Adın ardınca gələndə ad toponim/qurum adıdır, şəxs deyil: "Vəliyev küçəsi", "Heydər Əliyev Fondu"
_PLACE_WORDS = ("küçə", "kuce", "prospekt", "döngə", "dalan", "meydan", "park", "bağ", "qəsəbə", "kənd", "rayon",
                "fond", "məktəb", "lisey", "universitet", "xəstəxana", "adına", "metro", "stansiya", "mərkəz")
_PLACE_ABBREVIATIONS = {"küç", "pr", "prosp", "döng", "qəs", "r", "ray"}  # nöqtə ilə: "Ziya Bünyadov pr."

_FOLD = str.maketrans("əıöüğşç", "eiougsc")

_GIVEN_NAMES = """
Əli Vəli Məmməd Məhəmməd Hüseyn Həsən Rəşad Elçin Rəhim Kamran Ramin Orxan Tural Elvin Elnur Elşən Anar Murad
Nicat Nihad Fuad Fərid Fərhad Rauf Ruslan Samir Səməd Tofiq Vüqar Vüsal Zaur Ziya Azad Aydın Babək Bəxtiyar Cavid
Cəlal Cəmil Cümşüd Elxan Emil Eldar Eldəniz Elmar Elman Emin Etibar Fikrət Firudin Fazil Füzuli Habil Hikmət
İlham İlqar İlkin İsmayıl İbrahim Kənan Kərim Qasım Məhəmməd Mirzə Mübariz Natiq Nemət Nizami Nurlan Oqtay
Pərviz Qabil Rafael Rafiq Ramiz Rasim Rəsul Rövşən Rüstəm Sabir Sahib Sənan Seymur Şahin Şamil Tahir Teymur
Telman Toğrul Ülvi Vahid Vasif Yaşar Yusif Zakir Zahid Ceyhun Coşqun Faiq Heydər İlyas Kamal Mahir Musa Namiq
Nəsimi Rəhman Sərxan Siyavuş Valeh Xəyal Yaqub Zöhrab Aqil Arif Asif Akif Əkbər Əhməd Əziz Ənvər Hacı Səlim
Leyla Nigar Səbinə Aynur Günel Gülnar Aysel Sevinc Lalə Nərgiz Nərmin Ülviyyə Könül Xədicə Fatimə Zəhra Aytən
Gülşən Səadət Şəhla Rəna Vüsalə Sevda Təranə Tamilla Afaq Mehriban Səidə Nazlı Zərifə Sona Kəmalə Ləman Aygün
Arzu Ülkər Esmira Elmira Zemfira Solmaz Mələk Gülarə Pərvin Bahar Jalə Rəhilə Nailə Zülfiyyə Mətanət Samirə
Aidə Aliyə Almaz Aytac Ayşən Bənövşə Dilarə Dilbər Elnarə Firəngiz Fidan Gülbəniz Gülnarə Gülər Gültəkin
Hicran İradə İlahə Kəmalə Lamiyə Lətafət Mədinə Məlahət Nübar Nurlana Nuranə Pakizə Rəhimə Rəsmiyyə Sara
Sima Şəfa Şölə Turan Ulduz Validə Zərintac Zaur Ruhiyyə Rübabə Fəridə Həcər Kifayət Səkinə Tünzalə Xanım
"""
GIVEN_NAMES = frozenset(az_lower(name).translate(_FOLD) for name in _GIVEN_NAMES.split())


def _fold(word: str) -> str:
    return az_lower(word).replace("sh", "s").replace("ch", "c").translate(_FOLD)


def _is_capitalized(word: str) -> bool:
    return word[:1].isupper()


def _is_given(word: str) -> bool:
    return _is_capitalized(word) and _fold(word) in GIVEN_NAMES


def _is_surname(word: str) -> bool:
    return bool(_SURNAME.fullmatch(word) or _RU_SURNAME.fullmatch(word))


def _is_name_part(word: str, cyrillic: bool) -> bool:
    """Soyadın yanında ad/ata adı ola bilən söz."""
    if cyrillic:
        return bool(_CYRILLIC_CAP.fullmatch(word) or _RU_PATRONYMIC.fullmatch(word))
    return _is_given(word)


def person_spans(text: str) -> list[tuple[int, int]]:
    """Mətndəki şəxs adlarının (başlanğıc, son) mövqeləri, artan sıra ilə, üst-üstə düşmədən."""
    tokens = [(m.start(), m.end(), m.group(0)) for m in _WORD.finditer(text)]
    # bitişik: iki söz arasında yalnız boşluq var (vergül və nöqtə adı bölür)
    joined = [i > 0 and not text[tokens[i - 1][1]:tokens[i][0]].strip() for i in range(len(tokens))]
    words = [t[2] for t in tokens]
    found: list[tuple[int, int]] = []  # söz indeksləri, daxil olmaqla

    for i, word in enumerate(words):
        if _is_surname(word):
            cyrillic = bool(_RU_SURNAME.fullmatch(word))
            a = b = i
            if a > 0 and joined[a] and _is_name_part(words[a - 1], cyrillic):
                a -= 1
            while b + 1 < len(words) and b - i < 2 and joined[b + 1] and _is_name_part(words[b + 1], cyrillic):
                b += 1
            if a > 0 and joined[a] and az_lower(words[a - 1]) in _HONORIFIC_BEFORE:
                a -= 1
            found.append((a, b))
        elif _PATRONYMIC.fullmatch(word) and i >= 2 and joined[i] and joined[i - 1] \
                and _is_capitalized(words[i - 1]) and _is_capitalized(words[i - 2]):
            a = i - 2
            if a > 0 and joined[a] and _is_surname(words[a - 1]):
                a -= 1
            found.append((a, i))
        elif az_lower(word) in _HONORIFIC_AFTER and i >= 1 and joined[i] and _is_given(words[i - 1]):
            found.append((i - 1, i))
        elif az_lower(word) in _HONORIFIC_BEFORE and i + 1 < len(words) and joined[i + 1] \
                and _is_given(words[i + 1]):
            found.append((i, i + 1))

    def is_place(b: int) -> bool:
        if b + 1 >= len(words) or not joined[b + 1]:
            return False
        following = az_lower(words[b + 1])
        abbreviation = following in _PLACE_ABBREVIATIONS and text[tokens[b + 1][1]:tokens[b + 1][1] + 1] == "."
        return following.startswith(_PLACE_WORDS) or abbreviation

    merged: list[list[int]] = []
    for a, b in sorted(span for span in found if not is_place(span[1])):
        if merged and a <= merged[-1][1] + 1 and (a <= merged[-1][1] or joined[a]):
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(tokens[a][0], tokens[b][1]) for a, b in merged]

"""Şəxsi məlumatların LLM-ə getməzdən əvvəl əvəzedicilərlə dəyişdirilməsi (spec 6-E və 9-cu bölmələr).

İki mənbə istifadə olunur:
1. Vətəndaşın profilindəki dəqiq dəyərlər (ad, soyad, ata adı, ünvan, telefon, FİN). Şəkilçili
   formalar ("Əlinin" → "[AD_1]nin") və translit ("Memmedov" = "Məmmədov") da tutulur.
2. Regex: telefon, FİN, e-poçt və kart. Bunlar başqa şəxslərin məlumatını da tutur.
3. Lokal NER (`app/privacy/ner.py`): profildə olmayan başqa şəxslərin adları ([SEXS_1]).

Eyni dəyər həmişə eyni əvəzedicini alır (məs. iki fərqli formatda yazılmış telefon). Bərpa
(unmask) dəyərin mətndə ilk göründüyü formanı qaytarır.

İctimai dəyərlər (qurumların rəsmi telefonu, e-poçtu və s.) maskalanmır və başqa şəxsin məlumatı
sayılmır. Standart olaraq bilik bazası sənədlərində (data/knowledge) olan bütün belə dəyərlər ictimaidir.

Qalıq risk: NER qaydalara əsaslanır, tək adı və -li/-lı soyadlarını tanımır (bax ner.py).
"""
import re
from collections.abc import Callable, Iterable, Mapping
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.privacy.crypto import normalize_fin, normalize_phone
from app.privacy.ner import az_lower, person_spans

# SEXS: profildə olmayan başqa şəxsin adı (lokal NER, app/privacy/ner.py)
KINDS = ("AD", "SOYAD", "ATA", "UNVAN", "TELEFON", "FIN", "EMAIL", "KART", "SEXS")
PLACEHOLDER = re.compile(r"\[(" + "|".join(KINDS) + r")_(\d+)\]")
MIN_KNOWN_LENGTH = 3
KNOWLEDGE_DIR = Path(__file__).resolve().parents[2] / "data" / "knowledge"

# Azərbaycan hərfləri və onların translit qarşılıqları (hər iki istiqamətdə)
_TRANSLIT = {
    "ə": "[əe]", "e": "[eə]", "ı": "[ıi]", "i": "[iı]", "ö": "[öo]", "o": "[oö]", "ü": "[üu]", "u": "[uü]",
    "ğ": "[ğg]", "g": "[gğ]", "ş": "(?:ş|sh|s)", "s": "(?:s|ş)", "ç": "(?:ç|ch|c)", "c": "(?:c|ç)",
}
_PHONE = re.compile(r"(?<!\d)\(?(?:\+\s?994|994|0)[\s\-()]*\d{2}[\s\-()]*\d{3}[\s\-()]*\d{2}[\s\-()]*\d{2}(?!\d)")
_FIN = re.compile(r"(?i)(?<![0-9a-z])(?=[0-9a-z]*\d)(?=[0-9a-z]*[a-z])[0-9a-z]{7}(?![0-9a-z])")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){15,18}\d(?!\d)")
_DETECTORS = (("EMAIL", _EMAIL), ("KART", _CARD), ("TELEFON", _PHONE), ("FIN", _FIN))


def _key(kind: str, value: str) -> str:
    if kind == "TELEFON":
        return normalize_phone(value)
    if kind == "FIN":
        return normalize_fin(value)
    return " ".join(az_lower(value).split())


def _known_pattern(value: str) -> re.Pattern:
    body = []
    for char in az_lower(value.strip()):
        if char.isspace():
            if body and body[-1] != r"\s+":
                body.append(r"\s+")
        else:
            body.append(_TRANSLIT.get(char, re.escape(char)))
    # Yalnız sözün əvvəli yoxlanılır: sonda şəkilçi ola bilər ("Əlinin", "Məmmədovlar")
    return re.compile(r"(?<!\w)" + "".join(body), re.IGNORECASE)


def _public_keys(values: Iterable[str]) -> frozenset[tuple[str, str]]:
    keys = set()
    for value in values:
        for kind, pattern in _DETECTORS:
            keys.update((kind, _key(kind, m.group(0))) for m in pattern.finditer(value))
    return frozenset(keys)


@lru_cache
def knowledge_base_public_keys(folder: Path = KNOWLEDGE_DIR) -> frozenset[tuple[str, str]]:
    """Bilik bazası sənədlərindəki telefon, e-poçt və s.: qurumların rəsmi əlaqələridir."""
    texts = [path.read_text(encoding="utf-8") for path in sorted(folder.rglob("*"))
             if path.suffix.lower() in (".md", ".txt")] if folder.exists() else []
    return _public_keys(texts)


class Pseudonymizer:
    def __init__(self, known: Mapping[str, Iterable[str | None]] | None = None,
                 public: Iterable[str] | None = None):
        self._public = knowledge_base_public_keys() if public is None else _public_keys(public)
        self._placeholder: dict[tuple[str, str], str] = {}  # (növ, normallaşdırılmış dəyər) → əvəzedici
        self._original: dict[str, str] = {}  # əvəzedici → bərpa üçün forma
        self._seen: set[str] = set()  # mətndə artıq görünmüş əvəzedicilər
        self._counters = dict.fromkeys(KINDS, 0)
        self._profile: set[str] = set()  # profildən gələn əvəzedicilər
        patterns = []
        for kind, values in (known or {}).items():
            for value in values:
                if value and len(value.strip()) >= MIN_KNOWN_LENGTH:
                    self._profile.add(self._register(kind, value.strip()))
                    if kind not in ("TELEFON", "FIN"):  # bunları regex onsuz da tapır
                        patterns.append((len(value), kind, value.strip(), _known_pattern(value)))
        self._known = [(kind, value, pattern) for _, kind, value, pattern in sorted(patterns, key=lambda p: -p[0])]

    def _register(self, kind: str, value: str) -> str:
        key = (kind, _key(kind, value))
        if key not in self._placeholder:
            self._counters[kind] += 1
            placeholder = f"[{kind}_{self._counters[kind]}]"
            self._placeholder[key] = placeholder
            self._original[placeholder] = value
        return self._placeholder[key]

    def _replace(self, kind: str, canonical: str | None = None) -> Callable[[re.Match], str]:
        """canonical: profil dəyəri. Onun şablonu ilə tutulan hər forma (translit, böyük hərf) eyni əvəzedicini alır."""
        return lambda match: self._substitute(kind, match.group(0), canonical)

    def _substitute(self, kind: str, surface: str, canonical: str | None = None) -> str:
        if canonical is None and (kind, _key(kind, surface)) in self._public:
            return surface
        placeholder = self._register(kind, canonical or surface)
        if placeholder not in self._seen:  # bərpa mətndəki ilk formanı qaytarsın
            self._seen.add(placeholder)
            self._original[placeholder] = surface
        return placeholder

    def _outside_placeholders(self, text: str, fn: Callable[[str], str]) -> str:
        parts, last = [], 0
        for match in PLACEHOLDER.finditer(text):
            parts.append(fn(text[last:match.start()]))
            parts.append(match.group(0))
            last = match.end()
        parts.append(fn(text[last:]))
        return "".join(parts)

    def mask(self, text: str) -> str:
        if not text:
            return text
        for kind, value, pattern in self._known:
            text = self._outside_placeholders(
                text, lambda chunk, k=kind, v=value, p=pattern: p.sub(self._replace(k, v), chunk))
        text = self._outside_placeholders(text, self._mask_persons)
        for kind, pattern in _DETECTORS:
            text = self._outside_placeholders(text, lambda chunk, k=kind, p=pattern: p.sub(self._replace(k), chunk))
        return text

    def _mask_persons(self, chunk: str) -> str:
        parts, last = [], 0
        for start, end in person_spans(chunk):
            parts += [chunk[last:start], self._substitute("SEXS", chunk[start:end])]
            last = end
        parts.append(chunk[last:])
        return "".join(parts)

    def foreign_values(self, text: str, kinds: tuple[str, ...] = ("TELEFON", "FIN", "EMAIL", "KART")) -> list[str]:
        """Mətndə olub profilə aid olmayan, yəni başqa şəxsin telefon/FİN/e-poçt/kart məlumatının əvəzediciləri."""
        return [m.group(0) for m in PLACEHOLDER.finditer(self.mask(text))
                if m.group(1) in kinds and m.group(0) not in self._profile]

    def unmask(self, text: str) -> str:
        if not text:
            return text
        return PLACEHOLDER.sub(lambda m: self._original.get(m.group(0), m.group(0)), text)

    def mask_obj(self, obj: Any) -> Any:
        return self._walk(obj, self.mask)

    def unmask_obj(self, obj: Any) -> Any:
        return self._walk(obj, self.unmask)

    def _walk(self, obj: Any, fn: Callable[[str], str]) -> Any:
        if isinstance(obj, Enum):  # StrEnum həm də str-dir; sabit lüğətdir, şəxsi məlumat daşımır
            return obj
        if isinstance(obj, str):
            return fn(obj)
        if isinstance(obj, BaseModel):
            return obj.model_copy(update={name: self._walk(value, fn) for name, value in obj.__dict__.items()})
        if isinstance(obj, list):
            return [self._walk(item, fn) for item in obj]
        if isinstance(obj, tuple):
            return tuple(self._walk(item, fn) for item in obj)
        if isinstance(obj, dict):
            return {key: self._walk(value, fn) for key, value in obj.items()}
        return obj

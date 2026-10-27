"""Vətəndaşın şifrələnmiş profilindən maskalama üçün məlum dəyərlər (yalnız yaddaşda, heç yerə yazılmır)."""
from app.db.tables import Citizen
from app.privacy.crypto import Crypto
from app.privacy.masking import Pseudonymizer


def known_values(citizen: Citizen | None, crypto: Crypto) -> dict[str, list[str]]:
    if citizen is None:
        return {}

    def plain(token: str | None) -> list[str]:
        return [crypto.decrypt(token)] if token else []

    patronymic = (citizen.patronymic or "").strip()
    father = patronymic.split()[0] if patronymic else ""  # "Vəli oğlu" → "Vəli" də ayrıca maskalanır
    return {
        "AD": [citizen.first_name or ""],
        "SOYAD": [citizen.last_name or ""],
        "ATA": [patronymic, father],
        "UNVAN": plain(citizen.address_enc),
        "TELEFON": plain(citizen.phone_enc),
        "FIN": plain(citizen.fin_enc),
    }


def pseudonymizer_for(citizen: Citizen | None, crypto: Crypto) -> Pseudonymizer:
    return Pseudonymizer(known_values(citizen, crypto))

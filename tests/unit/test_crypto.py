import pytest
from cryptography.fernet import Fernet

from app.config import ConfigError
from app.privacy.crypto import Crypto, CryptoError, normalize_phone

pytestmark = pytest.mark.unit

OLD_KEY = Fernet.generate_key().decode()
NEW_KEY = Fernet.generate_key().decode()


@pytest.fixture
def crypto() -> Crypto:
    return Crypto([NEW_KEY], hmac_key="test-hmac-key")


def test_roundtrip(crypto):
    token = crypto.encrypt("5ABC12D")
    assert token != "5ABC12D" and "5ABC12D" not in token
    assert crypto.decrypt(token) == "5ABC12D"


def test_same_value_gives_different_ciphertexts(crypto):
    # Fernet hər dəfə təsadüfi IV istifadə edir: şifrəli mətndən eyni dəyəri tanımaq olmur
    assert crypto.encrypt("Nizami küç. 5") != crypto.encrypt("Nizami küç. 5")


def test_wrong_key_raises_crypto_error(crypto):
    token = crypto.encrypt("gizli")
    with pytest.raises(CryptoError):
        Crypto([OLD_KEY], hmac_key="k").decrypt(token)


def test_key_rotation():
    token_with_old = Crypto([OLD_KEY], hmac_key="k").encrypt("+994501234567")
    rotating = Crypto([NEW_KEY, OLD_KEY], hmac_key="k")  # yeni açar əsasdır, köhnə hələ oxunur
    assert rotating.decrypt(token_with_old) == "+994501234567"
    rotated = rotating.reencrypt(token_with_old)
    assert Crypto([NEW_KEY], hmac_key="k").decrypt(rotated) == "+994501234567"


def test_blind_index_is_stable_and_kind_specific(crypto):
    assert crypto.blind_index("fin", "5abc12d") == crypto.blind_index("fin", " 5ABC12D ")
    assert crypto.blind_index("fin", "5ABC12D") != crypto.blind_index("phone", "5ABC12D")
    assert len(crypto.blind_index("fin", "5ABC12D")) == 64


def test_blind_index_depends_on_hmac_key():
    a = Crypto([NEW_KEY], hmac_key="a").blind_index("fin", "5ABC12D")
    b = Crypto([NEW_KEY], hmac_key="b").blind_index("fin", "5ABC12D")
    assert a != b


@pytest.mark.parametrize("raw", ["+994 50 123 45 67", "0501234567", "994501234567", "(050) 123-45-67"])
def test_phone_formats_normalize_to_same_value(raw):
    assert normalize_phone(raw) == "+994501234567"


def test_phone_variants_share_blind_index(crypto):
    assert crypto.blind_index("phone", "050 123 45 67") == crypto.blind_index("phone", "+994501234567")


def test_missing_keys_give_config_error():
    with pytest.raises(ConfigError):
        Crypto([], hmac_key="k")
    with pytest.raises(ConfigError):
        Crypto([NEW_KEY], hmac_key="")


def test_invalid_key_gives_config_error():
    with pytest.raises(ConfigError):
        Crypto(["bu-fernet-acari-deyil"], hmac_key="k")


def test_bytes_roundtrip_and_wrong_key(crypto):
    data = b"%PDF-1.4 \x00\xff fayl"
    token = crypto.encrypt_bytes(data)
    assert data not in token and crypto.decrypt_bytes(token) == data
    with pytest.raises(CryptoError):
        Crypto([OLD_KEY], hmac_key="k").decrypt_bytes(token)

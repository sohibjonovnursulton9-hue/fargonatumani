"""Identity input validation and encryption boundary checks."""

import pytest

from app.bot import normalize_birth_date, normalize_passport
from app.pii_crypto import decrypt_identity, encrypt_identity


def test_passport_and_birth_date_validation():
    assert normalize_passport(" aa 1234567 ") == "AA1234567"
    assert normalize_passport("AA123456") is None
    assert normalize_passport("AA12345678") is None
    assert normalize_birth_date("29.02.2000") == "2000-02-29"
    assert normalize_birth_date("31.02.2000") is None
    assert normalize_birth_date("01.01.1899") is None
    assert normalize_birth_date("01.01.2099") is None


def test_identity_ciphertext_does_not_contain_plaintext():
    encrypted = encrypt_identity("AA1234567")
    assert encrypted.startswith("enc:v1:")
    assert "AA1234567" not in encrypted
    assert decrypt_identity(encrypted) == "AA1234567"
    with pytest.raises(ValueError):
        decrypt_identity("AA1234567")

"""Encrypt identity fields before they reach database storage."""

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.types import Text, TypeDecorator

from app.config import get_settings


def _cipher() -> Fernet:
    key = get_settings().pii_encryption_key
    if not key:
        raise RuntimeError("PII_ENCRYPTION_KEY is required for identity fields")
    return Fernet(key.encode("ascii"))


def encrypt_identity(value: str) -> str:
    return "enc:v1:" + _cipher().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_identity(value: str) -> str:
    if not value.startswith("enc:v1:"):
        raise ValueError("Unencrypted identity data is not supported")
    try:
        return _cipher().decrypt(value[7:].encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Identity data could not be decrypted") from exc


class EncryptedIdentity(TypeDecorator):
    """Keep ORM access transparent while storing only ciphertext."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return encrypt_identity(value) if value is not None else None

    def process_result_value(self, value, dialect):
        return decrypt_identity(value) if value is not None else None

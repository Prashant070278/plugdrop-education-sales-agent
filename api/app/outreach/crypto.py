"""Encrypt OAuth tokens at rest (Fernet / AES-128-CBC + HMAC). Passwords are never stored."""

import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings


class TokenKeyMissing(RuntimeError):
    pass


def _fernet() -> Fernet:
    key = get_settings().token_encryption_key
    if not key:
        raise TokenKeyMissing(
            'TOKEN_ENCRYPTION_KEY is not set. Generate one with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        )
    return Fernet(key.encode())


def encrypt_tokens(tokens: dict[str, Any]) -> str:
    return _fernet().encrypt(json.dumps(tokens).encode()).decode()


def decrypt_tokens(ciphertext: str) -> dict[str, Any]:
    try:
        return json.loads(_fernet().decrypt(ciphertext.encode()))
    except InvalidToken as e:
        raise TokenKeyMissing("Stored mailbox tokens cannot be decrypted with the current TOKEN_ENCRYPTION_KEY") from e

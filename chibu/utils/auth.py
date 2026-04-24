"""Authentication token utilities for Chibu agents."""

import secrets
import string

_ALPHABET = string.ascii_letters + string.digits  # a-z A-Z 0-9
TOKEN_LENGTH = 40


def generate_token() -> str:
    """Return a cryptographically random 40-character alphanumeric token."""
    return "".join(secrets.choice(_ALPHABET) for _ in range(TOKEN_LENGTH))


def validate_token(token: str) -> bool:
    """Check structural validity (length + charset) of an auth token."""
    if len(token) != TOKEN_LENGTH:
        return False
    return all(c in _ALPHABET for c in token)

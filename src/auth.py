"""Authentication helpers: password hashing/verification and input validation."""

import os

import bcrypt

# Allow tests to override the bcrypt work factor via environment variable.
# Production default is 12 (bcrypt's own default). Tests set this to 4
# (the minimum) to keep the test suite fast.
_BCRYPT_ROUNDS = int(os.environ.get("BCRYPT_LOG_ROUNDS", "12"))


def hash_password(plaintext: str) -> str:
    """Return a bcrypt hash of plaintext. Never returns the plaintext itself.

    bcrypt silently truncates inputs longer than 72 bytes; we truncate
    explicitly on the encoded bytes so the behaviour is predictable.
    """
    encoded = plaintext.encode()[:72]
    hashed_bytes = bcrypt.hashpw(encoded, bcrypt.gensalt(rounds=_BCRYPT_ROUNDS))
    return hashed_bytes.decode()


def _password_bytes(plaintext: str) -> bytes:
    """Return the UTF-8 encoding of plaintext truncated to 72 bytes."""
    return plaintext.encode()[:72]


def verify_password(plaintext: str, hashed: str) -> bool:
    """Return True iff plaintext matches the bcrypt hash."""
    return bcrypt.checkpw(_password_bytes(plaintext), hashed.encode())


def validate_email(email: str) -> bool:
    """Return True iff email contains exactly one '@' and a non-empty domain part."""
    parts = email.split("@")
    if len(parts) != 2:
        return False
    local, domain = parts
    return bool(local) and bool(domain)


def validate_password(password: str) -> bool:
    """Return True iff password is at least 8 characters long."""
    return len(password) >= 8

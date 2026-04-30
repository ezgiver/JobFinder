"""Unit and property-based tests for src/auth.py."""

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from src.auth import hash_password, validate_email, validate_password, verify_password


# ---------------------------------------------------------------------------
# Task 3.3 — Unit tests
# ---------------------------------------------------------------------------


class TestHashPassword:
    def test_returns_string(self):
        result = hash_password("mysecret")
        assert isinstance(result, str)

    def test_differs_from_plaintext(self):
        plaintext = "mysecret"
        assert hash_password(plaintext) != plaintext

    def test_two_hashes_differ(self):
        # bcrypt uses a random salt each time
        assert hash_password("mysecret") != hash_password("mysecret")


class TestVerifyPassword:
    def test_correct_password_returns_true(self):
        plaintext = "correct_password"
        hashed = hash_password(plaintext)
        assert verify_password(plaintext, hashed) is True

    def test_wrong_password_returns_false(self):
        hashed = hash_password("correct_password")
        assert verify_password("wrong_password", hashed) is False

    def test_empty_wrong_password_returns_false(self):
        hashed = hash_password("correct_password")
        assert verify_password("", hashed) is False


class TestValidateEmail:
    def test_valid_email_accepted(self):
        assert validate_email("user@example.com") is True

    def test_no_at_sign_rejected(self):
        assert validate_email("notanemail") is False

    def test_empty_local_part_rejected(self):
        assert validate_email("@nodomain") is False

    def test_two_at_signs_rejected(self):
        assert validate_email("two@@at.com") is False

    def test_at_only_rejected(self):
        assert validate_email("@") is False

    def test_trailing_at_rejected(self):
        assert validate_email("user@") is False


class TestValidatePassword:
    def test_eight_chars_accepted(self):
        assert validate_password("password1") is True  # 9 chars, also fine

    def test_exactly_eight_chars_accepted(self):
        assert validate_password("12345678") is True  # exactly 8

    def test_five_chars_rejected(self):
        assert validate_password("short") is False

    def test_empty_string_rejected(self):
        assert validate_password("") is False

    def test_seven_chars_rejected(self):
        assert validate_password("1234567") is False


# ---------------------------------------------------------------------------
# Task 3.4 — Property 1: Password hashing round-trip
# Validates: Requirements 1.6, 1.7, 2.4, 2.5
# ---------------------------------------------------------------------------


@given(st.text(min_size=1).filter(lambda p: len(p.encode()) <= 72))
@settings(max_examples=100)
def test_hash_never_equals_plaintext(p: str):
    """**Validates: Requirements 1.6**"""
    assert hash_password(p) != p


@given(st.text(min_size=1).filter(lambda p: len(p.encode()) <= 72))
@settings(max_examples=100)
def test_verify_correct_password_always_true(p: str):
    """**Validates: Requirements 1.7, 2.5**"""
    assert verify_password(p, hash_password(p)) is True


@given(
    st.text(min_size=1).filter(lambda p: len(p.encode()) <= 72),
    st.text(min_size=1).filter(lambda p: len(p.encode()) <= 72),
)
@settings(max_examples=100)
def test_verify_wrong_password_always_false(p: str, q: str):
    """**Validates: Requirements 2.4**"""
    assume(p != q)
    assert verify_password(q, hash_password(p)) is False


# ---------------------------------------------------------------------------
# Task 3.5 — Property 3: Short password rejection
# Validates: Requirements 1.4
# ---------------------------------------------------------------------------


@given(st.text(max_size=7))
@settings(max_examples=100)
def test_short_password_always_rejected(password: str):
    """**Validates: Requirements 1.4**"""
    assert validate_password(password) is False


# ---------------------------------------------------------------------------
# Task 3.6 — Property 4: Malformed email rejection
# Validates: Requirements 1.5
# ---------------------------------------------------------------------------


@given(st.text().filter(lambda s: "@" not in s))
@settings(max_examples=100)
def test_email_without_at_always_rejected(s: str):
    """**Validates: Requirements 1.5**"""
    assert validate_email(s) is False


@given(st.text(min_size=1).filter(lambda s: "@" not in s))
@settings(max_examples=100)
def test_email_with_empty_domain_always_rejected(local: str):
    """Strings of the form 'local@' (empty domain) must be rejected.
    **Validates: Requirements 1.5**"""
    assert validate_email(f"{local}@") is False

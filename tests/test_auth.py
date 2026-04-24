"""Tests for auth token generation and validation."""

import pytest
from chibu.utils.auth import TOKEN_LENGTH, generate_token, validate_token


def test_generated_token_has_correct_length():
    token = generate_token()
    assert len(token) == TOKEN_LENGTH, f"Expected {TOKEN_LENGTH} chars, got {len(token)}"


def test_generated_token_is_alphanumeric():
    token = generate_token()
    assert token.isalnum(), f"Token contains non-alphanumeric characters: {token!r}"


def test_tokens_are_unique():
    tokens = {generate_token() for _ in range(1000)}
    assert len(tokens) == 1000, "Collision in 1000 generated tokens — entropy too low"


def test_validate_token_accepts_valid():
    token = generate_token()
    assert validate_token(token) is True


def test_validate_token_rejects_short():
    assert validate_token("abc123") is False


def test_validate_token_rejects_special_chars():
    bad = "A" * 39 + "!"
    assert validate_token(bad) is False


def test_validate_token_rejects_wrong_length():
    assert validate_token("A" * 41) is False
    assert validate_token("A" * 39) is False

"""Redaction: secrets and PII removed, line count preserved, false positives low."""

from __future__ import annotations

from retrieval.redact import redact


def test_github_token_redacted_keeps_prefix():
    out, n = redact("token = ghp_" + "a" * 36)
    assert n >= 1
    assert "ghp_[REDACTED]" in out
    assert "a" * 36 not in out


def test_stripe_live_key_redacted():
    out, n = redact("STRIPE=sk_live_" + "0" * 24)
    assert n >= 1
    assert "0" * 24 not in out


def test_generic_secret_assignment_redacted():
    out, n = redact('password = "hunter2secret"')
    assert n == 1
    assert "hunter2secret" not in out
    assert 'password = "[REDACTED]"' in out


def test_credit_card_redacted_via_luhn():
    # 4111 1111 1111 1111 is the canonical Luhn-valid test Visa number.
    out, n = redact("Customer card 4111 1111 1111 1111 on file.")
    assert n >= 1
    assert "4111" not in out
    assert "[REDACTED CARD]" in out


def test_order_id_is_not_mistaken_for_a_card():
    # A long-but-not-Luhn digit run (order id) must survive untouched.
    text = "Order 8821399120014455 shipped late."
    out, n = redact(text)
    assert "8821399120014455" in out or "[REDACTED CARD]" not in out


def test_email_and_phone_redacted():
    out, n = redact("Reach me at jane.doe@example.com or (415) 555-0132.")
    assert "[REDACTED EMAIL]" in out
    assert "[REDACTED PHONE]" in out
    assert "jane.doe@example.com" not in out


def test_sku_and_prose_survive():
    text = "Reviews for the X230 laptop mention slow North fulfilment."
    out, n = redact(text)
    assert n == 0
    assert out == text


def test_line_count_preserved():
    text = "line one\npassword = \"supersecretvalue\"\nline three\n"
    out, _ = redact(text)
    assert out.count("\n") == text.count("\n")


def test_private_key_block_body_redacted_lines_kept():
    text = (
        "-----BEGIN PRIVATE KEY-----\n"
        "MIIBVgIBADANBgkqhkiG9w0BAQEFAASC\n"
        "-----END PRIVATE KEY-----\n"
    )
    out, n = redact(text)
    assert n >= 1
    assert "MIIBVgIBADAN" not in out
    assert "BEGIN PRIVATE KEY" in out
    assert out.count("\n") == text.count("\n")

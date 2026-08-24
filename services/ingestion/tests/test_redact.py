"""Offline tests for redaction: secrets + PII, line-count preservation."""

from __future__ import annotations

from ingestion.redact import redact_record, redact_text


def test_secret_tokens_redacted_with_prefix() -> None:
    src = "token = ghp_" + "a" * 36 + " end"
    out = redact_text(src)
    assert "ghp_[REDACTED]" in out.text
    assert "a" * 36 not in out.text
    assert out.count >= 1


def test_email_and_phone_redacted() -> None:
    src = "Reach me at jane.doe@example.com or +1-415-555-2671 today."
    out = redact_text(src)
    assert "jane.doe@example.com" not in out.text
    assert "555-2671" not in out.text
    assert "[REDACTED-PHONE]" in out.text
    assert out.text.startswith("Reach me at j***@[REDACTED]")


def test_card_number_luhn_gated() -> None:
    valid = "card 4111 1111 1111 1111 please"   # passes Luhn
    invalid = "order 1234 5678 9012 3456 ref"    # fails Luhn -> not a card
    assert "[REDACTED-CARD]" in redact_text(valid).text
    assert "[REDACTED-CARD]" not in redact_text(invalid).text


def test_line_count_preserved() -> None:
    src = "line1 sk-" + "b" * 24 + "\nline2 a@b.com\nline3\n"
    out = redact_text(src)
    assert out.text.count("\n") == src.count("\n")


def test_private_key_block_redacted_linewise() -> None:
    src = (
        "-----BEGIN PRIVATE KEY-----\n"
        "MIIBVAIBADANBgkqhkiG9w0BAQEFAASC\n"
        "AnotherSecretLine\n"
        "-----END PRIVATE KEY-----\n"
    )
    out = redact_text(src)
    assert "MIIBVAIBADANBgkqhkiG9w0BAQEFAASC" not in out.text
    assert out.text.count("\n") == src.count("\n")
    assert "BEGIN PRIVATE KEY" in out.text  # boundary kept


def test_redact_record_masks_name_and_contacts() -> None:
    record = {
        "customer_id": "7",
        "full_name": "Jane Doe",
        "email": "jane.doe@example.com",
        "phone": "+1-415-555-2671",
        "region": "North",  # not PII, passes through
    }
    clean, n = redact_record(record)
    assert clean["full_name"] == "[REDACTED-NAME]"
    assert "@example.com" not in str(clean["email"])
    assert clean["phone"] == "[REDACTED-PHONE]"
    assert clean["region"] == "North"
    assert clean["customer_id"] == "7"
    assert n >= 3

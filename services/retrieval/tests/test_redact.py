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


# --- shapes that reached the vector store before phase 2 ---------------------
# Each of these was found in a stored chunk and rendered back to a user in a
# citation snippet. They are regression tests, not hypotheticals.


def test_a_hyphenated_api_key_is_redacted() -> None:
    """`sk-live-…` and friends.

    The token rules only knew the underscore spelling (`sk_live_…`), so every
    hyphenated key - which is what most hosted model APIs now issue - matched
    no rule at all. Worse than missing it: the bare digits inside were then
    eaten by the phone rule, so the key emerged as
    `sk-live-ABCD[REDACTED PHONE]abcdef`, still a secret and now corrupted.
    """
    for key in (
        "sk-live-ABCD1234efgh5678abcdef",
        "sk-proj-aBcD1234EfGh5678IjKl9012MnOp",
        "rk-test-ZZZZ9999yyyy8888xxxx",
        "sk-abcdefghij0123456789ABCDEFGHIJ",
    ):
        out = redact(f"Internal API key: {key} end")
        text = out[0] if isinstance(out, tuple | list) else out
        assert key not in text, f"{key} survived redaction"
        assert "REDACTED" in text


def test_an_international_phone_number_is_redacted() -> None:
    """`+91 98765 43210` is the shape of every number in this demo's own data.

    The original rule matched only 3-3-4 grouping, so the numbers most likely
    to appear in a document uploaded to this product were the ones it missed.
    """
    for number in ("+91 98765 43210", "+44 20 7946 0958", "+1 415 555 2671"):
        out = redact(f"Phone: {number} Resolution: refunded.")
        text = out[0] if isinstance(out, tuple | list) else out
        assert "[REDACTED PHONE]" in text
        assert number not in text


def test_a_bearer_token_is_redacted() -> None:
    out = redact("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9abcdefghijklmnop")
    text = out[0] if isinstance(out, tuple | list) else out
    assert "eyJhbGciOiJIUzI1NiJ9abcdefghijklmnop" not in text


def test_ordinary_business_content_still_survives() -> None:
    """The other half of the job, and the easier one to break.

    A redactor that eats order ids, SKUs and figures is a redactor someone
    turns off, which protects nobody. The new patterns require a `+` on a
    phone and a known key prefix precisely so that a long run of digits in a
    retail document is left alone.
    """
    line = "Order ORD-88213 shipped, SKU 4051234 qty 12, revenue 1899, ref 2026-04-01"
    out = redact(line)
    text = out[0] if isinstance(out, tuple | list) else out
    assert text == line, f"redaction damaged legitimate content: {text}"

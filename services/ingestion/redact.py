"""Redaction at ingestion — the single place secrets and PII are removed before
anything is landed in ``raw`` or handed to the embedder (``docs/03`` §4).

Adapted from rememory's ``indexer/redact.py`` (the high-confidence secret token
formats and the line-count-preserving private-key handling are reused directly),
extended with the PII rules docs/03 §4 requires for retail data: email addresses,
phone numbers, and card-like numbers gated by a Luhn check. As in rememory, a
short recognizable prefix is kept (``ghp_[REDACTED]``, ``****@[REDACTED]``) so a
value stays *findable* ("where is the customer contact") without being exposed,
and redaction never changes the line count so document chunk offsets and citation
line numbers stay correct.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# --- high-confidence secret token formats (reused from rememory) ---------------
# Format-anchored: they match the secret itself, not a variable name, so they run
# on everything with essentially no false positives.
_TOKEN_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("aws-access-key", re.compile(r"\b(A3T[A-Z0-9]|AKIA|ASIA|ABIA|ACCA)[A-Z0-9]{16}\b")),
    ("github-token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b")),
    ("github-pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b")),
    ("openai-key", re.compile(r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{20,}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("stripe-key", re.compile(r"\b[sr]k_live_[A-Za-z0-9]{20,}\b")),
    ("gcp-api-key", re.compile(r"\bAIza[A-Za-z0-9_-]{35}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
]

_KEY_BLOCK_BOUNDARY = re.compile(r"-----(BEGIN|END) [A-Z0-9 ]*PRIVATE KEY( BLOCK)?-----")

_ASSIGNMENT = re.compile(
    r"""(?ix)
    \b(?P<name>[A-Z0-9_.-]*(?:secret|passwd|password|api[_-]?key|access[_-]?key|
       auth[_-]?token|private[_-]?key|client[_-]?secret)[A-Z0-9_.-]*)
    (?P<sep>\s*[:=]\s*|\s*=>\s*)
    (?P<q>["'])(?P<value>[^"'\n]{8,})(?P=q)
    """,
)

# --- PII patterns (retail-specific additions, docs/03 §4) ----------------------
_EMAIL = re.compile(r"\b([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
# Phone: optional +cc, then 7–14 digits with common separators. Deliberately
# conservative to avoid eating order ids; requires at least one separator or a
# leading '+' so bare integers are not matched.
_PHONE = re.compile(
    r"(?<!\w)(\+?\d{1,3}[ .-]\(?\d{2,4}\)?(?:[ .-]\d{2,4}){1,4}|\+\d{9,14})(?!\w)"
)
# Candidate card-like runs (13–19 digits, optional separators); confirmed by Luhn.
_CARD_CANDIDATE = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")


@dataclass
class RedactionResult:
    text: str
    count: int


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = ord(ch) - 48
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def redact_text(source: str) -> RedactionResult:
    """Redact secrets + PII in free text. Never changes the line count."""
    count = 0

    def token_sub(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        token = match.group(0)
        return token[:4] + "[REDACTED]"

    for _, pattern in _TOKEN_PATTERNS:
        source = pattern.sub(token_sub, source)

    def assign_sub(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        q = match.group("q")
        return f"{match.group('name')}{match.group('sep')}{q}[REDACTED]{q}"

    source = _ASSIGNMENT.sub(assign_sub, source)

    def email_sub(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return f"{match.group(1)}***@[REDACTED]"

    source = _EMAIL.sub(email_sub, source)

    def phone_sub(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return "[REDACTED-PHONE]"

    source = _PHONE.sub(phone_sub, source)

    def card_sub(match: re.Match[str]) -> str:
        nonlocal count
        raw = match.group(0)
        digits = re.sub(r"\D", "", raw)
        if not _luhn_ok(digits):
            return raw  # not a real card number; leave it
        count += 1
        return "[REDACTED-CARD]"

    source = _CARD_CANDIDATE.sub(card_sub, source)

    # Private key blocks, line-wise so line count is preserved exactly.
    if "PRIVATE KEY" in source:
        lines = source.split("\n")
        inside = False
        for i, line in enumerate(lines):
            boundary = _KEY_BLOCK_BOUNDARY.search(line)
            if boundary:
                inside = boundary.group(1) == "BEGIN"
                continue
            if inside and line.strip():
                lines[i] = "[REDACTED PRIVATE KEY MATERIAL]"
                count += 1
        source = "\n".join(lines)

    return RedactionResult(text=source, count=count)


# Column names whose *entire value* is masked (names are not pattern-detectable).
_NAME_COLUMNS = {"full_name", "name", "contact_name", "customer_name"}
# Columns that hold free-form contact strings — run the full text redactor.
_PII_TEXT_COLUMNS = {"email", "phone", "body", "subject", "title", "notes", "address"}


def redact_record(
    record: dict[str, object], pii_columns: set[str] | None = None
) -> tuple[dict[str, object], int]:
    """Redact PII in a structured record before it lands in ``raw``.

    ``full_name``-style columns are masked wholesale; text/contact columns are
    run through :func:`redact_text`. Non-PII columns pass through untouched.
    """
    pii_columns = pii_columns or (_NAME_COLUMNS | _PII_TEXT_COLUMNS)
    out: dict[str, object] = {}
    total = 0
    for key, value in record.items():
        if key in _NAME_COLUMNS and isinstance(value, str) and value.strip():
            out[key] = "[REDACTED-NAME]"
            total += 1
        elif key in pii_columns and isinstance(value, str) and value:
            result = redact_text(value)
            out[key] = result.text
            total += result.count
        else:
            out[key] = value
    return out, total

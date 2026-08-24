"""Redaction at ingestion — secrets and PII removed before they reach Qdrant.

Retrieved text enters an LLM prompt, and from there a transcript that may leave
the machine. A credential or a customer's card number that never enters the
store can never be retrieved into that prompt. So redaction runs BEFORE
chunking, embedding, or storage (docs/04-retrieval-rag.md §2.4), mirroring
rememory's ``redact()``-before-``chunk()`` ordering.

Design constraints (from rememory, extended with the PII a support corpus
carries — cards, emails, phone numbers):

* Precision over recall for GENERIC patterns (an assignment named ``api_key``),
  because false positives eat real content. Well-known token FORMATS (AWS,
  GitHub, Slack, Stripe, private-key blocks) are unambiguous and match
  aggressively.
* The replacement keeps a short prefix (``ghp_[REDACTED]``, ``sk_live_…``) so a
  question like "where is the payment key configured" stays findable — the
  location survives, the secret does not.
* Redaction never changes the line count, so any downstream line references
  stay correct.

Vendor note: only non-AI service credentials are matched here. No AI-vendor key
formats are referenced.
"""

from __future__ import annotations

import re

# High-confidence, format-anchored token patterns — they identify the SECRET
# itself, not a variable name, so they run on every document with essentially no
# false positives.
_TOKEN_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("aws-access-key", re.compile(r"\b(A3T[A-Z0-9]|AKIA|ASIA|ABIA|ACCA)[A-Z0-9]{16}\b")),
    ("github-token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b")),
    ("github-pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("stripe-key", re.compile(r"\b[sr]k_live_[A-Za-z0-9]{20,}\b")),
    ("gcp-api-key", re.compile(r"\bAIza[A-Za-z0-9_-]{35}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
]

# Private-key blocks: redact the body, keep the BEGIN/END lines (line counts
# must not change). Applied line-wise below.
_KEY_BLOCK_BOUNDARY = re.compile(r"-----(BEGIN|END) [A-Z0-9 ]*PRIVATE KEY( BLOCK)?-----")

# Generic secret assignment: `password = "..."`, `api_key: '...'`. Requires a
# secret-ish name AND a quoted value of plausible length, which keeps false
# positives near zero.
_ASSIGNMENT = re.compile(
    r"""(?ix)
    \b(?P<name>[A-Z0-9_.-]*(?:secret|passwd|password|api[_-]?key|access[_-]?key|
       auth[_-]?token|private[_-]?key|client[_-]?secret)[A-Z0-9_.-]*)
    (?P<sep>\s*[:=]\s*|\s*=>\s*)
    (?P<q>["'])(?P<value>[^"'\n]{8,})(?P=q)
    """,
)

# --- PII, specific to a support / review corpus -----------------------------
# Payment cards: 13–16 digits, optionally space/dash grouped, validated by the
# Luhn checksum so incidental long numbers (order ids, SKUs) are not eaten.
_CARD = re.compile(r"\b(?:\d[ -]?){13,16}\b")
# Emails and phone numbers. Kept conservative: a phone needs a plausible run of
# digits with separators, not any 7-digit integer.
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE = re.compile(r"(?<!\d)(?:\+?\d{1,3}[ .-]?)?(?:\(?\d{3}\)?[ .-]?)\d{3}[ .-]?\d{4}(?!\d)")


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def redact(source: str) -> tuple[str, int]:
    """Return ``(redacted_source, redaction_count)``. Never changes line count."""
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
        return (
            f"{match.group('name')}{match.group('sep')}"
            f"{match.group('q')}[REDACTED]{match.group('q')}"
        )

    source = _ASSIGNMENT.sub(assign_sub, source)

    def card_sub(match: re.Match[str]) -> str:
        nonlocal count
        digits = re.sub(r"\D", "", match.group(0))
        if not (13 <= len(digits) <= 16 and _luhn_ok(digits)):
            return match.group(0)  # not a real card — leave order ids / SKUs alone
        count += 1
        return "[REDACTED CARD]"

    source = _CARD.sub(card_sub, source)

    def email_sub(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return "[REDACTED EMAIL]"

    source = _EMAIL.sub(email_sub, source)

    def phone_sub(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return "[REDACTED PHONE]"

    source = _PHONE.sub(phone_sub, source)

    # Private-key blocks, line-wise so the line count is preserved exactly.
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

    return source, count

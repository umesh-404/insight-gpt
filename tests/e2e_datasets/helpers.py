"""Shared test helpers: login, upload, poll, ask, and number extraction.

Kept out of ``conftest.py`` so ``scripts/demo_check.py`` can reuse the same
polling and number-checking logic rather than growing a second copy that drifts
from what the tests assert.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# --- facts that can only have come from the fixture files -------------------
# A passing retrieval has to surface one of these. They are deliberately
# unguessable: no model and no fixture corpus contains them, so an answer that
# quotes one demonstrably read the uploaded file.
LIGHTHOUSE_FILE = "ketterdam_lighthouse.md"
LIGHTHOUSE_KEEPER = "Hestia Marlow"
LIGHTHOUSE_FACT_TOKENS = ("Hestia Marlow", "14 nautical miles", "KPL-1965-04")

RIDERSHIP_FILE = "harbour_ridership.csv"
RIDERSHIP_MARCH_HARBOUR_LOOP = "24719"
RIDERSHIP_TOKENS = ("Harbour Loop", "Quarry Line", RIDERSHIP_MARCH_HARBOUR_LOOP)

SECRET_FILE = "deploy_notes.txt"
#: The secret BODIES, not the retained prefixes. The redactor deliberately keeps
#: a short prefix (``AIza[REDACTED]``) so a question about where a key is
#: configured stays answerable, so asserting on ``AIza`` would fail for the
#: wrong reason. These bodies must never appear in the vector store.
SECRET_BODIES = (
    "AIzaFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAK",
    "FAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAK",
    "sk_live_FAKEFAKEFAKEFAKEFAKE",
    "harbour-ops@example.com",
)
#: Content from the same file that SHOULD survive redaction — a redactor that
#: passes by deleting the document would otherwise look like a success.
SECRET_FILE_SURVIVING_TEXT = "ninety seconds"

ANALYST = ("analyst@insightgpt.dev", "analyst-pass")
VIEWER = ("viewer@insightgpt.dev", "viewer-pass")
ADMIN = ("admin@insightgpt.dev", "admin-pass")

API = "/api/v1"


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def login(client, email: str, password: str) -> str:
    response = client.post(f"{API}/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def upload(client, token: str, files: list[tuple[str, bytes]], *, content_type: str = "text/plain"):
    """POST /datasets with ``files`` as ``(filename, bytes)`` pairs."""
    payload = [("files", (name, data, content_type)) for name, data in files]
    return client.post(f"{API}/datasets", files=payload, headers=auth(token))


def poll_status(
    client, token: str, dataset_id: str, *, timeout: float = 120.0, interval: float = 0.25
) -> dict[str, Any]:
    """Poll until the dataset reaches a terminal status, or the timeout.

    Returns the last ``DatasetJob`` seen. A job still at ``pending`` or
    ``indexing`` when the timeout expires is returned as-is so the caller can
    assert on it — contract §6.6 says that state is itself a defect, and
    swallowing it in a helper would hide exactly the thing being tested.
    """
    deadline = time.monotonic() + timeout
    body: dict[str, Any] = {}
    while time.monotonic() < deadline:
        response = client.get(f"{API}/datasets/{dataset_id}/status", headers=auth(token))
        assert response.status_code == 200, response.text
        body = response.json()
        if body["status"] in ("ready", "failed"):
            return body
        time.sleep(interval)
    return body


def ask(client, token: str, question: str) -> dict[str, Any]:
    """POST /ask in single-response JSON mode and return the envelope."""
    response = client.post(
        f"{API}/ask",
        json={"question": question, "stream": False},
        headers={**auth(token), "Accept": "application/json"},
    )
    assert response.status_code == 200, response.text
    return response.json()


# --- grounding checks -------------------------------------------------------

#: Absolute numbers in prose. Percentages are excluded (they are ratios derived
#: from grounded values and are not expected to appear verbatim), as are
#: four-digit years and citation markers, following the same rules as
#: ``tests/eval/faithfulness.py`` so the two harnesses agree about what counts
#: as a fabricated figure.
_NUMBER = re.compile(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)(?![\w.%])")
_YEAR = re.compile(r"^(19|20)\d{2}$")


def absolute_numbers(text: str) -> set[str]:
    """Numbers in ``text`` that a reader would take as a reported figure."""
    found = set()
    for raw in _NUMBER.findall(text or ""):
        cleaned = raw.replace(",", "")
        if _YEAR.match(cleaned):
            continue
        if cleaned.rstrip("0").rstrip(".") in ("", "0", "1", "2", "3", "4", "5"):
            # Small ordinals and counts ("2 documents", "one of 3 routes") are
            # structural, not reported figures.
            continue
        found.add(cleaned)
    return found


def numbers_in_evidence(envelope: dict[str, Any], *extra_texts: str) -> set[str]:
    """Every number the answer is ENTITLED to quote: table cells, citation
    titles and snippets, plus any extra source text the caller supplies."""
    evidence: list[str] = list(extra_texts)
    for table in envelope.get("tables", []):
        evidence.extend(str(cell) for row in table.get("rows", []) for cell in row)
        evidence.extend(str(column) for column in table.get("columns", []))
    for citation in envelope.get("citations", []):
        evidence.extend(
            str(citation.get(field) or "") for field in ("title", "snippet", "doc_id", "date")
        )
    numbers: set[str] = set()
    for text in evidence:
        for raw in _NUMBER.findall(text):
            numbers.add(raw.replace(",", ""))
    return numbers


def citation_mentions(envelope: dict[str, Any], needle: str) -> bool:
    """True when some citation identifies ``needle`` (a dataset name).

    Checks every string field rather than only ``title``, because the contract
    (§6.2) requires the dataset to be *nameable* from the citation and does not
    dictate which field carries it.
    """
    lowered = needle.lower()
    for citation in envelope.get("citations", []):
        for value in citation.values():
            if isinstance(value, str) and lowered in value.lower():
                return True
    return False

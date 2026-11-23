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

# --- the tabular fixture, and the arithmetic it forces ----------------------
# ``quarry_sales.csv`` exists so a numeric question has ONE right answer that
# this file computes rather than trusts. Three properties were deliberate:
#
# * 600 rows in 33 KB, so the table cannot fit in the handful of chunks a
#   retrieval pass returns — an aggregate over it is either computed over the
#   whole table or it is a sum of a sample, which contract §3.4 forbids;
# * a ``revenue`` column, colliding head-on with the governed metric of the
#   same name, so "total revenue in quarry_sales.csv" is the exact phrasing
#   that §3.1/§3.2 are about;
# * regions and products that appear nowhere in the retail fixture, so a
#   figure or a label from the warehouse is recognisable on sight.
QUARRY_FILE = "quarry_sales.csv"
QUARRY_REGIONS = ("Fjerda", "Ketterdam", "Novyi Zem", "Shu Han")


def quarry_rows() -> list[dict[str, str]]:
    """The fixture, parsed. The source of every expected number below."""
    import csv

    with (FIXTURES / QUARRY_FILE).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _quarry_expectations() -> dict[str, Any]:
    from decimal import Decimal

    rows = quarry_rows()
    per_region: dict[str, Decimal] = {}
    for row in rows:
        per_region[row["region"]] = per_region.get(row["region"], Decimal(0)) + Decimal(
            row["revenue"]
        )
    return {
        "rows": len(rows),
        "total_revenue": sum(Decimal(r["revenue"]) for r in rows),
        "total_units": sum(int(r["units"]) for r in rows),
        "max_revenue": max(Decimal(r["revenue"]) for r in rows),
        "revenue_by_region": per_region,
    }


_QUARRY = _quarry_expectations()

QUARRY_ROWS: int = _QUARRY["rows"]
QUARRY_TOTAL_REVENUE: float = float(_QUARRY["total_revenue"])
QUARRY_TOTAL_UNITS: int = _QUARRY["total_units"]
QUARRY_MAX_REVENUE: float = float(_QUARRY["max_revenue"])
QUARRY_REVENUE_BY_REGION: dict[str, float] = {
    region: float(value) for region, value in _QUARRY["revenue_by_region"].items()
}

#: The same numbers, pinned. Computing the expectation from the fixture is what
#: makes a wrong answer fail; pinning it as well is what makes an edited
#: fixture fail loudly instead of quietly moving the goalposts.
QUARRY_PINNED = {
    "rows": 600,
    "total_revenue": 686566.26,
    "total_units": 14491,
    "max_revenue": 4052.40,
    "Fjerda": 193651.46,
    "Ketterdam": 152133.44,
    "Novyi Zem": 166472.84,
    "Shu Han": 174308.52,
}

#: A region subtotal cannot be read off the table summary the parser emits (it
#: reports whole-column totals only) and cannot be derived from the ~50 rows a
#: retrieved chunk carries. It is therefore the sharpest §3.4 probe available:
#: the honest replies are the exact figure or a decline, and nothing else.
QUARRY_SAMPLE_PROOF_REGION = "Fjerda"

#: Named in questions but never uploaded. §3.1: naming it must abstain.
NEVER_UPLOADED_FILE = "fjerda_ledger_2031.csv"

#: A tabular upload whose CELLS hold secrets, not just its prose. The document
#: path redacts text before embedding; this file asks the same question of the
#: SQL path, where the unit of storage is a table row (§3.5).
LEAKY_FILE = "leaky_orders.csv"

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
# Same trailing-guard reasoning as ``_FIGURE`` below: a sentence-ending period
# must not hide a number from this check, or a fabricated figure in the last
# clause of an answer goes unseen. Percentages stay excluded.
_NUMBER = re.compile(r"(?<![\w.])(\d[\d,]*(?:\.\d+)?)(?![\w%])(?!\.\d)")
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


# --- is the FIGURE right? ----------------------------------------------------
# The phase-2 claim is not "a number came back" but "the number is the one the
# file actually contains". These two functions are what stops a plausible wrong
# total from passing: the expected value is computed from the fixture above, and
# the answer has to state it.

#: A reported figure, with the compact suffixes this product uses in prose
#: (``48.9Cr``, ``6.9 lakh``) and the currency symbols it prefixes them with.
# The trailing guard refuses a following word character and a following
# ``.<digit>``, but NOT a sentence-ending period: this product writes figures in
# the Indian grouping ("6,86,566.26") and usually at the end of a clause, and a
# `(?![\w.])` guard silently matches none of them — which would make every
# assertion below pass for the wrong reason.
_FIGURE = re.compile(
    r"(?<![\w.])(?:₹|rs\.?|inr)?\s*(\d[\d,]*(?:\.\d+)?)\s*"
    r"(cr|crore|crores|l|lakh|lakhs|k|m|mn|million|bn)?(?!\w)(?!\.\d)",
    re.IGNORECASE,
)
_MULTIPLIER = {
    None: 1.0,
    "": 1.0,
    "k": 1e3,
    "l": 1e5,
    "lakh": 1e5,
    "lakhs": 1e5,
    "m": 1e6,
    "mn": 1e6,
    "million": 1e6,
    "cr": 1e7,
    "crore": 1e7,
    "crores": 1e7,
    "bn": 1e9,
}


def reported_figures(text: str) -> list[float]:
    """Every number in ``text`` as the value a reader would take it to mean."""
    values: list[float] = []
    for raw, suffix in _FIGURE.findall(text or ""):
        try:
            number = float(raw.replace(",", ""))
        except ValueError:  # pragma: no cover - the regex already constrains it
            continue
        values.append(number * _MULTIPLIER.get((suffix or "").lower(), 1.0))
    return values


def states_figure(text: str, expected: float, *, tolerance: float = 0.005) -> bool:
    """True when ``text`` reports ``expected``.

    The tolerance exists because this product legitimately rounds a figure for
    prose ("₹6.87 lakh" for 686,566.26), and an assertion that demanded the
    exact decimals would fail a correct answer. It is deliberately tight —
    half a percent — so a figure computed from a *sample* of the table, which
    is the §3.4 failure this is guarding, is nowhere near it: the smallest
    region subtotal in the fixture is 22% of the total, so any partial sum
    misses by orders of magnitude more than the tolerance allows.
    """
    window = max(abs(expected) * tolerance, 0.01)
    return any(abs(value - expected) <= window for value in reported_figures(text))


def figures_not_in(text: str, allowed: list[float], *, tolerance: float = 0.005) -> list[float]:
    """Figures in ``text`` that match none of ``allowed``.

    Used to catch the number nobody asked for: a confident subtotal invented
    beside a correct total is still a fabrication (§3.8).
    """
    stray: list[float] = []
    for value in reported_figures(text):
        if value in (0.0, 1.0, 2.0) or 1900 <= value <= 2100:
            continue  # ordinals, counts and years are structural, not figures
        if not any(
            abs(value - permitted) <= max(abs(permitted) * tolerance, 0.01)
            for permitted in allowed
        ):
            stray.append(value)
    return stray

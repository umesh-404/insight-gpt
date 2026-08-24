"""Deterministic synthetic retail/e-commerce data generator for InsightGPT.

Produces the operational source shapes defined in ``docs/02-data-model.md`` §2
(customers, products, stores, orders, order_items, inventory) plus document
sources (support tickets, product reviews, quarterly reports). The dataset has
weekly + holiday seasonality and a single **planted dip** in 2026 Q2 concentrated
in the North region x Electronics category (a fulfilment backlog), echoed in the
documents — so "why did sales decline last quarter?" has a real, cross-
referenceable answer (``docs/02-data-model.md`` §8).

Determinism: everything is driven by a single integer seed. The same seed always
produces byte-identical output; no wall-clock reads and no unseeded randomness.
"""

from __future__ import annotations

from .config import GeneratorConfig
from .generate import generate

__all__ = ["GeneratorConfig", "generate"]

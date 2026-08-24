"""CLI: ``python -m data.generator [--seed N] [--out DIR] [--parquet]``.

Deterministic by default (seed 42). Prints a compact summary of what it wrote so
the seed step in ``scripts/seed.py`` and manual runs are legible.
"""

from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

from .config import GeneratorConfig
from .generate import generate


def _parse_date(value: str) -> dt.date:
    return dt.date.fromisoformat(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="data.generator", description=__doc__)
    parser.add_argument("--seed", type=int, default=42, help="deterministic seed")
    parser.add_argument("--out", type=Path, default=None, help="output directory")
    parser.add_argument("--start", type=_parse_date, default=None, help="start date")
    parser.add_argument("--end", type=_parse_date, default=None, help="end date")
    parser.add_argument("--customers", type=int, default=None, help="customer count")
    parser.add_argument("--scale", type=float, default=None, help="volume multiplier")
    parser.add_argument("--parquet", action="store_true", help="also write parquet")
    args = parser.parse_args(argv)

    overrides: dict = {"seed": args.seed}
    if args.start:
        overrides["start_date"] = args.start
    if args.end:
        overrides["end_date"] = args.end
    if args.customers:
        overrides["n_customers"] = args.customers
    if args.scale is not None:
        overrides["volume_scale"] = args.scale
    if args.parquet:
        overrides["write_parquet"] = True

    cfg = GeneratorConfig(**overrides)
    result = generate(cfg, out_dir=args.out)

    print(f"[generator] seed={cfg.seed} -> {result.out_dir}")
    for name, count in result.counts.items():
        print(f"  {name:<24} {count:>8,} rows")
    if cfg.write_parquet:
        state = "written" if result.parquet_written else "skipped (pyarrow missing)"
        print(f"  parquet: {state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

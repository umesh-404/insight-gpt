"""End-to-end demo seed: generate -> load raw -> dbt run -> dbt test.

One re-runnable command that stands up the whole warehouse from nothing
(``docs/03-ingestion-etl.md`` §8). Every step logs what it did and is idempotent
(the generator is deterministic, the loader is delete-then-write, dbt rebuilds
tables/views). The database-dependent steps (raw load, dbt) are **skipped with a
clear message** when Postgres or dbt is not configured, so the generator step
always runs — useful on a laptop with no warehouse yet.

Usage:
    python scripts/seed.py                # full run (skips DB steps if unset)
    python scripts/seed.py --seed 7       # different deterministic dataset
    python scripts/seed.py --skip-dbt     # generate + load, no transform
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_WAREHOUSE_DIR = _REPO_ROOT / "services" / "warehouse"


def _step(n: int, title: str) -> None:
    print(f"\n=== step {n}: {title} ===", flush=True)


def _postgres_configured() -> bool:
    return bool(os.environ.get("POSTGRES_DSN") or os.environ.get("POSTGRES_HOST"))


def run_generate(seed: int, scale: float | None) -> None:
    from data.generator import GeneratorConfig, generate

    overrides: dict = {"seed": seed}
    if scale is not None:
        overrides["volume_scale"] = scale
    result = generate(GeneratorConfig(**overrides))
    total = sum(result.counts.values())
    print(f"generated {total:,} records across {len(result.counts)} files -> {result.out_dir}")


def run_load() -> bool:
    """Load raw via the ingestion service. Returns True if the DB load happened."""
    from services.ingestion.run import run as run_ingest

    stats = run_ingest("full_ingest", "all")
    print(
        f"ingestion status={stats.status} units_loaded={stats.units_loaded} "
        f"rows_loaded={stats.rows_loaded} secrets_redacted={stats.secrets_redacted}"
    )
    if stats.rows_loaded == 0:
        for note in stats.notes[:3]:
            print(f"  - {note}")
    return stats.units_loaded > 0


def run_dbt(command: list[str]) -> bool:
    dbt = shutil.which("dbt")
    if not dbt:
        print("dbt not installed on PATH; skipping. Install dbt-postgres to transform.")
        return False
    full = [
        dbt,
        *command,
        "--project-dir",
        str(_WAREHOUSE_DIR),
        "--profiles-dir",
        str(_WAREHOUSE_DIR),
    ]
    print("$ " + " ".join(full))
    completed = subprocess.run(full, cwd=str(_REPO_ROOT), check=False)
    return completed.returncode == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--scale", type=float, default=None, help="volume multiplier")
    parser.add_argument("--skip-dbt", action="store_true")
    args = parser.parse_args(argv)

    _step(1, "generate synthetic dataset")
    run_generate(args.seed, args.scale)

    if not _postgres_configured():
        print(
            "\nPostgres is not configured (set POSTGRES_DSN or POSTGRES_HOST in .env).\n"
            "Skipping raw load and dbt. The generated CSVs + documents are ready in\n"
            "data/generated/. Configure Postgres and re-run to build the warehouse."
        )
        return 0

    _step(2, "load raw (extract -> redact -> delete-then-write)")
    loaded = run_load()

    if args.skip_dbt:
        print("\n--skip-dbt set; stopping after raw load.")
        return 0
    if not loaded:
        print("\nNo rows landed in raw; skipping dbt so a half-built mart is not published.")
        return 0

    _step(3, "dbt seed + run (staging -> marts + metrics)")
    if not run_dbt(["seed"]):
        return 1
    if not run_dbt(["run"]):
        return 1

    _step(4, "dbt test (data-quality gates)")
    if not run_dbt(["test"]):
        print("dbt tests failed — the mart is NOT considered publishable.")
        return 1

    print("\nSeed complete: warehouse built and tested.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

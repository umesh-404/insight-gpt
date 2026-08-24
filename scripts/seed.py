"""End-to-end demo seed: generate -> load raw + publish documents -> dbt.

One re-runnable command that stands up the whole warehouse from nothing
(``docs/03-ingestion-etl.md`` §8). Every step logs what it did and is idempotent
(the generator is deterministic, the loader is delete-then-write, the document
corpus is rewritten only when it changed, dbt rebuilds tables/views).

Without Postgres the raw load and dbt are **skipped with a clear message** while
the generator and the document hand-off still run — those need no database — so
a laptop with no warehouse still ends up with an indexable corpus. Pass
``--require-postgres`` to turn that skip into a failure (bootstrap does, having
already waited for the database).

Exit code is honest: 0 only when every step it claimed to run actually ran.

Usage:
    python scripts/seed.py                # full run (skips DB steps if unset)
    python scripts/seed.py --seed 7       # different deterministic dataset
    python scripts/seed.py --skip-dbt     # generate + load, no transform
    python scripts/seed.py --require-postgres   # fail if there is no database
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


def run_load(job: str = "full_ingest") -> bool:
    """Load raw + publish the document corpus. True when raw.* is current.

    "Current" deliberately includes *unchanged*: an incremental re-run that
    skips every unit because nothing changed has left raw exactly as correct as
    a run that reloaded it, and must not be mistaken for a failed load.
    """
    from services.ingestion.run import run as run_ingest

    stats = run_ingest(job, "all")
    print(
        f"ingestion status={stats.status} units_loaded={stats.units_loaded} "
        f"units_unchanged={stats.units_unchanged} rows_loaded={stats.rows_loaded} "
        f"secrets_redacted={stats.secrets_redacted}"
    )
    print(
        f"documents: {stats.documents_published} published "
        f"({stats.documents_changed} changed) -> {stats.corpus_path}"
    )
    if not stats.raw_is_current:
        for note in stats.notes[:3]:
            print(f"  - {note}")
    return stats.raw_is_current


def run_dbt(command: list[str]) -> bool:
    dbt = shutil.which("dbt")
    if not dbt:
        # Not "skipping": the caller treats False as a failed step and exits
        # non-zero, because a warehouse with raw data and no marts is not built.
        print(
            "dbt is not on PATH — cannot transform. Install dbt-postgres "
            "(`uv pip install 'dbt-postgres>=1.8,<2.0'`) or run seeding inside "
            "the worker container (`make seed`).",
            file=sys.stderr,
        )
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
    parser.add_argument(
        "--require-postgres",
        action="store_true",
        help="fail instead of skipping when Postgres is not configured "
             "(used by bootstrap, which has already waited for the database)",
    )
    args = parser.parse_args(argv)

    _step(1, "generate synthetic dataset")
    run_generate(args.seed, args.scale)

    if not _postgres_configured():
        message = (
            "Postgres is not configured (set POSTGRES_DSN or POSTGRES_HOST in .env)."
        )
        if args.require_postgres:
            print(f"\n{message}\nRefusing to report success for a warehouse that was "
                  "never built.", file=sys.stderr)
            return 1
        # Still run the document branch: it needs no database, and it is what
        # publishes the corpus retrieval indexes. Saying "skipping raw load and
        # dbt" while silently skipping the hand-off too would be a lie.
        _step(2, "publish redacted document corpus (no database needed)")
        run_load()
        print(
            f"\n{message}\n"
            "Skipped the raw load and dbt. The generated CSVs are in data/generated/\n"
            "and the redacted document corpus is published for retrieval.\n"
            "Configure Postgres and re-run to build the warehouse."
        )
        return 0

    _step(2, "load raw (extract -> redact -> delete-then-write) + publish documents")
    current = run_load()

    if args.skip_dbt:
        print("\n--skip-dbt set; stopping after raw load.")
        return 0
    if not current:
        print(
            "\nraw.* is not current (nothing loaded and nothing was unchanged); "
            "skipping dbt so a half-built mart is not published.",
            file=sys.stderr,
        )
        return 1

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

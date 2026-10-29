#!/usr/bin/env python3
"""Deploy the InsightGPT backend API to Hugging Face Spaces (Docker Space).

Prepares a clean, standalone deployment bundle or pushes directly to your
Hugging Face Space git repository.

Usage:
    # 1. Prepare standalone deployment bundle in dist/hf-space:
    python scripts/deploy_hf.py --space <hf-username>/insight-gpt-api

    # 2. Automatically push to Hugging Face Space:
    python scripts/deploy_hf.py --space <hf-username>/insight-gpt-api --push --token <hf_token>
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = REPO_ROOT / "dist" / "hf-space"

HF_README_TEMPLATE = """---
title: InsightGPT API
emoji: ⚡
colorFrom: indigo
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
---

# InsightGPT API

Grounded, cited conversational analytics and document/file Q&A powered by FastAPI and Gemini.

### Endpoints
- **Health**: `GET /health`
- **Documentation**: `GET /api/v1/docs`
- **Grounded Q&A**: `POST /api/v1/ask`
- **Multipart File Upload & Ask**: `POST /api/v1/ask/upload`

### Environment Variables & Secrets
Configure these in **Settings -> Variables and secrets**:
- `GEMINI_API_KEY` (Secret): Your Google AI Studio API key.
- `LLM_PROVIDER` (Variable): `gemini`
- `LLM_MODEL` (Variable): `gemini-2.5-flash`
- `JWT_SECRET` (Secret): A secure random string for JWT signatures.
- `COOKIE_SECURE` (Variable): `true`
- `COOKIE_SAMESITE` (Variable): `none`
- `CORS_ORIGIN_REGEX` (Variable): `^https://.*\\.vercel\\.app$|^https://.*\\.hf\\.space$`
"""

HF_DOCKERFILE = """# InsightGPT API — Hugging Face Spaces Dockerfile
FROM python:3.12-slim AS build

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_SYSTEM_PYTHON=1 \\
    UV_LINK_MODE=copy \\
    PYTHONDONTWRITEBYTECODE=1 \\
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY services/api ./services/api
COPY services/retrieval ./services/retrieval
COPY config ./config
COPY data ./data

RUN uv pip install --system --no-cache \\
        -e "./services/api[api]" \\
        -e "./services/retrieval"

FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \\
    PYTHONDONTWRITEBYTECODE=1 \\
    PORT=7860 \\
    PYTHONPATH=/home/user/app/services/api:$PYTHONPATH

RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \\
    PATH=/home/user/.local/bin:$PATH

WORKDIR /home/user/app

COPY --from=build /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=build /usr/local/bin /usr/local/bin
COPY --from=build --chown=user:user /app /home/user/app

EXPOSE 7860

HEALTHCHECK --interval=20s --timeout=5s --start-period=30s --retries=3 \\
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:7860/health', timeout=4).status==200 else 1)" || exit 1

CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "7860"]
"""


def _copy_tree_clean(src: Path, dst: Path, ignore_patterns: tuple[str, ...]) -> None:
    """Copy a directory tree, skipping build artifacts and caches."""
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True, exist_ok=True)

    for item in src.rglob("*"):
        rel = item.relative_to(src)
        # Skip ignored directories and files
        if any(ignored in rel.parts for ignored in ignore_patterns):
            continue
        if item.is_dir():
            (dst / rel).mkdir(parents=True, exist_ok=True)
        elif item.is_file():
            (dst / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dst / rel)


def prepare_bundle(out_dir: Path) -> None:
    """Bundle all necessary services, config, and data for Hugging Face Spaces."""
    print(f"[*] Packaging Hugging Face Space bundle into: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)

    ignore_patterns = (
        "__pycache__",
        ".venv",
        "venv",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        "*.egg-info",
        "target",
        "logs",
        ".next",
        "node_modules",
    )

    # Copy services/api
    print("  -> Copying services/api...")
    _copy_tree_clean(REPO_ROOT / "services" / "api", out_dir / "services" / "api", ignore_patterns)

    # Copy services/retrieval
    print("  -> Copying services/retrieval...")
    _copy_tree_clean(
        REPO_ROOT / "services" / "retrieval", out_dir / "services" / "retrieval", ignore_patterns
    )

    # Copy config
    print("  -> Copying config...")
    _copy_tree_clean(REPO_ROOT / "config", out_dir / "config", ignore_patterns)

    # Copy data (seed fixtures)
    print("  -> Copying data (fixtures)...")
    _copy_tree_clean(REPO_ROOT / "data", out_dir / "data", ignore_patterns + ("warehouse", "generated"))

    # Write README.md and Dockerfile
    print("  -> Writing README.md with HF Spaces frontmatter...")
    (out_dir / "README.md").write_text(HF_README_TEMPLATE, encoding="utf-8")

    print("  -> Writing Dockerfile (Port 7860, UID 1000)...")
    (out_dir / "Dockerfile").write_text(HF_DOCKERFILE, encoding="utf-8")

    # Write .dockerignore
    dockerignore = """__pycache__/
*.pyc
.venv/
.pytest_cache/
.ruff_cache/
.git/
"""
    (out_dir / ".dockerignore").write_text(dockerignore, encoding="utf-8")
    print("[+] Bundle successfully created.")


def push_to_space(bundle_dir: Path, space_name: str, token: str | None = None) -> None:
    """Push the prepared bundle to the Hugging Face Space git repo."""
    clean_name = space_name.replace("https://huggingface.co/spaces/", "").strip("/")
    if "/" not in clean_name:
        print(f"Error: Space name must be in format 'username/space-name' (got: {space_name})")
        sys.exit(1)

    user, space = clean_name.split("/", 1)
    if token:
        remote_url = f"https://user:{token}@huggingface.co/spaces/{user}/{space}"
    else:
        remote_url = f"https://huggingface.co/spaces/{user}/{space}"

    public_host = f"https://{user}-{space.replace('_', '-')}.hf.space"
    api_url = f"{public_host}/api/v1"

    print(f"\n[*] Initializing git in {bundle_dir}...")
    subprocess.run(["git", "init"], cwd=bundle_dir, check=True)
    subprocess.run(["git", "checkout", "-B", "main"], cwd=bundle_dir, check=True)
    subprocess.run(["git", "add", "."], cwd=bundle_dir, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Deploy InsightGPT backend to Hugging Face Spaces"],
        cwd=bundle_dir,
        check=False,
    )

    print(f"[*] Setting remote 'origin' -> https://huggingface.co/spaces/{user}/{space}...")
    subprocess.run(["git", "remote", "remove", "origin"], cwd=bundle_dir, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", remote_url], cwd=bundle_dir, check=True)

    print(f"[*] Pushing to Hugging Face Spaces ({user}/{space})...")
    result = subprocess.run(["git", "push", "-u", "origin", "main", "--force"], cwd=bundle_dir)

    if result.returncode == 0:
        print("\n========================================================")
        print("  HUGGING FACE SPACES DEPLOYMENT TRIGGERED!")
        print("========================================================")
        print(f"  Space URL:      https://huggingface.co/spaces/{user}/{space}")
        print(f"  Live API URL:   {api_url}")
        print("\nNext, open Space Settings -> 'Variables and secrets' and set:")
        print("  - GEMINI_API_KEY      (Secret)")
        print("  - LLM_PROVIDER        gemini")
        print("  - LLM_MODEL           gemini-2.5-flash")
        print("  - JWT_SECRET          (Secret, e.g. run `openssl rand -hex 32`)")
        print("  - COOKIE_SECURE       true")
        print("  - COOKIE_SAMESITE     none")
        print("\nThen deploy your frontend to Vercel with:")
        print(f"  NEXT_PUBLIC_API_URL={api_url}")
        print("========================================================")
    else:
        print("\nPush failed. If authentication was rejected:")
        print("  1. Create a Write Token at: https://huggingface.co/settings/tokens")
        print("  2. Re-run: python scripts/deploy_hf.py --space", clean_name, "--push --token <HF_TOKEN>")


def main() -> None:
    parser = argparse.ArgumentParser(description="Deploy InsightGPT backend to Hugging Face Spaces")
    parser.add_argument("--space", type=str, default="", help="Hugging Face Space (format: username/space-name)")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="Output bundle directory")
    parser.add_argument("--push", action="store_true", help="Push directly via git")
    parser.add_argument("--token", type=str, default=os.getenv("HF_TOKEN", ""), help="Hugging Face User Access Token")

    args = parser.parse_args()

    prepare_bundle(args.out_dir)

    if args.space:
        if args.push or args.token:
            push_to_space(args.out_dir, args.space, args.token or None)
        else:
            clean_name = args.space.replace("https://huggingface.co/spaces/", "").strip("/")
            user, space = clean_name.split("/", 1) if "/" in clean_name else (clean_name, "insight-gpt-api")
            public_host = f"https://{user}-{space.replace('_', '-')}.hf.space"
            print(f"\n[+] Bundle ready in: {args.out_dir}")
            print(f"To push manually:")
            print(f"  cd {args.out_dir}")
            print(f"  git init && git checkout -b main")
            print(f"  git remote add origin https://huggingface.co/spaces/{user}/{space}")
            print(f"  git add . && git commit -m 'Deploy InsightGPT API'")
            print(f"  git push -u origin main --force")
            print(f"\nYour backend will be available at: {public_host}/api/v1")


if __name__ == "__main__":
    main()

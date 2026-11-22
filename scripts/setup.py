"""InsightGPT setup / repair / doctor — one command from a fresh clone.

    python scripts/setup.py               # set up (or repair) the Docker stack
    python scripts/setup.py --preflight   # is the local stack ready to demo?
    python scripts/setup.py --doctor      # diagnose only; changes nothing
    python scripts/setup.py --repair      # force a rebuild + recreate, then re-verify
    python scripts/setup.py --native      # no Docker: prepare the local dev stack
    python scripts/setup.py --skip-models # don't pull Ollama models (fast, no LLM)

Design rules, learned the hard way on this project:

* **Idempotent.** Every step checks state before acting, so a second run is a
  near no-op and a partial failure is fixed by re-running.
* **Never silently succeeds.** A step that cannot do its job says so and exits
  non-zero. "Reported success while doing nothing" is the bug class this script
  exists to prevent.
* **Preserves your edits.** ``.env`` is created if missing and gap-filled, but
  values you set are never overwritten.
* **Stdlib only.** This file must run before anything is installed, on whatever
  Python the machine has (3.9+). No third-party imports, ever.
* **Read-only about other people's data.** The preflight check below talks to a
  Qdrant that may be shared with other projects. It issues GET requests only,
  and every write/delete path in this repo is fenced to the ``insightgpt_``
  collection namespace. A setup script that "cleans up" is a data-loss bug.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import platform
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENV_FILE = REPO / ".env"
ENV_EXAMPLE = REPO / ".env.example"

DEMO_EMAIL = "admin@insightgpt.dev"
DEMO_PASSWORD = "admin-pass"

IS_WINDOWS = platform.system() == "Windows"

# A fresh Windows console is cp1252: writing the UTF-8 output below would raise
# or mojibake. Force UTF-8 and degrade gracefully rather than crash the setup.
for _stream in (sys.stdout, sys.stderr):
    # Very old interpreters lack reconfigure(); output style is not worth a crash.
    with contextlib.suppress(AttributeError, ValueError):
        _stream.reconfigure(encoding="utf-8", errors="replace")


# --------------------------------------------------------------------------- #
# Output                                                                       #
# --------------------------------------------------------------------------- #
_USE_COLOR = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text


def step(n: int, total: int, title: str) -> None:
    print(f"\n{_c('1;36', f'[{n}/{total}]')} {_c('1', title)}", flush=True)


def ok(msg: str) -> None:
    print(f"  {_c('32', 'OK')}   {msg}", flush=True)


def info(msg: str) -> None:
    print(f"  {_c('90', '..')}   {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"  {_c('33', 'WARN')} {msg}", flush=True)


def bad(msg: str) -> None:
    print(f"  {_c('31', 'FAIL')} {msg}", flush=True)


class SetupError(RuntimeError):
    """A step could not complete. Carries the fix, not just the symptom."""

    def __init__(self, problem: str, fix: str = "") -> None:
        super().__init__(problem)
        self.problem = problem
        self.fix = fix


# --------------------------------------------------------------------------- #
# Process helpers                                                              #
# --------------------------------------------------------------------------- #
def run(
    cmd: list[str],
    *,
    timeout: float | None = None,
    cwd: Path | None = None,
    env: dict | None = None,
    capture: bool = True,
) -> subprocess.CompletedProcess:
    """Run a command. Never raises on non-zero — callers decide what that means."""
    merged = {**os.environ, **(env or {})}
    return subprocess.run(
        cmd,
        cwd=str(cwd or REPO),
        env=merged,
        timeout=timeout,
        capture_output=capture,
        text=True,
        check=False,
    )


def which(name: str) -> str | None:
    return shutil.which(name)


def http_get(url: str, timeout: float = 5.0, headers: dict | None = None) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def http_post_json(url: str, payload: dict, timeout: float = 30.0,
                   headers: dict | None = None) -> tuple[int, bytes]:
    body = json.dumps(payload).encode()
    hdrs = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def port_is_free(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.6)
        return s.connect_ex((host, port)) != 0


def find_free_port(start: int, limit: int = 40) -> int:
    for candidate in range(start, start + limit):
        if port_is_free(candidate):
            return candidate
    raise SetupError(
        f"no free TCP port found in {start}..{start + limit}",
        "Free a port or set WEB_PORT / API_PORT in .env by hand.",
    )


# --------------------------------------------------------------------------- #
# .env handling                                                                #
# --------------------------------------------------------------------------- #
def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    return values


def set_env_value(path: Path, key: str, value: str) -> None:
    """Set ``key`` in-place, preserving comments, ordering and unrelated lines."""
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
    if pattern.search(text):
        text = pattern.sub(f"{key}={value}", text)
    else:
        text = text.rstrip("\n") + f"\n{key}={value}\n"
    path.write_text(text, encoding="utf-8")


# --------------------------------------------------------------------------- #
# Docker                                                                       #
# --------------------------------------------------------------------------- #
def compose_cmd() -> list[str]:
    """The compose invocation, run from the repo root so root .env is read.

    Deliberately NOT ``-f docker/compose.yml``: that makes ``docker/`` the
    project directory, the root ``.env`` is never read, and every setting
    silently falls back to its in-file default.
    """
    return ["docker", "compose"]


def docker_ready() -> tuple[bool, str]:
    """Is the Docker CLI present *and* the engine actually answering?"""
    if which("docker") is None:
        return False, "the docker CLI is not on PATH"
    try:
        proc = run(["docker", "version", "--format", "{{.Server.Version}}"], timeout=25)
    except subprocess.TimeoutExpired:
        return False, "the Docker engine did not respond within 25s (it looks wedged)"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return False, detail[-1] if detail else "the Docker engine is not responding"
    return True, (proc.stdout or "").strip()


def compose_available() -> bool:
    proc = run(["docker", "compose", "version"], timeout=25)
    return proc.returncode == 0


def compose_ps() -> list[dict]:
    """Current service state as a list of dicts (empty when nothing is up)."""
    proc = run([*compose_cmd(), "ps", "--format", "json"], timeout=60)
    if proc.returncode != 0:
        return []
    out = (proc.stdout or "").strip()
    if not out:
        return []
    # Compose emits either a JSON array or newline-delimited objects by version.
    try:
        parsed = json.loads(out)
        return parsed if isinstance(parsed, list) else [parsed]
    except json.JSONDecodeError:
        rows = []
        for line in out.splitlines():
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return rows


# --------------------------------------------------------------------------- #
# Steps — shared                                                               #
# --------------------------------------------------------------------------- #
def step_repo_sanity() -> None:
    missing = [
        p for p in ("compose.yaml", "docker/compose.yml", ".env.example", "services/api")
        if not (REPO / p).exists()
    ]
    if missing:
        raise SetupError(
            "this does not look like a complete InsightGPT checkout "
            f"(missing: {', '.join(missing)})",
            "Run this script from inside the cloned repository.",
        )
    ok(f"repository root: {REPO}")


def step_environment(*, quiet: bool = False) -> dict[str, str]:
    """Create .env if absent, fill gaps, never overwrite existing values."""
    created = False
    if not ENV_FILE.exists():
        if not ENV_EXAMPLE.exists():
            raise SetupError(".env.example is missing", "Restore it from version control.")
        shutil.copyfile(ENV_EXAMPLE, ENV_FILE)
        created = True
        ok("created .env from .env.example")
    else:
        info(".env already exists — existing values are preserved")

    env = read_env(ENV_FILE)

    # A blank JWT_SECRET is the single value that must never ship as a default.
    if not env.get("JWT_SECRET"):
        set_env_value(ENV_FILE, "JWT_SECRET", secrets.token_hex(32))
        ok("generated a random JWT_SECRET")
    elif not quiet:
        info("JWT_SECRET already set")

    # Any key present in the example but absent here (e.g. after a git pull that
    # added a variable) is appended with its documented default.
    example = read_env(ENV_EXAMPLE)
    env = read_env(ENV_FILE)
    added = [k for k in example if k not in env]
    for key in added:
        set_env_value(ENV_FILE, key, example[key])
    if added:
        ok(f"added {len(added)} new variable(s) from .env.example: {', '.join(added)}")

    if created:
        info("edit .env if you want a cloud LLM key or different ports")
    return read_env(ENV_FILE)


def step_ports(env: dict[str, str]) -> dict[str, str]:
    """Ensure the published ports are free, and keep dependent URLs consistent.

    Changing API_PORT without also fixing NEXT_PUBLIC_API_URL/CORS_ORIGINS would
    leave the web app pointing at a port nothing listens on — so they move together.
    """
    web_port = int(env.get("WEB_PORT", "3000"))
    api_port = int(env.get("API_PORT", "8000"))

    running = {c.get("Service") for c in compose_ps() if c.get("State") == "running"}

    for name, port, key in (("web", web_port, "WEB_PORT"), ("api", api_port, "API_PORT")):
        if port_is_free(port):
            ok(f"port {port} free for {name}")
            continue
        if name in running:
            ok(f"port {port} is held by our own {name} container")
            continue
        new_port = find_free_port(port + 1)
        set_env_value(ENV_FILE, key, str(new_port))
        warn(f"port {port} is in use by something else — {name} moved to {new_port}")
        if name == "api":
            api_port = new_port
        else:
            web_port = new_port

    env = read_env(ENV_FILE)
    # Keep the cross-references honest.
    expected_api_url = f"http://localhost:{api_port}/api/v1"
    if env.get("NEXT_PUBLIC_API_URL") != expected_api_url:
        set_env_value(ENV_FILE, "NEXT_PUBLIC_API_URL", expected_api_url)
        info(f"NEXT_PUBLIC_API_URL -> {expected_api_url}")
    # CORS_ORIGINS is a LIST. Append the web origin instead of overwriting the
    # whole value: replacing it would drop every other origin the user needs
    # (the 3020 native-dev port, the 127.0.0.1 spellings), and a missing origin
    # fails preflight so login stops working in the browser with no server error.
    existing = [o.strip() for o in env.get("CORS_ORIGINS", "").split(",") if o.strip()]
    wanted = [f"http://localhost:{web_port}", f"http://127.0.0.1:{web_port}"]
    missing = [o for o in wanted if o not in existing]
    if missing:
        merged = ",".join(existing + missing)
        set_env_value(ENV_FILE, "CORS_ORIGINS", merged)
        info(f"CORS_ORIGINS += {', '.join(missing)}")

    return read_env(ENV_FILE)


# --------------------------------------------------------------------------- #
# Steps — Docker mode                                                          #
# --------------------------------------------------------------------------- #
def step_preflight_docker() -> None:
    ready, detail = docker_ready()
    if not ready:
        raise SetupError(
            f"Docker is not usable: {detail}",
            "Install Docker Desktop (https://docs.docker.com/get-docker/) and make sure it is "
            "running.\n         If it is running but wedged, quit Docker Desktop fully and "
            "reopen it; on Windows a stubborn engine\n         is cleared with `wsl --shutdown` "
            "before restarting Docker Desktop.\n         No Docker? Use the local dev stack "
            "instead:  python scripts/setup.py --native",
        )
    ok(f"docker engine {detail}")

    if not compose_available():
        raise SetupError(
            "`docker compose` (v2) is unavailable",
            "Update Docker Desktop, or install the Compose v2 plugin.",
        )
    ok("docker compose v2 available")


def step_build(force: bool) -> None:
    cmd = [*compose_cmd(), "build"]
    if force:
        cmd.append("--no-cache")
        info("rebuilding images from scratch (--repair)")
    else:
        info("building images (cached layers are reused)")
    proc = run(cmd, timeout=3600, capture=False)
    if proc.returncode != 0:
        raise SetupError(
            "image build failed",
            "Scroll up for the failing step. A rerun continues from the last good layer.",
        )
    ok("images built")


def step_start(force_recreate: bool) -> None:
    cmd = [*compose_cmd(), "up", "-d"]
    if force_recreate:
        cmd.append("--force-recreate")
    proc = run(cmd, timeout=1800, capture=False)
    if proc.returncode != 0:
        raise SetupError(
            "the stack failed to start",
            "Check `docker compose logs` for the service that failed.",
        )
    ok("containers started")


def step_wait_healthy(timeout: float = 300.0) -> None:
    """Wait for api + web to report healthy, showing what is still pending."""
    deadline = time.monotonic() + timeout
    wanted = {"postgres", "ollama", "api", "web"}
    last_report = ""
    while time.monotonic() < deadline:
        containers = compose_ps()
        states: dict[str, str] = {}
        for c in containers:
            svc = c.get("Service", "")
            health = (c.get("Health") or "").lower()
            state = (c.get("State") or "").lower()
            states[svc] = health or state
        pending = [s for s in wanted if states.get(s) not in ("healthy", "running")]
        # api/web must be healthy specifically; the others only need to be up.
        strict_pending = [
            s for s in ("api", "web") if states.get(s) != "healthy"
        ]
        report = ", ".join(f"{k}={v or '?'}" for k, v in sorted(states.items()))
        if report != last_report:
            info(report or "no containers reported yet")
            last_report = report
        if not strict_pending and not [s for s in ("postgres", "ollama") if
                                       states.get(s) not in ("healthy", "running")]:
            ok("all services healthy")
            return
        exited = [s for s, v in states.items() if v in ("exited", "dead")]
        if exited:
            raise SetupError(
                f"service(s) exited during startup: {', '.join(exited)}",
                f"Inspect with: docker compose logs {exited[0]}",
            )
        time.sleep(4)
        _ = pending
    raise SetupError(
        f"services did not become healthy within {timeout:.0f}s",
        "Inspect with: docker compose ps  and  docker compose logs api",
    )


def step_bootstrap_docker(skip_models: bool) -> None:
    """Run the first-run bootstrap inside the worker (models, warehouse, index)."""
    cmd = [*compose_cmd(), "exec", "-T", "worker", "python", "scripts/bootstrap.py"]
    if skip_models:
        cmd.append("--skip-models")
        warn("skipping model pulls (--skip-models): retrieval quality will be degraded")
    info("this is the slow step on a first run (model pulls + warehouse build)")
    proc = run(cmd, timeout=5400, capture=False)
    if proc.returncode != 0:
        raise SetupError(
            "bootstrap failed",
            "It is idempotent — fix the cause shown above and re-run this script.",
        )
    ok("warehouse built and documents indexed")


# --------------------------------------------------------------------------- #
# Steps — verification (mode-independent)                                      #
# --------------------------------------------------------------------------- #
def step_verify(api_base: str, *, deep: bool = True) -> None:
    """Prove the stack actually answers — not merely that containers are up."""
    status, body = http_get(f"{api_base}/health", timeout=15)
    if status != 200:
        raise SetupError(
            f"GET /health returned {status}",
            "The API is up but unhealthy; check `docker compose logs api`.",
        )
    ok(f"/health -> {json.loads(body).get('status')}")

    status, body = http_post_json(
        f"{api_base}/api/v1/auth/login",
        {"email": DEMO_EMAIL, "password": DEMO_PASSWORD},
        timeout=30,
    )
    if status != 200:
        raise SetupError(
            f"demo login returned {status}",
            "Auth is misconfigured — check JWT_SECRET in .env.",
        )
    token = json.loads(body)["access_token"]
    ok("demo login succeeded")

    if not deep:
        return

    status, body = http_post_json(
        f"{api_base}/api/v1/ask",
        {"question": "Why did sales decline last quarter?", "stream": False},
        timeout=180,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    if status != 200:
        raise SetupError(
            f"the smoke question returned {status}: {body[:300].decode(errors='replace')}",
            "The engine could not answer. Re-run bootstrap, or check `docker compose logs api`.",
        )
    env = json.loads(body)
    answer = (env.get("answer") or "").strip()
    if not answer:
        raise SetupError("the engine returned an empty answer", "Re-run bootstrap.")
    ok(f"end-to-end answer: {answer[:110]}...")
    info(
        f"route={env.get('route')} sql={len(env.get('sql') or [])} "
        f"tables={len(env.get('tables') or [])} citations={len(env.get('citations') or [])}"
    )


# --------------------------------------------------------------------------- #
# Steps — native (no Docker) mode                                              #
# --------------------------------------------------------------------------- #
def ensure_uv() -> str:
    uv = which("uv")
    if uv:
        return uv
    info("installing uv (the Python toolchain manager)")
    if IS_WINDOWS:
        cmd = ["powershell", "-ExecutionPolicy", "Bypass", "-Command",
               "irm https://astral.sh/uv/install.ps1 | iex"]
    else:
        cmd = ["sh", "-c", "curl -LsSf https://astral.sh/uv/install.sh | sh"]
    proc = run(cmd, timeout=600, capture=False)
    uv = which("uv") or shutil.which("uv", path=str(Path.home() / ".local" / "bin"))
    if proc.returncode != 0 or uv is None:
        raise SetupError(
            "could not install uv automatically",
            "Install it manually: https://docs.astral.sh/uv/getting-started/installation/",
        )
    ok("uv installed")
    return uv


def venv_python(service_dir: Path) -> Path:
    return service_dir / (".venv/Scripts/python.exe" if IS_WINDOWS else ".venv/bin/python")


def step_native_python(uv: str) -> None:
    """Create the API virtualenv and install it with the extras the app needs."""
    api = REPO / "services" / "api"
    py = venv_python(api)
    if not py.exists():
        proc = run([uv, "venv", "--python", "3.12"], cwd=api, timeout=900)
        if proc.returncode != 0:
            raise SetupError(
                f"could not create a virtualenv in {api}",
                (proc.stderr or "").strip() or "Check that uv can fetch Python 3.12.",
            )
        ok("created services/api/.venv (Python 3.12)")
    else:
        info("services/api/.venv already exists")

    proc = run(
        [uv, "pip", "install", "--python", str(py), "-e", ".[api]", "pytest", "ruff"],
        cwd=api,
        timeout=1800,
    )
    if proc.returncode != 0:
        raise SetupError(
            "installing the API dependencies failed",
            (proc.stderr or "").strip()[-400:] or "Re-run with a working network connection.",
        )
    ok("API dependencies installed")


def step_native_web() -> None:
    npm = which("npm")
    if npm is None:
        warn("npm not found — the web app cannot be prepared")
        info("install Node.js 20+ (https://nodejs.org) and re-run to enable the UI")
        return
    web = REPO / "web"
    if (web / "node_modules").exists():
        info("web/node_modules already present")
        return
    info("installing web dependencies (this takes a few minutes)")
    lock = web / "package-lock.json"
    cmd = [npm, "ci"] if lock.exists() else [npm, "install"]
    proc = run(cmd, cwd=web, timeout=2400, capture=False)
    if proc.returncode != 0:
        raise SetupError("npm install failed", "Check the npm output above.")
    ok("web dependencies installed")


def step_native_smoke() -> None:
    """Answer the demo question in-process — no server, no network."""
    api = REPO / "services" / "api"
    py = venv_python(api)
    code = (
        "import json;"
        "from app.engine.engine import InsightEngine;"
        "from app.providers.factory import get_provider;"
        "e=InsightEngine.fixture(provider=get_provider('fake'));"
        "r=e.ask('Why did sales decline last quarter?');"
        "print(json.dumps({'answer':r.answer,'sql':len(r.sql),'cites':len(r.citations)}))"
    )
    proc = run([str(py), "-c", code], cwd=api, timeout=300,
               env={"LLM_PROVIDER": "fake", "WAREHOUSE": "duckdb", "RETRIEVER": "fixture"})
    if proc.returncode != 0:
        raise SetupError(
            "the insight engine could not answer the smoke question",
            (proc.stderr or "").strip()[-500:],
        )
    payload = json.loads((proc.stdout or "{}").strip().splitlines()[-1])
    ok(f"engine answered: {payload['answer'][:100]}...")
    info(f"sql={payload['sql']} citations={payload['cites']}")


# --------------------------------------------------------------------------- #
# Preflight — is the configured local stack actually ready to demo?            #
# --------------------------------------------------------------------------- #
# The collection namespace this project owns. Everything InsightGPT creates,
# writes or deletes in Qdrant lives under this prefix. The instance on a
# developer machine is routinely shared with other projects, whose collections
# must survive us untouched, so the prefix is asserted in code rather than
# remembered — here, in scripts/bootstrap.py, and in the retrieval package.
QDRANT_COLLECTION_PREFIX = "insightgpt_"

# Mirrors services/api/app/providers/factory.py. Duplicated rather than imported
# because this file is stdlib-only and must run before anything is installed; if
# the factory gains a provider, add it here too.
_PROVIDER_ENDPOINTS = {
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY"),
}


class _Report:
    """Collects pass/fail lines so the exit code reflects the whole run."""

    def __init__(self) -> None:
        self.failures: list[tuple[str, str]] = []

    def good(self, msg: str) -> None:
        ok(msg)

    def note(self, msg: str) -> None:
        info(msg)

    def soft(self, msg: str, fix: str = "") -> None:
        """Something to know about, but the demo still works."""
        warn(msg)
        if fix:
            print(f"       fix: {fix}", flush=True)

    def hard(self, msg: str, fix: str) -> None:
        bad(msg)
        print(f"       fix: {fix}", flush=True)
        self.failures.append((msg, fix))


def _get_json(url: str, timeout: float = 6.0, headers: dict | None = None):
    """GET and parse JSON. Returns ``(status, obj_or_None, error_text)``.

    Never raises: a preflight that crashes on an unreachable service tells you
    less than one that reports which service was unreachable.
    """
    try:
        status, body = http_get(url, timeout=timeout, headers=headers)
    except Exception as exc:  # noqa: BLE001 - diagnosis must never crash
        return 0, None, f"{type(exc).__name__}: {exc}"
    try:
        return status, json.loads(body), ""
    except Exception:  # noqa: BLE001
        return status, None, body[:200].decode(errors="replace")


def _model_is_present(installed: set, wanted: str) -> bool:
    """Ollama lists models as ``name:tag``; a bare name means ``:latest``."""
    wanted = wanted.strip()
    if not wanted:
        return False
    candidates = {wanted, wanted if ":" in wanted else f"{wanted}:latest"}
    return bool(candidates & installed)


def _check_config(env: dict, rep: _Report) -> None:
    print(_c("1", "\nConfiguration (.env)"))
    if not ENV_FILE.exists():
        rep.hard(".env is missing", f"Run: {_entrypoint()}   (creates it from .env.example)")
        return

    secret = env.get("JWT_SECRET", "")
    if len(secret) < 32:
        rep.hard(
            "JWT_SECRET is empty or too short — the API refuses to start",
            "openssl rand -hex 32   (then put it in .env), or run "
            f"{_entrypoint()} which generates one",
        )
    else:
        rep.good("JWT_SECRET set")

    retriever = env.get("RETRIEVER", "").strip().lower()
    if retriever == "qdrant":
        rep.good("RETRIEVER=qdrant — real hybrid retrieval over the local index")
    else:
        rep.hard(
            f"RETRIEVER={retriever or 'unset'} — uploaded datasets would never be searched",
            "Set RETRIEVER=qdrant in .env. `fixture` is keyword overlap over six "
            "built-in demo documents and cannot see an uploaded file.",
        )

    embed_provider = (env.get("EMBED_PROVIDER") or "ollama").strip().lower()
    if embed_provider == "ollama":
        rep.good("EMBED_PROVIDER=ollama — document text is embedded on this machine")
    else:
        rep.soft(
            f"EMBED_PROVIDER={embed_provider} — document text is sent to a cloud "
            "embedding endpoint, not embedded locally",
            "Set EMBED_PROVIDER=ollama for the local-embedding configuration.",
        )


def _check_qdrant(env: dict, rep: _Report) -> None:
    print(_c("1", "\nVector store (Qdrant)"))
    url = (env.get("QDRANT_URL") or "http://127.0.0.1:6333").rstrip("/")
    collection = env.get("QDRANT_COLLECTION") or ""

    if not collection:
        rep.hard(
            "QDRANT_COLLECTION is not set",
            f"Set QDRANT_COLLECTION={QDRANT_COLLECTION_PREFIX}documents in .env.",
        )
    elif not collection.startswith(QDRANT_COLLECTION_PREFIX):
        rep.hard(
            f"QDRANT_COLLECTION={collection!r} is outside the "
            f"{QDRANT_COLLECTION_PREFIX!r} namespace this project owns",
            "A Qdrant instance is usually shared. Writing into another project's "
            f"collection would overwrite its points, so set "
            f"QDRANT_COLLECTION={QDRANT_COLLECTION_PREFIX}documents in .env.",
        )
        collection = ""  # do not go on to probe a collection we must not use
    else:
        rep.good(f"collection name {collection!r} is inside our namespace")

    status, payload, err = _get_json(f"{url}/collections")
    if status != 200 or not isinstance(payload, dict):
        rep.hard(
            f"Qdrant not reachable at {url} ({err or f'HTTP {status}'})",
            "Start it, e.g.  docker start <your-qdrant-container>  — or run the "
            "whole containerised stack with `make up`. Then check QDRANT_URL in "
            ".env points at the right host (127.0.0.1:6333 for a host run).",
        )
        return
    names = [c.get("name", "") for c in payload.get("result", {}).get("collections", [])]
    rep.good(f"Qdrant reachable at {url}")

    # Say out loud what else lives here, so it is obvious that this check is a
    # read and that nothing else is in scope for anything this repo does.
    others = sorted(n for n in names if n and not n.startswith(QDRANT_COLLECTION_PREFIX))
    if others:
        rep.note(f"other collections on this instance, left untouched: {', '.join(others)}")

    if not collection:
        return
    if collection not in names:
        rep.soft(
            f"collection {collection!r} does not exist yet (0 indexed chunks)",
            "This is normal before the first upload — the indexer creates it. To "
            "create it now:  uv run --project services/retrieval insight-retrieval setup",
        )
        return

    status, payload, err = _get_json(f"{url}/collections/{collection}")
    if status != 200 or not isinstance(payload, dict):
        rep.hard(
            f"could not read collection {collection!r} ({err or f'HTTP {status}'})",
            "Check the Qdrant logs; the instance answered /collections but not "
            "this collection.",
        )
        return
    result = payload.get("result", {}) or {}
    points = result.get("points_count")
    shown = points if points is not None else "?"
    rep.good(f"collection {collection!r} exists — {shown} chunks indexed")

    # A width mismatch does not error at query time, it returns nonsense, so it
    # is worth catching here rather than in a demo.
    vectors = ((result.get("config") or {}).get("params") or {}).get("vectors") or {}
    sizes = (
        [vectors.get("size")]
        if isinstance(vectors, dict) and "size" in vectors
        else [v.get("size") for v in vectors.values() if isinstance(v, dict)]
    )
    sizes = [s for s in sizes if isinstance(s, int)]
    configured = (env.get("EMBED_DIMENSIONS") or "").strip()
    if sizes and configured.isdigit() and int(configured) not in sizes:
        rep.hard(
            f"EMBED_DIMENSIONS={configured} but the collection stores "
            f"{'/'.join(str(s) for s in sizes)}-d vectors",
            "Qdrant cannot resize in place. Either set EMBED_DIMENSIONS back to "
            "the collection's width, or re-index from scratch after deleting ONLY "
            f"the {collection!r} collection.",
        )
    elif sizes:
        rep.good(f"vector width {sizes[0]}-d matches EMBED_DIMENSIONS")


def _check_ollama(env: dict, rep: _Report) -> None:
    print(_c("1", "\nLocal models (Ollama)"))
    host = (env.get("OLLAMA_HOST") or "http://127.0.0.1:11434").rstrip("/")
    status, payload, err = _get_json(f"{host}/api/tags", timeout=8.0)
    if status != 200 or not isinstance(payload, dict):
        rep.hard(
            f"Ollama not reachable at {host} ({err or f'HTTP {status}'})",
            "Start it with `ollama serve` (or `make up` for the containerised "
            "stack), then check OLLAMA_HOST in .env — a host run needs "
            "127.0.0.1:11434, not the compose service name.",
        )
        return
    installed = {m.get("name", "") for m in payload.get("models", [])}
    rep.good(f"Ollama reachable at {host} ({len(installed)} models installed)")

    embed = (env.get("EMBED_MODEL") or "nomic-embed-text").strip()
    if _model_is_present(installed, embed):
        rep.good(f"embedding model {embed!r} pulled")
    else:
        rep.hard(
            f"embedding model {embed!r} is not pulled — nothing can be indexed",
            f"ollama pull {embed}",
        )

    rerank = (env.get("RERANK_MODEL") or "").strip()
    if not rerank:
        rep.soft(
            "RERANK_MODEL is empty — reranking is disabled",
            "Set RERANK_MODEL=dengcao/Qwen3-Reranker-0.6B:F16 in .env to enable it.",
        )
    elif _model_is_present(installed, rerank):
        rep.good(f"reranker model {rerank!r} pulled")
    else:
        # Not a hard failure: search falls back to the RRF order.
        rep.soft(
            f"reranker model {rerank!r} is not pulled — results fall back to the "
            "RRF order instead of being reranked",
            f"ollama pull {rerank}",
        )


def _check_provider(env: dict, rep: _Report, *, offline: bool) -> None:
    print(_c("1", "\nReasoning provider"))
    provider = (env.get("LLM_PROVIDER") or "ollama").strip().lower()
    model = (env.get("LLM_MODEL") or "").strip()

    if provider not in _PROVIDER_ENDPOINTS:
        if provider == "fake":
            rep.hard(
                "LLM_PROVIDER=fake answers from a fixed template, not a model",
                "Set LLM_PROVIDER=gemini and GEMINI_API_KEY in .env.",
            )
        else:
            rep.note(f"LLM_PROVIDER={provider} — no key needed, not checked here")
        return

    base, key_env = _PROVIDER_ENDPOINTS[provider]
    key = (env.get(key_env) or os.environ.get(key_env) or "").strip()
    if not key:
        rep.hard(
            f"{key_env} is empty — the API refuses to start on LLM_PROVIDER={provider}",
            "Paste your key into .env. For Gemini: https://aistudio.google.com/apikey",
        )
        return
    rep.good(f"{key_env} is set ({len(key)} chars)")

    if not model:
        rep.soft(
            "LLM_MODEL is not set — the provider's default is used and may be retired",
            "Pin it, e.g. LLM_MODEL=gemini-2.5-flash",
        )
    if offline:
        rep.note("--offline: not calling the provider to validate the key")
        return

    # The only outbound call in this script: ask the provider for its catalogue.
    # It validates the key AND whether the pinned model still exists, which is
    # the failure a key-presence check cannot see.
    status, payload, err = _get_json(
        f"{base}/models", timeout=15.0, headers={"Authorization": f"Bearer {key}"}
    )
    if status in (401, 403):
        rep.hard(
            f"{key_env} was rejected by {provider} (HTTP {status})",
            "The key is present but not valid. Re-copy it from the provider console.",
        )
        return
    if status != 200 or not isinstance(payload, dict):
        rep.soft(
            f"could not validate {key_env}: {err or f'HTTP {status}'}",
            "Usually a network/proxy problem rather than a bad key. Re-run with "
            "--offline to skip this check.",
        )
        return
    rep.good(f"{key_env} accepted by {provider}")

    ids = {
        str(m.get("id", "")).rsplit("/", 1)[-1]
        for m in payload.get("data", [])
        if isinstance(m, dict)
    }
    if model and ids and model.rsplit("/", 1)[-1] not in ids:
        rep.hard(
            f"LLM_MODEL={model!r} is not in {provider}'s catalogue",
            "Provider catalogues are retired on a schedule. Pick a current id "
            f"from {base}/models.",
        )
    elif model and ids:
        rep.good(f"LLM_MODEL={model!r} is still in {provider}'s catalogue")


def preflight(*, offline: bool = False) -> int:
    """Check the configured local stack end to end. Changes nothing, anywhere.

    One command that answers "can I demo this right now?", with a fix on every
    failing line. Every call it makes is a GET.
    """
    print(_c("1", "\nInsightGPT preflight — read-only; nothing is started or changed."))
    env = read_env(ENV_FILE)
    rep = _Report()

    _check_config(env, rep)
    _check_qdrant(env, rep)
    _check_ollama(env, rep)
    _check_provider(env, rep, offline=offline)

    print(_c("1", "\nWhat this configuration means"))
    info("document text is embedded locally and the vectors are stored locally;")
    info("the snippets retrieved for a question are sent to the reasoning")
    info(f"provider ({(env.get('LLM_PROVIDER') or 'ollama').strip()}) inside the prompt.")

    print()
    if rep.failures:
        bad(f"{len(rep.failures)} blocking problem(s) — the demo will not work yet:")
        for msg, _ in rep.failures:
            print(f"       - {msg}")
        print()
        return 1
    ok("preflight passed — the local stack is ready")
    print()
    return 0


# --------------------------------------------------------------------------- #
# Doctor                                                                       #
# --------------------------------------------------------------------------- #
def _entrypoint() -> str:
    """The command the user actually typed, so hints are copy-pasteable."""
    return "setup.cmd" if IS_WINDOWS else "./setup.sh"


def doctor() -> int:
    """Read-only diagnosis. Changes nothing; names the exact broken thing."""
    print(_c("1", "\nInsightGPT doctor — diagnosis only, nothing is changed.\n"))
    problems: list[str] = []

    print(_c("1", "Host"))
    info(f"platform: {platform.platform()}")
    info(f"python:   {sys.version.split()[0]} ({sys.executable})")
    for tool in ("docker", "uv", "node", "npm", "git"):
        path = which(tool)
        (ok if path else warn)(f"{tool}: {path or 'not found'}")

    print(_c("1", "\nDocker"))
    ready, detail = docker_ready()
    if ready:
        ok(f"engine responding ({detail})")
        if compose_available():
            ok("compose v2 available")
            rows = compose_ps()
            if rows:
                for c in sorted(rows, key=lambda r: r.get("Service", "")):
                    state = c.get("Health") or c.get("State") or "?"
                    line = f"{c.get('Service','?'):<9} {state}"
                    (ok if state in ("healthy", "running") else warn)(line)
            else:
                info("no containers running for this project")
        else:
            problems.append("docker compose v2 is unavailable")
            bad("compose v2 unavailable")
    else:
        problems.append(f"docker unusable: {detail}")
        bad(detail)

    print(_c("1", "\nConfiguration"))
    if ENV_FILE.exists():
        env = read_env(ENV_FILE)
        ok(".env present")
        if not env.get("JWT_SECRET"):
            problems.append("JWT_SECRET is empty in .env")
            bad("JWT_SECRET is empty")
        else:
            ok("JWT_SECRET set")
        api_port = env.get("API_PORT", "8000")
        expected = f"http://localhost:{api_port}/api/v1"
        if env.get("NEXT_PUBLIC_API_URL") != expected:
            problems.append(
                f"NEXT_PUBLIC_API_URL ({env.get('NEXT_PUBLIC_API_URL')}) does not match "
                f"API_PORT ({api_port})"
            )
            bad(f"NEXT_PUBLIC_API_URL != API_PORT ({expected} expected)")
        else:
            ok("web -> api URL consistent")
        for key in ("WEB_PORT", "API_PORT"):
            port = int(env.get(key, "0") or 0)
            if port:
                free = port_is_free(port)
                info(f"{key}={port} ({'free' if free else 'in use'})")
    else:
        problems.append(".env is missing")
        bad(f".env missing — run: {_entrypoint()}")
        env = {}

    print(_c("1", "\nEndpoints"))
    api_port = (env or {}).get("API_PORT", "8000")
    base = f"http://localhost:{api_port}"
    try:
        status, body = http_get(f"{base}/health", timeout=4)
        if status == 200:
            ok(f"{base}/health -> {json.loads(body).get('status')}")
        else:
            problems.append(f"/health returned {status}")
            warn(f"{base}/health -> HTTP {status}")
    except Exception as exc:  # noqa: BLE001 - diagnosis must never crash
        warn(f"{base}/health unreachable ({type(exc).__name__})")

    # The checks above cover the Docker stack and the repo's own wiring. The
    # local-stack checks (Qdrant/Ollama/provider key) live in preflight() so
    # they can also be run on their own before a demo; run them here too rather
    # than have two commands that each tell half the story.
    rc = preflight(offline=False)

    print()
    if problems or rc != 0:
        if problems:
            bad(f"{len(problems)} problem(s) found in the Docker stack / wiring:")
            for p in problems:
                print(f"       - {p}")
        if rc != 0:
            bad("the local stack preflight above also failed — fix those lines first")
        hint = f"{_entrypoint()} --repair"
        print("\n  Most Docker issues are fixed by:  " + _c("1", hint) + "\n")
        return 1
    ok("no problems detected")
    print()
    return 0


# --------------------------------------------------------------------------- #
# Flows                                                                        #
# --------------------------------------------------------------------------- #
def run_docker_flow(args) -> int:
    total = 7
    step(1, total, "Check the repository")
    step_repo_sanity()

    step(2, total, "Check Docker")
    step_preflight_docker()

    step(3, total, "Prepare configuration (.env)")
    env = step_environment()

    step(4, total, "Reserve ports")
    env = step_ports(env)

    step(5, total, "Build images")
    step_build(force=args.repair)

    step(6, total, "Start services")
    step_start(force_recreate=args.repair)
    step_wait_healthy()

    step(7, total, "Load data and verify")
    step_bootstrap_docker(skip_models=args.skip_models)
    api_port = env.get("API_PORT", "8000")
    step_verify(f"http://localhost:{api_port}", deep=not args.no_verify)

    web_port = env.get("WEB_PORT", "3000")
    print(_c("1;32", "\n  InsightGPT is ready.\n"))
    print(f"    Web  {_c('1', f'http://localhost:{web_port}')}")
    print(f"    API  http://localhost:{api_port}  (docs: /api/v1/docs)")
    print(f"\n    Sign in: {DEMO_EMAIL} / {DEMO_PASSWORD}")
    print("    Other demo roles: analyst@insightgpt.dev / viewer@insightgpt.dev\n")
    print(f"    Stop with: {_c('1', 'make down')}   Diagnose with: "
          f"{_c('1', 'python scripts/setup.py --doctor')}\n")
    return 0


def run_native_flow(args) -> int:
    total = 6
    step(1, total, "Check the repository")
    step_repo_sanity()

    step(2, total, "Ensure the Python toolchain (uv)")
    uv = ensure_uv()

    step(3, total, "Prepare configuration (.env)")
    step_environment()

    step(4, total, "Install Python dependencies")
    step_native_python(uv)

    step(5, total, "Install web dependencies")
    step_native_web()

    step(6, total, "Verify the engine")
    step_native_smoke()

    runner = ".venv/Scripts/python.exe" if IS_WINDOWS else ".venv/bin/python"
    print(_c("1;32", "\n  Local dev stack ready (no Docker, offline fixture data).\n"))
    print("    Start the API:")
    print(_c("1", f"      cd services/api && LLM_PROVIDER=fake {runner} "
                  "-m uvicorn app.api.main:app --port 8020"))
    print("    Start the web app:")
    print(_c("1", "      cd web && NEXT_PUBLIC_API_URL=http://localhost:8020/api/v1 "
                  "npm run dev -- -p 3020"))
    print(f"\n    Then open http://localhost:3020 and sign in as {DEMO_EMAIL} / {DEMO_PASSWORD}")
    print("\n    This mode uses the built-in sample dataset. For the full Postgres +")
    print("    Qdrant stack, install Docker and run: python scripts/setup.py\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="setup.py",
        description="Set up, repair, or diagnose an InsightGPT checkout.",
    )
    parser.add_argument("--repair", action="store_true",
                        help="force a clean rebuild and container recreate, then re-verify")
    parser.add_argument("--doctor", action="store_true",
                        help="diagnose only; change nothing")
    parser.add_argument("--preflight", action="store_true",
                        help="check the configured local stack (Qdrant, Ollama models, "
                             "provider key) is ready to demo; changes nothing")
    parser.add_argument("--offline", action="store_true",
                        help="with --preflight: skip the outbound call that validates "
                             "the LLM provider key")
    parser.add_argument("--native", action="store_true",
                        help="set up the local dev stack instead of Docker")
    parser.add_argument("--skip-models", action="store_true",
                        help="do not pull Ollama models")
    parser.add_argument("--no-verify", action="store_true",
                        help="skip the end-to-end smoke question")
    args = parser.parse_args(argv)

    if args.preflight:
        return preflight(offline=args.offline)

    if args.doctor:
        return doctor()

    print(_c("1", "\nInsightGPT setup — idempotent; safe to re-run at any time."))

    try:
        if args.native:
            return run_native_flow(args)
        # Fall back to native automatically only when Docker is absent entirely;
        # a broken-but-installed Docker is reported, never silently worked around.
        if which("docker") is None:
            warn("Docker is not installed — falling back to the local dev stack")
            info("install Docker and re-run for the full Postgres + Qdrant stack")
            return run_native_flow(args)
        return run_docker_flow(args)
    except SetupError as exc:
        print(_c("1;31", f"\n  SETUP FAILED: {exc.problem}"))
        if exc.fix:
            print(f"\n  Fix: {exc.fix}")
        print("\n  This script is idempotent — fix the cause and run it again.\n")
        return 1
    except KeyboardInterrupt:
        print("\n  interrupted\n")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

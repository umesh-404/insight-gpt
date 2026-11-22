"""Provider selection by config + environment.

``LLM_PROVIDER`` picks the implementation; cloud keys come from the environment
and are never written to the repo. ``gemini`` uses Google AI Studio's
OpenAI-compatible endpoint, so it shares the provider class with OpenAI and
Groq. No provider here is a specific assistant vendor.

**Model ids are configuration, not constants.** The defaults below are a
starting point and will rot: provider catalogues are retired on a published
schedule, and a hardcoded id becomes a 404 on a date nobody is watching for
(Groq shut down ``llama-3.1-8b-instant`` on 2026-08-16). Set ``LLM_MODEL``
explicitly for every deployment.

Validation is split deliberately. :func:`verify_provider_config` runs at
startup and checks only what can be known without a network call - the
provider name, and that its key is present - so boot does not depend on a
third party being up. Whether the *model* still exists is answered by
``OpenAICompatibleProvider.health_check`` behind ``GET /system/status``, and
by the 404 path in the provider, which names ``LLM_MODEL`` in the error.
"""

from __future__ import annotations

import logging
import os

from .base import Provider, ProviderError
from .fake import FakeProvider
from .ollama import OllamaProvider
from .openai_compatible import OpenAICompatibleProvider

log = logging.getLogger("insightgpt.provider")

# base_url + env var name + a starting model id for each OpenAI-compatible option.
_OPENAI_COMPATIBLE = {
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY", "gpt-4o-mini"),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY", "openai/gpt-oss-20b"),
    "gemini": (
        "https://generativelanguage.googleapis.com/v1beta/openai",
        "GEMINI_API_KEY",
        "gemini-2.5-flash",
    ),
}

CLOUD_PROVIDERS = frozenset(_OPENAI_COMPATIBLE)
# Providers that answer from a template or a local process. Legitimate for
# tests and offline development, never for a deployment that serves users.
OFFLINE_PROVIDERS = frozenset({"fake"})

PROVIDER_KEY_ENV = {name: env for name, (_, env, _) in _OPENAI_COMPATIBLE.items()}


def _is_production() -> bool:
    return os.getenv("APP_ENV", "").strip().lower() in {"production", "prod"}


def _int_env(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        log.warning("%s=%r is not an integer; using %d", name, raw, default)
        return default


def _float_env(name: str, default: float) -> float:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        log.warning("%s=%r is not a number; using %.1f", name, raw, default)
        return default


def resolve_provider_name(name: str | None = None) -> str:
    return (name or os.getenv("LLM_PROVIDER") or "ollama").strip().lower()


def verify_provider_config() -> None:
    """Fail at startup on a provider configuration that cannot serve users.

    Called from ``create_app`` beside the JWT check, for the same reason: a
    deployment that is missing its key should die on boot with a message naming
    the variable, not answer health checks for an hour and then 503 the first
    real question.
    """
    name = resolve_provider_name()
    production = _is_production()

    if name in OFFLINE_PROVIDERS:
        if production:
            raise RuntimeError(
                f"LLM_PROVIDER={name!r} answers from a fixed template and cannot "
                "serve users. Set LLM_PROVIDER to one of "
                f"{', '.join(sorted(CLOUD_PROVIDERS))} and supply its API key, "
                "or unset APP_ENV for offline development."
            )
        log.warning("LLM_PROVIDER=%s: answers come from a template, not a model", name)
        return

    if name in CLOUD_PROVIDERS:
        key_env = PROVIDER_KEY_ENV[name]
        if not (os.getenv(key_env) or "").strip():
            raise RuntimeError(
                f"LLM_PROVIDER={name!r} needs {key_env} in the environment. "
                "Put it in .env (gitignored) or your host's secret store - "
                "never in the repository."
            )
        if not (os.getenv("LLM_MODEL") or "").strip():
            log.warning(
                "LLM_MODEL is not set; falling back to %r for provider %s. "
                "Provider catalogues are retired on a schedule, so pin this "
                "explicitly for a deployment.",
                _OPENAI_COMPATIBLE[name][2], name,
            )
        return

    if name == "ollama":
        if production:
            log.warning(
                "LLM_PROVIDER=ollama in production: this expects a model server "
                "at OLLAMA_HOST=%s and will 503 if it is not reachable.",
                os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434"),
            )
        return

    raise RuntimeError(
        f"unknown LLM_PROVIDER {name!r}; expected one of: "
        f"ollama, fake, {', '.join(sorted(CLOUD_PROVIDERS))}"
    )


def get_provider(name: str | None = None, model: str | None = None) -> Provider:
    name = resolve_provider_name(name)

    if name == "fake":
        return FakeProvider()
    if name == "ollama":
        return OllamaProvider(
            model=model or os.getenv("LLM_MODEL", "llama3.1:8b"),
            host=os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434"),
        )
    if name in _OPENAI_COMPATIBLE:
        base_url, key_env, default_model = _OPENAI_COMPATIBLE[name]
        key = (os.getenv(key_env) or "").strip()
        if not key:
            # A ProviderError rather than a bare RuntimeError, so a key removed
            # after boot surfaces as a 503 naming the variable instead of a 500.
            raise ProviderError(
                f"{name}: {key_env} is not set in the environment (not the repo)",
                provider=name,
                retryable=False,
            )
        return OpenAICompatibleProvider(
            name=name,
            model=model or os.getenv("LLM_MODEL") or default_model,
            api_key=key,
            base_url=base_url,
            timeout=_float_env("LLM_TIMEOUT_S", 60.0),
            max_retries=_int_env("LLM_MAX_RETRIES", 3),
            max_output_tokens=_int_env("LLM_MAX_OUTPUT_TOKENS", 2048),
            # OpenAI's newer reasoning models accept only the newer spelling.
            # It cannot be sent alongside the old one - Gemini rejects the pair.
            max_tokens_field=(
                os.getenv("LLM_MAX_TOKENS_FIELD") or "max_tokens"
            ).strip(),
        )
    raise ValueError(
        f"unknown LLM_PROVIDER {name!r}; expected one of: "
        f"ollama, fake, {', '.join(sorted(CLOUD_PROVIDERS))}"
    )

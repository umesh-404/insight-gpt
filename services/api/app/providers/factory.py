"""Provider selection by config + environment.

``LLM_PROVIDER`` picks the implementation; cloud keys come from the environment
and are never written to the repo. The default is the local, private Ollama
provider — and the fully offline ``FakeProvider`` backs tests and no-network
runs. No provider here is a specific assistant vendor.
"""

from __future__ import annotations

import os

from .base import Provider
from .fake import FakeProvider
from .ollama import OllamaProvider
from .openai_compatible import OpenAICompatibleProvider

# base_url + env var name for each OpenAI-compatible cloud option.
_OPENAI_COMPATIBLE = {
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY", "gpt-4o-mini"),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY", "llama-3.1-8b-instant"),
}


def get_provider(name: str | None = None, model: str | None = None) -> Provider:
    name = (name or os.getenv("LLM_PROVIDER") or "ollama").lower()

    if name == "fake":
        return FakeProvider()
    if name == "ollama":
        return OllamaProvider(
            model=model or os.getenv("LLM_MODEL", "llama3.1:8b"),
            host=os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434"),
        )
    if name in _OPENAI_COMPATIBLE:
        base_url, key_env, default_model = _OPENAI_COMPATIBLE[name]
        key = os.getenv(key_env)
        if not key:
            raise RuntimeError(
                f"provider {name!r} needs {key_env} in the environment (not the repo)"
            )
        return OpenAICompatibleProvider(
            name=name, model=model or os.getenv("LLM_MODEL", default_model),
            api_key=key, base_url=base_url,
        )
    if name == "gemini":
        raise NotImplementedError(
            "gemini provider is declared in config but not yet implemented; "
            "use 'ollama' (default) or 'openai'/'groq'"
        )
    raise ValueError(f"unknown LLM_PROVIDER {name!r}")

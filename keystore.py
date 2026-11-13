"""API keys for every voice engine, read from the local .env file.

Security rule: key VALUES never leave this module except through get_key(),
which the engine clients put into a request header. Everything else
(browser, logs, history) only ever sees key NAMES like "MAIN".
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"

ENGINES = {
    "murf": {"label": "Murf", "prefix": "MURF_KEY_"},
    "elevenlabs": {"label": "ElevenLabs", "prefix": "ELEVEN_KEY_"},
}


class VoiceError(Exception):
    """An error with a plain-language message that is safe to show the user."""


def _all_keys(prefix: str) -> dict[str, str]:
    """{NAME: value} for every PREFIX* variable (re-read on every call, so
    the user can edit .env without restarting). .env wins over the system env."""
    keys: dict[str, str] = {}
    for source in (os.environ, dotenv_values(ENV_PATH) if ENV_PATH.exists() else {}):
        for var, value in source.items():
            if (var.startswith(prefix) and value and value.strip()
                    and not value.strip().startswith("paste_your")):  # .env.example placeholder
                name = var[len(prefix):]
                if name:
                    keys[name] = value.strip()
    return keys


def list_key_names(engine: str) -> list[str]:
    """Names only (e.g. ["MAIN", "PROJECT2"]) - safe to send to the browser."""
    return sorted(_all_keys(ENGINES[engine]["prefix"]))


def get_key(engine: str, name: str) -> str:
    prefix = ENGINES[engine]["prefix"]
    key = _all_keys(prefix).get(name or "")
    if not key:
        raise VoiceError(
            f"{ENGINES[engine]['label']} API key '{name}' was not found. Add a line "
            f"{prefix}{name}=your_key to the .env file."
        )
    return key


def scrub(text: str) -> str:
    """Remove any key value (of any engine) that might appear in a message."""
    for engine in ENGINES.values():
        for value in _all_keys(engine["prefix"]).values():
            if value and value in text:
                text = text.replace(value, "***")
    return text

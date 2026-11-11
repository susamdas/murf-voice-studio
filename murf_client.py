"""Small Murf API client.

Security rule: API key VALUES never leave this module except inside the
`api-key` request header sent to Murf. Callers only ever see key NAMES.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import requests
from dotenv import dotenv_values

BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
KEY_PREFIX = "MURF_KEY_"

STREAM_URL = "https://global.api.murf.ai/v1/speech/stream"
VOICES_URL = "https://api.murf.ai/v1/speech/voices"
TIMEOUT_SECONDS = 60


class MurfError(Exception):
    """An error with a plain-language message that is safe to show the user."""


# ---------------------------------------------------------------- API keys

def _all_keys() -> dict[str, str]:
    """Return {NAME: value} for every MURF_KEY_* variable.

    Re-read on every call so the user can edit .env without restarting.
    Values from .env take priority over system environment variables.
    """
    keys: dict[str, str] = {}
    for source in (os.environ, dotenv_values(ENV_PATH) if ENV_PATH.exists() else {}):
        for var, value in source.items():
            if (var.startswith(KEY_PREFIX) and value and value.strip()
                    and not value.strip().startswith("paste_your")):  # .env.example placeholder
                name = var[len(KEY_PREFIX):]
                if name:
                    keys[name] = value.strip()
    return keys


def list_key_names() -> list[str]:
    """Names only (e.g. ["MAIN", "PROJECT2"]) - safe to send to the browser."""
    return sorted(_all_keys())


def _get_key(name: str) -> str:
    key = _all_keys().get(name or "")
    if not key:
        raise MurfError(
            f"API key '{name}' was not found. Add a line "
            f"{KEY_PREFIX}{name}=your_key to the .env file."
        )
    return key


def _scrub(text: str) -> str:
    """Remove any key value that might appear in a message."""
    for value in _all_keys().values():
        if value and value in text:
            text = text.replace(value, "***")
    return text


# ---------------------------------------------------------------- errors

def _murf_message(resp: requests.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        return resp.text.strip()[:300]
    if isinstance(data, dict):
        for field in ("errorMessage", "message", "error", "detail"):
            if data.get(field):
                return str(data[field])
    return str(data)[:300]


def _friendly_error(resp: requests.Response, key_name: str) -> MurfError:
    status = resp.status_code
    msg = _scrub(_murf_message(resp))
    low = msg.lower()

    if status in (401, 403) or "api key" in low or "api-key" in low:
        text = f"Invalid API key '{key_name}'. Check the value of {KEY_PREFIX}{key_name} in .env."
    elif status == 402 or "credit" in low or "quota" in low or "character limit" in low:
        text = f"Out of credits on API key '{key_name}'. Top up your Murf account or use another key."
    elif status == 429:
        text = "Too many requests at once. Wait a moment and try again."
    elif status >= 500:
        text = "Murf's service is having a problem right now. Please try again in a few minutes."
    elif "locale" in low or "voice" in low or "style" in low:
        text = "Voice not available with these settings (voice / locale / style)."
    elif status == 400:
        text = "Murf rejected the request."
    else:
        text = f"Murf returned an error (HTTP {status})."

    if msg:
        text += f" Murf says: {msg}"
    return MurfError(text)


def _request(method: str, url: str, key_name: str, **kwargs) -> requests.Response:
    """Send a request with a 60 s timeout and one retry on network errors."""
    headers = {"api-key": _get_key(key_name), **kwargs.pop("headers", {})}
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            return requests.request(method, url, headers=headers,
                                    timeout=TIMEOUT_SECONDS, **kwargs)
        except requests.Timeout as exc:
            last_exc = exc
        except requests.ConnectionError as exc:
            last_exc = exc
        if attempt == 0:
            time.sleep(1.5)
    if isinstance(last_exc, requests.Timeout):
        raise MurfError(f"Murf did not respond within {TIMEOUT_SECONDS} seconds (tried twice). Please try again.")
    raise MurfError("Could not connect to Murf (tried twice). Check your internet connection.")


# ---------------------------------------------------------------- public API

def synthesize(text: str, profile: dict) -> bytes:
    """Generate speech for one line and return the audio bytes."""
    text = (text or "").strip()
    if not text:
        raise MurfError("The line is empty.")

    fmt = str(profile.get("format") or "MP3").upper()
    body = {
        "text": text,
        "voiceId": profile.get("voice_id"),
        "model": profile.get("model") or "falcon-2.1",
        "format": fmt,
        "sampleRate": int(profile.get("sample_rate") or 24000),
        "channelType": "MONO",
        # Murf limits: integers -50..50 (0 = normal)
        "rate": max(-50, min(50, int(profile.get("rate") or 0))),
        "pitch": max(-50, min(50, int(profile.get("pitch") or 0))),
    }
    if profile.get("style"):
        body["style"] = profile["style"]
    if profile.get("locale"):
        body["locale"] = profile["locale"]

    key_name = profile.get("api_key_name", "")
    resp = _request("POST", STREAM_URL, key_name, json=body, stream=True)
    try:
        if resp.status_code != 200:
            resp.content  # read body for the error message
            raise _friendly_error(resp, key_name)

        ctype = resp.headers.get("Content-Type", "").lower()
        deadline = time.monotonic() + TIMEOUT_SECONDS * 2
        chunks = []
        try:
            for chunk in resp.iter_content(chunk_size=16384):
                chunks.append(chunk)
                if time.monotonic() > deadline:
                    raise MurfError("Audio download took too long. Please try again.")
        except requests.RequestException:
            raise MurfError("The connection to Murf dropped while receiving audio. Please try again.")
        audio = b"".join(chunks)
    finally:
        resp.close()

    if "json" in ctype:  # 200 but an error object instead of audio
        try:
            import json
            data = json.loads(audio.decode("utf-8"))
            msg = data.get("errorMessage") or data.get("message") or str(data)
        except Exception:
            msg = "unknown response"
        raise MurfError("Murf did not return audio. Murf says: " + _scrub(str(msg)))
    if len(audio) < 100:
        raise MurfError("Murf returned an empty audio file. Try again or check the voice settings.")
    return audio


def list_voices(key_name: str, model: str = "falcon-2.1") -> list[dict]:
    """Return Murf's voice list, simplified for the UI."""
    resp = _request("GET", VOICES_URL, key_name, params={"model": model})
    if resp.status_code != 200:
        raise _friendly_error(resp, key_name)
    try:
        raw = resp.json()
    except ValueError:
        raise MurfError("Murf returned an unexpected voice list.")

    voices = []
    for v in raw if isinstance(raw, list) else []:
        locales = {}
        for code, detail in (v.get("supportedLocales") or {}).items():
            locales[code] = {
                "detail": (detail or {}).get("detail", code),
                "styles": (detail or {}).get("availableStyles") or [],
            }
        if not locales and v.get("locale"):
            locales[v["locale"]] = {"detail": v.get("displayLanguage", v["locale"]),
                                    "styles": v.get("availableStyles") or []}
        voices.append({
            "voice_id": v.get("voiceId", ""),
            "display_name": v.get("displayName") or v.get("voiceId", ""),
            "gender": v.get("gender", ""),
            "description": v.get("description", ""),
            "locale": v.get("locale", ""),
            "locales": locales,
        })
    voices.sort(key=lambda x: x["display_name"].lower())
    return voices

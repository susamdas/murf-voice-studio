"""Small ElevenLabs API client (text to speech with emotion tags).

Security rule: API key VALUES are only fetched from keystore and put into the
`xi-api-key` request header. Callers only ever see key NAMES.

Docs: POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}
      GET  https://api.elevenlabs.io/v2/voices
"""

from __future__ import annotations

import time

import requests

import keystore
from keystore import VoiceError

BASE_URL = "https://api.elevenlabs.io"
TIMEOUT_SECONDS = 60
DEFAULT_MODEL = "eleven_v3"
MODELS = ["eleven_v3", "eleven_v4", "eleven_multilingual_v2", "eleven_flash_v2_5"]

# The app's speed slider is a "rate" like Murf's; ElevenLabs uses a speed
# factor 0.7-1.2, so speed = 1 + rate/100 and rate is limited to -30..+20.
RATE_MIN, RATE_MAX = -30, 20
SAMPLE_RATES = (16000, 22050, 24000, 44100)
STABILITY = {"Creative": 0.0, "Natural": 0.5, "Robust": 1.0}  # v3 presets


def list_key_names() -> list[str]:
    return keystore.list_key_names("elevenlabs")


def output_format(fmt: str, sample_rate: int) -> str:
    """Map the profile's format + sample rate to an ElevenLabs output_format."""
    if fmt.upper() == "WAV":
        return f"wav_{sample_rate if sample_rate in SAMPLE_RATES else 24000}"
    return {22050: "mp3_22050_32", 24000: "mp3_24000_48"}.get(sample_rate, "mp3_44100_128")


# ---------------------------------------------------------------- errors

def _message(resp: requests.Response) -> tuple[str, str]:
    """Return (status_code_word, message) from an ElevenLabs error body."""
    try:
        data = resp.json()
    except ValueError:
        return "", resp.text.strip()[:300]
    detail = data.get("detail") if isinstance(data, dict) else None
    if isinstance(detail, dict):
        return str(detail.get("status") or detail.get("code") or ""), str(detail.get("message") or detail)
    if isinstance(detail, list):  # 422 validation errors
        return "invalid", "; ".join(str(d.get("msg", d)) for d in detail[:3])
    return "", str(data)[:300]


def _friendly_error(resp: requests.Response, key_name: str) -> VoiceError:
    status = resp.status_code
    code, msg = _message(resp)
    msg = keystore.scrub(msg)
    low = (code + " " + msg).lower()

    if "missing_permissions" in low or "permission" in low:
        text = (f"ElevenLabs API key '{key_name}' is not allowed to do this. In ElevenLabs, open "
                "Profile → API Keys, edit the key and turn on the needed permission.")
    elif "free users" in low or "upgrade" in low or "subscription" in low:
        text = ("Your ElevenLabs plan doesn't allow this. Free plans can only use ElevenLabs' default voices "
                "through the API; Voice Library voices need a paid plan (Starter or higher).")
    elif "quota" in low or "credits" in low or status == 402:
        # checked before 401: ElevenLabs sends quota_exceeded with HTTP 401
        text = f"Out of credits on ElevenLabs key '{key_name}'. Top up or wait for your monthly reset."
    elif status == 401 or "invalid_api_key" in low:
        text = f"Invalid API key '{key_name}' (ElevenLabs). Check ELEVEN_KEY_{key_name} in .env."
    elif status == 404 or "voice_not_found" in low:
        text = "Voice not available: this ElevenLabs voice ID was not found in your account."
    elif status == 429 or "too_many" in low or "busy" in low:
        text = "ElevenLabs is busy or too many requests at once. Wait a moment and try again."
    elif status >= 500:
        text = "ElevenLabs' service is having a problem right now. Please try again in a few minutes."
    else:
        text = f"ElevenLabs rejected the request (HTTP {status})."
    if msg:
        text += f" ElevenLabs says: {msg}"
    return VoiceError(text)


def _request(method: str, path: str, key_name: str, **kwargs) -> requests.Response:
    """60 s timeout and one retry on network errors (same rules as Murf)."""
    headers = {"xi-api-key": keystore.get_key("elevenlabs", key_name), **kwargs.pop("headers", {})}
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            return requests.request(method, BASE_URL + path, headers=headers,
                                    timeout=TIMEOUT_SECONDS, **kwargs)
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_exc = exc
        if attempt == 0:
            time.sleep(1.5)
    if isinstance(last_exc, requests.Timeout):
        raise VoiceError(f"ElevenLabs did not respond within {TIMEOUT_SECONDS} seconds (tried twice).")
    raise VoiceError("Could not connect to ElevenLabs (tried twice). Check your internet connection.")


# ---------------------------------------------------------------- public API

def synthesize(text: str, profile: dict, previous_text: str | None = None,
               next_text: str | None = None) -> bytes:
    """Generate speech for one text part and return the audio bytes.

    Emotion tags like [whispers] or [sad] are passed through unchanged.
    previous_text / next_text keep the delivery natural across [pause] splits.
    """
    text = (text or "").strip()
    if not text:
        raise VoiceError("The line is empty.")
    voice_id = (profile.get("voice_id") or "").strip()
    if not voice_id:
        raise VoiceError("This ElevenLabs profile has no voice ID.")

    rate = max(RATE_MIN, min(RATE_MAX, int(profile.get("rate") or 0)))
    stability = float(profile.get("stability", 0.5))
    body: dict = {
        "text": text,
        "model_id": profile.get("model") or DEFAULT_MODEL,
        "voice_settings": {"stability": stability, "similarity_boost": 0.75,
                           "speed": round(1 + rate / 100, 2)},
    }
    lang = (profile.get("locale") or "").split("-")[0].strip().lower()
    if lang:
        body["language_code"] = lang  # e.g. "bn"; ignored by models that don't support it
    if previous_text:
        body["previous_text"] = previous_text
    if next_text:
        body["next_text"] = next_text

    fmt = str(profile.get("format") or "MP3").upper()
    params = {"output_format": output_format(fmt, int(profile.get("sample_rate") or 44100))}
    key_name = profile.get("api_key_name", "")
    resp = _request("POST", f"/v1/text-to-speech/{requests.utils.quote(voice_id, safe='')}",
                    key_name, params=params, json=body)
    if resp.status_code != 200:
        raise _friendly_error(resp, key_name)
    if "json" in resp.headers.get("Content-Type", "").lower():
        raise VoiceError("ElevenLabs did not return audio. ElevenLabs says: "
                         + keystore.scrub(_message(resp)[1]))
    if len(resp.content) < 100:
        raise VoiceError("ElevenLabs returned an empty audio file. Try again or check the voice.")
    return resp.content


def list_voices(key_name: str) -> list[dict]:
    """Voices in the user's ElevenLabs library ("My Voices"), simplified for the UI."""
    voices, token = [], None
    for _ in range(5):  # up to 500 voices
        params = {"page_size": 100}
        if token:
            params["next_page_token"] = token
        resp = _request("GET", "/v2/voices", key_name, params=params)
        if resp.status_code != 200:
            raise _friendly_error(resp, key_name)
        data = resp.json()
        for v in data.get("voices", []):
            labels = v.get("labels") or {}
            langs = sorted({l.get("language") for l in (v.get("verified_languages") or []) if l.get("language")})
            voices.append({
                "voice_id": v.get("voice_id", ""),
                "display_name": v.get("name") or v.get("voice_id", ""),
                "gender": labels.get("gender", ""),
                "age": labels.get("age", ""),
                "accent": labels.get("accent", ""),
                "description": labels.get("descriptive") or labels.get("description") or "",
                "category": v.get("category", ""),
                "languages": langs,
            })
        if not data.get("has_more"):
            break
        token = data.get("next_page_token")
    voices.sort(key=lambda x: x["display_name"].lower())
    return voices

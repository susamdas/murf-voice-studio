"""Murf Voice Studio - local voiceover generator.

Run:  python app.py   (or double-click start.bat)
Then open http://localhost:5050
"""

from __future__ import annotations

import csv
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import uuid
import webbrowser
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

import audio_tools
import autofit
import murf_client
from murf_client import MurfError

BASE_DIR = Path(__file__).resolve().parent
VOICES_PATH = BASE_DIR / "voices.json"
OUTPUT_DIR = BASE_DIR / "output"
CACHE_DIR = BASE_DIR / ".preview_cache"
HISTORY_PATH = BASE_DIR / "history.csv"
HISTORY_FIELDS = ["datetime", "project", "profile", "file_name", "text", "status"]

HOST = "127.0.0.1"  # local PC only
PORT = int(os.environ.get("PORT", "5050"))

FORMATS = ("MP3", "WAV")
SAMPLE_RATES = (8000, 16000, 24000, 44100, 48000)
DEFAULT_MODEL = "falcon-2.1"

app = Flask(__name__)
app.json.ensure_ascii = False

# Generated audio waiting to be saved: {preview_id: {...}}
PREVIEWS: dict[str, dict] = {}
_lock = threading.Lock()


# =============================================================== helpers

def ok(**data):
    return jsonify({"ok": True, **data})


def fail(message: str, status: int = 400):
    return jsonify({"ok": False, "error": message}), status


def safe_name(name: str, fallback: str = "untitled") -> str:
    """Make a string safe to use as a Windows file/folder name."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(name or "")).strip().strip(".")
    name = re.sub(r"\s+", " ", name)[:80].strip()
    if name.upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                        *(f"LPT{i}" for i in range(1, 10))}:
        name = "_" + name
    return name or fallback


def unique_path(folder: Path, stem: str, ext: str) -> Path:
    """Never overwrite: name.mp3, then name_v2.mp3, name_v3.mp3, ..."""
    path = folder / f"{stem}.{ext}"
    n = 2
    while path.exists():
        path = folder / f"{stem}_v{n}.{ext}"
        n += 1
    return path


def log_history(project: str, profile: str, file_name: str, text: str, status: str) -> None:
    """Append one row to history.csv (API keys are never passed here)."""
    new_file = not HISTORY_PATH.exists()
    try:
        with _lock, open(HISTORY_PATH, "a", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            if new_file:
                writer.writerow(HISTORY_FIELDS)
            writer.writerow([datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                             project, profile, file_name, text, status])
    except PermissionError:
        # Usually because history.csv is open in Excel - never crash for this.
        print("Note: could not write history.csv (is it open in Excel?)")


# =============================================================== profiles

def load_profiles() -> list[dict]:
    if not VOICES_PATH.exists():
        return []
    try:
        data = json.loads(VOICES_PATH.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"voices.json is not valid JSON (line {exc.lineno}). Fix or delete the file.")
    return data if isinstance(data, list) else []


def clean_profile(p: dict) -> dict:
    """Validate one profile coming from the UI and return a normalised copy."""
    if not isinstance(p, dict):
        raise ValueError("Invalid profile data.")
    name = str(p.get("name", "")).strip()
    voice_id = str(p.get("voice_id", "")).strip()
    if not name:
        raise ValueError("Every profile needs a name.")
    if not voice_id:
        raise ValueError(f"Profile '{name}' needs a voice ID.")
    fmt = str(p.get("format") or "MP3").upper()
    if fmt not in FORMATS:
        raise ValueError(f"Profile '{name}': format must be MP3 or WAV.")
    try:
        rate = int(p.get("sample_rate") or 24000)
    except (TypeError, ValueError):
        rate = 0
    if rate not in SAMPLE_RATES:
        raise ValueError(f"Profile '{name}': sample rate must be one of {', '.join(map(str, SAMPLE_RATES))}.")
    speed = _int_in_range(p.get("rate"), autofit.RATE_MIN, autofit.RATE_MAX, f"Profile '{name}': speed")
    pitch = _int_in_range(p.get("pitch"), autofit.PITCH_MIN, autofit.PITCH_MAX, f"Profile '{name}': pitch")
    return {
        "name": name,
        "voice_id": voice_id,
        "style": str(p.get("style") or "").strip(),
        "locale": str(p.get("locale") or "").strip(),
        "model": str(p.get("model") or DEFAULT_MODEL).strip(),
        "api_key_name": str(p.get("api_key_name") or "").strip(),
        "format": fmt,
        "sample_rate": rate,
        "rate": speed,
        "pitch": pitch,
    }


def _int_in_range(value, lo: int, hi: int, label: str) -> int:
    if value in (None, ""):
        return 0
    try:
        v = int(round(float(value)))
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a number.")
    if not lo <= v <= hi:
        raise ValueError(f"{label} must be between {lo} and {hi}.")
    return v


def _seconds(value, label: str) -> float | None:
    """Parse a seconds value from the UI ('4.5', '4,5' or empty)."""
    if value in (None, ""):
        return None
    try:
        v = float(str(value).replace(",", "."))
    except ValueError:
        raise ValueError(f"{label} must be a number of seconds.")
    if v < 0 or v > 600:
        raise ValueError(f"{label} must be between 0 and 600 seconds.")
    return v


def save_profiles(profiles: list[dict]) -> list[dict]:
    cleaned = [clean_profile(p) for p in profiles]
    names = [p["name"].lower() for p in cleaned]
    dupes = {n for n in names if names.count(n) > 1}
    if dupes:
        raise ValueError("Profile names must be unique. Duplicate: " + ", ".join(sorted(dupes)))
    tmp = VOICES_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, VOICES_PATH)
    return cleaned


def find_profile(name: str) -> dict:
    for p in load_profiles():
        if p.get("name") == name:
            return p
    raise ValueError(f"Voice profile '{name}' was not found. Reload the page.")


# =============================================================== routes

@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/keys")
def api_keys():
    return ok(keys=murf_client.list_key_names())  # names only, never values


@app.get("/api/profiles")
def api_get_profiles():
    try:
        return ok(profiles=load_profiles())
    except ValueError as exc:
        return fail(str(exc))


@app.put("/api/profiles")
def api_put_profiles():
    data = request.get_json(silent=True) or {}
    try:
        return ok(profiles=save_profiles(data.get("profiles") or []))
    except ValueError as exc:
        return fail(str(exc))


@app.get("/api/murf-voices")
def api_murf_voices():
    key_name = request.args.get("key", "")
    try:
        return ok(voices=murf_client.list_voices(key_name, request.args.get("model") or DEFAULT_MODEL))
    except MurfError as exc:
        return fail(str(exc), 502)


@app.get("/api/config")
def api_config():
    return ok(rate_min=autofit.RATE_MIN, rate_max=autofit.RATE_MAX,
              pitch_min=autofit.PITCH_MIN, pitch_max=autofit.PITCH_MAX,
              natural_rate_min=autofit.NATURAL_RATE_MIN, natural_rate_max=autofit.NATURAL_RATE_MAX,
              max_tries=autofit.MAX_TRIES, default_lead=autofit.DEFAULT_LEAD_S)


def _history_status(r: dict) -> str:
    base = f"{r['duration']:.2f}s, speed {r['rate']:+d}, pitch {r['pitch']:+d}"
    if r["fit_status"] == "fitted":
        text = f"fitted {base}, speech {r['speech_duration']:.2f}s, {r['tries']} tries"
    elif r["fit_status"] == "too_long":
        text = f"too long {base}, {r['tries']} tries"
    else:
        text = f"generated {base}"
    return text + (f" - {r['warning']}" if r.get("warning") else "")


@app.post("/api/generate")
def api_generate():
    data = request.get_json(silent=True) or {}
    project = safe_name(data.get("project"), "My project")
    file_name = safe_name(data.get("file_name"), "line")
    text = str(data.get("text") or "").strip()
    profile_name = str(data.get("profile") or "")

    try:
        profile = find_profile(profile_name)
        # per-line values override the profile defaults
        rate = _int_in_range(data["rate"] if data.get("rate") not in (None, "") else profile.get("rate"),
                             autofit.RATE_MIN, autofit.RATE_MAX, "Speed")
        pitch = _int_in_range(data["pitch"] if data.get("pitch") not in (None, "") else profile.get("pitch"),
                              autofit.PITCH_MIN, autofit.PITCH_MAX, "Pitch")
        target = _seconds(data.get("target"), "Target length")
        if target is not None and target < 0.5:
            raise ValueError("Target length must be at least 0.5 seconds.")
        before = _seconds(data.get("silence_before"), "Silence before")
        after = _seconds(data.get("silence_after"), "Silence after")
    except ValueError as exc:
        return fail(str(exc))

    fmt = profile.get("format", "MP3")

    def synth(r: int, p: int) -> bytes:
        return murf_client.synthesize(text, {**profile, "rate": r, "pitch": p})

    try:
        result = autofit.generate_line(synth, fmt, rate, pitch, target=target,
                                       auto_fit=bool(data.get("auto_fit")), before=before, after=after)
    except (MurfError, audio_tools.MergeError) as exc:
        log_history(project, profile_name, file_name, text, f"error: {exc}")
        return fail(str(exc), 502)
    except Exception as exc:  # never crash the app
        log_history(project, profile_name, file_name, text, "error: unexpected")
        return fail(f"Unexpected error: {type(exc).__name__}", 500)

    ext = fmt.lower()
    pid = uuid.uuid4().hex
    CACHE_DIR.mkdir(exist_ok=True)
    path = CACHE_DIR / f"{pid}.{ext}"
    path.write_bytes(result.pop("audio"))
    with _lock:
        PREVIEWS[pid] = {"path": path, "ext": ext, "project": project, "profile": profile_name,
                         "file_name": file_name, "text": text}
    log_history(project, profile_name, file_name, text, _history_status(result))
    return ok(preview_id=pid, url=f"/api/preview/{pid}", target=target, **result)


@app.get("/api/preview/<pid>")
def api_preview(pid: str):
    item = PREVIEWS.get(pid)
    if not item or not item["path"].exists():
        return fail("This preview has expired. Please generate again.", 404)
    mime = "audio/mpeg" if item["ext"] == "mp3" else "audio/wav"
    return send_file(item["path"], mimetype=mime, conditional=True)


def _profile_folder(project: str, profile: str) -> Path:
    folder = OUTPUT_DIR / safe_name(project, "My project") / safe_name(profile, "voice")
    folder.mkdir(parents=True, exist_ok=True)
    return folder


@app.post("/api/save")
def api_save():
    data = request.get_json(silent=True) or {}
    item = PREVIEWS.get(str(data.get("preview_id") or ""))
    if not item or not item["path"].exists():
        return fail("This preview has expired. Please generate again.", 404)

    project = safe_name(data.get("project") or item["project"], "My project")
    file_name = safe_name(data.get("file_name") or item["file_name"], "line")
    folder = _profile_folder(project, item["profile"])
    with _lock:
        dest = unique_path(folder, file_name, item["ext"])
        shutil.copyfile(item["path"], dest)
    rel = dest.relative_to(BASE_DIR).as_posix()
    log_history(project, item["profile"], dest.name, item["text"], "saved")
    return ok(saved_to=rel, file=dest.name)


@app.post("/api/merge")
def api_merge():
    data = request.get_json(silent=True) or {}
    ids = data.get("preview_ids") or []
    items = [PREVIEWS.get(str(i)) for i in ids]
    if not items:
        return fail("Generate some lines first.")
    if any(i is None or not i["path"].exists() for i in items):
        return fail("Some previews have expired. Please generate again.", 404)
    exts = {i["ext"] for i in items}
    profiles = {i["profile"] for i in items}
    if len(exts) > 1:
        return fail("All lines must use the same format (MP3 or WAV) to merge.")

    try:
        pause = float(data.get("pause", 0.5))
    except (TypeError, ValueError):
        pause = 0.5
    try:
        merged = audio_tools.merge([i["path"].read_bytes() for i in items], exts.pop(), pause)
    except audio_tools.MergeError as exc:
        return fail(str(exc))

    project = safe_name(data.get("project") or items[0]["project"], "My project")
    profile = items[0]["profile"] if len(profiles) == 1 else "mixed voices"
    name = safe_name(data.get("file_name") or f"{project}_full", "merged")
    folder = _profile_folder(project, profile)
    with _lock:
        dest = unique_path(folder, name, items[0]["ext"])
        dest.write_bytes(merged)
    rel = dest.relative_to(BASE_DIR).as_posix()
    log_history(project, profile, dest.name, " / ".join(i["text"] for i in items), "merged")
    return ok(saved_to=rel, file=dest.name)


@app.post("/api/open-folder")
def api_open_folder():
    data = request.get_json(silent=True) or {}
    folder = OUTPUT_DIR
    if data.get("project"):
        folder = OUTPUT_DIR / safe_name(data["project"])
    if not folder.exists():
        folder = OUTPUT_DIR
    folder.mkdir(exist_ok=True)
    try:
        if sys.platform.startswith("win"):
            os.startfile(folder)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(folder)])
        else:
            subprocess.Popen(["xdg-open", str(folder)])
    except OSError:
        return fail(f"Could not open the folder. It is here: {folder}")
    return ok(folder=str(folder))


@app.get("/api/history")
def api_history():
    if not HISTORY_PATH.exists():
        return ok(rows=[])
    try:
        with open(HISTORY_PATH, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
    except (OSError, csv.Error):
        return fail("Could not read history.csv.")
    return ok(rows=rows[-100:][::-1])


# =============================================================== start

def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    shutil.rmtree(CACHE_DIR, ignore_errors=True)  # old previews from last run
    CACHE_DIR.mkdir(exist_ok=True)

    url = f"http://localhost:{PORT}"
    keys = murf_client.list_key_names()
    print("=" * 56)
    print(" Murf Voice Studio")
    print(f" Open in your browser:  {url}")
    print(f" API keys found: {', '.join(keys) if keys else 'NONE - add MURF_KEY_... to .env'}")
    print(" Keep this window open. Close it to stop the app.")
    print("=" * 56)

    if os.environ.get("NO_BROWSER") != "1":
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    app.run(host=HOST, port=PORT, debug=False, threaded=True)


if __name__ == "__main__":
    main()

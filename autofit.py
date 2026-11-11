"""Generate one line and (optionally) fit it to a target video length.

Auto-fit steps:
  1. Generate at the line's speed and measure the speech length.
  2. If too long / much too short, estimate a better speed and regenerate
     (at most MAX_TRIES generations in total, speeds kept in the natural range).
  3. Pick the best try. If shorter than the target, pad with silence
     (0.5 s at the start - or the user's "silence before" - and the rest at the end).
     If still too long, return it with a warning.
"""

from __future__ import annotations

from typing import Callable

import audio_tools
from murf_client import MurfError

# Murf API limits (POST /v1/speech/stream: rate and pitch are integers -50..50)
RATE_MIN, RATE_MAX = -50, 50
PITCH_MIN, PITCH_MAX = -50, 50

# Auto-fit never picks a speed outside this range, so the voice stays natural.
NATURAL_RATE_MIN, NATURAL_RATE_MAX = -20, 20
MAX_TRIES = 3            # total generations per line, including the first
DEFAULT_LEAD_S = 0.5     # silence at the start when padding
MAX_SILENCE_S = 30.0


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _tolerance(target: float) -> float:
    """How much shorter than the target is 'close enough' (the gap becomes silence)."""
    return max(0.15, 0.04 * target)


def _next_rate(tries: list[dict], aim: float) -> int:
    """Estimate the speed that gives `aim` seconds of speech.

    Model: duration ~ k / (100 + rate). With two or more tries, use a secant
    step between the last two (handles voices that don't follow the model).
    """
    last = tries[-1]
    guess = (100 + last["rate"]) * last["duration"] / aim - 100
    if len(tries) >= 2:
        prev = tries[-2]
        dd = last["duration"] - prev["duration"]
        if abs(dd) > 0.02 and last["rate"] != prev["rate"]:
            slope = dd / (last["rate"] - prev["rate"])  # seconds per rate step (negative)
            if slope < 0:
                guess = last["rate"] + (aim - last["duration"]) / slope
    return int(round(clamp(guess, NATURAL_RATE_MIN, NATURAL_RATE_MAX)))


def generate_line(synth: Callable[[int, int], bytes], fmt: str, rate: int, pitch: int,
                  target: float | None = None, auto_fit: bool = False,
                  before: float | None = None, after: float | None = None) -> dict:
    """Return the final audio and a report of what was done.

    synth(rate, pitch) -> audio bytes (one Murf call).
    before / after: manual silence in seconds (None = not set).
    """
    rate = int(clamp(rate, RATE_MIN, RATE_MAX))
    pitch = int(clamp(pitch, PITCH_MIN, PITCH_MAX))
    manual_before = before is not None and before > 0
    before = clamp(before or 0.0, 0.0, MAX_SILENCE_S)
    after = clamp(after or 0.0, 0.0, MAX_SILENCE_S)

    def one_try(r: int) -> dict:
        audio = synth(r, pitch)
        return {"rate": r, "audio": audio, "duration": audio_tools.duration(audio, fmt)}

    # ---- no fitting: generate once, add manual silence -----------------
    if not (auto_fit and target):
        t = one_try(rate)
        audio = audio_tools.add_silence(t["audio"], fmt, before, after)
        final = audio_tools.duration(audio, fmt)
        warning = None
        if target and final > target + 0.05:
            warning = f"{final:.1f} sec is longer than the {target:g} sec target (Auto-fit is off)."
        return _report(audio, final, t, [t], pitch, "generated", warning)

    # ---- auto-fit -------------------------------------------------------
    speech_target = target - before - after
    if speech_target < 0.3:
        raise MurfError(f"Silence before/after ({before:g} + {after:g} sec) leaves no room "
                        f"for speech in a {target:g} sec clip.")
    tol = _tolerance(speech_target)
    aim = speech_target - tol / 2   # aim slightly short: a small gap is filled with silence

    tries: list[dict] = []
    note = None
    r = rate
    while len(tries) < MAX_TRIES:
        try:
            tries.append(one_try(r))
        except MurfError as exc:
            if not tries:
                raise
            note = f"Stopped after {len(tries)} tries: {exc}"
            break
        d = tries[-1]["duration"]
        if speech_target - tol <= d <= speech_target:
            break  # close enough
        if d > speech_target and r >= NATURAL_RATE_MAX:
            break  # already at the fastest natural speed
        if d < speech_target and r <= NATURAL_RATE_MIN:
            break  # already at the slowest natural speed: pad with silence
        nxt = _next_rate(tries, aim)
        if nxt in {t["rate"] for t in tries}:
            break  # no new speed left to try
        r = nxt

    fitting = [t for t in tries if t["duration"] <= speech_target + 0.01]
    if fitting:
        best = max(fitting, key=lambda t: t["duration"])  # least silence needed
        gap = target - best["duration"]
        lead = before if manual_before else min(DEFAULT_LEAD_S, max(0.0, gap - after))
        audio = audio_tools.pad_to_length(best["audio"], fmt, target, lead)
        final = audio_tools.duration(audio, fmt)
        return _report(audio, final, best, tries, pitch, "fitted", note)

    best = min(tries, key=lambda t: t["duration"])  # closest from above
    audio = audio_tools.add_silence(best["audio"], fmt, before, after)
    final = audio_tools.duration(audio, fmt)
    warning = (f"Line too long for {target:g} sec – shorten the text or make the clip longer. "
               f"(Speech is {best['duration']:.1f} sec at speed {best['rate']:+d}"
               + (", the fastest natural speed)" if best["rate"] >= NATURAL_RATE_MAX else ")"))
    if note:
        warning += " " + note
    return _report(audio, final, best, tries, pitch, "too_long", warning)


def _report(audio: bytes, final: float, best: dict, tries: list[dict], pitch: int,
            status: str, warning: str | None) -> dict:
    return {
        "audio": audio,
        "duration": round(final, 2),
        "speech_duration": round(best["duration"], 2),
        "rate": best["rate"],
        "pitch": pitch,
        "tries": len(tries),
        "attempts": [{"rate": t["rate"], "duration": round(t["duration"], 2)} for t in tries],
        "fit_status": status,
        "warning": warning,
    }

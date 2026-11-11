"""Merge several audio clips into one file with a short pause between them.

Pure Python (no ffmpeg needed):
- WAV: PCM samples are joined and silence samples inserted.
- MP3: MPEG audio frames are joined and silent frames inserted.
"""

from __future__ import annotations

import io
import struct
import wave


class MergeError(Exception):
    pass


# =============================================================== WAV

def _read_wav(data: bytes) -> tuple[tuple[int, int, int], bytes]:
    """Return ((channels, sample_width, sample_rate), pcm_bytes).

    Parsed by hand because streamed WAV headers often have a wrong data size.
    """
    if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise MergeError("One of the clips is not a valid WAV file.")
    pos, fmt = 12, None
    while pos + 8 <= len(data):
        cid = data[pos:pos + 4]
        size = struct.unpack("<I", data[pos + 4:pos + 8])[0]
        body_start = pos + 8
        if cid == b"fmt ":
            audio_fmt, ch, rate, _, _, bits = struct.unpack("<HHIIHH", data[body_start:body_start + 16])
            if audio_fmt not in (1, 0xFFFE):
                raise MergeError("Only PCM WAV clips can be merged.")
            fmt = (ch, bits // 8, rate)
        elif cid == b"data":
            if fmt is None:
                raise MergeError("WAV clip has no format header.")
            end = body_start + size
            if size == 0 or size == 0xFFFFFFFF or end > len(data):
                end = len(data)  # streamed: take everything that is there
            pcm = data[body_start:end]
            block = fmt[0] * fmt[1]
            return fmt, pcm[: len(pcm) - len(pcm) % block]
        pos = body_start + size + (size & 1)
    raise MergeError("WAV clip has no audio data.")


def merge_wav(clips: list[bytes], pause_s: float) -> bytes:
    parsed = [_read_wav(c) for c in clips]
    fmt = parsed[0][0]
    if any(p[0] != fmt for p in parsed):
        raise MergeError("Clips have different audio settings and cannot be merged.")
    ch, width, rate = fmt
    silence = b"\x00" * (int(rate * pause_s) * ch * width)

    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(ch)
        w.setsampwidth(width)
        w.setframerate(rate)
        for i, (_, pcm) in enumerate(parsed):
            if i and silence:
                w.writeframes(silence)
            w.writeframes(pcm)
    return out.getvalue()


# =============================================================== MP3

_BITRATES = {  # Layer III only, kbps
    "1": [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320],
    "2": [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],
}
_RATES = {3: [44100, 48000, 32000], 2: [22050, 24000, 16000], 0: [11025, 12000, 8000]}


def _parse_header(h: bytes):
    """Return frame info for a Layer III header, or None if not a valid header."""
    if len(h) < 4 or h[0] != 0xFF or (h[1] & 0xE0) != 0xE0:
        return None
    version = (h[1] >> 3) & 3          # 3=MPEG1, 2=MPEG2, 0=MPEG2.5
    layer = (h[1] >> 1) & 3            # 1 = Layer III
    br_idx = h[2] >> 4
    sr_idx = (h[2] >> 2) & 3
    if version == 1 or layer != 1 or br_idx in (0, 15) or sr_idx == 3:
        return None
    mpeg1 = version == 3
    bitrate = _BITRATES["1" if mpeg1 else "2"][br_idx] * 1000
    rate = _RATES[version][sr_idx]
    padding = (h[2] >> 1) & 1
    mono = (h[3] >> 6) == 3
    size = (144 if mpeg1 else 72) * bitrate // rate + padding
    side_info = (17 if mono else 32) if mpeg1 else (9 if mono else 17)
    return {"size": size, "rate": rate, "samples": 1152 if mpeg1 else 576,
            "side_info": side_info, "protected": not (h[1] & 1),
            "key": (version, sr_idx, h[3] >> 6)}


def _mp3_frames(data: bytes) -> list[bytes]:
    pos = 0
    if data[:3] == b"ID3" and len(data) >= 10:  # skip ID3v2 tag
        size = (data[6] << 21) | (data[7] << 14) | (data[8] << 7) | data[9]
        pos = 10 + size + (10 if data[5] & 0x10 else 0)
    end = len(data) - 128 if data[-128:-125] == b"TAG" else len(data)

    frames = []
    while pos + 4 <= end:
        info = _parse_header(data[pos:pos + 4])
        if info and pos + info["size"] <= end:
            frame = data[pos:pos + info["size"]]
            # Drop Xing/Info/VBRI header frames - they carry the duration of
            # the single clip and would confuse players about the merged length.
            off = 4 + (2 if info["protected"] else 0) + info["side_info"]
            if not frames and (frame[off:off + 4] in (b"Xing", b"Info") or frame[36:40] == b"VBRI"):
                pos += info["size"]
                continue
            frames.append(frame)
            pos += info["size"]
        else:
            pos += 1  # resync
    return frames


def _silent_frame(template: bytes) -> bytes:
    """A frame with the same settings as `template` whose content decodes to silence."""
    h = bytearray(template[:4])
    h[1] |= 0x01         # no CRC
    h[2] &= ~0x02 & 0xFF  # no padding
    info = _parse_header(bytes(h))
    return bytes(h) + b"\x00" * (info["size"] - 4)


def merge_mp3(clips: list[bytes], pause_s: float) -> bytes:
    all_frames = [_mp3_frames(c) for c in clips]
    if any(not f for f in all_frames):
        raise MergeError("One of the clips is not a valid MP3 file.")
    first = _parse_header(all_frames[0][0])
    if any(_parse_header(f[0])["key"] != first["key"] for f in all_frames):
        raise MergeError("Clips have different audio settings and cannot be merged.")

    silent = _silent_frame(all_frames[0][0])
    n_silent = round(pause_s * first["rate"] / first["samples"])
    out = bytearray()
    for i, frames in enumerate(all_frames):
        if i:
            out += silent * n_silent
        for fr in frames:
            out += fr
    return bytes(out)


# =============================================================== duration & silence

class _Clip:
    """A clip split into equal 'units' so silence can be added exactly.

    WAV: one unit = one sample frame.  MP3: one unit = one MPEG frame (~24 ms).
    """

    def __init__(self, data: bytes, fmt: str):
        self.data, self.fmt = data, fmt.upper()
        if self.fmt == "WAV":
            self.wav_fmt, self.pcm = _read_wav(data)
            ch, width, rate = self.wav_fmt
            self.unit = 1.0 / rate
            self.units = len(self.pcm) // (ch * width)
        elif self.fmt == "MP3":
            self.frames = _mp3_frames(data)
            if not self.frames:
                raise MergeError("The audio is not a valid MP3 file.")
            info = _parse_header(self.frames[0])
            self.unit = info["samples"] / info["rate"]
            self.units = len(self.frames)
        else:
            raise MergeError(f"{fmt} files are not supported.")

    @property
    def seconds(self) -> float:
        return self.units * self.unit

    def build(self, lead_units: int, end_units: int) -> bytes:
        lead_units, end_units = max(0, lead_units), max(0, end_units)
        if not lead_units and not end_units:
            return self.data
        if self.fmt == "WAV":
            ch, width, rate = self.wav_fmt
            out = io.BytesIO()
            with wave.open(out, "wb") as w:
                w.setnchannels(ch)
                w.setsampwidth(width)
                w.setframerate(rate)
                w.writeframes(b"\x00" * (lead_units * ch * width) + self.pcm
                              + b"\x00" * (end_units * ch * width))
            return out.getvalue()
        silent = _silent_frame(self.frames[0])
        return silent * lead_units + b"".join(self.frames) + silent * end_units


def duration(data: bytes, fmt: str) -> float:
    """Length of the audio in seconds.

    Measured with mutagen and cross-checked against an exact count of
    samples/frames. If they disagree (mutagen only estimates the length of
    MP3 files without a VBR header, and streamed WAV headers can be wrong),
    the exact count is used.
    """
    exact = _Clip(data, fmt).seconds
    try:
        import mutagen
        f = mutagen.File(io.BytesIO(data))
        measured = float(f.info.length) if f and f.info and f.info.length else None
    except Exception:
        measured = None
    if measured is None or abs(measured - exact) > 0.05:
        return round(exact, 3)
    return round(measured, 3)


def add_silence(data: bytes, fmt: str, before_s: float = 0.0, after_s: float = 0.0) -> bytes:
    clip = _Clip(data, fmt)
    return clip.build(round(max(0.0, before_s) / clip.unit), round(max(0.0, after_s) / clip.unit))


def pad_to_length(data: bytes, fmt: str, target_s: float, lead_s: float) -> bytes:
    """Add `lead_s` silence at the start and the rest at the end so the
    file is `target_s` long (exact for WAV, nearest ~24 ms frame for MP3)."""
    clip = _Clip(data, fmt)
    total = round(target_s / clip.unit)
    lead = min(round(max(0.0, lead_s) / clip.unit), max(0, total - clip.units))
    return clip.build(lead, total - lead - clip.units)


def merge(clips: list[bytes], fmt: str, pause_s: float = 0.5) -> bytes:
    if not clips:
        raise MergeError("There are no clips to merge.")
    pause_s = max(0.0, min(float(pause_s), 10.0))
    if fmt.upper() == "WAV":
        return merge_wav(clips, pause_s)
    if fmt.upper() == "MP3":
        return merge_mp3(clips, pause_s)
    raise MergeError(f"Merging {fmt} files is not supported.")

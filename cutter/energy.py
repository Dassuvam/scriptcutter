"""
Loudness envelope + adaptive speech detection.

Everything here works on a 16 kHz mono 16-bit WAV and a 10 ms frame grid.
The old version used a fixed -32 dB silencedetect threshold, which sat *above*
the median level of real recordings and chopped soft word endings. Here the
threshold is derived from each file's own noise floor and speech level.
"""
from __future__ import annotations

import array
import math
import operator
import wave

try:  # removed in Python 3.13; fast C path when present
    import audioop  # type: ignore
except Exception:  # pragma: no cover
    audioop = None

HOP = 0.01  # seconds per envelope frame
FLOOR_DB = -100.0


class Envelope:
    """Per-10ms loudness in dBFS plus helpers for querying it."""

    def __init__(self, db: list, hop: float = HOP):
        self.db = db
        self.hop = hop
        self.duration = len(db) * hop
        valid = sorted(d for d in db if d > -95.0) or [FLOOR_DB]
        n = len(valid)
        self.noise_floor = valid[int(n * 0.10)]
        self.speech_level = valid[min(n - 1, int(n * 0.95))]

    # -- indexing -------------------------------------------------------
    def idx(self, t: float) -> int:
        return max(0, min(len(self.db) - 1, int(round(t / self.hop))))

    def level(self, start: float, end: float) -> float:
        """Mean power (dB) between two times."""
        a, b = self.idx(start), self.idx(end)
        if b <= a:
            return self.db[a] if self.db else FLOOR_DB
        p = sum(10 ** (d / 10.0) for d in self.db[a:b]) / (b - a)
        return 10 * math.log10(p) if p > 0 else FLOOR_DB

    def peak(self, start: float, end: float) -> float:
        a, b = self.idx(start), self.idx(end)
        return max(self.db[a:b + 1]) if self.db else FLOOR_DB

    def quietest(self, start: float, end: float) -> float:
        """Time of the quietest 30 ms window inside [start, end]."""
        a, b = self.idx(start), self.idx(end)
        if b - a < 3:
            return (start + end) / 2.0
        best_i, best_v = a, float("inf")
        for i in range(a, b - 2):
            v = self.db[i] + self.db[i + 1] + self.db[i + 2]
            if v < best_v:
                best_v, best_i = v, i
        return (best_i + 1) * self.hop


def compute_envelope(wav_path: str) -> Envelope:
    with wave.open(wav_path, "rb") as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1:
            raise ValueError("Envelope expects 16-bit mono WAV")
        sr = w.getframerate()
        hop_n = int(sr * HOP)
        hop_b = hop_n * 2
        db = []
        while True:
            buf = w.readframes(hop_n * 2000)
            if not buf:
                break
            if audioop is not None:
                for i in range(0, len(buf), hop_b):
                    r = audioop.rms(buf[i:i + hop_b], 2)
                    db.append(20 * math.log10(r / 32768.0) if r > 0 else FLOOR_DB)
            else:
                a = array.array("h")
                a.frombytes(buf[: len(buf) - (len(buf) % 2)])
                for i in range(0, len(a), hop_n):
                    seg = a[i:i + hop_n]
                    if not seg:
                        continue
                    ms = sum(map(operator.mul, seg, seg)) / len(seg)
                    db.append(10 * math.log10(ms / (32768.0 ** 2)) if ms > 0 else FLOOR_DB)
    return Envelope(db)


def thresholds(env: Envelope, sensitivity_db: float = 0.0) -> tuple:
    """(on, off) thresholds in dB. Positive sensitivity = treat quieter sound as speech."""
    span = max(0.0, env.speech_level - env.noise_floor)
    on = env.noise_floor + max(7.0, 0.38 * span)
    on = min(on, env.speech_level - 8.0) - sensitivity_db
    return on, on - 4.0


def detect_speech(
    env: Envelope,
    sensitivity_db: float = 0.0,
    min_speech: float = 0.10,
    bridge_gap: float = 0.18,
) -> list:
    """
    Returns speech regions [(start, end), ...] in seconds.
    Hysteresis thresholds + a 50 ms smoothing window; dips shorter than
    `bridge_gap` are treated as part of the same region (gaps inside words).
    """
    on, off = thresholds(env, sensitivity_db)
    db = env.db
    n = len(db)
    if n == 0:
        return []
    # 50 ms moving average in the power domain
    pw = [10 ** (d / 10.0) for d in db]
    k = 2
    sm = []
    acc = sum(pw[0:k + 1])
    cnt = min(n, k + 1)
    for i in range(n):
        sm.append(10 * math.log10(acc / cnt) if acc > 0 else FLOOR_DB)
        if i + k + 1 < n:
            acc += pw[i + k + 1]
            cnt += 1
        if i - k >= 0:
            acc -= pw[i - k]
            cnt -= 1
    regions = []
    active = False
    start = 0
    for i, d in enumerate(sm):
        if not active and d >= on:
            active, start = True, i
        elif active and d < off:
            active = False
            regions.append([start, i])
    if active:
        regions.append([start, n])

    merged = []
    bridge = int(bridge_gap / env.hop)
    for r in regions:
        if merged and r[0] - merged[-1][1] <= bridge:
            merged[-1][1] = r[1]
        else:
            merged.append(r)
    out = []
    for a, b in merged:
        if (b - a) * env.hop >= min_speech:
            out.append((round(a * env.hop, 3), round(b * env.hop, 3)))
    return out


def speech_overlap(regions: list, start: float, end: float) -> float:
    """Seconds of detected speech inside [start, end]. `regions` must be sorted."""
    total = 0.0
    for a, b in regions:
        if b <= start:
            continue
        if a >= end:
            break
        total += min(b, end) - max(a, start)
    return total


def clip_to_speech(regions: list, start: float, end: float, slack: float = 0.0) -> tuple:
    """Shrink [start, end] to the first/last detected speech inside it."""
    inside = [(a, b) for a, b in regions if b > start - slack and a < end + slack]
    if not inside:
        return None
    return max(start - slack, inside[0][0]), min(end + slack, inside[-1][1])

"""Measure the overall feel of a piece of audio.

Only aggregate numbers come out of here: tempo, key, how the energy is spread
across the spectrum, how busy the drums are, and how the energy moves over
time. No melodies, chord sequences or note data are extracted, so nothing
recognisable from a reference track can be carried into a new one.
"""

from __future__ import annotations

import numpy as np
import librosa
import pyloudnorm as pyln

SR = 22050

# Krumhansl-Kessler key profiles
_MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
_MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
_NAMES = ["C", "Db", "D", "Eb", "E", "F", "Gb", "G", "Ab", "A", "Bb", "B"]

BANDS = {"sub": (20, 60), "bass": (60, 250), "low_mid": (250, 2000),
         "high_mid": (2000, 6000), "air": (6000, 11000)}


def load(path: str, max_seconds: float = 600) -> np.ndarray:
    """Mono audio at SR. Long files (DJ mixes) are sampled from the middle."""
    total = librosa.get_duration(path=path)
    offset = max(0.0, (total - max_seconds) / 2) if total > max_seconds else 0.0
    y, _ = librosa.load(path, sr=SR, mono=True, offset=offset, duration=max_seconds)
    return y


def _key(y: np.ndarray) -> dict:
    chroma = librosa.feature.chroma_cqt(y=librosa.effects.harmonic(y), sr=SR).mean(axis=1)
    scores = []
    for i in range(12):
        for mode, prof in (("major", _MAJOR), ("minor", _MINOR)):
            scores.append((np.corrcoef(np.roll(prof, i), chroma)[0, 1], _NAMES[i], mode))
    scores.sort(reverse=True)
    best, runner = scores[0], scores[1]
    return {"key": f"{best[1]} {best[2]}", "root": best[1], "mode": best[2],
            "confidence": round(float(best[0] - runner[0]), 3)}


def _tempo(y: np.ndarray) -> tuple[float, np.ndarray]:
    tempo, beats = librosa.beat.beat_track(y=y, sr=SR, start_bpm=122, units="time")
    bpm = float(np.atleast_1d(tempo)[0])
    while bpm < 95:
        bpm *= 2
    while bpm > 150:
        bpm /= 2
    return round(bpm, 1), beats


def _swing(y: np.ndarray, beats: np.ndarray) -> float:
    """How late the off-beat 16ths land, as a fraction of a 16th (0 = straight)."""
    if len(beats) < 16:
        return 0.0
    onsets = librosa.onset.onset_detect(y=y, sr=SR, units="time")
    step = float(np.median(np.diff(beats))) / 4
    pos = []
    for o in onsets:
        i = np.searchsorted(beats, o) - 1
        if 0 <= i < len(beats):
            frac = (o - beats[i]) / step  # 0..4 within the beat
            if 0.5 < frac < 1.6 or 2.5 < frac < 3.6:  # near the 2nd or 4th 16th
                pos.append(frac % 2 - 1)
    return round(float(np.clip(np.median(pos), 0, 0.4)), 3) if pos else 0.0


def _bands(y: np.ndarray) -> dict:
    S = np.abs(librosa.stft(y, n_fft=4096)) ** 2
    freqs = librosa.fft_frequencies(sr=SR, n_fft=4096)
    total = S.sum()
    return {k: round(float(S[(freqs >= lo) & (freqs < hi)].sum() / total), 4) for k, (lo, hi) in BANDS.items()}


def _energy_curve(y: np.ndarray, bpm: float, bars_per_block: int = 8) -> list[float]:
    block = int(SR * 60 / bpm * 4 * bars_per_block)
    rms = [float(np.sqrt(np.mean(y[i:i + block] ** 2))) for i in range(0, len(y) - block // 2, block)]
    top = max(rms) or 1.0
    return [round(r / top, 2) for r in rms]


def analyse(path: str) -> dict:
    y = load(path)
    bpm, beats = _tempo(y)
    harm, perc = librosa.effects.hpss(y)
    onsets = librosa.onset.onset_detect(y=perc, sr=SR, units="time")
    seconds = len(y) / SR
    centroid = float(np.mean(librosa.feature.spectral_centroid(y=y, sr=SR)))
    return {
        "bpm": bpm,
        **_key(y),
        "swing": _swing(y, beats),
        "loudness_lufs": round(float(pyln.Meter(SR).integrated_loudness(y)), 1),
        "brightness_hz": round(centroid),
        "bands": _bands(y),
        "percussive_ratio": round(float(np.sum(perc ** 2) / (np.sum(harm ** 2) + np.sum(perc ** 2))), 3),
        "onsets_per_beat": round(len(onsets) / max(seconds * bpm / 60, 1), 2),
        "energy_per_8_bars": _energy_curve(y, bpm),
        "analysed_seconds": round(seconds, 1),
    }

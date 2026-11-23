"""The beatbox synth engine.

A track is a short Python script that builds a `Track`, places parts on it bar
by bar, and calls `render()`. Every sound is synthesised from scratch here
(oscillators, noise and filters), so a rendered track contains no samples from
anywhere else.

    from beatbox import Track

    t = Track(bpm=124, bars=96, key="F minor", swing=0.08, seed=7)
    t.section("intro", 1)
    t.drum("kick", "x...x...x...x...", bars=(9, 88))
    t.chords(["Fm9", "Dbmaj7", "Ebsus4", "Cm7"], bars=(1, 96), sound="pad")
    t.sidechain(depth=0.6)
    t.render(__file__)

Bars are 1-indexed and ranges are inclusive. Patterns are 16 steps per bar
(16th notes) and loop when shorter than the range.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import numpy as np
import pedalboard as pb
import pyloudnorm as pyln
import soundfile as sf
from scipy import signal
from scipy.ndimage import minimum_filter1d, uniform_filter1d

SR = 44100

# --- Notes and chords ---------------------------------------------------------

_PITCH = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}

CHORDS = {
    "": [0, 4, 7], "maj": [0, 4, 7], "m": [0, 3, 7], "5": [0, 7, 12],
    "6": [0, 4, 7, 9], "m6": [0, 3, 7, 9], "7": [0, 4, 7, 10],
    "maj7": [0, 4, 7, 11], "m7": [0, 3, 7, 10], "mmaj7": [0, 3, 7, 11],
    "9": [0, 4, 7, 10, 14], "maj9": [0, 4, 7, 11, 14], "m9": [0, 3, 7, 10, 14],
    "11": [0, 4, 7, 10, 14, 17], "m11": [0, 3, 7, 10, 14, 17],
    "add9": [0, 4, 7, 14], "madd9": [0, 3, 7, 14],
    "sus2": [0, 2, 7], "sus4": [0, 5, 7], "sus": [0, 5, 7], "7sus4": [0, 5, 7, 10],
    "dim": [0, 3, 6], "m7b5": [0, 3, 6, 10], "aug": [0, 4, 8],
}


def midi(note: str) -> int:
    """'F#2' -> 42. Octave numbers follow the C4 = middle C convention."""
    m = re.fullmatch(r"([A-Ga-g])([#b]?)(-?\d)", note.strip())
    if not m:
        raise ValueError(f"not a note: {note!r} (expected e.g. F1, Ab2, C#3)")
    pc = _PITCH[m[1].upper()] + {"#": 1, "b": -1, "": 0}[m[2]]
    return 12 * (int(m[3]) + 1) + pc


def hz(n: float) -> float:
    return 440.0 * 2 ** ((n - 69) / 12)


def chord_notes(name: str) -> list[int]:
    """'Fm9' -> pitch classes relative to C (root first)."""
    m = re.fullmatch(r"([A-G][#b]?)(.*)", name.strip())
    if not m or m[2] not in CHORDS:
        raise ValueError(f"unknown chord {name!r}; qualities: {sorted(CHORDS)}")
    root = (_PITCH[m[1][0]] + {"#": 1, "b": -1}.get(m[1][1:], 0)) % 12
    return [root + i for i in CHORDS[m[2]]]


def _voice(pcs: list[int], prev: list[int] | None, octave: int) -> list[int]:
    """Place a chord near `octave`, choosing the inversion closest to `prev`."""
    base = [12 * (octave + 1) + p for p in pcs]
    options = []
    for inv in range(len(base)):
        v = sorted(base[inv:] + [n + 12 for n in base[:inv]])
        for shift in (-12, 0):
            options.append([n + shift for n in v])
    centre = np.mean(prev) if prev else 12 * (octave + 1) + 7
    return min(options, key=lambda v: abs(np.mean(v) - centre))


# --- DSP helpers --------------------------------------------------------------

def _t(n: int) -> np.ndarray:
    return np.arange(n) / SR


def _sos(kind: str, freq, order: int = 2):
    nyq = SR / 2
    f = np.clip(np.atleast_1d(freq), 20, nyq * 0.95) / nyq
    return signal.butter(order, f if len(f) > 1 else f[0], btype=kind, output="sos")


def _filt(x: np.ndarray, kind: str, freq, order: int = 2) -> np.ndarray:
    return signal.sosfilt(_sos(kind, freq, order), x, axis=-1)


def _adsr(n: int, a: float, d: float, s: float, r: float, gate: float) -> np.ndarray:
    """Envelope of n samples; the note is held for `gate` seconds then released."""
    t = _t(n)
    env = np.where(t < a, t / max(a, 1e-4),
                   s + (1 - s) * np.exp(-(t - a) / max(d, 1e-4)))
    held = np.interp(gate, t, env) if gate < t[-1] else env[-1]
    rel = held * np.exp(-(t - gate) / max(r, 1e-4))
    return np.where(t < gate, env, rel)


def _saw(f: float, n: int, phase: float = 0.0) -> np.ndarray:
    p = (f * _t(n) + phase) % 1.0
    return 2 * p - 1


def _square(f: float, n: int, phase: float = 0.0) -> np.ndarray:
    return np.sign(np.sin(2 * np.pi * (f * _t(n) + phase)))


def _noise(n: int, rng) -> np.ndarray:
    return rng.uniform(-1, 1, n)


def _db(x: float) -> float:
    return 10 ** (x / 20)


def _limit(x: np.ndarray, ceiling_db: float = -1.0, lookahead_s: float = 0.005,
           release_s: float = 0.06) -> np.ndarray:
    """Look-ahead brickwall limiter: the output never exceeds the ceiling."""
    ceiling = _db(ceiling_db)
    peak = np.abs(x).max(axis=0)
    need = np.minimum(1.0, ceiling / np.maximum(peak, 1e-9))
    la, rel = int(SR * lookahead_s), int(SR * release_s)
    held = minimum_filter1d(need, size=2 * la + rel, origin=-(rel // 2))
    smooth = uniform_filter1d(held, size=la)
    return np.clip(x * np.minimum(smooth, need), -ceiling, ceiling)


# --- Drums --------------------------------------------------------------------

_METAL = [205.3, 304.4, 369.6, 522.7, 540.0, 800.0]  # classic 808 hat partials


def _metallic(n: int) -> np.ndarray:
    return sum(_square(f * 1.7, n) for f in _METAL) / len(_METAL)


def _drum(sound: str, rng, tune: float | None, decay: float | None) -> np.ndarray:
    if sound == "kick":
        f0 = tune or 54.0
        d = decay or 0.3
        n = int(SR * (d + 0.1))
        t = _t(n)
        f = f0 + (f0 * 3.6 - f0) * np.exp(-t / 0.03)
        body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / (d * 0.45))
        click = _filt(_noise(n, rng), "highpass", 2500) * np.exp(-t / 0.004) * 0.35
        return np.tanh(2.4 * (body + click)) * 0.42
    if sound in ("clap", "snare"):
        d = decay or (0.14 if sound == "clap" else 0.16)
        n = int(SR * (d * 4 + 0.05))
        t = _t(n)
        noise = _filt(_noise(n, rng), "bandpass", (900, 5000))
        if sound == "clap":
            env = sum(np.where(t >= o, np.exp(-(t - o) / 0.006), 0) for o in (0, 0.011, 0.022))
            env = env + np.where(t >= 0.03, 0.6 * np.exp(-(t - 0.03) / d), 0)
            return noise * env * 1.3
        tone = np.sin(2 * np.pi * (tune or 190) * t) * np.exp(-t / 0.06)
        return (0.7 * noise * np.exp(-t / d) + 0.5 * tone) * 0.8
    if sound in ("hat", "openhat", "ride", "crash"):
        d = decay or {"hat": 0.04, "openhat": 0.28, "ride": 0.7, "crash": 1.6}[sound]
        n = int(SR * (d * 5 + 0.02))
        t = _t(n)
        hp = {"hat": 7000, "openhat": 6500, "ride": 4500, "crash": 3500}[sound]
        src = 0.55 * _metallic(n) + 0.45 * _noise(n, rng)
        lvl = {"hat": 1.5, "openhat": 1.2, "ride": 0.7, "crash": 0.8}[sound]
        return _filt(src, "highpass", hp, 4) * np.exp(-t / d) * lvl
    if sound == "shaker":
        d = decay or 0.05
        n = int(SR * (d * 5 + 0.02))
        t = _t(n)
        env = np.minimum(t / 0.012, 1) * np.exp(-np.maximum(t - 0.012, 0) / d)
        return _filt(_noise(n, rng), "bandpass", (4500, 11000)) * env * 0.9
    if sound == "rim":
        n = int(SR * 0.08)
        t = _t(n)
        tone = np.sin(2 * np.pi * 1700 * t) + 0.6 * np.sin(2 * np.pi * 480 * t)
        return tone * np.exp(-t / 0.012) * 0.45
    if sound in ("tom", "conga", "perc"):
        f0 = tune or {"tom": 110, "conga": 260, "perc": 420}[sound]
        d = decay or {"tom": 0.25, "conga": 0.15, "perc": 0.08}[sound]
        n = int(SR * (d * 4 + 0.02))
        t = _t(n)
        f = f0 * (1 + 0.6 * np.exp(-t / 0.015))
        return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / d) * 0.6
    raise ValueError(f"unknown drum {sound!r}")


DRUMS = ["kick", "clap", "snare", "hat", "openhat", "shaker", "rim", "ride",
         "crash", "tom", "conga", "perc"]


# --- Pitched voices -----------------------------------------------------------

def _voice_bass(sound: str, f: float, gate: float, cutoff: float) -> np.ndarray:
    n = int(SR * (gate + 0.15))
    t = _t(n)
    if sound == "sub":
        x = np.sin(2 * np.pi * f * t) + 0.15 * np.sin(4 * np.pi * f * t)
        return x * _adsr(n, 0.005, 0.3, 0.9, 0.03, gate) * 0.8
    if sound == "organ":  # the classic 90s house organ bass
        x = sum(w * np.sin(2 * np.pi * f * k * t) for k, w in ((1, 1), (2, 0.6), (3, 0.3), (4, 0.25), (6, 0.1)))
        env = _adsr(n, 0.003, 0.18, 0.25, 0.05, gate)
        return np.tanh(1.5 * x / 2.2) * env * 0.8
    if sound in ("deep", "reese"):
        cents = 8 if sound == "deep" else 18
        saw = (_saw(f * 2 ** (cents / 1200), n) + _saw(f * 2 ** (-cents / 1200), n, 0.3)) / 2
        dark = _filt(saw, "lowpass", cutoff * 0.35)
        bright = _filt(saw, "lowpass", cutoff)
        fenv = np.exp(-t / (0.12 if sound == "deep" else 0.5))
        x = dark + fenv * (bright - dark) + 0.35 * np.sin(2 * np.pi * f * t)
        return np.tanh(1.2 * x) * _adsr(n, 0.004, 0.25, 0.75, 0.04, gate) * 0.75
    raise ValueError(f"unknown bass sound {sound!r}; use sub, deep, organ or reese")


def _voice_keys(sound: str, f: float, gate: float, cutoff: float, rng) -> np.ndarray:
    """Returns a stereo (2, n) note."""
    if sound == "pad":
        n = int(SR * (gate + 1.2))
        left = right = 0
        for i, c in enumerate((-9, -3, 4, 10)):
            v = _saw(f * 2 ** (c / 1200), n, rng.random())
            if i % 2:
                right = right + v
            else:
                left = left + v
        st = np.stack([left, right]) / 2
        st = _filt(st, "lowpass", cutoff, 2)
        return st * _adsr(n, 0.35, 0.8, 0.85, 0.9, gate) * 0.35
    if sound == "stab":
        n = int(SR * (gate + 0.3))
        t = _t(n)
        x = 0.6 * _saw(f, n) + 0.4 * _saw(f * 2 ** (7 / 1200), n, 0.5) + 0.2 * _square(f / 2, n)
        dark, bright = _filt(x, "lowpass", cutoff * 0.25), _filt(x, "lowpass", cutoff)
        x = dark + np.exp(-t / 0.08) * (bright - dark)
        x = x * _adsr(n, 0.002, 0.15, 0.35, 0.12, gate) * 0.4
        return np.stack([x, np.roll(x, 90)])
    if sound in ("rhodes", "bell"):
        n = int(SR * (gate + 1.0))
        t = _t(n)
        ratio, depth, tau = (1.0, 1.8, 0.35) if sound == "rhodes" else (3.5, 2.5, 0.6)
        mod = depth * np.exp(-t / tau) * np.sin(2 * np.pi * f * ratio * t)
        x = np.sin(2 * np.pi * f * t + mod)
        x = x * _adsr(n, 0.003, 0.9 if sound == "rhodes" else 1.4, 0.0, 0.3, gate) * 0.45
        trem = 1 + 0.15 * np.sin(2 * np.pi * 4.5 * t)
        return np.stack([x * trem, x * (2 - trem)]) / 1.15
    if sound == "piano":
        n = int(SR * (gate + 1.5))
        t = _t(n)
        x = sum(np.sin(2 * np.pi * f * k * (1 + 0.0004 * k * k) * t) * np.exp(-t * (0.8 + 0.9 * k)) / k
                for k in range(1, 9))
        hammer = _filt(_noise(n, rng), "bandpass", (1000, 4000)) * np.exp(-t / 0.004) * 0.1
        x = (x + hammer) * _adsr(n, 0.002, 2.0, 0.0, 0.25, gate) * 0.5
        return np.stack([x, np.roll(x, 40)])
    if sound == "organ":
        n = int(SR * (gate + 0.2))
        t = _t(n)
        vib = 1 + 0.003 * np.sin(2 * np.pi * 6 * t)
        x = sum(w * np.sin(2 * np.pi * f * k * vib * t) for k, w in ((0.5, 0.5), (1, 1), (2, 0.7), (3, 0.4), (4, 0.3)))
        x = x * _adsr(n, 0.01, 0.2, 0.9, 0.08, gate) * 0.2
        return np.stack([x, np.roll(x, 200)])
    if sound in ("pluck", "lead"):
        n = int(SR * (gate + (0.4 if sound == "pluck" else 0.2)))
        t = _t(n)
        if sound == "pluck":
            x = _saw(f, n) + 0.5 * _square(f * 1.005, n)
            dark, bright = _filt(x, "lowpass", cutoff * 0.15), _filt(x, "lowpass", cutoff)
            x = (dark + np.exp(-t / 0.05) * (bright - dark)) * _adsr(n, 0.002, 0.25, 0.0, 0.1, gate) * 0.35
        else:
            vib = 1 + 0.004 * np.sin(2 * np.pi * 5.5 * t) * np.minimum(t / 0.4, 1)
            x = _square(f * vib, n) * 0.5 + _saw(f * 1.004 * vib, n) * 0.5
            x = _filt(x, "lowpass", cutoff) * _adsr(n, 0.02, 0.3, 0.7, 0.15, gate) * 0.25
        return np.stack([x, np.roll(x, 120)])
    raise ValueError(f"unknown keys sound {sound!r}")


BASS_SOUNDS = ["sub", "deep", "organ", "reese"]
KEY_SOUNDS = ["pad", "stab", "rhodes", "piano", "organ", "bell", "pluck", "lead"]


# --- FX -----------------------------------------------------------------------

def _fx(kind: str, seconds: float, rng) -> np.ndarray:
    n = int(SR * seconds)
    t = _t(n)
    ramp = t / seconds
    if kind == "riser":
        noise = _noise(n, rng)
        out = np.zeros(n)
        for i in range(0, n, 2048):  # sweep a bandpass upward block by block
            fc = 300 * (9000 / 300) ** (i / n)
            seg = slice(max(i - 256, 0), min(i + 2048, n))
            out[i:i + 2048] = _filt(noise[seg], "bandpass", (fc * 0.7, fc * 1.4))[i - seg.start:][:2048]
        tone = np.sin(2 * np.pi * np.cumsum(200 * 2 ** (2 * ramp)) / SR) * 0.15
        return (out * 0.5 + tone) * ramp ** 2
    if kind == "downlifter":
        return _fx("riser", seconds, rng)[::-1] * 0.7
    if kind == "impact":
        boom = np.sin(2 * np.pi * np.cumsum(40 + 60 * np.exp(-t / 0.08)) / SR) * np.exp(-t / 0.6)
        crash = _filt(_noise(n, rng), "highpass", 3000) * np.exp(-t / 0.4) * 0.3
        return boom * 0.9 + crash
    if kind == "vinyl":
        hiss = _filt(_noise(n, rng), "bandpass", (2000, 9000)) * 0.015
        pops = (rng.random(n) > 0.99985) * rng.uniform(-1, 1, n)
        pops = _filt(pops, "highpass", 1500) * 0.5
        return hiss + pops
    raise ValueError(f"unknown fx {kind!r}; use riser, downlifter, impact or vinyl")


# --- Pattern parsing ----------------------------------------------------------

def _steps(pattern: str) -> list[str]:
    """Drum patterns are characters; note patterns are whitespace-separated tokens."""
    pattern = pattern.replace("|", " ")
    tokens = pattern.split()
    if all(len(tok) == 1 for tok in tokens) or len(tokens) == 1:
        tokens = list("".join(tokens))
    if len(tokens) % 16:
        raise ValueError(f"pattern has {len(tokens)} steps; use a multiple of 16")
    return tokens


def _bars(bars) -> range:
    a, b = bars
    return range(a, b + 1)


# --- Track --------------------------------------------------------------------

class Track:
    def __init__(self, bpm: float, bars: int, key: str = "", swing: float = 0.0, seed: int = 0):
        self.bpm, self.n_bars, self.key, self.swing = bpm, bars, key, swing
        self.step_s = 60 / bpm / 4
        self.rng = np.random.default_rng(seed)
        self.length = int(SR * (bars * 16 * self.step_s + 4))
        self.buses: dict[str, np.ndarray] = {}
        self.bus_fx: dict[str, dict] = {}
        self.automation: list[tuple] = []
        self.kicks: list[float] = []
        self.duck: tuple | None = None
        self.sections: list[tuple[str, int]] = []
        self._cache: dict = {}

    # placement

    def _time(self, bar: int, step: int) -> float:
        swing = self.swing * self.step_s if step % 2 else 0.0
        return ((bar - 1) * 16 + step) * self.step_s + swing

    def _bus(self, name: str) -> np.ndarray:
        if name not in self.buses:
            self.buses[name] = np.zeros((2, self.length))
        return self.buses[name]

    def _place(self, bus: str, x: np.ndarray, at: float, gain: float, pan: float = 0.0):
        if x.ndim == 1:
            x = np.stack([x * np.sqrt((1 - pan) / 2) * 1.414, x * np.sqrt((1 + pan) / 2) * 1.414])
        i = int(at * SR)
        if i >= self.length or i < 0:
            return
        x = x[:, : self.length - i]
        self._bus(bus)[:, i:i + x.shape[1]] += x * _db(gain)

    def _cached(self, key, make):
        if key not in self._cache:
            self._cache[key] = make()
        return self._cache[key]

    # parts

    def section(self, name: str, bar: int):
        """Mark where a section starts (intro, build, drop, break, outro)."""
        self.sections.append((name, bar))

    def drum(self, sound: str, pattern: str, bars, gain: float = 0.0, pan: float = 0.0,
             tune: float | None = None, decay: float | None = None, bus: str | None = None,
             humanize: float = 0.0):
        """x = hit, X = accent (+4 dB), g = ghost (-9 dB), . = rest."""
        steps = _steps(pattern)
        bus = bus or ("kick" if sound == "kick" else "drums")
        for bar in _bars(bars):
            for s in range(16):
                ch = steps[((bar - bars[0]) * 16 + s) % len(steps)]
                if ch not in "xXg":
                    continue
                variant = int(self.rng.integers(4))
                x = self._cached(("drum", sound, tune, decay, variant),
                                 lambda: _drum(sound, np.random.default_rng(variant), tune, decay))
                lvl = gain + {"x": 0, "X": 4, "g": -9}[ch] + self.rng.normal(0, humanize)
                at = self._time(bar, s) + self.rng.normal(0, humanize * 0.002)
                self._place(bus, x, max(at, 0), lvl, pan)
                if sound == "kick":
                    self.kicks.append(at)

    def _notes(self, pattern: str, bars):
        """Yield (bar, step, note, length_in_steps). '-' holds, '.' rests."""
        steps = _steps(pattern)
        total = len(_bars(bars)) * 16
        for i in range(total):
            tok = steps[i % len(steps)]
            if tok in ".-":
                continue
            length = 1
            while i + length < total and steps[(i + length) % len(steps)] == "-":
                length += 1
            yield bars[0] + i // 16, i % 16, tok, length

    def bass(self, pattern: str, bars, sound: str = "deep", gain: float = 0.0,
             cutoff: float = 900, bus: str = "bass"):
        """Space-separated notes per 16th, e.g. 'F1 - . F1 . . Ab1 - ...'."""
        for bar, s, tok, length in self._notes(pattern, bars):
            gate = length * self.step_s * 0.92
            x = self._cached(("bass", sound, tok, round(gate, 3), cutoff),
                             lambda: _voice_bass(sound, hz(midi(tok)), gate, cutoff))
            self._place(bus, x, self._time(bar, s), gain)

    def melody(self, pattern: str, bars, sound: str = "pluck", gain: float = 0.0,
               cutoff: float = 4000, pan: float = 0.0, bus: str = "lead"):
        """Same notation as bass(); chords allowed as 'C4+Eb4+G4'."""
        for bar, s, tok, length in self._notes(pattern, bars):
            gate = length * self.step_s * 0.9
            for note in tok.split("+"):
                x = self._cached(("keys", sound, note, round(gate, 3), cutoff),
                                 lambda: _voice_keys(sound, hz(midi(note)), gate, cutoff, np.random.default_rng(1)))
                x = x * np.array([[1 - max(pan, 0)], [1 + min(pan, 0)]])
                self._place(bus, x, self._time(bar, s), gain)

    def chords(self, progression: list[str], bars, sound: str = "pad",
               rhythm: str = "x" + "-" * 15, octave: int = 3, gain: float = 0.0,
               cutoff: float = 2200, bars_per_chord: int = 1, bus: str = "chords"):
        """One chord per `bars_per_chord` bars, cycling. Rhythm: x hit, - hold, . rest."""
        steps = _steps(rhythm)
        prev = None
        voicings = {}
        for bar in _bars(bars):
            idx = ((bar - bars[0]) // bars_per_chord) % len(progression)
            name = progression[idx]
            if (name, idx) not in voicings:
                voicings[(name, idx)] = _voice(chord_notes(name), prev, octave)
            prev = voicings[(name, idx)]
            s = 0
            while s < 16:
                tok = steps[((bar - bars[0]) * 16 + s) % len(steps)]
                if tok != "x":
                    s += 1
                    continue
                length = 1
                while s + length < 16 and steps[((bar - bars[0]) * 16 + s + length) % len(steps)] == "-":
                    length += 1
                gate = length * self.step_s * 0.95
                for n in prev:
                    x = self._cached(("keys", sound, n, round(gate, 3), cutoff),
                                     lambda: _voice_keys(sound, hz(n), gate, cutoff, np.random.default_rng(n)))
                    self._place(bus, x, self._time(bar, s), gain - 2.5 * np.log2(len(prev)))
                s += length

    def fx(self, kind: str, bar: int, length_bars: float = 4, gain: float = 0.0, bus: str = "fx"):
        """riser/downlifter span `length_bars` starting at `bar`; a riser ends on the
        downbeat of bar + length_bars. impact hits on `bar`. vinyl crackles throughout."""
        seconds = length_bars * 16 * self.step_s
        x = _fx(kind, seconds if kind != "impact" else 3.0, self.rng)
        self._place(bus, x, self._time(bar, 0), gain)

    # mix controls

    def mix(self, bus: str, gain: float = 0.0, reverb: float = 0.0, delay: float = 0.0,
            lowpass: float | None = None, highpass: float | None = None,
            chorus: float = 0.0, drive: float = 0.0):
        """Per-bus effects. reverb/delay/chorus are wet amounts 0..1; delay is a dotted 8th."""
        self.bus_fx[bus] = dict(gain=gain, reverb=reverb, delay=delay, lowpass=lowpass,
                                highpass=highpass, chorus=chorus, drive=drive)

    def sweep(self, bus: str, bars, start_hz: float, end_hz: float):
        """Low-pass filter sweep on a bus across a bar range (exponential)."""
        self.automation.append((bus, bars, start_hz, end_hz))

    def sidechain(self, depth: float = 0.6, release: float = 0.22, exclude=("kick", "drums", "fx")):
        """Duck every bus except `exclude` on each kick: the house pump."""
        self.duck = (depth, release, set(exclude))

    # render

    def _apply_fx(self, name: str, x: np.ndarray) -> np.ndarray:
        cfg = self.bus_fx.get(name, {})
        chain = []
        if cfg.get("highpass"):
            chain.append(pb.HighpassFilter(cfg["highpass"]))
        if cfg.get("lowpass"):
            chain.append(pb.LowpassFilter(cfg["lowpass"]))
        if cfg.get("drive"):
            chain.append(pb.Distortion(drive_db=cfg["drive"]))
        if cfg.get("chorus"):
            chain.append(pb.Chorus(rate_hz=0.6, depth=0.3, mix=cfg["chorus"]))
        if cfg.get("delay"):
            chain.append(pb.Delay(delay_seconds=self.step_s * 3, feedback=0.35, mix=cfg["delay"]))
        if cfg.get("reverb"):
            chain.append(pb.Reverb(room_size=0.8, damping=0.5, wet_level=cfg["reverb"],
                                   dry_level=1.0, width=1.0))
        y = pb.Pedalboard(chain)(x.astype(np.float32), SR) if chain else x
        for bus, bars, f0, f1 in self.automation:
            if bus == name:
                y = self._sweep(y, bars, f0, f1)
        return y * _db(cfg.get("gain", 0.0))

    def _sweep(self, x: np.ndarray, bars, f0: float, f1: float) -> np.ndarray:
        a, b = int(self._time(bars[0], 0) * SR), int(self._time(bars[1] + 1, 0) * SR)
        a, b = min(a, x.shape[1]), min(b, x.shape[1])
        lpf = pb.LadderFilter(mode=pb.LadderFilter.Mode.LPF24, cutoff_hz=f0, resonance=0.2)
        board = pb.Pedalboard([lpf])
        out = x.astype(np.float32).copy()
        block = 512
        for i in range(a, b, block):
            lpf.cutoff_hz = float(f0 * (f1 / f0) ** ((i - a) / max(b - a, 1)))
            out[:, i:i + block] = board(out[:, i:i + block], SR, reset=(i == a))
        return out

    def _duck_curve(self) -> np.ndarray:
        depth, release, _ = self.duck
        g = np.ones(self.length)
        n = int(SR * release * 2)
        shape = 1 - depth * np.exp(-((_t(n) / release) ** 2) * 3)  # quick dip, smooth recovery
        for k in self.kicks:
            i = int(k * SR)
            seg = g[i:i + n]
            g[i:i + n] = np.minimum(seg, shape[: len(seg)])
        return g

    def render(self, script: str, name: str = "track", lufs: float = -14.0, stems: bool = False):
        """Mix, master and write <name>.wav, <name>.mp3 and render.json next to `script`."""
        out_dir = Path(script).resolve().parent
        duck = self._duck_curve() if self.duck and self.kicks else None
        mix = np.zeros((2, self.length))
        for bus, x in self.buses.items():
            y = self._apply_fx(bus, x)
            if duck is not None and bus not in self.duck[2]:
                y = y * duck
            if stems:
                sf.write(out_dir / f"stem-{bus}.wav", y.T, SR, subtype="PCM_24")
            mix += y

        master = pb.Pedalboard([
            pb.HighpassFilter(28),
            pb.Compressor(threshold_db=-14, ratio=2.0, attack_ms=20, release_ms=200),
        ])
        y = master(mix.astype(np.float32), SR)
        # trim trailing silence, then fade the tail
        end = int(self._time(self.n_bars + 1, 0) * SR + SR * 2)
        y = y[:, :end]
        fade = int(SR * 2)
        y[:, -fade:] *= np.linspace(1, 0, fade) ** 2

        meter = pyln.Meter(SR)
        src, gain = y, 0.0
        for _ in range(4):  # gain to target, limit, re-measure
            y = _limit(src * _db(gain))
            loud = meter.integrated_loudness(y.T)
            gain += lufs - loud

        wav, mp3 = out_dir / f"{name}.wav", out_dir / f"{name}.mp3"
        sf.write(wav, y.T, SR, subtype="PCM_24")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav),
                        "-codec:a", "libmp3lame", "-b:a", "320k", str(mp3)], check=True)
        info = {
            "bpm": self.bpm, "key": self.key, "bars": self.n_bars, "duration_s": round(y.shape[1] / SR, 2),
            "lufs": round(float(loud), 2), "peak_db": round(float(20 * np.log10(np.abs(y).max())), 2),
            "sections": [{"name": s, "bar": b, "time_s": round(self._time(b, 0), 2)} for s, b in self.sections],
            "buses": sorted(self.buses),
        }
        (out_dir / "render.json").write_text(json.dumps(info, indent=2) + "\n")
        print(f"RENDERED={mp3}  {info['duration_s']}s  {info['lufs']} LUFS  peak {info['peak_db']} dB")
        return info

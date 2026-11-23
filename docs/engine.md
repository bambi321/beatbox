# Engine reference

Every track is a Python script that builds a `Track`, places parts on it, sets the mix and calls `render()`. All sounds are synthesised in `beatbox/engine.py`. There are no samples.

```python
from beatbox import Track

t = Track(bpm=124, bars=112, key="F minor", swing=0.08, seed=7)   # key is a label for render.json and the thumbnail; seed makes the humanising repeatable
...
t.render(__file__)   # writes track.wav, track.mp3, render.json next to the script
```

## Conventions

- **Bars are 1-indexed and ranges are inclusive.** `bars=(17, 48)` is 32 bars.
- **Patterns are 16 steps per bar** (16th notes) and loop across the range. They can be several bars long (32, 48 or 64 steps). Use `|` or spaces anywhere to make them readable.
- **Note names** use C4 = middle C: `F1` ≈ 44 Hz (very sub), `F2` ≈ 87 Hz (the house bass sweet spot), `F3` ≈ 175 Hz.
- **Gains are in dB.** 0 is the default level, so use negative numbers to sit things back.

## Drums

```python
t.drum(sound, pattern, bars, gain=0, pan=0, tune=None, decay=None, bus=None, humanize=0)
```

| Char | Meaning |
|---|---|
| `x` | hit |
| `X` | accent (+4 dB) |
| `g` | ghost (−9 dB) |
| `.` | rest |

Sounds: `kick clap snare hat openhat shaker rim ride crash tom conga perc`.

- `tune` sets the kick's or a tom's pitch in Hz. Default kick is 54 Hz; tune it to the key's root, e.g. 49 Hz for G.
- `decay` sets the length in seconds.
- `humanize` adds random level jitter in dB, plus a matching slight timing jitter. 1–1.5 suits hats and shakers.
- The kick goes to the `kick` bus and everything else goes to `drums`, unless you pass `bus=`.

```python
t.drum("kick",    "x...x...x...x...", bars=(17, 80))
t.drum("clap",    "....x.......x...", bars=(17, 80), gain=-3)
t.drum("openhat", "..x...x...x...x.", bars=(17, 80), gain=-6)
t.drum("shaker",  "xgxgxgxgxgxgxgxg", bars=(25, 80), gain=-12, humanize=1.5)
```

## Bass

```python
t.bass(pattern, bars, sound="deep", gain=0, cutoff=900, bus="bass")
```

Space-separated tokens, one per 16th: a note (`F2`, `Ab2`), `-` to hold the previous note, `.` to rest.

| Sound | Character |
|---|---|
| `deep` | detuned saw + sub, filter pluck. The default deep-house bass. |
| `sub` | near-pure sine. Minimal and dub. |
| `organ` | the bouncy 90s house organ bass |
| `reese` | wide, dark and moving. Tech and garage. |

```python
t.bass("F2 - . F2 . . F3 . . F2 - . . Ab2 . . ", bars=(17, 48), sound="deep")
```

House basslines sit **between** the kicks: leave step 0 empty, or keep it short, so the bass and kick don't fight.

## Chords

```python
t.chords(progression, bars, sound="pad", rhythm="x---------------", octave=3,
         gain=0, cutoff=2200, bars_per_chord=1, bus="chords")
```

- `progression` is a list of chord names, cycled at one chord per `bars_per_chord` bars.
- The rhythm string uses `x` to start the chord, `-` to hold it and `.` to rest.
- Voicings are chosen automatically for smooth voice leading.

Chord qualities: `"" maj m 5 6 m6 7 maj7 m7 mmaj7 9 maj9 m9 11 m11 add9 madd9 sus2 sus4 sus 7sus4 dim m7b5 aug`, e.g. `Fm9`, `Dbmaj7`, `Eb7sus4`, `C#m11`.

Sounds: `pad stab rhodes piano organ bell pluck lead`.

```python
t.chords(["Fm9", "Dbmaj7", "Ebsus4", "Cm7"], bars=(17, 48), sound="stab",
         rhythm="...x..x....x.x..", cutoff=3000, gain=-4)
```

## Melody

```python
t.melody(pattern, bars, sound="pluck", gain=0, cutoff=4000, pan=0, bus="lead")
```

Same notation as bass. Stack notes with `+` (`C4+Eb4+G4`). It takes the same sounds as chords.

## FX

```python
t.fx(kind, bar, length_bars=4, gain=0)
```

| Kind | What it does |
|---|---|
| `riser` | filtered noise and a rising tone over `length_bars`. Ends on the downbeat of `bar + length_bars`. |
| `downlifter` | a reversed riser |
| `impact` | a boom and crash on `bar`. Put it on the drop. |
| `vinyl` | crackle and hiss across `length_bars`. A lo-fi bed. |

## Mix

```python
t.mix(bus, gain=0, reverb=0, delay=0, lowpass=None, highpass=None, chorus=0, drive=0)
t.sweep(bus, bars, start_hz, end_hz)       # low-pass filter sweep (intro open-up, outro close-down)
t.sidechain(depth=0.6, release=0.22)       # pump every bus except kick, drums and fx on each kick
t.section(name, bar)                       # label sections; shows up in render.json
```

- **reverb, delay and chorus** are wet amounts from 0 to 1. The delay is a dotted 8th.
- **Sections** are worth labelling: `intro`, `groove`, `break`, `drop`, `outro`.
- **Rendering** masters to −14 LUFS (YouTube's level) with a −1 dB brickwall ceiling, and writes `track.wav` (24-bit), `track.mp3` (320k) and `render.json`. Pass `stems=True` to also write one WAV per bus.

## Levels that work

These are starting points. The engine's defaults are balanced against real house tracks.

| Part | Gain |
|---|---|
| kick | 0 |
| clap | −3 |
| hats | −3 to −6 |
| shaker | −12 |
| bass | −1 to 0 |
| stabs | −4 |
| pads | −8 to −10 (with `bus="pad"`) |
| rhodes or lead | −6 |

Sidechain depth 0.5–0.7. Put a high-pass of 120–150 on `drums` so the hats stay out of the bass.

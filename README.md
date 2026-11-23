# beatbox

Original, royalty-free house music written as code. Give it a YouTube link to a track you like the feel of, and it composes a **new** track in that style, then renders it and draws a thumbnail ready for YouTube.

```
/beat https://www.youtube.com/watch?v=...
```

| Output | What it is |
|---|---|
| `tracks/<name>/track.py` | The song, as a Python script. Edit it and re-run to change the music. |
| `tracks/<name>/track.mp3` | The rendered audio: 320k, mastered to −14 LUFS, YouTube's loudness. |
| `tracks/<name>/thumbnail.png` | A 1280×720 thumbnail drawn from the track's own spectrum. |
| `tracks/<name>/meta.md` | Title, YouTube description and tags. |

## How it works

```
YouTube link
   │  beatbox.fetch      yt-dlp → analyse → delete the audio
   ▼
style.json               tempo, key, swing, low-end share, brightness, drum density, energy curve
   │  Claude              turns the numbers into a brief and composes original parts
   ▼
track.py                 a script using the beatbox engine (drums, bass, chords, melody, fx, mix)
   │  python track.py     every sound synthesised from scratch, then mixed and mastered
   ▼
track.mp3 + render.json
   │  beatbox.check       measures the new track against the reference and flags problems
   │  beatbox.thumbnail   visualiser ring from the track's spectrum, title, BPM and key
   ▼
thumbnail.png + meta.md
```

## Why it's copyright-safe

Content ID flags audio that matches a recording or a recognisable melody. beatbox is built so neither can get into a track:

- **Only aggregate numbers leave the reference.** The analysis measures tempo, key, spectrum balance and energy, never notes, chords or melodies, so there is nothing recognisable to copy. See `beatbox/analysis.py`.
- **The reference audio is deleted** straight after analysis, and it's never mixed in.
- **Every sound is synthesised** from oscillators and noise in `beatbox/engine.py`. There are no samples or loops from anywhere.
- **Chords, basslines and melodies are written fresh** for each track. The skill also checks earlier tracks so progressions don't repeat.

Tempo, key, genre and groove aren't owned by anyone. False Content ID claims can still happen on any upload; if one does, dispute it and point to the track's `track.py`.

Downloading from YouTube, even only for analysis, is a grey area under YouTube's terms. beatbox keeps it to a temporary download that's deleted once measured.

## Requirements

| Requirement | Why |
|---|---|
| **macOS**, Apple Silicon or Intel | The main platform. Linux works too, but thumbnails use a plain fallback font because the DIN fonts ship with macOS. |
| **[Homebrew](https://brew.sh)** | Installs the command-line tools below. |
| **ffmpeg** | Converts audio and encodes the MP3. |
| **yt-dlp** | Downloads the reference audio from YouTube. |
| **[uv](https://docs.astral.sh/uv/)** | Creates the Python environment. It downloads Python 3.12 for you. |
| **Python 3.12** | The audio libraries (librosa, pedalboard) are pinned to it. |
| **[Claude Code](https://docs.claude.com/en/docs/claude-code/overview)** | Runs the `/beat` skill, which writes each track. |
| **A Claude account** | A Claude Pro or Max plan, or an Anthropic API key, to sign in to Claude Code. |
| **Internet access** | Only to download the reference from YouTube. Rendering is fully offline. |

No other API keys, paid services or plugins are needed. Every sound is synthesised locally.

## Setup

**1. Install the tools**

```bash
brew install ffmpeg yt-dlp uv
```

On Linux, use `sudo apt install ffmpeg` and `pipx install yt-dlp`, then install uv with `curl -LsSf https://astral.sh/uv/install.sh | sh`.

**2. Get the code and the Python environment**

```bash
git clone https://github.com/bambi321/beatbox.git
cd beatbox
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e .
```

**3. Check it works** by rendering the demo track. This needs no internet and no Claude:

```bash
.venv/bin/python examples/demo/track.py
open examples/demo/track.mp3
```

**4. Install Claude Code and sign in**

```bash
curl -fsSL https://claude.ai/install.sh | bash
claude        # first run asks you to sign in
```

## Making your first track

Start Claude Code **from the repo folder**, so it picks up the `/beat` skill and the project rules in `CLAUDE.md`:

```bash
cd beatbox
claude
```

Then give it a YouTube link to a house track or mix whose feel you like:

```
/beat https://www.youtube.com/watch?v=...
```

It runs unattended for a few minutes and ends with the paths to `track.py`, `track.mp3`, `thumbnail.png` and `meta.md` in `tracks/<name>/`. Listen before you post: Claude checks the mix by measurement but can't hear it.

The skill pre-approves the commands it runs. Claude Code may still ask permission for anything else.

**Changing a track:** ask Claude ("make the bass deeper", "add a longer break"), or edit `tracks/<name>/track.py` yourself and re-run it:

```bash
.venv/bin/python tracks/<name>/track.py
```

**Same link again:** running `/beat` on it makes a new, different track. It never overwrites the earlier one.

**Everything in `tracks/` stays on your machine.** It's git-ignored, so your references and renders are never pushed.

## Running the steps by hand

The `/beat` skill does all of this. Each step also works on its own:

```bash
.venv/bin/python -m beatbox.fetch "<youtube-url>"              # → tracks/.wip-<id>/style.json
.venv/bin/python -m beatbox.rename tracks/.wip-<id> "Night Bus" # → tracks/night-bus/
.venv/bin/python tracks/<name>/track.py                         # → track.wav, track.mp3, render.json
.venv/bin/python -m beatbox.check tracks/<name>                 # → reference vs new track report
.venv/bin/python -m beatbox.thumbnail tracks/<name> --title "Night Bus" [--palette ocean]
```

To make a track with no reference at all, copy `examples/demo/track.py` into a new folder under `tracks/` and edit it. `docs/engine.md` is the full engine reference.

## Troubleshooting

| Problem | Fix |
|---|---|
| The download fails with "Sign in to confirm" or "HTTP Error 403" | YouTube changed something. Update the downloader with `brew upgrade yt-dlp`. |
| `/beat` isn't recognised | Start `claude` from inside the `beatbox` folder. |
| `No module named beatbox` | Use `.venv/bin/python`, not your system Python, and run the setup step 2 install again. |
| The thumbnail text looks plain | You're not on macOS, so the DIN fonts aren't available and the default font is used. |

## Layout

```
beatbox/
  engine.py      synth engine: drums, bass, keys, fx, mix, sidechain, master
  analysis.py    style measurement (aggregate numbers only)
  fetch.py       download → analyse → delete
  rename.py      names a finished track's folder after its title
  check.py       new track vs reference report
  thumbnail.py   1280x720 YouTube thumbnail
docs/engine.md   engine reference for writing tracks
examples/demo/   a demo track script, and the layout new tracks follow
tracks/<name>/   one folder per track, named after its title (git-ignored, stays local)
.claude/skills/beat/SKILL.md   the /beat pipeline
```

## Limits

- **It sounds like synthesised house, not a studio release.** Think clean, groovy, deep or piano house background music. Better samples or a real synth engine, such as SuperCollider, would be the next step up.
- **Claude can't hear the result.** Quality control is by measurement: loudness, peaks, spectrum balance against the reference, energy per 8 bars and a spectrogram. Listen before posting.
- **Key detection can be off.** It sometimes reports the relative major or minor (F minor ↔ Ab major), which is musically the same set of notes.

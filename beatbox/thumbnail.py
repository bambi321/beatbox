"""Draw a 1280x720 YouTube thumbnail from the rendered track itself.

    .venv/bin/python -m beatbox.thumbnail tracks/<name> --title "Night Bus" [--subtitle "..."] [--palette dusk]

The visualiser ring is drawn from the track's own spectrum, so every
thumbnail is unique to its track. No AI generation or credits involved.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import librosa
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H = 1280, 720
FONTS = Path("/System/Library/Fonts/Supplemental")

# background top, background bottom, accent, highlight
PALETTES = {
    "midnight": ("#07061a", "#241052", "#ff3cac", "#2be8ff"),
    "dusk":     ("#120618", "#5a1440", "#ff8a3c", "#ffd36e"),
    "ocean":    ("#02101c", "#06415a", "#2bf0c8", "#c6fff5"),
    "neon":     ("#050505", "#1a1a3a", "#b6ff3c", "#ff3cf0"),
    "sunset":   ("#1a0710", "#7a1f2b", "#ffb43c", "#ffe7b0"),
    "ice":      ("#060b18", "#1d2d5c", "#8fb8ff", "#ffffff"),
}
MINOR = ["midnight", "ocean", "neon", "ice"]
MAJOR = ["dusk", "sunset", "neon", "ocean"]


def _rgb(h: str) -> tuple[int, int, int]:
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(str(FONTS / name), size)
    except OSError:
        return ImageFont.load_default(size)


def _spectrum(wav: Path, bands: int = 120) -> np.ndarray:
    """Average mel spectrum of the loudest 20 seconds, scaled 0..1."""
    y, sr = librosa.load(wav, sr=22050, mono=True)
    win = sr * 20
    if len(y) > win:
        rms = [np.mean(y[i:i + win] ** 2) for i in range(0, len(y) - win, sr * 5)]
        i = int(np.argmax(rms)) * sr * 5
        y = y[i:i + win]
    mel = librosa.power_to_db(librosa.feature.melspectrogram(y=y, sr=sr, n_mels=bands, fmax=11000)).mean(axis=1)
    mel = (mel - mel.min()) / (np.ptp(mel) or 1)
    return mel ** 1.4


def draw(track_dir: Path, title: str, subtitle: str, palette: str | None) -> Path:
    style = json.loads((track_dir / "style.json").read_text())["style"]
    render = json.loads((track_dir / "render.json").read_text())
    seed = int(hashlib.sha1(title.encode()).hexdigest(), 16)
    rng = np.random.default_rng(seed % 2**32)
    key = render.get("key") or style["key"]
    choices = MINOR if key.endswith("minor") else MAJOR
    top, bottom, accent, hi = map(_rgb, PALETTES[palette or choices[seed % len(choices)]])

    # gradient background
    t = np.linspace(0, 1, H)[:, None, None]
    bg = (np.array(top) * (1 - t) + np.array(bottom) * t) * np.ones((1, W, 1))
    img = Image.fromarray(bg.astype(np.uint8), "RGB")

    # radial visualiser ring on the right
    cx, cy, r0 = int(W * 0.75), H // 2, 140
    spec = _spectrum(track_dir / "track.wav")
    spec = np.concatenate([spec, spec[::-1]])  # mirror for symmetry
    glow = Image.new("RGB", (W, H))
    g = ImageDraw.Draw(glow)
    for i, v in enumerate(spec):
        a = 2 * np.pi * i / len(spec) - np.pi / 2
        r1 = r0 + 12 + v * 170
        mix = i / len(spec)
        col = tuple(int(accent[k] * (1 - abs(mix - 0.5) * 2) + hi[k] * abs(mix - 0.5) * 2) for k in range(3))
        g.line([(cx + r0 * np.cos(a), cy + r0 * np.sin(a)), (cx + r1 * np.cos(a), cy + r1 * np.sin(a))],
               fill=col, width=5)
    g.ellipse([cx - r0 + 4, cy - r0 + 4, cx + r0 - 4, cy + r0 - 4], outline=hi, width=3)
    # record grooves in the middle
    for k in range(6):
        rr = r0 - 22 - k * 16
        g.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], outline=tuple(int(c * 0.35) for c in accent), width=2)
    g.ellipse([cx - 28, cy - 28, cx + 28, cy + 28], fill=accent)
    img = Image.composite(glow, img, glow.convert("L").point(lambda p: 255 if p > 8 else 0))
    halo = glow.filter(ImageFilter.GaussianBlur(26))
    img = Image.fromarray(np.clip(np.asarray(img, float) + np.asarray(halo, float) * 0.9, 0, 255).astype(np.uint8))

    # text block on the left
    d = ImageDraw.Draw(img)
    words = title.upper().split()
    lines, cur = [], ""
    for w in words:  # wrap at ~11 characters per line
        if cur and len(cur) + 1 + len(w) > 11:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    lines.append(cur)
    size, max_w = (150 if len(lines) <= 2 else 112), cx - r0 - 190 - 72
    font = _font("DIN Condensed Bold.ttf", size)
    while size > 60 and max(d.textlength(line, font=font) for line in lines) > max_w:
        size -= 4  # shrink until the longest line clears the ring
        font = _font("DIN Condensed Bold.ttf", size)
    y = cy - (len(lines) * size * 0.92) / 2 - 30
    for line in lines:
        d.text((76, y + 5), line, font=font, fill=(0, 0, 0))
        d.text((72, y), line, font=font, fill=(255, 255, 255))
        y += size * 0.92
    d.rectangle([76, y + 14, 76 + 120, y + 22], fill=accent)
    small = _font("DIN Alternate Bold.ttf", 34)
    d.text((76, y + 40), subtitle.upper(), font=small, fill=hi)

    # film grain and vignette
    arr = np.asarray(img, float)
    arr += rng.normal(0, 7, (H, W, 1))
    yy, xx = np.mgrid[0:H, 0:W]
    vig = 1 - 0.45 * (((xx - W / 2) / (W / 1.6)) ** 2 + ((yy - H / 2) / (H / 1.3)) ** 2)
    arr *= np.clip(vig, 0.4, 1)[..., None]
    out = track_dir / "thumbnail.png"
    Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).save(out, optimize=True)
    print(f"THUMBNAIL={out}  ({render['bpm']:g} BPM)")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("track_dir", type=Path)
    ap.add_argument("--title", required=True)
    ap.add_argument("--subtitle", help="defaults to '<bpm> BPM / <key> / original house'")
    ap.add_argument("--palette", choices=sorted(PALETTES))
    a = ap.parse_args()
    style = json.loads((a.track_dir / "style.json").read_text())["style"]
    render = json.loads((a.track_dir / "render.json").read_text())
    sub = a.subtitle or f"{render['bpm']:g} BPM  /  {render.get('key') or style['key']}  /  original house"
    draw(a.track_dir, a.title, sub, a.palette)


if __name__ == "__main__":
    main()

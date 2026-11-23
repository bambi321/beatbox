"""Measure a rendered track and compare it with its reference's style.

    .venv/bin/python -m beatbox.check tracks/<id>

Prints a short report and writes check.json. Exits 1 if a hard limit fails
(clipping, wrong loudness, silence, too short).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .analysis import analyse


def main() -> None:
    d = Path(sys.argv[1])
    ref = json.loads((d / "style.json").read_text())["style"]
    render = json.loads((d / "render.json").read_text())
    got = analyse(str(d / "track.wav"))

    low = lambda s: s["bands"]["sub"] + s["bands"]["bass"]
    rows = [
        ("tempo", f"{ref['bpm']} BPM", f"{render['bpm']} BPM (detected {got['bpm']})"),
        ("key", ref["key"], got["key"]),
        ("swing", ref["swing"], got["swing"]),
        ("low end (sub+bass)", f"{low(ref):.0%}", f"{low(got):.0%}"),
        ("brightness", f"{ref['brightness_hz']} Hz", f"{got['brightness_hz']} Hz"),
        ("drum density", f"{ref['onsets_per_beat']}/beat", f"{got['onsets_per_beat']}/beat"),
        ("energy per 8 bars", ref["energy_per_8_bars"], got["energy_per_8_bars"]),
    ]
    problems = []
    if render["peak_db"] > -0.4:
        problems.append(f"peak {render['peak_db']} dB is too hot")
    if abs(render["lufs"] - (-14)) > 1.5:
        problems.append(f"loudness {render['lufs']} LUFS is off target (-14)")
    if render["duration_s"] < 60:
        problems.append(f"only {render['duration_s']}s long")
    if min(got["energy_per_8_bars"][1:-1] or [1]) < 0.05:
        problems.append("a section is near-silent")

    print(f"{'':20} {'reference':>22}   new track")
    for name, a, b in rows:
        print(f"{name:20} {str(a):>22}   {b}")
    print(f"\n{render['duration_s']}s, {render['lufs']} LUFS, peak {render['peak_db']} dB")
    print("PROBLEMS: " + "; ".join(problems) if problems else "OK")
    (d / "check.json").write_text(json.dumps({"reference": ref, "track": got, "render": render,
                                              "problems": problems}, indent=2) + "\n")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()

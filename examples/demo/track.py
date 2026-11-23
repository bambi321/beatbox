"""Demo — deep house, 122 BPM, F minor.

The reference layout for a track script: settings, arrangement, mix, render.
Run: .venv/bin/python examples/demo/track.py
"""

from beatbox import Track

BPM, BARS = 122, 72
t = Track(bpm=BPM, bars=BARS, key="F minor", swing=0.1, seed=11)

PROG = ["Fm9", "Dbmaj7", "Ebsus4", "Cm7"]
BASS = "F2 - . F2 . . F3 . . F2 - . . Ab2 . . "
BASS2 = "Db2 - . Db2 . . Db3 . Eb2 - . Eb2 . . C2 C3"

t.section("intro", 1)
t.chords(PROG, bars=(1, 16), sound="pad", cutoff=900)
t.sweep("chords", (1, 16), 400, 3000)
t.drum("hat", "..x...x...x...x.", bars=(5, 16), gain=-4, humanize=1)
t.drum("kick", "x...x...x...x...", bars=(9, 16))
t.fx("vinyl", 1, length_bars=BARS)
t.fx("riser", 13, length_bars=4, gain=-6)

t.section("groove", 17)
t.fx("impact", 17, gain=-6)
t.drum("kick", "x...x...x...x...", bars=(17, 48))
t.drum("clap", "....x.......x...", bars=(17, 48), gain=-3)
t.drum("hat", "..x...x...x...x.", bars=(17, 48), gain=-3, humanize=1)
t.drum("shaker", "xgxgxgxgxgxgxgxg", bars=(25, 48), gain=-12, humanize=1.5)
t.bass(BASS + BASS2, bars=(17, 48), sound="deep", gain=-1)
t.chords(PROG, bars=(17, 48), sound="stab", rhythm="...x..x....x.x..", gain=-4, cutoff=3000)
t.chords(PROG, bars=(17, 48), sound="pad", gain=-9, bus="pad")
t.melody("C5 - . . Eb5 . F5 - . . . . . . . . " + ". " * 16, bars=(33, 48), sound="rhodes", gain=-6)

t.section("break", 49)
t.chords(PROG, bars=(49, 56), sound="rhodes", rhythm="x-------x-------", gain=-2)
t.drum("hat", "..x...x...x...x.", bars=(53, 56), gain=-6)
t.fx("riser", 53, length_bars=4, gain=-6)

t.section("drop", 57)
t.fx("impact", 57, gain=-6)
t.drum("kick", "x...x...x...x...", bars=(57, 72))
t.drum("clap", "....x.......x...", bars=(57, 72), gain=-3)
t.drum("openhat", "..x...x...x...x.", bars=(57, 72), gain=-6)
t.bass(BASS + BASS2, bars=(57, 72), sound="deep", gain=-1)
t.chords(PROG, bars=(57, 72), sound="stab", rhythm="...x..x....x.x..", gain=-4, cutoff=3000)
t.sweep("bass", (69, 72), 3000, 200)

t.mix("chords", reverb=0.25, delay=0.15)
t.mix("pad", reverb=0.4, gain=-2)
t.mix("lead", reverb=0.3, delay=0.25)
t.mix("drums", highpass=150)
t.mix("fx", reverb=0.3)
t.sidechain(depth=0.6)
t.render(__file__)

"""Move a work-in-progress track folder to its final name once it has a title.

    .venv/bin/python -m beatbox.rename tracks/.wip-<id> "Night Bus"

Prints TRACK_DIR=tracks/night-bus. Refuses to overwrite an existing track.
"""

from __future__ import annotations

import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def slug(title: str) -> str:
    s = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def main() -> None:
    src, title = Path(sys.argv[1]), sys.argv[2]
    name = slug(title)
    if not name:
        sys.exit(f"title {title!r} has no usable characters")
    dst = ROOT / "tracks" / name
    if dst.exists() and dst.resolve() != src.resolve():
        sys.exit(f"{dst.relative_to(ROOT)} already exists: pick another title")
    src.resolve().rename(dst)
    print(f"TRACK_DIR={dst.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

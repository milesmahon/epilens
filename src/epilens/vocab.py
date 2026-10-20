"""Vocabulary: each alias maps to (region base name, modifier) pairs"""

import csv
import functools
import re
from importlib import resources

from .atlas import THIRDS, Atlas

MODIFIERS = set(THIRDS)   # "anterior", "head", "lateral", ...


def normalize(s: str) -> str:
    s = s.lower().replace("'", "").replace("’", "")
    return " ".join(re.sub(r"[^a-z0-9.\-]+", " ", s).split())


@functools.cache
def aliases(atlas: Atlas | None = None) -> dict[str, list[tuple[str, str | None]]]:
    atlas = atlas or Atlas.load()
    out: dict[str, list[tuple[str, str | None]]] = {}

    def add(alias, base, mod):
        key = normalize(alias)
        if (base, mod) not in out.setdefault(key, []):
            out[key].append((base, mod))

    bases = sorted({r.base for r in atlas.regions})
    abbrev_bases: dict[str, set[str]] = {}
    for r in atlas.regions:
        abbrev_bases.setdefault(r.abbrev.lower(), set()).add(r.base)
    for b in bases:
        add(b, b, None)
    for ab, bs in abbrev_bases.items():
        if len(bs) == 1:
            add(ab, next(iter(bs)), None)
    text = (resources.files("epilens") / "data" / "aliases.csv").read_text()
    known = {b.lower(): b for b in bases}
    for row in csv.DictReader(text.splitlines()):
        base = known.get(row["base"].strip().lower())
        if base is None:
            raise ValueError(f"aliases.csv: unknown region {row['base']!r} for alias {row['alias']!r}")
        add(row["alias"], base, (row.get("modifier") or "").strip() or None)
    return out

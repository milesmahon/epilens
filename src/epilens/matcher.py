import difflib
import functools
import re
from dataclasses import dataclass, field, replace

import numpy as np

from .atlas import THIRDS, Atlas
from .vocab import MODIFIERS, aliases, normalize

HEMI_WORDS = {"left": "L", "l": "L", "lt": "L", "right": "R", "r": "R", "rt": "R"}
ENTRY_PRE = {"from", "through", "via", "entering", "enter", "thru"}
TARGET_PRE = {"to", "into", "targeting", "toward", "towards"}
NOUN_CUES = {"entry": "entry", "target": "target"}
FILLER = {"the", "of", "a", "an", "part", "division", "gyrus", "cortex", "region", "electrode", "with", "at",
          "in", "on", "approach", "trajectory", "contacts", "contact", "depth", "side", "nucleus", "area",
          "lobe", "hemisphere", "point", "and", "then", "please", "place", "put", "one", "lead"}
FUZZY_CUTOFF, FUZZY_TIE = 0.75, 0.1

_COORD = r"(-?\d+(?:\.\d+)?)"
COORD_RE = re.compile(rf"\b(target|entry)\s*(?:at|:)?\s*\(?\s*{_COORD}\s*[, ]\s*{_COORD}\s*[, ]\s*{_COORD}\s*\)?")
# "lateral entry" only as its own clause: "Frontal Pole, lateral entry" names the lateral third of the region
LATERAL_RE = re.compile(r"\bentry\s*:?\s*(?:lateral|orthogonal)\b|\b(?:lateral|orthogonal)\s+(?:approach|trajectory)\b"
                        r"|(?:^|[,;])\s*lateral\s+entry\s*(?=$|[,;])|\borthogonal\b")
COUNT_RE = re.compile(r"\b(\d{1,2})\s*(?:contacts?|c|ch|channels?)\b")
BARE_NUM_RE = re.compile(r"(?<![\w.\-])(\d{1,2})(?![\w.])")


@dataclass
class End:
    xyz: list[float]
    text: str
    abbrev: str
    region: str | None
    mode: str  # "region" | "through" | "lateral" | "coords"


@dataclass
class Parse:
    ok: bool
    target: End | None = None
    entry: End | None = None
    n_contacts: int | None = None
    default_contacts: int | None = None
    name: str = ""
    read_as: str = ""
    warnings: list[str] = field(default_factory=list)
    question: str | None = None
    options: list["Parse"] = field(default_factory=list)
    label: str = ""
    text: str = ""  # the phrase this result was parsed from (options: the rewritten phrase to try)


@dataclass
class _Mention:
    cands: list[tuple[str, str | None]]  # (base, alias modifier)
    hemi: str | None = None
    mods: list[str] = field(default_factory=list)
    role: str | None = None
    text: str = ""
    prefixed: bool = False  # written as "rhh" (side letter glued on)


@dataclass
class _Fuzzy:
    span: str
    choices: list[str]  # alias keys, best first, one per distinct region


def _fuzzy(phrase: str, keys: list[str], cutoff: float) -> list[tuple[float, str]]:
    scored = [(difflib.SequenceMatcher(None, phrase, k).ratio(), k) for k in keys]
    return sorted([s for s in scored if s[0] >= cutoff], reverse=True)


def _scan(clause: str, table: dict, keys: list[str], unknown: list[str], fuzzy: list[_Fuzzy]):
    toks = normalize(clause).split()
    mentions: list[_Mention] = []
    hemi, mods, role = None, [], None

    def attach_trailing():
        nonlocal hemi, mods
        if mentions and mods and not mentions[-1].mods:
            mentions[-1].mods, mods = mods, []
        if mentions and hemi and not mentions[-1].hemi:
            mentions[-1].hemi, hemi = hemi, None

    def emit(cands, text, h=None):
        nonlocal hemi, mods, role
        mentions.append(_Mention(cands, h or hemi, mods, role, text, prefixed=h is not None))
        hemi, mods, role = None, [], None

    i = 0
    while i < len(toks):
        t = toks[i]
        # longest exact alias first (multi-word aliases like "temporal pole" beat single words)
        for n in range(min(6, len(toks) - i), 0, -1):
            key = " ".join(toks[i:i + n])
            if key in table and len(key) > 1:
                emit(table[key], key)
                i += n
                break
        else:
            if t in HEMI_WORDS:
                hemi = HEMI_WORDS[t]
            elif t in MODIFIERS:
                mods.append(t)
            elif t in ENTRY_PRE or t in TARGET_PRE:
                attach_trailing()
                role = "entry" if t in ENTRY_PRE else "target"
            elif t in NOUN_CUES:
                attach_trailing()
                if mentions and mentions[-1].role is None:
                    mentions[-1].role = NOUN_CUES[t]
                else:
                    role = NOUN_CUES[t]
            elif len(t) > 1 and t[0] in "lr" and t[1:] in table:   # "rhh" = right + hh
                emit(table[t[1:]], t, h=t[0].upper())
            elif t in FILLER:
                pass
            else:
                # fuzzy: try the longest run of plain words starting here
                run = []
                for u in toks[i:i + 4]:
                    if u in HEMI_WORDS or u in MODIFIERS or u in FILLER or u in NOUN_CUES or u in ENTRY_PRE | TARGET_PRE:
                        break
                    run.append(u)
                hit = None
                for n in range(len(run), 0, -1):
                    phrase = " ".join(run[:n])
                    if len(phrase) < 4:
                        continue
                    scored = _fuzzy(phrase, keys, FUZZY_CUTOFF)
                    if scored:
                        hit = (n, phrase, scored)
                        break
                if hit is None:
                    unknown.append(t)
                    i += 1
                    continue
                n, phrase, scored = hit
                distinct, seen = [], set()
                for s, k in scored:
                    sig = tuple(b for b, _ in table[k])
                    if sig not in seen:
                        seen.add(sig)
                        distinct.append((s, k))
                if len(distinct) > 1 and distinct[0][0] - distinct[1][0] < FUZZY_TIE:
                    fuzzy.append(_Fuzzy(phrase, [k for _, k in distinct[:5]]))
                emit(table[distinct[0][1]], distinct[0][1])
                i += n
                continue
            i += 1
    attach_trailing()
    return mentions, {"hemi": hemi, "mods": mods, "role": role}


def _pick_base(m: _Mention) -> tuple[list[str], str | None]:
    mods = [x for x in m.mods + [mod for _, mod in m.cands if mod] if x in THIRDS]
    bases = [b for b, _ in m.cands]
    for x in mods if len(bases) > 1 else []:
        match = [b for b in bases if x in b.lower().split()]
        if len(match) == 1:
            mods.remove(x)
            return match, (mods[0] if mods else None)
    return bases, (mods[0] if mods else None)


def _abbrev(region, mod: str | None, table: dict) -> str:
    words = [k for k, cands in table.items() if mod and (region.base, mod) in cands and " " not in k]
    return min(words, key=len).upper() if words else region.abbrev


def _label(hemi: str, base: str, mod: str | None) -> str:
    return f"{hemi} {base}" + (f" ({mod})" if mod else "") # no comma: the text is re-parsed on list edits


def _rewrite(raw: str, phrase: str, repl: str) -> str:
    # Replace the first occurrence of a normalized phrase in the user's raw text
    pat = r"[\W_]*".join(re.escape(w) for w in phrase.split())
    return re.sub(pat, repl, raw, count=1, flags=re.I)


def _ask(read_as: str, question: str, choices: list[tuple[str, str]], atlas: Atlas, spacing: float, depth: int,
         only_ok: bool = False) -> Parse:
    options = [] if depth else [replace(_resolve(t, atlas, spacing, depth + 1), label=lab) for lab, t in choices]
    options = [o for o in options if o.ok or not only_ok]
    return Parse(ok=False, read_as=read_as, question=question if options else None, options=options)


def _resolve(text: str, atlas: Atlas, spacing: float, depth: int) -> Parse:
    p = _resolve_inner(text, atlas, spacing, depth)
    p.text = text.strip()
    return p


def _resolve_inner(text: str, atlas: Atlas, spacing: float, depth: int) -> Parse:
    raw = text.strip()
    if not raw:
        return Parse(ok=False)
    table = aliases(atlas)
    keys = [k for k in table if len(k) > 2]
    work = raw.lower()

    coords = {}
    for m in COORD_RE.finditer(work):
        coords[m.group(1)] = [float(m.group(k)) for k in (2, 3, 4)]
    work = COORD_RE.sub(" , ", work)
    lateral = bool(LATERAL_RE.search(work))
    work = LATERAL_RE.sub(" , ", work)
    n_contacts = None
    m = COUNT_RE.search(work) or BARE_NUM_RE.search(work)
    if m:
        n_contacts = int(m.group(1))
        work = work[:m.start()] + " " + work[m.end():]

    unknown, fuzzy, mentions = [], [], []
    for clause in re.split(r"[,;]|\band\b", work):
        found, left = _scan(clause, table, keys, unknown, fuzzy)
        if not found and mentions:   # ", tail" / ", anterior target": belongs to the previous region
            last = mentions[-1]
            last.mods += [x for x in left["mods"] if x not in last.mods]
            last.hemi = last.hemi or left["hemi"]
            if left["role"] and last.role is None:
                last.role = left["role"]
        mentions += found

    ask = functools.partial(_ask, atlas=atlas, spacing=spacing, depth=depth)

    # ---- a word we could not place ----
    if unknown and not mentions and not coords:
        word = " ".join(unknown)
        return ask(f'Didn\'t recognize "{word}"', "Did you mean…",
                   [(k, _rewrite(raw, unknown[0], k)) for _, k in _fuzzy(word, keys, 0.5)[:3]])
    if fuzzy:
        f = fuzzy[0]
        p = ask(f'"{f.span}" could mean several regions', f'"{f.span}": which one?',
                [(table[k][0][0], _rewrite(raw, f.span, k)) for k in f.choices], only_ok=True)
        if p.options:
            return p

    # ---- roles ----
    ent = next((m for m in mentions if m.role == "entry"), None)
    tgt = next((m for m in reversed(mentions) if m.role == "target"), None)
    free = [m for m in mentions if m.role is None]
    if tgt is None and "target" not in coords and free:
        tgt = free.pop()
    if ent is None and "entry" not in coords and free and not lateral:
        ent = free.pop(0)
    if tgt is None and "target" not in coords:
        got = f"entry: {_label(ent.hemi or '?', ent.cands[0][0], None)} · " if ent else ""
        return Parse(ok=False, read_as=got + 'need a target, e.g. "right amygdala"')

    # ---- sides ----
    t_hemi = ("L" if coords["target"][0] < 0 else "R") if "target" in coords else tgt.hemi
    e_hemi = ("L" if coords["entry"][0] < 0 else "R") if "entry" in coords else (ent.hemi if ent else None)
    t_hemi, e_hemi = t_hemi or e_hemi, e_hemi or t_hemi
    if t_hemi is None:
        what = " ".join(tgt.mods + [tgt.text]) if ent is None else tgt.text
        return ask("which side?", f'"{what}": which side?', [("Right", f"right {raw}"), ("Left", f"left {raw}")])

    # ---- a target that names several parts ("fusiform gyrus"): ask which ----
    if tgt is not None and "target" not in coords:
        bases, _ = _pick_base(tgt)
        if len(bases) > 1:
            side = ("right " if t_hemi == "R" else "left ") if tgt.prefixed else ""
            return ask(f'"{tgt.text}" has several parts', f'"{tgt.text}": which part?',
                       [(b, _rewrite(raw, tgt.text, side + normalize(b))) for b in bases])

    warnings = []
    # ---- target ----
    if "target" in coords:
        t_xyz = np.array(coords["target"])
        t_region = atlas.label_at(t_xyz)
        target = End(list(map(float, t_xyz)), f"MNI ({', '.join(f'{v:g}' for v in t_xyz)}) · {t_region}", "",
                     t_region, "coords")
    else:
        bases, mod = _pick_base(tgt)
        region = atlas.region(bases[0], t_hemi)
        t_xyz = atlas.target_point(region, mod)
        target = End([float(v) for v in t_xyz], _label(t_hemi, region.base, mod), t_hemi + _abbrev(region, mod, table),
                     region.name, "region")

    def surface(xyz) -> tuple[str, str | None]:
        r = atlas.surface_region(xyz)
        return (r.hemi + r.abbrev, r.name) if r else ("", None)

    def lateral_end(default: bool) -> End:
        sign = -1.0 if t_xyz[0] < 0 else 1.0
        xyz = atlas.exit_point(t_xyz, [sign, 0, 0])
        return End([float(v) for v in xyz], "lateral (default)" if default else "lateral", *surface(xyz), "lateral")

    if "entry" in coords:
        e_xyz = atlas.snap_entry(t_xyz, coords["entry"])
        r = atlas.surface_region(e_xyz)
        entry = End(list(map(float, e_xyz)), f"MNI ({', '.join(f'{v:g}' for v in np.round(e_xyz, 1))})"
                    + (f" · {_label(r.hemi, r.base, None)}" if r else ""), *surface(e_xyz), "coords")
    elif ent is None:
        entry = lateral_end(default=not lateral)
    else:
        bases, mod = _pick_base(ent)
        regions = [atlas.region(b, e_hemi) for b in bases]

        def where(r):
            return atlas.entry_point(r, mod) if atlas.reaches_surface(r) else atlas.target_point(r, mod)
        # several candidate regions takes the one nearest the target
        region = min(regions, key=lambda r: float(np.linalg.norm(where(r) - t_xyz)))
        if atlas.reaches_surface(region):
            e_xyz = atlas.snap_entry(t_xyz, atlas.entry_point(region, mod))
            entry = End([float(v) for v in e_xyz], _label(e_hemi, region.base, mod), e_hemi + region.abbrev, region.name,
                        "region")
        else:
            way = atlas.target_point(region, mod)
            if np.linalg.norm(way - t_xyz) < 2:
                entry = lateral_end(default=True)
            else:
                e_xyz = atlas.exit_point(t_xyz, way - t_xyz)
                entry = End([float(v) for v in e_xyz], f"through {_label(e_hemi, region.base, mod)} → surface",
                            e_hemi + region.abbrev, region.name, "through")

    if unknown:
        warnings.append(f"ignored: {' '.join(unknown)}")
    default_n = atlas.fit_contacts(target.xyz, entry.xyz, spacing)
    n_text = f"{n_contacts} contacts" if n_contacts else f"{default_n} contacts (fits)"
    name = entry.abbrev or target.abbrev or "Electrode"   # named by where it enters
    return Parse(ok=True, target=target, entry=entry, n_contacts=n_contacts, default_contacts=default_n, name=name,
                 read_as=f"entry: {entry.text} · target: {target.text} · {n_text}", warnings=warnings)


def parse(text: str, atlas: Atlas | None = None, spacing: float = 5.0) -> Parse:
    return _resolve(text, atlas or Atlas.load(), spacing, depth=0)

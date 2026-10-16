"""The ranked list of microphones, and how Waffler picks one from it (S12).

``mic_ranking`` in settings.json is an ordered list of ``{"name", "id"?}``:
the microphones the person prefers, best first. The choice is automatic: the
highest-ranked microphone that is plugged in right now. When none of them is,
Waffler follows the system's microphone, minus a Bluetooth headset it would
rather not open (audio.py ``_resolve_input_device``), exactly as it does when
the list is empty. An empty list is the default and means "follow the
system".

A microphone is kept as a name and, where there is one, an ``id``: the
system's own identifier (``uid:...``, macOS) or the host API it was listed
under (``api:...``, which tells apart the several lists Windows keeps of one
device). It is never kept as a PortAudio index, which means another device
after the next restart of PortAudio. The index is worked out only when a
stream is opened (``open_resolver``).

Older versions saved one microphone as an index, ``audio_device_index`` in
config.json. While ``mic_ranking`` is absent that index is read as a
ranking of one (``migrate``), the choice the person made and not the default's
index that the old code fell back to. It is carried over without being
deleted, so an older Waffler still finds what it wrote.
"""

from __future__ import annotations

from typing import Callable, Dict, Iterable, List, Optional

try:
    import mic_devices as md
    import user_messages as um
except ImportError:  # imported as src.mic_ranking
    from src import mic_devices as md
    from src import user_messages as um

SETTING = "mic_ranking"
LEGACY_KEY = "audio_device_index"       # config.json, the single index of older versions
MAX_ENTRIES = 8
MAX_TEXT = 200


# ── the setting ───────────────────────────────────────────────────────────

def clean_entry(raw) -> Optional[Dict[str, str]]:
    """``{"name", "id"?}`` from one stored entry, or None if it is not one."""
    if not isinstance(raw, dict):
        return None
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > MAX_TEXT:
        return None
    out = {"name": name.strip()}
    ident = raw.get("id")
    if isinstance(ident, str) and ident.strip() and len(ident.strip()) <= MAX_TEXT:
        out["id"] = ident.strip()
    return out


def canonical(raw) -> List[Dict[str, str]]:
    """A list of entries as it is kept: each cleaned, a microphone listed once
    (the first place), at most ``MAX_ENTRIES``. Anything unreadable is left
    out, so a damaged file reads as a shorter list, never as an error."""
    out: List[Dict[str, str]] = []
    for item in raw if isinstance(raw, list) else ():
        entry = clean_entry(item)
        if entry is None or any(md.same_name(entry["name"], e["name"]) for e in out):
            continue
        out.append(entry)
        if len(out) == MAX_ENTRIES:
            break
    return out


def ranking_of(stored) -> List[Dict[str, str]]:
    """The saved ranking (``[]`` when there is none)."""
    if not isinstance(stored, dict):
        return []
    return canonical(stored.get(SETTING))


def has_ranking(stored) -> bool:
    """Whether settings.json has a ranking at all, empty or not. An empty one
    is the person's choice to follow the system and ends the old index's
    say; an absent one has not been decided yet."""
    return isinstance(stored, dict) and isinstance(stored.get(SETTING), list)


def touches(payload) -> bool:
    return isinstance(payload, dict) and SETTING in payload


def _strict(item) -> Optional[Dict[str, str]]:
    """One entry of a save, or None if it is not exactly a name in text with,
    at most, an id in text. (Reading is lenient; saving is not.)"""
    if not isinstance(item, dict):
        return None
    ident = item.get("id")
    if ident is not None and not isinstance(ident, str):
        return None
    return clean_entry(item)


def settings_problem(payload) -> Optional[str]:
    """Why a save of ``mic_ranking`` is refused, in a plain sentence, or None.
    Checked as a whole before anything is saved: a list of at most 8, each a
    name (and an optional id) in text, none twice."""
    if not touches(payload):
        return None
    raw = payload[SETTING]
    if not isinstance(raw, list) or len(raw) > MAX_ENTRIES:
        return um.MIC_RANKING_UNKNOWN
    seen: List[str] = []
    for item in raw:
        entry = _strict(item)
        if entry is None or any(md.same_name(entry["name"], s) for s in seen):
            return um.MIC_RANKING_UNKNOWN
        seen.append(entry["name"])
    return None


def merged(payload) -> List[Dict[str, str]]:
    """The list a checked save stores."""
    return canonical(payload.get(SETTING))


# ── editing ───────────────────────────────────────────────────────────────

def promote(ranking: Iterable[dict], entry: dict) -> List[Dict[str, str]]:
    """``entry`` at rank 1, any earlier place of it removed, the rest in
    order; the lowest-ranked entry goes if the list is full."""
    new = clean_entry(entry)
    rest = canonical(ranking)
    if new is None:
        return rest
    rest = [e for e in rest if not md.same_name(e["name"], new["name"])]
    return ([new] + rest)[:MAX_ENTRIES]


def without(ranking: Iterable[dict], name: str) -> List[Dict[str, str]]:
    return [e for e in canonical(ranking) if not md.same_name(e["name"], name)]


# ── the single index of older versions ────────────────────────────────────

def migrate(legacy_index, pa_devices: Iterable[md.Device],
            live_devices: Optional[Iterable[md.Device]] = None) -> Optional[List[Dict[str, str]]]:
    """The saved index as a ranking of one, from the list PortAudio has now;
    None when that index is not an input here (a USB microphone that is not
    plugged in at this start, or a list that has been renumbered since), in
    which case the old key is left and the next read tries again. With the
    system's own list the entry carries its identifier."""
    if isinstance(legacy_index, bool) or not isinstance(legacy_index, int):
        return None
    for dev in pa_devices:
        if dev.index == legacy_index:
            if live_devices is not None:
                live = md.find({"name": dev.name}, live_devices)
                if live is not None:
                    return [live.entry()]
            return [dev.entry()]
    return None


def effective(stored, legacy_index, pa_devices, live_devices=None) -> List[Dict[str, str]]:
    """The ranking to use now: the saved one, or, while none is saved, the old
    index read as a ranking of one."""
    if has_ranking(stored):
        return ranking_of(stored)
    return migrate(legacy_index, pa_devices, live_devices) or []


# ── choosing, at the moment a stream is opened ────────────────────────────

def _pa_inside_the_stream_lock() -> List[md.Device]:
    return md.pa_inputs(locked=True)


def open_resolver(read_settings: Callable[[], dict],
                  read_legacy: Optional[Callable[[], Optional[int]]] = None,
                  pa_reader: Optional[Callable[[], List[md.Device]]] = None,
                  live_reader: Optional[Callable[[], Optional[List[md.Device]]]] = None
                  ) -> Callable[[], Optional[int]]:
    """A function for the recorder (``AudioRecorder(device_resolver=...)``):
    the PortAudio index of the highest-ranked microphone that is here, or None
    to follow the system.

    Asked by the recorder right after PortAudio has been started again, and
    while it holds the stream lock (so PortAudio is read without taking it
    again), which means the list read here is the list the stream is made from
    and its index is good. Never raises: whatever goes wrong, the answer is
    None, and a recording is never held up by a preference."""
    def resolve() -> Optional[int]:
        try:
            stored = read_settings()
            legacy = None
            if not has_ranking(stored) and read_legacy is not None:
                legacy = read_legacy()
            if not has_ranking(stored) and legacy is None:
                return None
            pa = (pa_reader or _pa_inside_the_stream_lock)()
            live = (live_reader or md.live_inputs)()
            ranking = effective(stored, legacy, pa, live)
            if not ranking:
                return None
            chosen = md.first_present(ranking, live if live is not None else pa)
            return md.index_of(chosen, pa)
        except Exception:
            return None
    return resolve


# ── what Settings shows ───────────────────────────────────────────────────

def _row(dev: Optional[md.Device], name: str, ident: Optional[str]) -> dict:
    return {"name": name, "id": ident, "present": dev is not None,
            "bluetooth": bool(dev.known_bluetooth if dev is not None else md.is_known_bluetooth(name)),
            "builtin": bool(dev.builtin if dev is not None else md.is_builtin(name)),
            "active": False}


def setup_view(ranking: Iterable[dict], devices: Iterable[md.Device]) -> dict:
    """The microphone sheet: the ranked list with which are here and which one
    is in use (the first that is here), what the system's microphone is, and
    the inputs that can be added, with the virtual ones apart.

    ``following_system`` is True when no ranked microphone is here, which
    includes an empty list."""
    ranking = canonical(ranking)
    devices = list(devices)
    rows = []
    for entry in ranking:
        dev = md.find(entry, devices)
        rows.append(_row(dev, entry["name"], entry.get("id")))
    used = next((i for i, r in enumerate(rows) if r["present"]), None)
    if used is not None:
        rows[used]["active"] = True
    system = md.default_of(devices)
    add, other = [], []
    for dev in md.distinct(devices):
        if any(md.same_name(dev.name, e["name"]) for e in ranking):
            continue
        item = {"name": dev.name, "id": dev.ident(), "bluetooth": dev.known_bluetooth,
                "builtin": dev.builtin, "default": dev.is_default}
        (other if dev.virtual else add).append(item)
    return {
        "ranking": rows,
        "following_system": used is None,
        "system": system.name if system is not None else None,
        # The system's microphone is one Waffler avoids (audio.py): it listens
        # through another, so music stays in high quality.
        "system_avoided": bool(system.bluetooth) if system is not None else False,
        "add": add,
        "other": other,
        "max": MAX_ENTRIES,
        "full": len(ranking) >= MAX_ENTRIES,
    }

"""Load arrow recordings exported by the userscript (userscript/thoughtcoach-arrows.user.js).

Format 2 (userscript >= 0.3): `position` events carry the piece placement (FEN board part)
each time the board changes. Format 1 (older test versions) recorded last-move squares instead;
it can still be loaded and summarised.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

GAME_FORMATS = {"thoughtcoach-arrows/1", "thoughtcoach-arrows/2"}
BUNDLE_FORMAT = "thoughtcoach-arrows-bundle/1"
EVENT_TYPES = {"load", "move", "position", "flip", "shape_add", "shape_remove", "game_end", "resume",
               "pick_up", "put_down"}
FILES = "abcdefgh"


def load_recordings(path: str | Path) -> list[dict]:
    """Return a list of game recordings from a single-game file or an "export all" bundle."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    games = data["games"] if data.get("format") == BUNDLE_FORMAT else [data]
    for game in games:
        validate(game)
    return games


def validate(game: dict) -> None:
    if game.get("format") not in GAME_FORMATS:
        raise ValueError(f"not a Thought Coach arrow recording (format={game.get('format')!r})")
    for i, event in enumerate(game["events"]):
        if event.get("type") not in EVENT_TYPES:
            raise ValueError(f"event {i}: unknown type {event.get('type')!r}")
        if not isinstance(event.get("t"), (int, float)):
            raise ValueError(f"event {i}: missing timestamp")


def squares_of(placement: str) -> dict[str, str]:
    """'rnbqkbnr/pppppppp/8/...' -> {'a8': 'r', 'b8': 'n', ...} (occupied squares only)."""
    pieces = {}
    for row_index, row in enumerate(placement.split("/")):
        rank = 8 - row_index
        file = 0
        for ch in row:
            if ch.isdigit():
                file += int(ch)
            else:
                pieces[f"{FILES[file]}{rank}"] = ch
                file += 1
    return pieces


def placement_change(before: str | None, after: str) -> str:
    """Short description of what changed between two placements, e.g. 'g1 -> f3' or 'e1/h1 -> f1/g1'."""
    if before is None:
        return "(first position seen)"
    old, new = squares_of(before), squares_of(after)
    left = sorted(sq for sq in old if new.get(sq) != old[sq] and sq not in new)
    arrived = sorted(sq for sq in new if old.get(sq) != new[sq])
    return f"{'/'.join(left) or '-'} -> {'/'.join(arrived) or '-'}"


def _clock(ms: float, start: float) -> str:
    seconds = (ms - start) / 1000
    return f"{int(seconds // 60):3d}:{seconds % 60:04.1f}"


def summarise(game: dict) -> str:
    """Readable timeline of one recording: what was drawn, when, and in which position."""
    events = game["events"]
    you = game.get("my_color") or game.get("orientation")
    out = [
        f"Game {game['game_id']}  ({game['url']})  you: {you}",
        f"Recorded {game['started_at']} -> {game.get('ended_at') or 'not finished'}",
    ]
    adds = [e for e in events if e["type"] == "shape_add"]
    arrows = sum(1 for e in adds if e["to"])
    seen = game.get("positions_seen", game.get("moves_seen", 0))
    out.append(f"{seen} positions seen, {arrows} arrows and {len(adds) - arrows} circles drawn")
    out.append("")
    if not events:
        return "\n".join(out)

    start = events[0]["t"]
    counter = "positions_seen" if "positions_seen" in events[0] else "moves_seen"
    previous = None
    out.append("   time    pos  event")
    for e in events:
        when = _clock(e["t"], start)
        kind = e["type"]
        if kind in ("shape_add", "shape_remove"):
            sign = "+" if kind == "shape_add" else "-"
            text = f"{sign} arrow  {e['from']}->{e['to']}" if e["to"] else f"{sign} circle {e['from']}"
            if e["brush"] != "green":  # lichess default; other colours only if you used modifier keys
                text += f"  ({e['brush']})"
        elif kind == "position":
            clocks = f"  [W {e.get('clock_white') or '-'} | B {e.get('clock_black') or '-'}]"
            text = f"board {placement_change(previous, e['placement'])}{clocks}"
            previous = e["placement"]
        elif kind in ("move", "load"):  # format 1
            clocks = f"  [W {e.get('clock_white') or '-'} | B {e.get('clock_black') or '-'}]"
            text = f"{kind:<5} {'/'.join(e['squares']) or '(start)'}{clocks}"
        elif kind == "flip":
            text = f"board flipped to {e['orientation']}"
        elif kind == "resume":
            text = "board changed after 'game over' (false alarm), recording continued"
        else:
            text = "game over"
        out.append(f"{when}  {e[counter]:5d}  {text}")

    first = datetime.fromtimestamp(start / 1000, tz=timezone.utc).isoformat(timespec="seconds")
    out.append("")
    out.append(f"time = seconds since the first event ({first}); pos = positions seen so far")
    return "\n".join(out)

"""How your thinking changes with the game: the same habits, split by situation, next to masters'.

Every move with time to think (not skipped, not in time trouble) is put in one bucket of each split:

  situation    winning (win chance 65%+), level, losing (under 35%)
  after_slip   the 3 moves after one of yours that cost 10%+, or otherwise
  phase        opening (to move 12), middlegame, endgame (little material left)
  their_move   their last move was a capture or check, or a quiet move

and the per-move habits are counted in each bucket: threats left unstopped, the best move among your
candidates (when you drew some), thinking shown, candidates per move, costly moves, and time against that game's average.
The interesting part is the change, not the level: masters' rates move between buckets too (they are
losing less often, and endgames are slower everywhere), so a finding is your change set against theirs.

Masters' split rates come from master_baseline.json ("splits"), pooled per player and averaged across
players like the other rates. Splits by win chance need every move read exactly, so games read off a
video board (board_read) only count in the other splits. No engine work: read from the review.
"""

from __future__ import annotations

import html
import statistics

AHEAD, LEVEL = 0.65, 0.35
COSTLY = 0.10
AFTER = 3  # own moves after a costly one
ENDGAME = 26  # non-pawn material on the board, both sides (Q9 R5 B3 N3): two rooks and a minor each
MIN_YOU = 25  # your cases in a bucket before it's shown
MIN_MASTERS = 3  # master players with enough cases in a bucket

SPLITS = {  # key: (title, [(bucket, label)], needs exact moves)
    "situation": ("Winning, level or losing", [("ahead", "When you're winning"), ("level", "When it's level"),
                                               ("behind", "When you're losing")], True),
    "after_slip": ("After a costly move", [("after", f"The {AFTER} moves after a costly one"), ("steady", "Otherwise")], True),
    "phase": ("Opening, middlegame, endgame", [("opening", "In the opening"), ("middle", "In the middlegame"),
                                               ("endgame", "In the endgame")], False),
    "their_move": ("After their capture or check", [("forcing", "After their capture or check"),
                                                    ("quiet", "After a quiet move")], False),
}
# key: (label, kind, True when more is better (None: neither), needs exact moves)
#   kind "rate": (cases where it happened, cases);  "mean": (sum, cases)
MEASURES = {
    "q3": ("Real threats left unstopped", "rate", False, False),
    "best": ("A good move among your candidates", "rate", True, False),
    "shown": ("Moves with your thinking drawn", "rate", True, False),
    "cands": ("Candidates drawn per move", "mean", None, False),
    "costly": ("Moves that cost 10%+", "rate", False, True),
    "think": ("Time spent, against your average", "mean", None, False),
}
SHIFT = {"rate": 0.08, "mean": 0.3}  # how much more your change must be than masters' to call it out
VALUES = {"N": 3, "B": 3, "R": 5, "Q": 9}


def _material(fen: str) -> int:
    board = fen.split()[0]
    return sum(VALUES.get(c.upper(), 0) for c in board if c.isalpha())


def records(g: dict, exact: bool = True) -> list[dict]:
    """One record per move with time to think: its bucket in each split and what each habit counted.
    `exact` False for games read off a video board: no win-chance splits or engine-only habits."""
    r, a = g["report"], g.get("analysis") or {}
    tt = (r.get("thresholds") or {}).get("time_trouble", 30)
    by_ply = {m.get("ply"): m for m in a.get("moves", [])}
    moves = [m for m in r.get("moves", []) if not m.get("skipped")]
    calm = [m for m in moves if m.get("clock_before") is None or m["clock_before"] >= tt]
    times = [t for t in ((by_ply.get(m.get("ply")) or {}).get("time_spent") for m in calm) if t is not None]
    usual = statistics.mean(times) if times else None  # this game's average move time
    out, slips = [], []
    for i, m in enumerate(moves):
        loss = m.get("wp_loss")
        recent = [j for j in slips if i - j <= AFTER]
        if loss is not None and loss >= COSTLY:
            slips.append(i)
        if m.get("clock_before") is not None and m["clock_before"] < tt:
            continue
        am = by_ply.get(m.get("ply")) or {}
        b = {}
        if exact and m.get("wp_before") is not None:
            wp = m["wp_before"]
            b["situation"] = "ahead" if wp >= AHEAD else "level" if wp >= LEVEL else "behind"
            b["after_slip"] = "after" if recent else "steady"
        if m.get("fen"):
            b["phase"] = ("opening" if (m.get("move_number") or 99) <= 12
                          else "endgame" if _material(m["fen"]) <= ENDGAME else "middle")
        their = am.get("opponent_move_san")
        if their:
            b["their_move"] = "forcing" if ("x" in their or "+" in their or "#" in their) else "quiet"
        v = {}
        q3 = (m.get("q3") or {}).get("status")
        if q3 in ("spotted", "handled", "not_drawn"):
            v["q3"] = (int(q3 == "not_drawn"), 1)
        cands = {c.get("move_san") for c in m.get("drawn_candidates") or []} - {None}
        q4 = m.get("q4") or {}
        if cands and q4.get("engine"):
            v["best"] = (int(bool(q4.get("found"))), 1)
        v["shown"] = (int(bool(cands or m.get("drawn_opponent_ideas") or m.get("key_squares"))), 1)
        if cands:
            v["cands"] = (len(cands), 1)
        if exact and loss is not None:
            v["costly"] = (int(loss >= COSTLY), 1)
        t = am.get("time_spent")
        if t is not None and usual:
            v["think"] = (min(float(t) / usual, 10.0), 1)
        out.append({"b": b, "v": v})
    return out


def tally(recs: list[dict]) -> dict:
    """{split: {bucket: {measure: [k, n]}}}, plus "all" for each split's moves overall."""
    out: dict = {}
    for rec in recs:
        for split, bucket in rec["b"].items():
            for name in (bucket, "all"):
                cell = out.setdefault(split, {}).setdefault(name, {})
                for key, (k, n) in rec["v"].items():
                    c = cell.setdefault(key, [0, 0])
                    c[0] += k
                    c[1] += n
    return out


def of_games(games: list[dict]) -> dict:
    return tally([rec for g in games for rec in records(g, not g.get("board_read"))])


def master_splits(per_player: list[dict], min_n: int = 10) -> dict:
    """Masters' split rates from each player's tally: each player's rate where they have min_n+ cases,
    averaged across players. {split: {bucket: {measure: {"rate", "players", "n"}}}}."""
    out: dict = {}
    for split, (_, buckets, _) in SPLITS.items():
        for bucket in [b for b, _ in buckets] + ["all"]:
            for key in MEASURES:
                vals, n = [], 0
                for t in per_player:
                    k, m = t.get(split, {}).get(bucket, {}).get(key, (0, 0))
                    if m >= min_n:
                        vals.append(k / m)
                        n += m
                if vals:
                    out.setdefault(split, {}).setdefault(bucket, {})[key] = {
                        "rate": round(sum(vals) / len(vals), 3), "players": len(vals), "n": n}
    return out


def _masters() -> dict:
    from . import masters

    return masters.splits()


def _rate(cell: dict | None, key: str) -> tuple[float | None, int]:
    k, n = (cell or {}).get(key, (0, 0))
    return (k / n if n else None), n


def _m(ms: dict, split: str, bucket: str, key: str) -> float | None:
    c = ms.get(split, {}).get(bucket, {}).get(key)
    return c["rate"] if c and c["players"] >= MIN_MASTERS else None


def _fmt(key: str, x: float | None) -> str:
    if x is None:
        return "–"
    kind = MEASURES[key][1]
    if key == "think":
        return f"{x:.1f}×"
    return f"{x:.1f}" if kind == "mean" else f"{x:.0%}"


def findings(yours: dict, ms: dict | None = None, limit: int = 4) -> list[dict]:
    """Where your change between buckets differs most from masters'. Each: split, bucket, measure, your
    rate there and overall, masters' rate there and overall, and a sentence."""
    ms = _masters() if ms is None else ms
    out = []
    for split, (_, buckets, _) in SPLITS.items():
        for bucket, blabel in buckets:
            for key, (label, kind, up, _) in MEASURES.items():
                mine, n = _rate(yours.get(split, {}).get(bucket), key)
                base, nb = _rate(yours.get(split, {}).get("all"), key)
                mb, mall = _m(ms, split, bucket, key), _m(ms, split, "all", key)
                if mine is None or base is None or n < MIN_YOU or nb - n < MIN_YOU or mb is None or mall is None:
                    continue
                gap = (mine - base) - (mb - mall)
                if kind == "mean":
                    gap = (mine / base if base else 1) - (mb / mall if mall else 1)
                own = (mine / base - 1) if kind == "mean" else (mine - base)
                if abs(gap) < SHIFT[kind] or abs(own) < SHIFT[kind] / 2:
                    continue  # not different from masters, or only masters changed: nothing to say about you
                worse = None if up is None else (gap < 0) == up
                text = (f"{blabel}: {label.lower()} {_fmt(key, base)} → {_fmt(key, mine)} "
                        f"({n} move{'s' * (n != 1)}). Masters: {_fmt(key, mall)} → {_fmt(key, mb)}.")
                out.append({"split": split, "bucket": bucket, "key": key, "mine": mine, "base": base, "n": n,
                            "masters": mb, "masters_all": mall, "gap": gap, "worse": worse, "text": text,
                            "score": abs(gap) / SHIFT[kind] * (1.3 if worse else 1)})
    out.sort(key=lambda f: -f["score"])
    seen, best = set(), []
    for f in out:  # one per split and measure: the clearest bucket
        if (f["split"], f["key"]) in seen:
            continue
        seen.add((f["split"], f["key"]))
        best.append(f)
    return best[:limit]


def section(games: list[dict], ms: dict | None = None) -> str:
    """The "How your thinking changes" section: the biggest differences from masters, then a table per split."""
    ms = _masters() if ms is None else ms
    yours = of_games(games)
    if not yours:
        return ""
    found = findings(yours, ms)
    tops = "".join(f"<li class='{'off' if f['worse'] else 'ok' if f['worse'] is False else ''}'>{html.escape(f['text'])}</li>"
                   for f in found)
    lead = (f"<ul class='sfind'>{tops}</ul>" if tops else
            f"<p class='cap'>No clear difference from masters yet: a bucket needs {MIN_YOU}+ of your moves before it's compared. "
            f"Play more rapid and classical games and this fills in.</p>")
    tables = []
    for split, (title, buckets, _) in SPLITS.items():
        if split not in yours:
            continue
        head = "".join(f"<th>{html.escape(lab)}</th>" for _, lab in buckets)
        body = []
        for key, (label, _, _, _) in MEASURES.items():
            cells = []
            for b, _ in buckets:
                mine, n = _rate(yours[split].get(b), key)
                m = _m(ms, split, b, key)
                you = _fmt(key, mine) if n >= MIN_YOU else f"<span class='muted' title='{n} moves: not enough yet'>–</span>"
                cells.append(f"<td><b>{you}</b><small>masters {_fmt(key, m)}</small></td>")
            body.append(f"<tr><th scope='row'>{html.escape(label)}</th>{''.join(cells)}</tr>")
        tables.append(f"<details class='fold'><summary>{html.escape(title)}</summary><div class='stab'><table>"
                      f"<thead><tr><th></th>{head}</tr></thead><tbody>{''.join(body)}</tbody></table></div></details>")
    return (f"<section class='splits'><h2>How your thinking changes</h2><p class='cap'>The same habits when you're "
            f"winning or losing, after a costly move, by phase, and after their capture or check, next to masters'. "
            f"What matters is the change: masters' numbers move too. Moves in time trouble aren't counted.</p>"
            f"{lead}{''.join(tables)}</section>")


CSS = """
.splits .sfind { margin:8px 0 12px; padding-left:18px; } .splits .sfind li { margin:6px 0; color:var(--fg2); }
.splits .sfind li.off::marker { color:var(--warn); } .splits .sfind li.ok::marker { color:var(--good); }
.splits .stab { overflow-x:auto; } .splits table { border-collapse:collapse; width:100%; font-size:13px; margin:8px 0; }
.splits th, .splits td { text-align:left; padding:6px 8px; border-bottom:1px solid var(--line); vertical-align:top; }
.splits thead th { color:var(--muted); font-weight:600; font-size:12px; }
.splits tbody th { font-weight:500; color:var(--fg2); }
.splits td b { display:block; font-variant-numeric:tabular-nums; }
.splits td small { color:var(--muted); font-size:11.5px; white-space:nowrap; }
"""

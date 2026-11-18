"""What kind of game it was, from the win-chance curve and the result: a swindle, a crazy game, a
stomp, a close fight or a grind. No engine work: it reads the review's win chances.

  swindle   whoever got the result was at SWINDLE or worse on the way (you, from 8% to a win; or
            "swindled": you were at 1 - SWINDLE or better and lost). A draw from there counts too.
  crazy     both sides had a swing of SWING or more in one move, and there were CRAZY swings in all
  stomp     decisive, the winner was at DECIDED by STOMP_BY of the way through, and the loser
            was never better than LOSER_MAX ("stomped" when it was you)
  close     none of these: nobody was ever clearly winning, or one or two big swings decided it
  grind     none of these: one side pulled ahead slowly, without a single big swing

Win chances are yours (0-1), from the moves the engine reviewed (book moves are left out).
"""

from __future__ import annotations

from .fmt import pct

SWINDLE = 0.15
SWING = 0.20
CRAZY = 3
DECIDED = 0.80
STOMP_BY = 0.60
LOSER_MAX = 0.60

LABEL = {"swindle": "A swindle", "swindled": "Swindled", "crazy": "A crazy game", "stomp": "A stomp",
         "stomped": "Stomped", "close": "A close fight", "grind": "A grind"}


def curve(analysis: dict) -> list[tuple[int, float]]:
    """(ply, your win chance after it) before and after each of your reviewed moves, in order."""
    out: list[tuple[int, float]] = []
    for m in sorted(analysis.get("moves", []), key=lambda m: m["ply"]):
        if m.get("skipped") or m.get("wp_before") is None:
            continue
        out.append((m["ply"] - 1, m["wp_before"]))
        if m.get("wp_after") is not None:
            out.append((m["ply"], 1.0 if (m.get("move_san") or "").endswith("#") else m["wp_after"]))
    return out


def classify(analysis: dict, game: dict) -> dict | None:
    """{"kind", "label", "why"} for one game, or None when there is too little to go on."""
    plies = curve(analysis)
    w = [x for _, x in plies]
    res = game.get("result")
    if len(w) < 6 or res not in ("1-0", "0-1", "1/2-1/2"):
        return None
    white = game.get("my_color") == "white"
    me = 0.5 if res == "1/2-1/2" else float((res == "1-0") == white)
    lo, hi = min(w), max(w)
    deltas = [b - a for a, b in zip(w, w[1:])]
    mine = sum(1 for d in deltas if d >= SWING)
    theirs = sum(1 for d in deltas if d <= -SWING)

    def out(kind: str, why: str) -> dict:
        return {"kind": kind, "label": LABEL[kind], "why": why}

    if me >= 0.5 and lo <= SWINDLE:
        return out("swindle", f"You were down to {pct(lo)} and {'won' if me == 1 else 'still drew'}.")
    if me <= 0.5 and hi >= 1 - SWINDLE:
        return out("swindled", f"You were up to {pct(hi)} and {'lost' if me == 0 else 'only drew'}.")
    if mine and theirs and mine + theirs >= CRAZY:
        return out("crazy", f"The win chance swung {SWING * 100:.0f} points or more in one move {mine + theirs} times.")
    if me != 0.5:
        won = me == 1
        ahead = w if won else [1 - x for x in w]  # the winner's win chance
        at = next((i for i, x in enumerate(ahead) if x >= DECIDED), None)
        if at is not None and at <= STOMP_BY * len(w) and min(ahead) >= 1 - LOSER_MAX:
            when = f"by move {(plies[at][0] + 1) // 2}"
            return out("stomp" if won else "stomped",
                       f"{'You' if won else 'They'} were winning {when}, and {'they' if won else 'you'} never got "
                       f"better than {pct(1 - min(ahead))}.")
    if 1 - DECIDED < lo and hi < DECIDED:
        return out("close", "Neither side was ever clearly winning.")
    if mine + theirs == 0:
        return out("grind", "No single move decided it: one side pulled ahead bit by bit.")
    if mine + theirs == 1:
        return out("close", "Level until one move decided it.")
    return out("close", "It swung both ways before it was decided.")

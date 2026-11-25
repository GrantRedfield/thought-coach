"""The coaching summary at the top of a game review: one focus for the next game, what went well, and the
decisions that mattered. Everything is derived from the gap report (gaps.py); nothing is written for one game.

Evidence is kept apart on purpose:
  recorded      an idea that is in the arrows or the words (drawn, circled or said)
  not recorded  an idea that isn't: the player may still have seen it, so this never says "you missed it"
  answered      whether the move played dealt with an idea (a separate question from whether it was recorded)
"""

from __future__ import annotations

SMALL = 5  # a rate over fewer cases than this is shown as a count and flagged as a small sample


def _label(m: dict, white: bool) -> str:
    return f"{m['move_number']}{'.' if white else '...'} {m['move_san']}"


def _pct(x: float) -> str:
    return f"{round(x * 100)}%"


def ratio(k: int, n: int) -> str:
    """'7 of 7', with the rate only when the sample can carry one."""
    if not n:
        return "none this game"
    return f"{k} of {n}" + ("" if n < SMALL else f" ({_pct(k / n)})")


def human_tc(tc: str | None) -> str:
    """'600+0' -> '10+0 rapid' (the lichess speed bands, by estimated game length)."""
    try:
        base, inc = (int(x) for x in str(tc).split("+"))
    except (TypeError, ValueError):
        return str(tc or "")
    est = base + 40 * inc
    speed = "bullet" if est < 180 else "blitz" if est < 480 else "rapid" if est < 1500 else "classical"
    mins = base / 60
    return f"{mins:g}+{inc} {speed}"


def costly_patterns(report: dict) -> list[dict]:
    """Each costly move (with time to think) with what the evidence says about it."""
    white = report["game"]["my_color"] == "white"
    out = []
    for m in report["moves"]:
        if not m.get("mistake") or m.get("gap") == "time_trouble":
            continue
        reply = m.get("reply") or {}
        q4 = m.get("q4") or {}
        out.append({
            "ply": m["ply"], "label": _label(m, white), "loss": m["wp_loss"], "wp_before": m["wp_before"], "wp_after": m["wp_after"],
            "best": m.get("best_move_san"), "rejected": q4.get("wrongly_rejected") or [],
            "reply": reply.get("san"), "reply_recorded": bool(reply.get("recorded")),
            "gap": m.get("gap"), "threat": (m.get("q3") or {}).get("threat_san"),
        })
    return sorted(out, key=lambda c: -c["loss"])


FOCUS = {
    "test_reply": ("Test each candidate against their strongest reply",
                   "Before committing to a move, ask: what is their best answer to it?"),
    "keep_best": ("Trust the good move you found",
                  "When your first good idea holds, check it once more before switching to a new one."),
    "their_idea": ("Ask what your opponent wants before you move",
                   "Before each move, name the move they'd play if it were their turn."),
    "widen": ("Find a second candidate when it matters",
              "In sharp positions, draw two or three moves before you choose."),
    "clock": ("Keep time for the moments that decide the game",
              "Spend less on the opening and quiet moves so the critical ones get your time."),
    "record": ("Record your thinking so the review can coach it",
               "Draw an arrow for each move you consider, and their threat, before you play."),
    "keep": ("Keep doing what worked",
             "Carry the same routine into the next game: candidates first, then their reply."),
}


def _held(report: dict) -> int:
    """Real threats (with time to think) that the move played dealt with: it answered the threat, or it cost nothing.
    A costly move there isn't counted as holding, whatever its cause."""
    tt = report["thresholds"].get("time_trouble", 30)
    return sum(1 for m in report["moves"] if (m.get("q3") or {}).get("status") in ("spotted", "handled", "not_drawn")
               and not (m.get("clock_before") is not None and m["clock_before"] < tt)
               and (m["q3"]["status"] == "handled" or not m.get("mistake")))


def summary(report: dict, analysis: dict) -> dict:
    """{"focus": {"key", "title", "why", "habit", "plies"}, "well": [...], "stats": [...], "decisions": [...]}."""
    s = report["summary"]
    white = report["game"]["my_color"] == "white"
    costly = costly_patterns(report)
    tt = [m for m in report["moves"] if m.get("mistake") and m.get("gap") == "time_trouble"]
    seen_reply = [c for c in costly if c["reply_recorded"]]
    rejected = [c for c in costly if c["gap"] == "wrongly_rejected"]
    unrecorded_threat = [c for c in costly if c["gap"] == "opponent_idea"]
    not_found = [c for c in costly if c["gap"] in ("missed_candidate", "no_thoughts")]
    k, n = s.get("q4_best_considered", 0), s.get("q4_positions", 0)

    def names(cs):
        return ", ".join(c["label"] for c in cs[:2])

    weight = {
        "test_reply": sum(c["loss"] for c in seen_reply),
        "keep_best": sum(c["loss"] for c in rejected if not c["reply_recorded"]),
        "their_idea": sum(c["loss"] for c in unrecorded_threat),
        "widen": sum(c["loss"] for c in not_found),
        "clock": sum(m["wp_loss"] for m in tt),
    }
    key = max(weight, key=weight.get) if any(weight.values()) else "keep"
    if not s.get("moves_with_thoughts"):
        key = "record"  # nothing recorded: there is no evidence about the thinking, either way
    if key == "test_reply":
        cs = seen_reply
        cs = sorted(cs, key=lambda c: c["ply"])
        share = ("In your costly decision" if len(costly) == 1 else "In both of your costly decisions" if len(cs) == len(costly) == 2
                 else f"In all {len(costly)} of your costly decisions" if len(cs) == len(costly)
                 else f"In {len(cs)} of your {len(costly)} costly decisions")
        why = (f"{share} ({names(cs)}), their best reply "
               f"({', '.join(c['reply'] for c in cs[:2])}) was already in your recorded thinking, and the move you chose didn't answer it.")
        if rejected:
            why += (" The engine's best move was among your candidates there too ("
                    + ", ".join(f"{c['rejected'][0]} instead of {c['label']}" for c in sorted(rejected, key=lambda c: c["ply"])[:2]) + ").")
        why += " The opportunity isn't finding more candidates: it's checking each one against their reply."
    elif key == "keep_best":
        cs = [c for c in rejected if not c["reply_recorded"]]
        why = (f"At {names(cs)} the engine's best move ({', '.join(cs[0]['rejected'][:2])}) was among your candidates, "
               f"and you played something else that cost {_pct(cs[0]['loss'])} of your win chance.")
    elif key == "their_idea":
        cs = unrecorded_threat
        why = (f"At {names(cs)} their best reply ({cs[0]['reply'] or cs[0]['threat']}) isn't in your recorded thinking, and it is "
               f"what the move allowed. You may have seen it without drawing or saying it, but the move didn't answer it.")
    elif key == "widen":
        cs = not_found
        why = ("At " + " and ".join(f"{c['label']} (best {c['best']})" for c in cs[:2])
               + f", the engine's best move isn't among the moves you drew or said. The costliest cost {_pct(cs[0]['loss'])} of your win chance.")
    elif key == "clock":
        cs = []
        why = (f"{len(tt)} costly move{'s' * (len(tt) != 1)} came in time trouble, from move {min(m['move_number'] for m in tt)} on.")
    elif key == "record":
        cs = costly
        why = ("No arrows or words were recorded this game, so the review can only say which moves were costly, not why. "
               + (f"The costliest was {costly[0]['label']} ({_pct(costly[0]['wp_before'])} → {_pct(costly[0]['wp_after'])})." if costly else ""))
    else:
        cs = []
        why = "No costly decisions with time to think this game."
    title, habit = FOCUS[key]

    well = []
    if n and k / n >= 0.6:
        well.append({"text": f"The engine's best move was among your candidates in {ratio(k, n)} positions where you drew some.",
                     "small": n < SMALL})
    real = s.get("q3_real_threats", 0)
    if real:
        held = _held(report)
        if held and held / real >= 0.75:
            well.append({"text": f"Your moves held against {ratio(held, real)} real threats.", "small": real < SMALL})
    from . import timing

    crit_held = [dict(p, label=timing.label(p, white)) for p in timing.positions(analysis, report)
                 if p["critical"] and not p["obvious"] and (p["wp_loss"] or 0) < 0.02] if analysis.get("moves") else []
    if crit_held:
        p = crit_held[0]
        well.append({"text": f"You found the only move at {p['label']} when nothing else held.", "small": False, "ply": p["ply"]})

    # Recognition and resolution, kept apart.
    rec_k, rec_n = s.get("q3_spotted", 0), real
    resolved = _held(report)
    stats = [
        {"label": "Best move among your candidates", "value": ratio(k, n), "small": 0 < n < SMALL,
         "tip": "Positions where you drew at least one candidate, and the engine's best move was one of them."},
        {"label": "Their threats in your recorded thinking", "value": ratio(rec_k, rec_n), "small": 0 < rec_n < SMALL,
         "tip": "Real threats (worth 10%+ of win chance) that you drew or said. Not drawing one doesn't prove you missed it."},
        {"label": "Your move held against their threat", "value": ratio(resolved, rec_n), "small": 0 < rec_n < SMALL,
         "tip": "Whether the move you played dealt with the threat, whatever was recorded."},
        {"label": "Costly decisions with time to think", "value": str(len(costly)),
         "tip": "Moves that cost 5%+ of your estimated win chance, outside time trouble."},
    ]

    decisions = [{"ply": c["ply"], "label": c["label"], "kind": "costly",
                  "text": f"{_pct(c['wp_before'])} → {_pct(c['wp_after'])}"} for c in costly[:3]]
    for p in crit_held[:1]:
        decisions.append({"ply": p["ply"], "label": p["label"], "kind": "good", "text": "only move found"})
    decisions.sort(key=lambda d: d["ply"])
    start = max(cs, key=lambda c: c["loss"])["ply"] if cs else None  # the page opens on the costliest of them
    return {"focus": {"key": key, "title": title, "why": why, "habit": habit, "plies": [c["ply"] for c in cs], "start": start},
            "well": well[:2], "stats": stats, "decisions": decisions}


THIRD = [(r"\byou'd\b", "he'd"), (r"\byou're\b", "he's"), (r"\byou've\b", "he's"), (r"\bYou\b", "He"), (r"\byou\b", "he"),
         (r"\byours\b", "his"), (r"\bYour\b", "His"), (r"\byour\b", "his"), (r"\bhe were\b", "he was"), (r"\bhe have\b", "he has"),
         (r"\bhe are\b", "he is"), (r"\bhe don't\b", "he doesn't"), (r"\bHe were\b", "He was"), (r"\bHe have\b", "He has")]


def third(text: str) -> str:
    """The review's wording about someone else's game (the public sample): "you drew" -> "he drew", "your" -> "his"."""
    import re

    for a, b in THIRD:
        text = re.sub(a, b, text)
    return text

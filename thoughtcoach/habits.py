"""Is your focus working? Each game's story ends with a focus for the next game; this checks it.

A game's focus is the one the game before it gave you. For each focus there is one rate it should
move (threats spotted for "what does my opponent want?", good moves dropped for "trust your
candidates"...). The games since you first got that focus are compared with the games before it,
as the chance you've really improved (bayes.py: Beta posteriors with a sceptical prior, so one good
game doesn't count as a habit).

Focuses about the voice and mood (play it out, notice don't judge, look once more) have no simple
rate yet and aren't measured.
"""

from __future__ import annotations

from . import bayes, timing

MIN_GAMES = 2  # games since the focus before saying it works (or doesn't): one game can be luck


def _q(key_found: str, key_total: str):
    return lambda g: (g["report"]["summary"].get(key_found, 0), g["report"]["summary"].get(key_total, 0))


def _dropped(g):
    s = g["report"]["summary"]
    return s.get("by_gap", {}).get("wrongly_rejected", {}).get("count", 0), s.get("q4_best_considered", 0)


def _sharp(g):
    t = timing.game(g["analysis"], g["report"])
    return (t["costly_critical"], t["critical"]) if t else (0, 0)


def _clock(g):
    tt = g["report"]["thresholds"].get("time_trouble", 30)
    moves = [m for m in g["report"]["moves"] if not m.get("skipped")]
    return sum(1 for m in moves if m.get("clock_before") is not None and m["clock_before"] < tt), len(moves)


def _ahead(g):
    from .ahead import rows

    rs = [r for r in rows(g["analysis"], g["report"]) if r["zone"] == "ahead"]
    return sum(r["costly"] for r in rs), len(rs)


def _shown(g):
    s = g["report"]["summary"]
    return s.get("moves_with_thoughts", 0), s.get("moves_reviewed", 0)


# focus key: (what is counted, how to count it in one game, True when more is better)
MEASURE = {
    "q3": ("threats spotted before they happened", _q("q3_spotted", "q3_real_threats"), True),
    "candidates": ("positions with the best move among yours", _q("q4_best_considered", "q4_positions"), True),
    "trust": ("good moves you drew and then dropped", _dropped, False),
    "q1": ("weaknesses you pointed at", _q("q1_found", "q1_weaknesses"), True),
    "q2": ("worst pieces you named", _q("q2_found", "q2_positions"), True),
    "sharp": ("critical positions that went wrong", _sharp, False),
    "time": ("moves played in time trouble", _clock, False),
    "input": ("moves with an arrow or words", _shown, True),
    "ahead": ("moves that went wrong while you were winning", _ahead, False),
}


def _rate(k: int, n: int) -> str:
    return f"{k} of {n} ({k / n:.0%})" if n else "none"


def compute(games: list[dict], stories: dict[str, dict]) -> list[dict]:
    """One entry per focus you've had a game with, the most recent first. `games` oldest first
    (profile.load), `stories` the saved story summaries (with their focus)."""
    active = [None] + [((stories.get(g["id"]) or {}).get("focus") or {}) for g in games[:-1]]
    out = []
    seen = []
    for i, f in enumerate(active):
        if f and f.get("key") in MEASURE and f["key"] not in seen:
            seen.append(f["key"])
    for key in seen:
        label, count, up = MEASURE[key]
        first = next(i for i, f in enumerate(active) if f and f.get("key") == key)
        title = active[first]["title"]
        since = [g for g, f in zip(games, active) if f and f.get("key") == key]
        before = games[:first]
        kb = nb = ka = na = 0
        for g in before:
            k, n = count(g)
            kb, nb = kb + k, nb + n
        for g in since:
            k, n = count(g)
            ka, na = ka + k, na + n
        entry = {"key": key, "title": title, "label": label, "before": (kb, nb), "since": (ka, na),
                 "games": len(since), "last": max(i for i, f in enumerate(active) if f and f.get("key") == key),
                 "p": None}
        if na and nb:
            prior = bayes.sceptic((kb + ka) / (nb + na))
            # the chance the rate really moved the right way
            entry["p"] = (bayes.prob_greater_prior(ka, na, kb, nb, prior) if up
                          else bayes.prob_greater_prior(kb, nb, ka, na, prior))
        entry["text"] = _text(entry)
        out.append(entry)
    return sorted(out, key=lambda e: -e["last"])


def _text(e: dict) -> str:
    (kb, nb), (ka, na) = e["before"], e["since"]
    games = f"{e['games']} game{'s' * (e['games'] != 1)}"
    head = f"{e['label'].capitalize()}: {_rate(kb, nb)} before, {_rate(ka, na)} in the {games} since."
    if not na:
        return f"{e['label'].capitalize()}: no chance to use it yet in the {games} since."
    if e["p"] is None:
        return head
    if e["games"] < MIN_GAMES:
        return f"{head} Too early to tell after one game."
    if e["p"] >= bayes.LIKELY:
        return f"{head} Working: {bayes.verdict(e['p'])} ({bayes.odds(e['p'])} that you've improved)."
    if e["p"] <= 1 - bayes.LIKELY:
        return f"{head} Not showing yet: {bayes.odds(1 - e['p'])} that it has gone the wrong way."
    return f"{head} Too early to tell: {bayes.odds(e['p'])} that it's helping, so far."


def this_game(key: str | None, game: dict, earlier: list[dict]) -> str | None:
    """'This game: 3 of 4 threats spotted before they happened; before: 17 of 125 (14%).' for the
    focus the previous game gave you."""
    if key not in MEASURE:
        return None
    label, count, _ = MEASURE[key]
    k, n = count(game)
    if not n:
        return None
    kb = nb = 0
    for g in earlier:
        a, b = count(g)
        kb, nb = kb + a, nb + b
    return f"This game: {label}, {_rate(k, n)}" + (f"; in your games before: {_rate(kb, nb)}." if nb else ".")

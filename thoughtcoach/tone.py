"""The tone of what you said while you played, set against how you played.

Each of your moves gets a mood from what you said while its position (or their move before it)
was on the board: upbeat, worried, neutral (you talked, but nothing with a tone) or quiet (nothing
said). The tone comes from plain word lists (no LLM, so it is easy to check and to argue with);
"not bad" and "no problem" count as upbeat, "not good" as worried.

Then, for your moves outside time trouble:

  by mood        how often a move went wrong, and where your average move took your win chance
  aggression     when the engine's best move was forcing (a capture or a check): how often you
                 played a forcing move yourself, worried against not worried
  calibration    upbeat while the engine had you below ROSY_BELOW, worried while it had you above
                 GLOOMY_ABOVE

Each comparison is a chance that the difference is real (bayes.py: Beta posteriors on the
two rates): stated as a finding from LIKELY on, otherwise kept as a note with its chance. Nothing here decides what a good move is: that is the engine.
"""

from __future__ import annotations

import re
import statistics

from .bayes import LIKELY, chance, prob_greater_prior, sceptic

UPBEAT = ["good", "great", "nice", "i like", "love", "winning", "better for me", "i'm better",
          "comfortable", "solid", "fine", "should be good", "should be fine", "easy", "crushing", "happy", "perfect", "beautiful", "got him", "got it", "free pawn", "up a pawn", "in trouble",
          "good position", "no problem", "not bad", "not worried", "safe", "excellent", "let's go", "jackpot",
          "he's lost", "she's lost", "they're lost", "much better", "promising", "confident"]
WORRIED = ["worried", "worry", "scary", "scared", "dangerous", "danger", "careful", "nervous", "trouble", "problem",
           "problems", "hate", "ugly", "awkward", "uncomfortable", "worse", "losing", "lost", "bad", "terrible",
           "don't like", "annoying", "pressure", "sketchy", "risky", "afraid", "oh no", "uh oh", "ugh", "damn",
           "i'm dead", "busted", "blunder", "oops", "panic", "concerned", "yikes",
           "not good", "i'm in trouble", "under attack", "attack on me", "tough", "difficult"]
NEGATIONS = {"not", "don't", "dont", "isn't", "wasn't", "doesn't", "no", "never", "nothing", "isnt"}
# Chess words that describe the position, not a mood, are left out on purpose: "weak" (a weak
# pawn), "strong", "like this" (a move), "hanging".
# Said about the other side, these are good news for you.
THEM = re.compile(r"\b(he|she|they|his|her|their|opponent|white|black)\b")

MIN_MOVES = 8   # moves in each group before comparing them (one game)
FINDING_MIN = 5  # and never a finding from fewer moves than this in a group
MIN_MOVES_ALL = 20  # ... across games
GAP = 0.08      # costly-move rates this far apart count as a difference
ROSY_BELOW, GLOOMY_ABOVE = 0.40, 0.65


def _words(text: str) -> str:
    return " " + re.sub(r"[^a-z' ]+", " ", text.lower().replace("’", "'")) + " "


def score(text: str) -> tuple[int, list[str]]:
    """+1 for each upbeat phrase, -1 for each worried one ("not good" is worried, "not bad" upbeat,
    "he's in trouble" upbeat), with the phrases found."""
    low = _words(text)
    total, found, used = 0, [], set()
    for phrase, sign in sorted([(p, 1) for p in UPBEAT] + [(p, -1) for p in WORRIED], key=lambda x: -len(x[0])):
        start = 0
        while True:
            at = low.find(f" {phrase} ", start)
            if at < 0:
                break
            span = set(range(at, at + len(phrase) + 2))
            start = at + 1
            if span & used:  # inside a longer phrase already counted ("not bad" holds "bad")
                continue
            used |= span
            before = low[:at].split()[-3:]
            s = sign
            if set(before) & NEGATIONS and not phrase.startswith(("not ", "no ")):
                s = -s  # "isn't good", "no danger"
            if sign < 0 and THEM.search(" ".join(before + low[at:].split()[:1])) and phrase in ("trouble", "lost", "losing", "in trouble"):
                s = 1  # "he's in trouble", "she's lost"
            total += s
            found.append(phrase)
    return (1 if total > 0 else -1 if total < 0 else 0), found


def _forcing(san: str | None) -> bool:
    return bool(san) and ("x" in san or "+" in san or "#" in san)


def analyse(game, me, report: dict, recording: dict | None, transcript: dict | None, utterances) -> dict | None:
    """{"moves": [one row per reviewed move of yours]} or None without a voice recording."""
    if not (recording and transcript and utterances):
        return None
    from .insights import Game
    from .psych import Voice

    g = Game(game, me, report)
    v = Voice(g, recording, transcript, utterances)
    said: dict[int, list[dict]] = {}
    for c in v.clauses:
        pos = v.position_at(c["t"])
        if pos is not None:
            said.setdefault(pos, []).append(c)
    tt = report["thresholds"].get("time_trouble", 30)
    rows = []
    for ply, m in sorted(g.mine.items()):
        clauses = said.get(ply - 2, []) + said.get(ply - 1, [])  # their turn before it, then yours
        scored = [(score(c["text"]), c["text"].strip()) for c in clauses]
        total = sum(s for (s, _), _ in scored)
        mood = "quiet" if not clauses else "upbeat" if total > 0 else "worried" if total < 0 else "neutral"
        # The quote: a clear mood, said plainly ("I have a good position" over "not back rank problems").
        plain = lambda text: not (set(_words(text).split()) & NEGATIONS)  # noqa: E731
        strongest = max(scored, key=lambda x: (abs(x[0][0]), plain(x[1]), len(x[1])), default=None)
        quote = strongest[1].rstrip(",.") if strongest and strongest[0][0] else None
        rows.append({"ply": ply, "move_number": m["move_number"], "move_san": m["move_san"], "mood": mood,
                     "quote": quote, "phrases": sorted({p for (_, ps), _ in scored for p in ps}),
                     "wp_before": m["wp_before"], "wp_after": m["wp_after"], "wp_loss": m["wp_loss"],
                     "costly": bool(m["mistake"]), "best": m.get("best_move_san"),
                     "best_forcing": _forcing(m.get("best_move_san")), "played_forcing": _forcing(m["move_san"]),
                     "tt": m.get("clock_before") is not None and m["clock_before"] < tt})
    return {"moves": rows}


def _group(rows: list[dict]) -> dict:
    n = len(rows)
    return {"n": n, "costly": sum(r["costly"] for r in rows),
            "rate": sum(r["costly"] for r in rows) / n if n else 0.0,
            "before": statistics.mean(r["wp_before"] for r in rows) if n else None,
            "after": statistics.mean(r["wp_after"] for r in rows) if n else None}


def stats(rows: list[dict], min_moves: int = MIN_MOVES) -> dict:
    """Tone against play for these moves (one game, or many). Findings only where there is enough."""
    rows = [r for r in rows if not r["tt"]]
    by = {k: _group([r for r in rows if r["mood"] == k]) for k in ("upbeat", "worried", "neutral", "quiet")}
    talked = [r for r in rows if r["mood"] != "quiet"]
    chances = [r for r in rows if r["best_forcing"]]
    worried_ch = [r for r in chances if r["mood"] == "worried"]
    other_ch = [r for r in chances if r["mood"] != "worried"]
    out = {"by": by, "moves": len(rows), "talked": len(talked),
           "aggression": {"worried": (sum(r["played_forcing"] for r in worried_ch), len(worried_ch)),
                          "other": (sum(r["played_forcing"] for r in other_ch), len(other_ch))},
           "rosy": [r for r in rows if r["mood"] == "upbeat" and r["wp_before"] < ROSY_BELOW],
           "gloomy": [r for r in rows if r["mood"] == "worried" and r["wp_before"] > GLOOMY_ABOVE],
           "findings": []}
    up, wo = by["upbeat"], by["worried"]
    out["notes"] = []  # comparisons that aren't sure yet, with how sure they are
    # A sceptical prior: both groups start out like FINDING_MIN typical moves of yours.
    prior = sceptic(sum(r["costly"] for r in rows) / len(rows) if rows else 0.25)
    if up["n"] >= 3 and wo["n"] >= 3:
        p = prob_greater_prior(wo["costly"], wo["n"], up["costly"], up["n"], prior)  # worried moves go wrong more
        out["p_worried_worse"] = p
        counts = f"{wo['n']} worried, {up['n']} upbeat moves"
        if max(p, 1 - p) >= LIKELY and min(up["n"], wo["n"]) >= FINDING_MIN:
            better, worse = ("upbeat", "worried") if p >= 0.5 else ("worried", "upbeat")
            out["findings"].append(
                f"You play better when you sound {better}: {by[worse]['rate']:.0%} of your {worse} moves went wrong, "
                f"against {by[better]['rate']:.0%} of your {better} ones. {chance(max(p, 1 - p))} chance that's real, "
                f"not luck ({counts}).")
        else:
            out["notes"].append(
                f"No clear link yet between your mood and your play: {wo['rate']:.0%} of worried moves went wrong, "
                f"{up['rate']:.0%} of upbeat ones. The chance worried moves really go wrong more often is {chance(p)} "
                f"({counts}).")
    (wf, wn), (of, on) = out["aggression"]["worried"], out["aggression"]["other"]
    if wn >= 3 and on >= 3:
        rate = (of + wf) / (on + wn)
        p = prob_greater_prior(of, on, wf, wn, sceptic(rate))  # the forcing move more often when not worried
        out["p_worry_holds_back"] = p
        if p >= LIKELY and wn >= FINDING_MIN:
            out["findings"].append(
                f"Worry holds back your attacking moves: when the best move was a capture or a check you played one "
                f"{wf / wn:.0%} of the time while worried, {of / on:.0%} otherwise. {chance(p)} chance that's real "
                f"({wn} and {on} positions).")
        elif p <= 1 - LIKELY and wn >= FINDING_MIN:
            out["findings"].append(
                f"Worry doesn't stop you attacking: with a capture or check as the best move you played one "
                f"{wf / wn:.0%} of the time while worried, {of / on:.0%} otherwise ({chance(1 - p)} sure).")
        else:
            out["notes"].append(
                f"Does worry hold back your attacking moves? {chance(p)} chance, from {wn} worried positions where the "
                f"best move was a capture or a check: too few to say.")
    return out

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

from .bayes import LIKELY, odds, prob_greater_prior, sceptic, verdict

UPBEAT = ["good", "great", "nice", "i like", "love", "winning", "better for me", "i'm better",
          "comfortable", "solid", "fine", "should be good", "should be fine", "easy", "crushing", "happy", "perfect", "beautiful", "got him", "got it", "free pawn", "up a pawn", "in trouble",
          "good position", "decent position", "no problem", "not bad", "not worried", "safe", "excellent", "jackpot",
          "he's lost", "she's lost", "they're lost", "much better", "promising", "confident"]
WORRIED = ["worried", "worry", "scary", "scared", "dangerous", "danger", "careful", "nervous", "trouble", "problem",
           "problems", "hate", "ugly", "awkward", "uncomfortable", "worse", "losing", "lost", "bad", "terrible",
           "don't like", "annoying", "pressure", "sketchy", "risky", "afraid", "oh no", "uh oh", "ugh", "damn",
           "i'm dead", "busted", "blunder", "oops", "panic", "concerned", "yikes",
           "not good", "i'm in trouble", "under attack", "attack on me", "tough", "difficult",
           "not what i expected", "what was that", "what the fuck", "what the hell", "so confused", "unfortunate",
           "too late", "collapse", "collapsed"]
NEGATIONS = {"not", "don't", "dont", "isn't", "wasn't", "doesn't", "no", "never", "nothing", "isnt"}
# Chess words that describe the position, not a mood, are left out on purpose: "weak" (a weak
# pawn), "strong", "like this" (a move), "hanging".
# Said about the other side, these are good news for you.
THEM = re.compile(r"\b(he|she|they|his|her|their|opponent|white|black)\b")

# The local model (Ollama), when the review run has it: the word lists decide WHETHER a sentence
# carries a mood (plain move talk stays neutral), the model reads the whole sentence (and the one
# before) to decide WHICH mood, so negation, comparison and context come out right ("the king is
# more exposed than mine", "I don't think I'm lost"). It also decides whether a defeat phrase
# really means giving up. Set by the review run; None means word lists only.
LLM: dict | None = None  # {"model": ..., "url": ..., "gpu": bool}
LLM_INSTRUCTIONS = """You read what a chess player said out loud during their own game. Judge the SENTENCE (the earlier sentence is only context).
Answer with JSON only: {"mood": "upbeat" | "worried" | "neutral", "gave_up": true | false}
- upbeat: the player feels good about their own game (confident, pleased, relieved), or says the opponent is in trouble.
- worried: the player feels bad or anxious about their own game or position, or says the opponent is doing well.
- neutral: calculating, naming moves or squares, describing, no feeling about how the game is going.
- gave_up: the player says their own game is lost, over or hopeless. Denying it ("I'm not lost", "I don't think I'm lost") is NOT giving up.
Examples:
"I have a good position now" -> {"mood": "upbeat", "gave_up": false}
"I don't think I'm lost" -> {"mood": "upbeat", "gave_up": false}
"the king is a little more exposed than mine" -> {"mood": "upbeat", "gave_up": false}
"this is getting scary, my king is open" -> {"mood": "worried", "gave_up": false}
"I'm just lost here" -> {"mood": "worried", "gave_up": true}
"not bad" -> {"mood": "upbeat", "gave_up": false}
"he's in big trouble now" -> {"mood": "upbeat", "gave_up": false}"""
_read_cache: dict[tuple[str, str], dict | None] = {}
# No mood word, but a mood that needs the whole sentence to read: whose king is exposed, better than whose.
CONTEXT = re.compile(r"\b(exposed|than (mine|his|hers|theirs|yours|me|him|her|them)|better for (me|him|her|them)|"
                     r"worse for (me|him|her|them))\b")


def llm_read(prev: str, text: str) -> dict | None:
    """The model's reading of one sentence ({"mood", "gave_up"}), or None without a model."""
    if not LLM:
        return None
    key = (prev, text)
    if key in _read_cache:
        return _read_cache[key]
    import json
    import urllib.request

    body = {"model": LLM["model"], "format": "json", "stream": False, "keep_alive": "30m",
            "options": {"temperature": 0, **({} if LLM.get("gpu") else {"num_gpu": 0})},
            "prompt": f"{LLM_INSTRUCTIONS}\n\nEarlier sentence: \"{prev}\"\nSENTENCE: \"{text}\"\nJSON:"}
    out = None
    try:
        req = urllib.request.Request(f"{LLM['url']}/api/generate", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            a = json.loads(json.loads(r.read())["response"])
        if a.get("mood") in ("upbeat", "worried", "neutral"):
            out = {"mood": a["mood"], "gave_up": bool(a.get("gave_up"))}
    except Exception:  # noqa: BLE001 - no model, or an odd answer: the word lists decide
        out = None
    _read_cache[key] = out
    return out


def mood(text: str, prev: str = "") -> tuple[int, list[str]]:
    """Like score(), but when a mood word is there and the model is available, the model reads the
    whole sentence to decide which way it goes."""
    sign, found = score(text)
    if found or CONTEXT.search(text.lower()):
        a = llm_read(prev, text)
        if a:
            sign = {"upbeat": 1, "worried": -1, "neutral": 0}[a["mood"]]
    return sign, found


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
    for i, c in enumerate(v.clauses):
        pos = v.position_at(c["t"])
        if pos is not None:
            said.setdefault(pos, []).append({**c, "prev": v.clauses[i - 1]["text"] if i else ""})
    tt = report["thresholds"].get("time_trouble", 30)
    rows = []
    for ply, m in sorted(g.mine.items()):
        clauses = said.get(ply - 2, []) + said.get(ply - 1, [])  # their turn before it, then yours
        scored = [(mood(c["text"], c.get("prev", "")), c["text"].strip()) for c in clauses]
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
                f"against {by[better]['rate']:.0%} of your {better} ones: {verdict(max(p, 1 - p))} "
                f"({odds(max(p, 1 - p))} it's not luck; {counts}).")
        else:
            out["notes"].append(
                f"No clear link yet between your mood and your play: {wo['rate']:.0%} of worried moves went wrong, "
                f"{up['rate']:.0%} of upbeat ones. The odds that worried moves really go wrong more often: {odds(p)} "
                f"({counts}).")
    (wf, wn), (of, on) = out["aggression"]["worried"], out["aggression"]["other"]
    if wn >= 3 and on >= 3:
        rate = (of + wf) / (on + wn)
        p = prob_greater_prior(of, on, wf, wn, sceptic(rate))  # the forcing move more often when not worried
        out["p_worry_holds_back"] = p
        if p >= LIKELY and wn >= FINDING_MIN:
            out["findings"].append(
                f"Worry holds back your attacking moves: when the best move was a capture or a check you played one "
                f"{wf / wn:.0%} of the time while worried, {of / on:.0%} otherwise: {verdict(p)} ({odds(p)}; "
                f"{wn} and {on} positions).")
        elif p <= 1 - LIKELY and wn >= FINDING_MIN:
            out["findings"].append(
                f"Worry doesn't stop you attacking: with a capture or check as the best move you played one "
                f"{wf / wn:.0%} of the time while worried, {of / on:.0%} otherwise ({verdict(1 - p)}).")
        else:
            out["notes"].append(
                f"Does worry hold back your attacking moves? {odds(p).capitalize()} so far, from {wn} worried positions where the "
                f"best move was a capture or a check: too few to say.")
    return out

"""The mental game: what your voice and arrows say about how you were playing, set against the
engine. Four kinds of insight (same shape as insights.py, so they flow into the review and story):

  gave_up   "I'm lost", "busted", "white's winning"... said while the engine still gave you a real
            chance (GAVE_UP_WP or more). With what came next: did the game come back to you?
  tide      the opponent's move that handed the game back (your win chance up TIDE or more from
            behind), whether anything you said showed you noticed, and whether you took it.
  instinct  System 1 against System 2 (fast, intuitive against slow, deliberate): where your first
            arrow was already a good move, how often you talked yourself out of it; and how often
            more thinking rescued a poor first idea.
  inner     the "Inner Game" idea that a judging voice ("stupid", "why did I...") gets in the way
            and plain noticing ("this weakens my knight") doesn't: your judging remarks, and how
            your next FOLLOW moves went against your game average.

Nothing here decides what a good move is: that is always the engine's verdict.
"""

from __future__ import annotations

import re
import statistics

from .align import match_positions
from .fmt import pct, avg_went

GAVE_UP_WP = 0.25
TIDE = 0.15
FOLLOW = 3
NOTICE_S = 30

DEFEAT = re.compile(r"\b(i'?m lost|i am lost|lost here|busted|it'?s over|game over|i'?m done|resign\w*|stop the game|too late|a loss here|collaps\w*|i have nothing( else)?|nothing left|throwing (this|it|the game)|hopeless|"
                    r"(white|black|he|she|they)('s| is| are)( probably| just| clearly)? winning|"
                    r"no moves|not finding a single move|completely losing|this (just )?loses|just losing|i'?m losing)\b")
NOTICED = re.compile(r"\b(oh wait|wait|nice|interesting|what|chance|free|blunder|now i|good for me|"
                     r"i'?m (back|fine|better|winning)|he missed|she missed|they missed|lucky|let'?s go)\b")
JUDGING = re.compile(r"\b(stupid|idiot|dumb|terrible|awful|horrible|why did i|so bad|what am i doing|"
                     r"i suck|trash|embarrassing|unbelievable|come on|ridiculous|pathetic)\b")


class Voice:
    """What was said, and which position was on the board when it was said."""

    def __init__(self, g, recording: dict, transcript: dict, utterances):
        events = sorted(recording["events"], key=lambda e: e["t"])
        _, self.on_screen = match_positions(events, [b.board_fen() for b in g.boards])
        self.times = [e["t"] for e in events]
        self.landed: dict[int, int] = {}
        for t, pos in zip(self.times, self.on_screen):
            if pos is not None:
                self.landed.setdefault(pos, t)
        self.clauses = utterances(transcript)

    def position_at(self, t: int) -> int | None:
        index = None
        for i, et in enumerate(self.times):
            if et > t:
                break
            index = i
        return None if index is None else self.on_screen[index]

    def said_between(self, t0: int, t1: int) -> list[dict]:
        return [c for c in self.clauses if t0 <= c["t"] <= t1]


def _clean(text: str) -> str:
    return text.strip().rstrip(",.")


def _next_moves(g, position: int) -> list[dict]:
    return [g.mine[p] for p in sorted(g.mine) if p > position][:FOLLOW]


def _pace(g) -> float:
    times = [m.get("own_time_s") or m.get("time_spent_pgn") or 0 for m in g.mine.values()]
    return statistics.mean(times) if times else 0


NOT = {"not", "don't", "dont", "doesn't", "doesnt", "didn't", "won't", "aren't", "never", "no", "isn't", "isnt", "wasn't", "doubt", "hardly"}


def defeated(text: str) -> bool:
    """Says the game is lost, and doesn't deny it: "I'm lost" yes, "I don't think I'm lost" no."""
    low = text.lower().replace("’", "'")
    for m in DEFEAT.finditer(low):
        before = set(re.findall(r"[a-z']+", low[:m.start()])[-4:])
        if not before & NOT:
            return True
    return False


def gave_up(g, v: Voice) -> list[dict]:
    from . import tone

    for i, c in enumerate(v.clauses):
        if not DEFEAT.search(c["text"].lower().replace("’", "'")):
            continue
        # A defeat phrase: the model reads the whole sentence when it can ("I don't think I'm lost"
        # is not giving up), the word check otherwise.
        read = tone.llm_read(v.clauses[i - 1]["text"] if i else "", c["text"])
        if not (read["gave_up"] if read else defeated(c["text"])):
            continue
        pos = v.position_at(c["t"])
        now = None if pos is None else g.wp(pos)
        if now is None or now < GAVE_UP_WP:
            continue
        later = [(p, m["wp_before"]) for p, m in sorted(g.mine.items()) if p > pos]
        peak = max(later, key=lambda x: x[1], default=None)
        nxt = _next_moves(g, pos)
        lost = sum(m["wp_loss"] for m in nxt)
        secs = statistics.mean([m.get("own_time_s") or m.get("time_spent_pgn") or 0 for m in nxt]) if nxt else 0
        body = (f"Around move {g.move_number(pos)} you said '{_clean(c['text'])}'. But the engine still gave you a "
                f"{pct(now)} chance.")
        if peak and peak[1] >= now + 0.1:
            body += f" Later, before {g.label(peak[0])}, you were back up to {pct(peak[1])}: the game wasn't over."
        if nxt:
            body += (f" Over your next {len(nxt)} moves your win chance went from {pct(nxt[0]['wp_before'])} to "
                     f"{pct(nxt[-1]['wp_after'])}.")
        return [{"kind": "gave_up", "ply": pos, "weight": 0.6 + now + (0.2 if peak and peak[1] >= now + 0.1 else 0),
                 "head": "You called it lost too early", "body": body, "wp": round(now, 3)}]
    return []


def tide(g, v: Voice | None) -> list[dict]:
    mine = sorted(g.mine.items())
    best = None
    for (p0, a), (p1, b) in zip(mine, mine[1:]):
        if a.get("wp_after") is None or b.get("wp_before") is None or p1 != p0 + 2:
            continue
        swing = b["wp_before"] - a["wp_after"]
        if swing >= TIDE and a["wp_after"] <= 0.45 and (best is None or swing > best[2]):
            best = (p1 - 1, p1, swing, a["wp_after"], b)
    if not best:
        return []
    their_ply, my_ply, swing, before, nxt = best
    heard = []
    if v is not None and their_ply in v.landed:
        t0 = v.landed[their_ply]
        heard = [_clean(c["text"]) for c in v.said_between(t0, t0 + NOTICE_S * 1000) if NOTICED.search(c["text"].lower())]
    took = nxt["wp_loss"] < 0.05
    body = (f"{g.label(their_ply)} was a mistake by your opponent: your win chance jumped from {pct(before)} to "
            f"{pct(nxt['wp_before'])}. "
            + (f"You noticed: '{heard[0]}'. " if heard else "Nothing you said showed you'd seen it. ")
            + ("And you made the most of it." if took else
               f"But then {g.label(my_ply)} gave it back ({pct(nxt['wp_before'])} → {pct(nxt['wp_after'])})."))
    return [{"kind": "tide", "ply": their_ply, "weight": 0.5 + swing + (0.2 if not took else 0),
             "head": "They gave you a chance", "body": body, "noticed": bool(heard), "took": took}]


def instinct(g) -> list[dict]:
    talked_out, rescued = [], []
    for p, m in sorted(g.mine.items()):
        cands = [d for d in m.get("drawn_candidates", []) if d.get("legal") and d.get("first_drawn_s") is not None]
        if len(cands) < 2:
            continue
        first = min(cands, key=lambda d: d["first_drawn_s"])
        played = next((d for d in cands if d.get("played")), None)
        good_first = bool(first.get("engine_candidate"))
        if good_first and not first.get("played") and m["wp_loss"] >= 0.05:
            talked_out.append((p, m, first))
        elif not good_first and played and played.get("engine_candidate"):
            rescued.append((p, m, first))
    if not talked_out and not rescued:
        return []
    lost = sum(m["wp_loss"] for _, m, _ in talked_out)
    parts = [f"at {g.label(p)} your first idea {f['move_san']} was good ({pct(m['wp_before'])} → {pct(m['wp_after'])} "
             f"with the move you played)" for p, m, f in talked_out[:2]]
    body = "The first move you drew, against the move you played. "
    if talked_out:
        n = len(talked_out)
        body += (f"{'Once' if n == 1 else f'{n} times'} your first idea was already a good move, and you went with "
                 f"something else: {'; '.join(parts)}. ")
    if rescued:
        n = len(rescued)
        body += f"{'Once' if n == 1 else f'{n} times'} your first idea was poor, and thinking it through found a good move."
    head = "Your instinct was right" if len(talked_out) > len(rescued) else "Thinking it through paid off"
    return [{"kind": "instinct", "ply": (talked_out or rescued)[0][0], "weight": 0.45 + lost + 0.05 * len(talked_out),
             "head": head, "body": body.strip(), "talked_out": len(talked_out), "rescued": len(rescued),
             "loss": round(lost, 3)}]


def judging(text: str) -> bool:
    """A verdict on yourself, not denied: "so stupid" yes, "it's not horrible" no."""
    low = text.lower().replace("’", "'")
    for m in JUDGING.finditer(low):
        before = set(re.findall(r"[a-z']+", low[:m.start()])[-3:])
        if not before & NOT:
            return True
    return False


def inner(g, v: Voice) -> list[dict]:
    remarks = []
    for c in v.clauses:
        if judging(c["text"]):
            pos = v.position_at(c["t"])
            if pos is not None:
                remarks.append((pos, _clean(c["text"])))
    if not remarks:
        return []
    per_move = statistics.mean(m["wp_loss"] for m in g.mine.values()) if g.mine else 0
    after = []
    for pos, _ in remarks:
        after += _next_moves(g, pos)
    after_rate = statistics.mean(m["wp_loss"] for m in after) if after else 0
    pos, text = remarks[0]
    nxt = _next_moves(g, pos)
    more = f" (and {len(remarks) - 1} more time{'s' * (len(remarks) != 2)})" if len(remarks) > 1 else ""
    body = (f"Around move {g.move_number(pos)} you said '{text}'{more}. Being harsh with yourself in the middle of a game "
            f"tends to make the next moves worse.")
    if nxt:
        body += (f" Your next {len(nxt)} moves took your win chance from {pct(nxt[0]['wp_before'])} to "
                 f"{pct(nxt[-1]['wp_after'])}.")
    body += " Next time, say what happened instead ('that drops the knight') and get straight back to the board."
    return [{"kind": "inner", "ply": pos, "weight": 0.4 + max(0.0, after_rate - per_move) * 5,
             "head": "Hard on yourself", "body": body, "count": len(remarks),
             "loss": round(max(0.0, after_rate - per_move) * FOLLOW * len(remarks), 3)}]


def build(g, recording: dict | None, transcript: dict | None, utterances) -> list[dict]:
    out = instinct(g)
    if recording and transcript and utterances is not None:
        v = Voice(g, recording, transcript, utterances)
        out += gave_up(g, v) + tide(g, v) + inner(g, v)
    else:
        out += tide(g, None)
    return out

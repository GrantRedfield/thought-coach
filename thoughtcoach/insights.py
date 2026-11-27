"""Insights from your thoughts: what you said and drew, checked against what the engine and the game did.

Every insight is a rule that pairs something you thought (a spoken weakness, a reaction, a drawn
opponent idea, a plan) with an engine number or a move that was actually played. Nothing here
judges a move on its own: the WP numbers come from the engine analysis.

Rules:
  called_it   - a square you called weak (said, or marked) is where the opponent landed right after
                one of your costly moves.
  one_track   - a costly move where the only move you drew or said was the one you played.
  reaction    - something you exclaimed ("oh man", "jackpot"), with the engine's view of the
                moment and how your next moves went.
  read_plan   - opponent moves you drew before they were played.
  planned     - your own move drawn a move or more before you played it.
"""

from __future__ import annotations

import re
import statistics

import chess

from .align import match_positions
from .voice import WEAKNESS_WORDS, tokens
from .fmt import pct, spct, move_went, went

# Words and phrases that carry a feeling. Matched on whole words, lower case.
REACTIONS = {
    "shock": ["oh man", "oh no", "oh god", "oh my god", "gosh", "damn", "dang", "shoot", "ugh", "what a shot",
              "oops", "blunder", "blundered", "terrible", "crap", "no way", "i missed", "missed that", "wow",
              "that's bad", "i'm lost", "i'm losing", "losing", "lost a pawn", "i lost"],
    "confident": ["jackpot", "winning", "got him", "got it", "nice", "easy", "crushing", "great",
                  "beautiful", "perfect", "love it", "that's mate"],
    "regret": ["should have", "should've", "shouldn't have", "why did i", "i knew it"],
    "clock": ["on time", "enough time", "no time", "seconds", "the clock", "time is", "hustling me", "low on time",
              "short on time", "flag", "flagging", "played faster", "play faster", "moved faster", "move faster"],
    "unsure": ["not sure", "i don't know", "pretty close", "confusing", "tricky", "no idea"],
}

FOLLOW_MOVES = 3  # how many of your moves after a reaction to look at
MAX_INSIGHTS = 8  # the dashboard shows the top few; the story picks among all
HINDSIGHT_S = 25  # what you say this soon after your move landed
HINDSIGHT_WORDS = ("should have", "should've", "dropped", "weakens", "unfortunate", "oh man", "oh no", "oops",
                   "blunder", "missed", "didn't see", "damn", "ugh", "hung", "that's bad", "so bad")
# Whole words ("though" isn't "ugh"), allowing endings like "unfortunately".
HINDSIGHT_RE = re.compile(r"\b(" + "|".join(re.escape(w) for w in HINDSIGHT_WORDS) + r")(ly|s|ed)?\b")
CALLED_IT_MOVES = 8  # a weakness you named counts if it is hit within this many moves
JOIN_MS = 3000  # a clause said this soon after a reaction is part of it ("Oh man, what a shot")


def _phrase(text: str) -> tuple[str, str] | None:
    """(feeling, phrase) for the strongest reaction phrase in text, if any."""
    low = " " + re.sub(r"[^a-z' ]+", " ", text.lower()) + " "
    about_a_move = bool(MOVE_TALK.search(text.lower()))
    for feeling in ("clock", "regret", "shock", "confident", "unsure"):
        for phrase in sorted(REACTIONS[feeling], key=len, reverse=True):
            at = low.find(f" {phrase} ")
            if at < 0:
                continue
            if set(low[:at].split()[-4:]) & NEGATIONS:  # "I don't think g3 is great": not a feeling
                continue
            if phrase in DESCRIBES and about_a_move:  # "this beautiful bishop": about a piece, not the game
                continue
            return feeling, phrase
    return None


NEGATIONS = {"not", "don't", "dont", "isn't", "wasn't", "doesn't", "no", "never", "hardly", "think"}
DESCRIBES = {"great", "beautiful", "nice", "perfect", "easy", "terrible", "love it", "wow"}
# A square (also as speech-to-text writes it: "93" for g3), a piece, or a move word.
MOVE_TALK = re.compile(r"\b([a-h][1-8]|\d\d|knight|bishop|rook|queen|king|pawn|takes|castle)\b")


class Game:
    """The game's positions, moves and the WP of the positions the engine looked at."""

    def __init__(self, game, me: chess.Color, report: dict):
        self.me = me
        self.boards = [game.board()]
        self.moves: list[chess.Move] = []
        for move in game.mainline_moves():
            self.moves.append(move)
            board = self.boards[-1].copy(stack=False)
            board.push(move)
            self.boards.append(board)
        self.mine = {m["ply"]: m for m in report["moves"] if not m.get("skipped")}
        self.clock = [None] + [node.clock() for node in game.mainline()]  # clock after each ply

    def label(self, ply: int) -> str:
        """'22... Bxg3' for the move that makes ply `ply` (1-based)."""
        board = self.boards[ply - 1]
        dots = "." if board.turn == chess.WHITE else "..."
        return f"{board.fullmove_number}{dots} {board.san(self.moves[ply - 1])}"

    def wp(self, position: int) -> float | None:
        """My WP in the position after `position` plies, where the engine has it."""
        if position + 1 in self.mine:
            return self.mine[position + 1]["wp_before"]
        if position in self.mine:
            return self.mine[position]["wp_after"]
        return None

    def clocks(self, position: int) -> tuple[float | None, float | None]:
        """(my clock, their clock) in the position after `position` plies."""
        mine = theirs = None
        for ply in range(position, 0, -1):
            if self.is_mine(ply):
                mine = self.clock[ply] if mine is None else mine
            else:
                theirs = self.clock[ply] if theirs is None else theirs
            if mine is not None and theirs is not None:
                break
        return mine, theirs

    def is_mine(self, ply: int) -> bool:
        return self.boards[ply - 1].turn == self.me

    def move_number(self, position: int) -> int:
        return self.boards[min(position, len(self.boards) - 1)].fullmove_number


def _signed(x: float) -> str:
    return spct(x)


# ---------------------------------------------------------------- rules


def called_it(g: Game, windows: list[dict], mistake: float) -> list[dict]:
    named: dict[int, tuple[int, str]] = {}  # square -> (my move ply, how you named it)
    for w in windows:
        for s in w["shapes"]:
            if s["kind"] != "key_square" or not s.get("from"):
                continue
            if s["source"] == "voice" and not set(re.findall(r"[a-z]+", s.get("said", "").lower())) & WEAKNESS_WORDS:
                continue  # a square said without calling it weak
            square = chess.parse_square(s["from"])
            how = f"said '{s['said']}'" if s["source"] == "voice" else f"marked {s['from']}"
            if square not in named or (s["source"] == "voice" and not named[square][1].startswith("said")):
                named[square] = (w["ply"], how)
    out = []
    for square, (ply, how) in named.items():
        for m in sorted(g.mine.values(), key=lambda m: m["ply"]):
            reply = m["ply"] + 1
            if m["ply"] < ply or m["wp_loss"] < mistake or reply > len(g.moves):
                continue
            if m["ply"] - ply > 2 * CALLED_IT_MOVES:
                break
            if g.moves[reply - 1].to_square != square or g.moves[m["ply"] - 1].to_square == square:
                continue  # not that square, or just taking back on the square I moved to
            last = reply == len(g.moves)
            later = (m["ply"] - ply) // 2
            when = "Then" if later == 0 else f"{later} move{'s' * (later != 1)} later"
            out.append({
                "kind": "called_it", "ply": m["ply"], "weight": 1.0 + m["wp_loss"],
                "head": "You called it",
                "body": f"You {how} around move {g.move_number(ply - 1)}. {when}, {g.label(m['ply'])} "
                        f"({move_went(m)}) let {g.label(reply)} land on exactly that square"
                        + (", and the game ended there." if last else "."),
            })
            break
    return out


def one_track(g: Game, mistake: float) -> list[dict]:
    out = []
    for m in g.mine.values():
        if m["wp_loss"] < max(mistake, 0.10) or m.get("gap") == "time_trouble":
            continue
        drawn = [c for c in m.get("drawn_candidates", []) if c.get("legal")]
        if len(drawn) > 1 or not drawn or not drawn[0].get("played"):
            continue
        took = m.get("own_time_s") or m.get("time_spent_pgn") or 0
        out.append({
            "kind": "one_track", "ply": m["ply"], "weight": 0.6 + m["wp_loss"],
            "head": "One idea at the key moment",
            "body": f"At {g.label(m['ply'])} ({move_went(m)}) the only move you drew or said was the one you "
                    + (f"played, picked while they were thinking and played "
                       f"{'instantly' if took < 1 else str(round(took)) + 's later'}. "
                       if (drawn[0].get("first_drawn_s") or 0) < 0 or took < 1 else
                       f"played, {drawn[0].get('first_drawn_s', 0):.0f}s into a {took:.0f}s think. ")
                    +
                    f"{m['best_move_san']} (best) isn't in your recorded thinking. A second candidate is the cheapest check there is.",
        })
    return out


PIECE_NAME = {"P": "pawn", "N": "knight", "B": "bishop", "R": "rook", "Q": "queen", "K": "king"}


def right_piece(g: Game) -> list[dict]:
    """You picked up the piece the engine's best move uses, then moved something else."""
    out = []
    for m in g.mine.values():
        for p in m.get("picked_up", []):
            if p["best_piece"] and not p["moved"]:
                out.append({
                    "kind": "right_piece", "ply": m["ply"], "weight": 0.45 + m["wp_loss"],
                    "head": "Right piece, other move",
                    "body": f"At {g.label(m['ply'])} ({move_went(m)}) you picked up the "
                            f"{PIECE_NAME.get(p['piece'], 'piece')} on {p['square']}, the piece for the engine's best "
                            f"move {m['best_move_san']}, then moved something else.",
                })
                break
    out.sort(key=lambda i: -i["weight"])
    return out[:1]


def named_worst(report: dict) -> list[dict]:
    """You called a piece your worst, and the activity count agrees."""
    for w in report.get("summary", {}).get("q2_list", []):
        if w.get("you") == "named":
            plan = f" The engine's plan improves it with {w['engine_move']}." if w.get("engine_move") else ""
            return [{
                "kind": "named_worst", "ply": None, "weight": 0.55,
                "head": "Right about your worst piece",
                "body": f"Around move {w['move']} you called the {w['piece']} your worst piece, and it was: "
                        f"the {w['piece']} on {w['square']} reached {pct(w['activity'])} of its squares.{plan}",
            }]
    return []


OPENING_MOVES = 12  # foreseeing a move before this is mostly knowing the opening, not reading the game


def _worth_foreseeing(g: Game, ply: int) -> bool:
    """A move that says something when you saw it coming: out of the opening, and not simply taking
    back on the square that was just taken on."""
    if g.boards[ply - 1].fullmove_number < OPENING_MOVES:
        return False
    if ply >= 2:
        mv, prev = g.moves[ply - 1], g.moves[ply - 2]
        if g.boards[ply - 1].is_capture(mv) and g.boards[ply - 2].is_capture(prev) and mv.to_square == prev.to_square:
            return False
    return True


def read_plan(g: Game, windows: list[dict]) -> list[dict]:
    hits: dict[int, str] = {}
    drawn = set()
    for w in windows:
        for s in w["shapes"]:
            if s["kind"] != "opponent_idea" or not s.get("to") or s.get("source", "arrow") != "arrow":
                continue
            frm, to = chess.parse_square(s["from"]), chess.parse_square(s["to"])
            if g.boards[w["ply"] - 1].fullmove_number < OPENING_MOVES:
                continue  # an opening move you saw coming is mostly theory you both know
            if s["phase"] == "opponent_time":
                if s.get("drawn_s", 0) > -0.3:
                    continue  # drawn as the move appeared: not a prediction
                targets = [w["ply"] - 1, w["ply"] + 1]
            else:
                targets = [w["ply"] + 1, w["ply"] + 3]
            drawn.add((frm, to))
            for ply in targets:
                if 1 <= ply <= len(g.moves) and not g.is_mine(ply) and _worth_foreseeing(g, ply):
                    mv = g.moves[ply - 1]
                    if (mv.from_square, mv.to_square) == (frm, to):
                        hits.setdefault(ply, g.label(ply))
                        break
    if len(hits) < 2:
        return []
    names = [hits[p] for p in sorted(hits)]
    side = "White" if g.me == chess.BLACK else "Black"
    return [{
        "kind": "read_plan", "ply": min(hits), "weight": 0.35 + 0.05 * len(hits), "names": names,
        "head": f"You read {side}'s plan",
        "body": f"You predicted {', '.join(names[:3]) if len(names) > 3 else ', '.join(names[:-1]) + ' and ' + names[-1]}"
                f"{f' (+{len(names) - 3} more)' if len(names) > 3 else ''} before they were played: "
                f"{len(hits)} of the {len(drawn)} different opponent moves you predicted came on the board.",
    }]


def planned(g: Game, windows: list[dict]) -> list[dict]:
    found = []
    for w in windows:
        for s in w["shapes"]:
            if s["kind"] != "candidate" or not s.get("to") or s.get("played"):
                continue
            frm, to = chess.parse_square(s["from"]), chess.parse_square(s["to"])
            for later in (w["ply"] + 2, w["ply"] + 4, w["ply"] + 6):
                if later > len(g.moves):
                    break
                mv = g.moves[later - 1]
                if (mv.from_square, mv.to_square) == (frm, to) and _worth_foreseeing(g, later):
                    early = (later - w["ply"]) // 2
                    found.append((w["ply"], later, early, s.get("legal")))
                    break
    seen, uniq = set(), []
    for item in found:
        if item[1] not in seen:
            seen.add(item[1])
            uniq.append(item)
    if not uniq:
        return []
    parts = []
    for ply, later, early, legal in uniq[:2]:
        note = " before it was even possible" if legal is False else ""
        mm = g.mine.get(later)
        verdict = ("" if not mm or mm.get("wp_loss") is None else f" (held at {pct(mm['wp_after'])})"
                   if mm["wp_loss"] < 0.01 else f" ({move_went(mm)})")
        parts.append(f"{g.label(later)}{verdict} was considered {early} move{'s' * (early != 1)} early{note}")
    return [{
        "kind": "planned", "ply": uniq[0][1], "weight": 0.3,
        "head": "Thinking ahead",
        "body": "; ".join(parts) + ".",
    }]


def _dest(san: str | None) -> str | None:
    found = re.findall(r"[a-h][1-8]", san or "")
    return found[-1] if found else None


PIECE_WORDS = {"knight": "N", "night": "N", "bishop": "B", "rook": "R", "queen": "Q", "king": "K", "pawn": "P"}


def names_move(words: list[str], san: str | None) -> bool:
    """Does this run of spoken tokens name `san`? Its destination square must be said, and when a piece word comes
    right before it ("knight a6") that piece must be the one that moves: "knight a6" is Na6, never the pawn move a6."""
    sq = _dest(san)
    if not sq:
        return False
    piece = san[0] if san[0] in "NBRQK" else "P"
    for i, w in enumerate(words):
        if w != sq:
            continue
        said = PIECE_WORDS.get(words[i - 1]) if i else None
        if said is None or said == piece:
            return True
    return False


def hindsight(g: Game, recording: dict | None, transcript: dict | None, utterances) -> list[dict]:
    """Seen right after moving: after a costly move of yours, in the HINDSIGHT_S seconds after it
    landed, you named the engine's better move, or named their reply with a word of regret, or
    (for a move that cost 10%+) just said one ("oh man, just dropped a piece"). The idea was there,
    a moment too late."""
    if not recording or not transcript:
        return []
    placements = [b.board_fen() for b in g.boards]
    events = sorted(recording["events"], key=lambda e: e["t"])
    _, on_screen = match_positions(events, placements)
    landed: dict[int, int] = {}  # position (plies played) -> when it first showed on the board
    for e, pos in zip(events, on_screen):
        if pos is not None:
            landed.setdefault(pos, e["t"])
    clauses = utterances(transcript)
    hits = []
    for ply, m in sorted(g.mine.items()):
        if not m.get("mistake") or m.get("gap") == "time_trouble" or ply not in landed:
            continue
        t0 = landed[ply]
        said = [c for c in clauses if t0 <= c["t"] <= t0 + HINDSIGHT_S * 1000]
        if not said:
            continue
        best, best_sq = m.get("best_move_san"), _dest(m.get("best_move_san"))
        reply = g.boards[ply].san(g.moves[ply]) if ply < len(g.moves) else None
        reply_sq = _dest(reply)
        keep = []
        why = None
        for c in said:
            seq, low = tokens(c["text"]), c["text"].lower()
            regret = bool(HINDSIGHT_RE.search(low))
            # naming the move just played ("let's go knight a6" for Na6) is not naming the better one
            if best_sq and best != m["move_san"] and names_move(seq, best) and not names_move(seq, m["move_san"]):
                keep.append(c["text"].strip()); why = why or "best"
            elif reply_sq and names_move(seq, reply):
                keep.append(c["text"].strip()); why = why or ("reply" if regret else None)
            elif regret:
                keep.append(c["text"].strip()); why = why or ("regret" if m["wp_loss"] >= 0.10 else None)
        if not why:
            continue
        if why != "best" and not any(HINDSIGHT_RE.search(k.lower()) for k in keep):
            continue
        quote = " … ".join(dict.fromkeys(k.rstrip(",.") for k in keep))[:120]
        hits.append({"ply": ply, "loss": m["wp_loss"], "wp_before": m["wp_before"], "wp_after": m["wp_after"], "quote": quote, "best": best, "why": why,
                     "reply": g.label(ply + 1) if reply else None})
    if not hits:
        return []
    parts = []
    for h in sorted(hits, key=lambda h: -h["loss"])[:3]:
        what = {"best": f"{h['best']} was the move", "reply": f"and {h['reply']} came", "regret": f"{h['best']} was the move"}[h["why"]]
        parts.append(f"after {g.label(h['ply'])} ({move_went(h)}): '{h['quote']}', {what}")
    n = len(hits)
    lost = sum(h["loss"] for h in hits)
    return [{
        "kind": "hindsight", "ply": hits[0]["ply"], "weight": 0.6 + 0.1 * n + lost, "loss": round(lost, 3), "count": n,
        "head": "Seen right after moving",
        "body": (f"{n} time{'s' * (n != 1)} the right idea came out loud just after you'd moved: " if n > 1 else
                 "The right idea came out loud just after you'd moved: ") + "; ".join(parts)
                + ". One more look before you let go of the piece.",
    }]


def reactions(g: Game, recording: dict | None, transcript: dict | None, utterances) -> list[dict]:
    if not recording or not transcript:
        return []
    placements = [b.board_fen() for b in g.boards]
    events = sorted(recording["events"], key=lambda e: e["t"])
    _, on_screen = match_positions(events, placements)
    times = [e["t"] for e in events]

    def position_at(t: int) -> int | None:
        index = None
        for i, et in enumerate(times):
            if et > t:
                break
            index = i
        return None if index is None else on_screen[index]

    own_times = [m.get("own_time_s") or 0 for m in g.mine.values()]
    pace = statistics.mean(own_times) if own_times else 0
    out, used = [], set()
    clock_said = False
    clauses = utterances(transcript)
    for k, utt in enumerate(clauses):
        hit = _phrase(utt["text"])
        if not hit:
            continue
        feeling, phrase = hit
        if feeling == "clock":
            if clock_said:
                continue
            position = position_at(utt["t"])
            if position is None:
                continue
            clock_said = True
            mine_c, theirs_c = g.clocks(position)
            mmss = lambda x: f"{int(x) // 60}:{int(x) % 60:02d}"  # noqa: E731
            clocks = f" with {mmss(mine_c)} to their {mmss(theirs_c)}" if mine_c is not None and theirs_c is not None else ""
            tt = [m for m in g.mine.values() if m.get("gap") == "time_trouble" and m["ply"] > position]
            later = [m for m in g.mine.values() if m["ply"] > position]
            avg_after = statistics.mean([m.get("own_time_s") or 0 for m in later]) if later else 0
            if tt:
                worst = max(tt, key=lambda m: m["wp_loss"])
                tail = (f" After that, {len(tt)} move{'s' * (len(tt) != 1)} played under 30s; the costliest "
                        f"{g.label(worst['ply'])} ({move_went(worst)}).")
            else:
                tail = " You kept out of time trouble after that."
            out.append({
                "kind": "clock", "ply": position, "weight": 0.5 + sum(m["wp_loss"] for m in tt),
                "head": "You saw the clock coming",
                "body": f"'{utt['text'].strip().rstrip(',.')}' around move {g.move_number(position)}{clocks}. "
                        f"Your moves after it averaged {avg_after:.0f}s.{tail}",
            })
            continue
        position = position_at(utt["t"])
        if position is None or position in used:
            continue
        now = g.wp(position)
        if now is None:
            continue
        used.add(position)
        said = utt["text"].strip()
        for nxt_clause in clauses[k + 1 : k + 3]:  # "Oh man," + "what a shot."
            if nxt_clause["t"] - utt["t"] > JOIN_MS or said.endswith((".", "!", "?")):
                break
            said += " " + nxt_clause["text"].strip()
        said = said.rstrip(",.")

        # What just happened on the board (the last move before you spoke), in your WP.
        last = position
        before = g.wp(last - 1) if last >= 1 else None
        if before is None and last >= 2:
            before = g.wp(last - 2)
        swing = None if before is None else now - before
        trigger = g.label(last) if last >= 1 else "the start"

        # How the next few of your moves went.
        nxt = [g.mine[p] for p in sorted(g.mine) if p > position][:FOLLOW_MOVES]
        lost = sum(m["wp_loss"] for m in nxt)
        avg = statistics.mean([m.get("own_time_s") or 0 for m in nxt]) if nxt else 0

        if feeling == "shock":
            read = (f"a real blow (your win chance {went(before, now)})" if swing is not None and swing <= -0.10
                    else f"the engine still had you at {pct(now)} ({went(before, now)})" if swing is not None
                    else f"the engine had you at {pct(now)}")
        elif feeling == "confident":
            read = (f"the engine agreed: {pct(now)} win chance" if now >= 0.6
                    else f"but the position was only level ({pct(now)} win chance)" if now >= 0.4
                    else f"but the engine had you worse ({pct(now)} win chance)")
        elif feeling == "regret" and [p for p in g.mine if p <= position]:
            mine = g.mine[max(p for p in g.mine if p <= position)]
            read = (f"the engine agrees, {g.label(mine['ply'])} took you from {pct(mine['wp_before'])} to "
                    f"{pct(mine['wp_after'])}" if mine["wp_loss"] >= 0.05
                    else f"the engine was fine with {g.label(mine['ply'])} ({move_went(mine)})")
        else:
            read = f"the engine had you at {pct(now)}"
        follow = ""
        if nxt:
            follow = (f" Your next {len(nxt)} moves took you from {pct(nxt[0]['wp_before'])} to "
                      f"{pct(nxt[-1]['wp_after'])}, {avg:.0f}s each (game average {pace:.0f}s).")
            if feeling == "shock" and lost >= 0.10 and avg < pace:
                follow += " Speeding up after a shock is worth watching."
            elif feeling == "shock" and lost < 0.05:
                follow += " You steadied well."
            elif feeling == "regret" and lost >= 0.10 and "fine with" in read:
                follow += " Dwelling on a move that was fine cost more than the move did."
            elif feeling == "confident" and lost >= 0.10:
                follow += " The moves that felt easy were where the game slipped."
        weight = 0.4 + abs(swing or 0) + lost
        notable = abs(swing or 0) >= 0.10 or lost >= 0.10 or (feeling == "confident" and now < 0.55)
        if not notable:
            continue  # a feeling with nothing on the board behind it isn't worth a line
        out.append({
            "kind": "reaction", "ply": position, "weight": weight,
            "head": {"shock": "Your reaction", "confident": "Feeling good", "unsure": "Doubt",
                     "regret": "Second-guessing"}[feeling],
            "body": f"'{said}' after {trigger}{', ' if read.startswith('but') else ': '}{read}.{follow}",
        })
    out.sort(key=lambda i: -i["weight"])
    return out[:2]


def build(game, me: chess.Color, report: dict, thinking: dict | None, recording: dict | None = None,
          transcript: dict | None = None, utterances=None) -> list[dict]:
    """The insights worth showing, most important first."""
    g = Game(game, me, report)
    windows = (thinking or {}).get("windows", [])
    mistake = report["thresholds"]["mistake"]
    found = (called_it(g, windows, mistake) + one_track(g, mistake) + right_piece(g) + named_worst(report)
             + read_plan(g, windows) + planned(g, windows))
    if utterances is not None:
        found += reactions(g, recording, transcript, utterances) + hindsight(g, recording, transcript, utterances)
    from . import psych  # the mental game: giving up early, the tide turning, instinct, the inner game

    found += psych.build(g, recording, transcript, utterances)
    found.sort(key=lambda i: -i["weight"])
    return found[:MAX_INSIGHTS]



# ---------------------------------------------------------------- in short: one good thing, one to watch


def _sharpness(record: dict) -> float:
    wps = sorted((c["wp"] for c in record.get("candidates") or []), reverse=True)
    return wps[0] - wps[1] if len(wps) > 1 else 0.0


def verdict(report: dict, analysis: dict | None, insights: list[dict]) -> dict:
    """{'good': {...}, 'watch': {...}}: the strongest positive and the most costly habit, each one
    sentence built from the engine numbers (no LLM). Either can be None."""
    dots = "." if report["game"]["my_color"] == "white" else "..."
    label = lambda m: f"{m['move_number']}{dots} {m['move_san']}"  # noqa: E731
    s, t = report["summary"], report["thresholds"]
    moves = [m for m in report["moves"] if not m.get("skipped")]
    records = {r["ply"]: r for r in (analysis or {}).get("moves", [])}
    tt = t.get("time_trouble", 30)
    kinds = {i["kind"]: i for i in insights}

    # ---- went well: every candidate gets a score, the best one wins.
    good = []
    real, spotted = s["q3_real_threats"], s["q3_spotted"]
    through = real - spotted - s.get("q3_handled", 0)
    if real >= 3 and spotted / real >= 0.5:  # drawn, not just "your move happened to deal with it"
        good.append((0.45 + 0.3 * spotted / real, f"You spotted {spotted} of the {real} real threats against you "
                                                   f"before they could happen."))
    elif real >= 5 and not through:  # most threats are answered without a word: stopping them all counts
        good.append((0.5, f"You stopped all {real} real threats against you"
                          + (f", naming {spotted} of them before they could happen." if spotted else ".")))
    sharp = [m for m in moves if m["ply"] in records and _sharpness(records[m["ply"]]) >= 0.10
             and m["wp_loss"] < 0.02 and (m.get("clock_before") or tt + 1) >= tt]
    if sharp:
        best = max(sharp, key=lambda m: _sharpness(records[m["ply"]]))
        more = f" ({len(sharp)} times in all)" if len(sharp) > 1 else ""
        good.append((0.55 + 0.05 * len(sharp), f"You found the only good move when it mattered: {label(best)}, "
                                                f"{pct(_sharpness(records[best['ply']]))} better than anything else{more}."))
    n_weak, n_seen = s.get("q1_weaknesses", 0), s.get("q1_found", 0)
    if n_weak >= 3 and n_seen / n_weak >= 0.5:
        seen = [w["square"] for w in s.get("q1_list", []) if w.get("you")][:3]
        good.append((0.45 + 0.3 * n_seen / n_weak, f"You saw {n_seen} of the {n_weak} weaknesses the engine went for, "
                                                    f"like {' and '.join(seen[:2])}."))
    if "named_worst" in kinds:
        good.append((0.5, kinds["named_worst"]["body"]))
    if "read_plan" in kinds:
        names = kinds["read_plan"]["names"]
        eg = " and ".join(names[:2])
        good.append((0.5 + 0.03 * len(names), f"You read your opponent: {len(names)} of the moves you predicted for them "
                                               f"were played, like {eg}."))
    if s["q4_positions"] >= 5 and s["q4_best_considered"] / s["q4_positions"] >= 0.5:
        r = s["q4_best_considered"] / s["q4_positions"]
        good.append((0.3 + r * 0.4, f"The engine's best move was among your ideas in {s['q4_best_considered']} of "
                                    f"{s['q4_positions']} moves where you considered something."))
    run, best_run = [], []
    for m in moves:
        if (m.get("clock_before") or tt + 1) < tt:
            break
        run = [] if m["mistake"] else run + [m]
        best_run = max(best_run, run, key=len)
    if len(best_run) >= 8:
        good.append((0.35 + 0.015 * len(best_run), f"No costly move for {len(best_run)} moves in a row, "
                                                    f"{label(best_run[0])} to {label(best_run[-1])}."))
    peak = max(moves, key=lambda m: m["wp_after"], default=None)
    if peak and peak["wp_after"] >= 0.85 and peak["wp_after"] - moves[0]["wp_before"] >= 0.20:
        good.append((0.4, f"You got to a winning position: {pct(peak['wp_after'])} after {label(peak)}."))
    clean = sum(m["wp_loss"] < 0.02 for m in moves)
    if moves:
        good.append((0.2, f"{clean} of your {len(moves)} moves cost under 2%."))

    # ---- watch out: the gap that cost the most, told through its worst move.
    watch = None
    costly = [m for m in moves if m["mistake"]]
    if costly:
        by_gap: dict[str, list] = {}
        for m in costly:
            by_gap.setdefault(m["gap"], []).append(m)
        gap, ms = max(by_gap.items(), key=lambda kv: sum(m["wp_loss"] for m in kv[1]))
        worst = max(ms, key=lambda m: m["wp_loss"])
        lost = round(sum(m["wp_loss"] for m in ms) * 100)
        many = (f" It happened {len(ms)} times this game."
                if len(ms) > 1 else "")
        swing = lambda m: f"{pct(m['wp_before'])} to {pct(m['wp_after'])}"  # noqa: E731
        had = [d["move_san"] for d in worst.get("drawn_candidates", []) if d.get("legal") and not d.get("played")]
        best_drawn = worst["best_move_san"] in had
        if gap == "time_trouble":
            first = min((m for m in moves if (m.get("clock_before") or tt + 1) < tt), key=lambda m: m["ply"])
            text = (f"The clock. You were under {tt:.0f}s from move {first['move_number']}, and {label(worst)} "
                    f"took your win chance from {swing(worst)}"
                    + (f" with {worst['best_move_san']} (best) already considered" if best_drawn else "") + "." + many
                    + (" You saw it coming early: bank time in the middlegame." if "clock" in kinds else
                       " Bank time in the middlegame for the end."))
        elif gap == "wrongly_rejected":
            text = (f"Trust your candidates. At {label(worst)} you considered {worst['best_move_san'] if best_drawn else ', '.join(worst['q4']['wrongly_rejected'])}"
                    f" and played {worst['move_san']} instead, which took your win chance from {swing(worst)}.{many}")
        elif gap == "opponent_idea":
            text = (f"Check their idea first. {label(worst)} let {worst['q3']['threat_san']} through "
                    f"({move_went(worst)}).{many}")
        else:
            if had:  # you looked at other moves, just not the right one
                more = f" (+{len(had) - 3} more)" if len(had) > 3 else ""
                text = (f"Widen the search. At {label(worst)} ({move_went(worst)}) you drew or said "
                        f"{', '.join(had[:3])}{more}, but {worst['best_move_san']} isn't in your recorded thinking.{many}")
            else:
                one = any(d.get("played") for d in worst.get("drawn_candidates", []))
                what = "you considered only the move you played" if one else "you showed no candidates"
                text = (f"A second candidate. At {label(worst)} ({move_went(worst)}) {what}; "
                        f"{worst['best_move_san']} was best.{many}")
        watch = {"ply": worst["ply"], "text": text}

    top = max(good, key=lambda g: g[0]) if good else None
    return {"good": {"text": top[1]} if top else None, "watch": watch}

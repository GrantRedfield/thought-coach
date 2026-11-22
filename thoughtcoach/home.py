"""The home page: your latest game up front (its story, the moment that decided it, your focus for
the next game), then your form across games (rating, accuracy, what you see), then every game.

    render(loaded, prof, stories, statuses, owner) -> the page's <main> content

`loaded` is profile.load(...) (oldest first), `prof` profile.compute(loaded), `stories` the saved
<id>.story.json summaries, `statuses` the review queue (game_id -> state) for games not done yet.
"""

from __future__ import annotations

import html
import statistics

import chess
import chess.svg

from . import profile
from .gametype import classify
from . import bayes, habits, timing
from .fmt import ACCURACY_EXPLAIN, acc, accuracy, game_url, went

RECENT = 5  # "your form": the last few games against the ones before


def _e(x) -> str:
    return html.escape(str(x), quote=True)



# ---------------------------------------------------------------- per-game facts


def facts(loaded: list[dict], prof: dict) -> list[dict]:
    """One row per game, oldest first: who, result, rating, the win-chance curve, accuracy."""
    per = {g["id"]: g for g in prof["games"]}
    out = []
    for g in loaded:
        r, gid = g["report"]["game"], g["id"]
        mine = r["my_color"] == "white"
        p = per.get(gid, {})
        res = r.get("result", "*")
        won = (res == "1-0") == mine and res in ("1-0", "0-1")
        elo = r.get("white_elo" if mine else "black_elo")
        out.append({
            "id": gid, "date": r.get("date"), "tc": r.get("time_control"), "color": r["my_color"],
            "opp": r["black"] if mine else r["white"], "opp_elo": r.get("black_elo" if mine else "white_elo"),
            "elo": int(elo) if str(elo or "").isdigit() else None, "result": res,
            "outcome": "draw" if res == "1/2-1/2" else "won" if won else "lost", "opening": r.get("opening"),
            "per_move": p.get("per_move", 0.0), "costly": p.get("costly", 0), "moves": p.get("moves", 0),
            "before": p.get("before"), "after": p.get("after"), "acc": p.get("acc"),
            "curve": [(m["ply"], 1.0 if m["move_san"].endswith("#") else m["wp_after"])
                      for m in g["analysis"].get("moves", []) if m.get("wp_after") is not None],
            "strike": (p.get("strike") or {}).get("side"),
            "type": classify(g["analysis"], r),
            "timing": timing.game(g["analysis"], g["report"]),
        })
    return out


# ---------------------------------------------------------------- small charts


def _spark(curve: list[tuple[int, float]], w: int = 220, h: int = 56, cls: str = "spark") -> str:
    """Win chance through one game: a line with the 50% mark, shaded above and below it."""
    if len(curve) < 2:
        return ""
    p0, p1 = curve[0][0], curve[-1][0]
    x = lambda p: 2 + (p - p0) / max(1, p1 - p0) * (w - 4)  # noqa: E731
    y = lambda v: 3 + (1 - v) * (h - 6)  # noqa: E731
    pts = " ".join(f"{x(p):.1f},{y(v):.1f}" for p, v in curve)
    area = f"{x(p0):.1f},{y(0.5):.1f} {pts} {x(p1):.1f},{y(0.5):.1f}"
    return (f"<svg class='{cls}' viewBox='0 0 {w} {h}' preserveAspectRatio='none' aria-hidden='true'>"
            f"<polygon class='area' points='{area}'/><line class='mid' x1='0' x2='{w}' y1='{y(0.5):.1f}' y2='{y(0.5):.1f}'/>"
            f"<polyline class='ln' points='{pts}' pathLength='1'/></svg>")


def _trend(values: list[float | None], w: int = 120, h: int = 30) -> str:
    """A tiny line of one number across games (oldest to newest), last point marked."""
    pts = [(i, v) for i, v in enumerate(values) if v is not None]
    if len(pts) < 2:
        return ""
    lo, hi = min(v for _, v in pts), max(v for _, v in pts)
    span = (hi - lo) or 1
    x = lambda i: 3 + i / max(1, len(values) - 1) * (w - 6)  # noqa: E731
    y = lambda v: 4 + (1 - (v - lo) / span) * (h - 8)  # noqa: E731
    line = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in pts)
    li, lv = pts[-1]
    return (f"<svg class='trend' viewBox='0 0 {w} {h}' aria-hidden='true'><polyline points='{line}'/>"
            f"<circle cx='{x(li):.1f}' cy='{y(lv):.1f}' r='3'/></svg>")


def _games_chart(rows: list[dict]) -> str:
    """Win chance lost per move in every game (columns, coloured by result), with the 5-game average."""
    if len(rows) < 2:
        return "<p class='cap'>Your games show up here from the second one on.</p>"
    W, H, L, R, T, B = 1000, 220, 4, 4, 8, 6
    top = max(r["per_move"] for r in rows) * 1.15 or 0.01
    n = len(rows)
    slot = (W - L - R) / n
    bw = max(4, min(26, slot - 4))
    y = lambda v: T + (1 - v / top) * (H - T - B)  # noqa: E731
    parts = []
    for frac in (0.5, 1.0):
        v = top * frac / 1.15
        parts.append(f"<line class='grid' x1='{L}' x2='{W - R}' y1='{y(v):.1f}' y2='{y(v):.1f}'/>")
    parts.append(f"<line class='base' x1='{L}' x2='{W - R}' y1='{y(0):.1f}' y2='{y(0):.1f}'/>")
    avg = []
    for i, r in enumerate(rows):
        cx = L + slot * (i + 0.5)
        h = max(2.0, y(0) - y(r["per_move"]))
        tip = (f"{r['date'] or ''} vs {r['opp']} ({r['outcome']}): accuracy {acc(r.get('acc'))}, "
               f"{r['costly']} costly move{'s' * (r['costly'] != 1)}")
        parts.append(f"<a href='{_e(game_url(r['id']))}' class='col {r['outcome']}' data-tip='{_e(tip)}'>"
                     f"<rect class='hit' x='{cx - slot / 2:.1f}' y='{T}' width='{slot:.1f}' height='{H - T - B}'/>"
                     f"<rect class='bar' x='{cx - bw / 2:.1f}' y='{y(0) - h:.1f}' width='{bw:.1f}' height='{h:.1f}' rx='3'/></a>")
        window = [x["per_move"] for x in rows[max(0, i - 4): i + 1]]
        avg.append((cx, statistics.mean(window)))
    if n >= 3:
        parts.append("<polyline class='avg' points='" + " ".join(f"{cx:.1f},{y(v):.1f}" for cx, v in avg) + "'/>")
    return (f"<div class='chartwrap'><svg class='games' viewBox='0 0 {W} {H}' preserveAspectRatio='none' role='img' "
            f"aria-label='How much your average move cost in each game'>{''.join(parts)}</svg><div class='tip' hidden></div></div>"
            "<p class='legend'><span><i class='sw won'></i>won</span><span><i class='sw lost'></i>lost</span>"
            "<span><i class='sw draw'></i>draw</span><span><i class='sw avg'></i>average of the last 5</span>"
            "<span class='muted'>lower is better · oldest game on the left, newest on the right · click a column to open it</span></p>")


# ---------------------------------------------------------------- the latest game


VERDICT = {"won": "You beat", "lost": "You lost to", "draw": "You drew with"}


def _short(text: str, n: int = 190) -> str:
    return text if len(text) <= n else text[: n - 1].rsplit(" ", 1)[0] + "…"


def _board(beat: dict | None, orientation: str) -> str:
    if not beat or not beat.get("fen"):
        return ""
    board = chess.Board(beat["fen"])
    arrows = [chess.svg.Arrow(f, t, color=c + "dd") for f, t, c in beat.get("arrows") or []]
    fill = {int(sq): c + "cc" for sq, c in (beat.get("fill") or {}).items()}
    return chess.svg.board(board, orientation=orientation == "white", arrows=arrows, fill=fill, coordinates=False, size=360)


def _type_chip(t: dict | None, big: bool = False) -> str:
    """The kind of game (gametype.py): a swindle, a crazy game, a stomp..."""
    if not t:
        return ""
    why = f"<small>{_e(t['why'])}</small>" if big else ""
    return f"<p class='gtype {t['kind']}{' big' if big else ''}' title='{_e(t['why'])}'>{_e(t['label'])}{why}</p>"


def hero(last: dict, story: dict | None, avg_before: tuple | None) -> str:
    """avg_before: your earlier games' (win chance lost per move, accuracy)."""
    s = story or {}
    beats = s.get("beats") or []
    top = beats[0] if beats else None
    turning = next((b for b in beats if b.get("kind") == "turning"), None)
    # The position shown: the deciding move when one decided it, else the first moment with a board.
    key = (turning if s.get("decided_by") and turning and turning.get("fen")
           else next((b for b in beats if b.get("fen")), None))
    if s.get("decided_by"):
        headline, detail = f"It came down to one move: {s['decided_by']}", (turning or {}).get("body")
    elif top:
        headline, detail = top["title"], top.get("body")
    else:
        headline, detail = "A quiet game: nothing stood out enough for its own card.", None
    focus = s.get("focus")
    compare = ""
    if avg_before is not None and last["moves"]:
        better = last["per_move"] <= avg_before[0]
        compare = (f"<div class='chip {'up' if better else 'down'}'><b>{'better' if better else 'worse'}</b>"
                   f"than usual (accuracy {acc(last.get('acc'))}, usually {acc(avg_before[1])})</div>")
    elo = f" ({_e(last['opp_elo'])})" if last.get("opp_elo") else ""
    stats = [(s.get("arrows"), "arrows drawn"), (s.get("said"), "things said aloud"),
             (last["costly"], "costly moves"), (acc(last["acc"]) if last.get("acc") is not None else None, "accuracy")]
    chips = "".join(f"<div class='chip'><b data-count>{_e(v)}</b>{_e(t)}</div>" for v, t in stats if v is not None)
    board = _board(key, s.get("orientation") or last["color"])
    figure = (f"<figure class='hfig'><div class='brd'>{board}</div><figcaption>{_e(key['eyebrow'])}</figcaption></figure>"
              if board else "")
    focus_html = (f"<div class='focus'><p class='eyebrow'>Your focus for the next game</p><h3>{_e(focus['title'])}</h3>"
                  f"<p>{_e(focus['habit'])}</p></div>" if focus else "")
    meta = " · ".join(_e(x) for x in (last["date"], last["tc"], f"you had {last['color']}", last.get("opening")) if x)
    return f"""<section class="hero {last['outcome']}">
  <div class="htext">
    <p class="eyebrow">Your latest game · {meta}</p>{_type_chip(last.get('type'), True)}
    <h1><span class="verdict">{VERDICT[last['outcome']]}</span> {_e(last['opp'])}{elo}</h1>
    <p class="headline">{_e(headline)}</p>{f'<p class="detail">{_e(_short(detail))}</p>' if detail else ''}
    <div class="hcurve">{_spark(last['curve'], 520, 90, 'spark big')}<span>your win chance, move by move</span></div>
    <div class="chips">{chips}{compare}</div>
    <div class="cta"><a class="btn play" href="{_e(game_url(last['id'], story=True))}">&#9654; Watch your game story</a>
      <a class="btn ghost" href="{_e(game_url(last['id']))}">Full review</a></div>
  </div>
  <div class="hside">{figure}{focus_html}</div>
</section>"""


# ---------------------------------------------------------------- your form


def form(rows: list[dict], prof: dict) -> str:
    n = len(rows)
    won = sum(r["outcome"] == "won" for r in rows)
    drawn = sum(r["outcome"] == "draw" for r in rows)
    recent, before = rows[-RECENT:], rows[:-RECENT]
    tiles, more = [], []

    def tile(value, label, trend="", delta="", tone="", hint="", extra=False):
        (more if extra else tiles).append(f"<div class='tile {tone}'><span class='lab'>{label}</span><b>{value}</b>"
                     f"{f'<span class=hint>{hint}</span>' if hint else ''}{trend}"
                     f"{f'<span class=delta>{delta}</span>' if delta else ''}</div>")

    elos = [r["elo"] for r in rows]
    if any(elos):
        known = [e for e in elos if e]
        change = known[-1] - known[0] if len(known) > 1 else 0
        tile(known[-1], "Rating (lichess)", _trend(elos),
             f"{'+' if change >= 0 else ''}{change} since your first review" if len(known) > 1 else "",
             "up" if change > 0 else "down" if change < 0 else "")
    tile(n, "Games reviewed", "", f"{won} won · {drawn} drawn · {n - won - drawn} lost", extra=True)
    per = [r["per_move"] for r in rows]
    if before:
        d = statistics.mean(x["per_move"] for x in recent) - statistics.mean(x["per_move"] for x in before)
        mean = lambda rs, k: statistics.mean(x[k] for x in rs if x.get(k) is not None)  # noqa: E731
        delta = f"last {len(recent)} games: {acc(mean(recent, 'acc'))} · before that: {acc(mean(before, 'acc'))}"
        tone = "up" if d <= 0 else "down"
    else:
        delta, tone = "", ""
    known = [r["acc"] for r in rows if r.get("acc") is not None]
    tile(acc(statistics.mean(known)) if known else "?",
         f"<span title='{_e(ACCURACY_EXPLAIN)}'>Accuracy</span>", _trend(known), delta, tone,
         "like lichess's: 100% = no move gave away winning chances")
    tile(f"{statistics.mean(r['costly'] for r in rows):.1f}", "Costly moves per game", _trend([r["costly"] for r in rows]))
    tm = [r["timing"] for r in rows if r.get("timing")]
    if tm:
        crit = statistics.median([x for t in tm for x in t["crit_times"]])
        other = statistics.median([x for t in tm for x in t["other_times"]])
        cc, costly = sum(t["costly_critical"] for t in tm), sum(t["costly"] for t in tm)
        rushed = sum(len(t["rushed"]) for t in tm)
        n_crit, n_all = sum(t["critical"] for t in tm), sum(t["positions"] for t in tm)
        worse = bayes.prob_greater_prior(cc, n_crit, costly - cc, n_all - n_crit, bayes.sceptic(costly / n_all if n_all else 0.25))
        tile(f"{crit:.0f}s<small> vs {other:.0f}s</small>", "Think time, one move holds vs rest",
             "", f"{cc} of {costly} costly moves · {rushed} fast · "
                 f"{bayes.verdict(worse).capitalize()}: costlier",
             "down" if crit <= other else "up", extra=True)
    q = prof["questions"]
    if q["q3"][1]:
        tile(f"{q['q3'][0] / q['q3'][1] * 100:.0f}%", "Their threats you saw coming", bayes.range_svg(*q['q3'], 200),
             f"{q['q3'][0]} of {q['q3'][1]} · probably {bayes.interval(*q['q3'])[0] * 100:.0f}–{bayes.interval(*q['q3'])[1] * 100:.0f}%",
             extra=True)
    if q["q4"][1]:
        tile(f"{q['q4'][0] / q['q4'][1] * 100:.0f}%", "Best move among your candidates", bayes.range_svg(*q['q4'], 200),
             f"{q['q4'][0]} of {q['q4'][1]} · probably {bayes.interval(*q['q4'])[0] * 100:.0f}–{bayes.interval(*q['q4'])[1] * 100:.0f}%")
    st = prof["strikes"]
    if st["games"]:
        tile(f"{st['you']['n']}<small>/{st['games']}</small>", "Games where you struck first", "",
             f"won {st['you']['won']} of those" if st["you"]["n"] else "", extra=True)
    fold = (f"<details class='fold more'><summary>More numbers</summary><div class='tiles'>{''.join(more)}</div></details>"
            if more else "")
    return (f"<section class='form'><h2>Your form <small>{n} games · {won} won · {drawn} drawn · {n - won - drawn} lost</small></h2>"
            f"<div class='tiles'>{''.join(tiles)}</div>{fold}</section>")


# ---------------------------------------------------------------- every game


def cards(rows: list[dict], statuses: dict[str, dict]) -> str:
    out = []
    for gid, st in statuses.items():  # still being reviewed, or failed
        if st["state"] == "done":  # reviewed, but its saved review can't be read for the summaries
            out.append(f"<a class='gcard' href='{_e(game_url(gid))}'><p class='gtop'><span class='res'>✓</span>"
                       f"<b>{_e(gid)}</b></p><p class='wait'>Reviewed: open the dashboard</p></a>")
            continue
        label = {"queued": "Waiting to be reviewed…", "running": "Being reviewed right now…"}.get(st["state"], "Review failed")
        err = f"<small>{_e(st.get('error') or '')}</small>" if st["state"] == "error" else ""
        out.append(f"<div class='gcard pending {st['state']}'><p class='gtop'><span class='res'>…</span>"
                   f"<a href='https://lichess.org/{_e(gid)}'>{_e(gid)}</a></p><p class='wait'>{label}</p>{err}</div>")
    for r in reversed(rows):
        res = {"won": "W", "lost": "L", "draw": "D"}[r["outcome"]]
        out.append(f"""<a class='gcard {r['outcome']}' href='{_e(game_url(r['id']))}'>
<p class='gtop'><span class='res'>{res}</span><b>vs {_e(r['opp'])}</b><span class='gdate'>{_e(r['date'] or '')}</span></p>{_type_chip(r.get('type'))}
{_spark(r['curve'])}
<p class='gmeta'>{_e(r['tc'] or '')} · {_e(r['color'])} · {r['costly']} costly · {acc(r.get('acc'))} accuracy</p>
<span class='gplay' data-href='{_e(game_url(r['id'], story=True))}'>&#9654; story</span></a>""")
    return "".join(out)


# ---------------------------------------------------------------- the page


def focus_check(loaded: list[dict], stories: dict[str, dict]) -> str:
    """Is your focus working? (habits.py): each focus you've played with, before against since."""
    items = habits.compute(loaded, stories)
    if not items:
        return ""
    shown, rest = items[:2], items[2:4]
    fold = (f"<details class='fold more'><summary>Earlier focuses</summary><div class='fgrid'>"
            f"{''.join(_focus_card(e) for e in rest)}</div></details>" if rest else "")
    return (f"<section class='focuscheck'><h2>Is your focus working?</h2><p class='cap'>Games before each focus "
            f"against the games since.</p><div class='fgrid'>{''.join(_focus_card(e) for e in shown)}</div>{fold}</section>")


def focus_state(e: dict) -> tuple[str, str, str]:
    """(css tone, badge, plain sentence) for one focus: how sure we are it changed, in words."""
    (kb, nb), (ka, na) = e["before"], e["since"]
    p = e["p"]
    if not na:
        return "", "waiting", "It hasn't come up yet in the games since."
    if p is None or e["games"] < habits.MIN_GAMES:
        g = e["games"]
        return "", "too early", (f"Only {g} game{'s' * (g != 1)} so far: one game says almost nothing. "
                                 f"Give it {habits.MIN_GAMES - g} more.")
    n = round(p * 10)
    if p >= bayes.LIKELY:
        if p >= 0.95:
            return "up", "working", "Working, and almost certainly a real change, not luck."
        return "up", "working", f"Working. The odds this is a real change, not luck: about {n} in 10."
    if p >= 0.6:
        return "lean", "looks promising", (f"Heading the right way, but not sure yet: about {n} in 10 that it's real. "
                                           "A few more games will settle it.")
    if p > 0.4:
        return "", "no change yet", "No real difference yet: it could go either way."
    if p > 1 - bayes.LIKELY:
        return "slip", "slipping", "Slightly the wrong way so far, but that could still be luck."
    return "down", "not yet", f"Going the wrong way: about {10 - n} in 10 that it really got worse."


def _focus_card(e: dict) -> str:
    (kb, nb), (ka, na) = e["before"], e["since"]
    tone, badge, words = focus_state(e)
    up = habits.MEASURE[e["key"]][2]
    rb, ra = (kb / nb if nb else None), (ka / na if na else None)
    top = max([x for x in (rb, ra) if x is not None] + [0.05])

    def bar(label, r, k, n, cls):
        if r is None:
            return f"<div class='fbar'><span class='fl'>{label}</span><span class='ftrk'></span><span class='fv'>none yet</span></div>"
        return (f"<div class='fbar'><span class='fl'>{label}</span><span class='ftrk'><i class='{cls}' "
                f"style='width:{max(2, r / top * 100):.0f}%'></i></span><span class='fv'><b>{r:.0%}</b> "
                f"<small>{k} of {n}</small></span></div>")
    better = ra is not None and rb is not None and ((ra > rb) == up) and ra != rb
    bars = bar("Before", rb, kb, nb, "b0") + bar("Since", ra, ka, na, "b1 good" if better else "b1")
    g = e["games"]
    return (f"<div class='fcard {tone}'><p class='ftop'><b>{_e(e['title'])}</b><span class='fbadge'>{badge}</span></p>"
            f"<p class='fwhat'>{_e(e['label'].capitalize())} ({'higher' if up else 'lower'} is better) · "
            f"{g} game{'s' * (g != 1)} since</p>{bars}<p class='ftext'>{_e(words)}</p></div>")


def render(loaded: list[dict], prof: dict | None, stories: dict[str, dict], statuses: dict[str, dict]) -> str:
    rows = facts(loaded, prof) if prof else []
    if not rows:
        waiting = cards([], statuses)
        return f"""<section class="hero empty"><div class="htext"><p class="eyebrow">Thought Coach</p>
<h1>Your first review is on its way</h1><p class="headline">Play a game on lichess with the extension on: about five
minutes after it ends, its story shows up here. Draw an arrow for each move you consider and think out
loud: the more the review sees, the more it can tell you.</p><div class="cta"><a class="btn play" href="https://lichess.org/">Play on lichess</a>
<a class="btn ghost" href="https://thoughtcoach.org/play">Tips for your first games</a></div></div></section>{f"<div class='grid'>{waiting}</div>" if waiting else ''}"""
    last = rows[-1]
    before = [r for r in rows[:-1] if r["moves"] and r.get("acc") is not None]
    parts = profile.sections(prof, kpis_shown=False) if len(rows) >= 2 else {}
    focus = focus_check(loaded, stories)
    res = {"won": "you won", "lost": "you lost", "draw": "a draw"}[last["outcome"]]
    tabs = [("story", f"&#9654; Your latest story<small>vs {_e(last['opp'])} · {res}</small>",
             hero(last, stories.get(last['id']), (statistics.mean(r["per_move"] for r in before),
                                                  statistics.mean(r["acc"] for r in before)) if before else None)),
            ("form", "Your progress", f"""{form(rows, prof)}{focus}
<section class="chart"><h2>Every game at a glance</h2><p class="cap">Win chance an average move gave away, game by game (lower is better; hover a column for its accuracy)</p>
{_games_chart(rows)}</section>"""),
            ("games", f"Your games<small>{len(rows)}</small>", f"<section><div class='grid'>{cards(rows, statuses)}</div></section>"),
            ("patterns", "Patterns", parts.get("summary", "") + parts.get("charts", "")
             + "".join(f"<section class='profile'><details class='fold big'><summary>{t}</summary>{parts[k]}</details></section>"
                       for k, t in (("questions", "Aagaard's four questions"), ("voice", "Your voice and your play"))
                       if parts.get(k)))]
    tabs = [t for t in tabs if t[2]]
    nav = "".join(f"<button type='button' role='tab' class='tab{' story' if k == 'story' else ''}' data-tab='{k}' "
                  f"aria-selected='{'true' if k == 'story' else 'false'}'>{label}</button>" for k, label, _ in tabs)
    panels = "".join(f"<div class='panel' role='tabpanel' data-panel='{k}'{'' if k == 'story' else ' hidden'}>{html_}</div>"
                     for k, _, html_ in tabs)
    return f"""<nav class="hometabs" role="tablist" aria-label="Your home page">{nav}</nav>
{panels}"""


CSS = bayes.RANGE_CSS + """
.form h2 small { font-weight:400; color:var(--muted); font-size:13px; margin-left:8px; }
details.fold.more { margin-top:10px; }
details.fold.more > summary, details.fold.big > summary { padding:8px 2px; }
details.fold.big { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:6px 14px; margin-top:12px; }
details.fold.big > summary { font-size:13px; }
details.fold.big[open] { padding-bottom:12px; }
details.fold.big .profile h2 { margin-top:8px; }
.panel section + section { margin-top:22px; }
.panel .grid { grid-template-columns:repeat(auto-fill, minmax(200px, 1fr)); gap:8px; }
.panel .gcard { padding:10px 12px; border-radius:12px; }
.gcard .spark { height:36px; } .gcard .spark .area { fill:color-mix(in srgb, var(--glow) 8%, transparent); }
.gcard .spark .ln { stroke-width:1.6; opacity:.85; }
.gcard .gmeta { margin-top:4px; }
@media (max-width:640px) { .panel .grid { grid-template-columns:1fr 1fr; gap:6px; } .panel .gcard { padding:8px 10px; }
  .gcard .gtop { font-size:13px; gap:6px; } .gcard .gdate { display:none; } .gcard .gmeta { font-size:11px; } }
.hometabs { display:flex; gap:8px; align-items:stretch; margin:4px 0 18px; overflow-x:auto; padding:2px 2px 6px;
  scrollbar-width:thin; }
.hometabs .tab { flex:none; font:inherit; font-size:14px; font-weight:600; color:var(--fg2); background:var(--card);
  border:1px solid var(--line); border-radius:999px; padding:8px 16px; cursor:pointer; white-space:nowrap;
  display:flex; align-items:center; gap:8px; }
.hometabs .tab small { font-weight:500; color:var(--muted); font-size:12px; }
.hometabs .tab:hover { color:var(--fg); border-color:var(--fg2); }
.hometabs .tab[aria-selected="true"] { color:var(--fg); border-color:var(--fg); }
.hometabs .tab.story { font-size:15.5px; padding:10px 20px; color:#fff; border:0;
  background:linear-gradient(135deg, #e8590c, #d9480f 55%, #c2255c); box-shadow:0 6px 20px #d9480f40; }
.hometabs .tab.story small { color:#ffffffcc; }
.hometabs .tab.story[aria-selected="true"] { outline:2px solid var(--fg); outline-offset:2px; }
.hometabs .tab:focus-visible { outline:2px solid var(--accent, #2a78d6); outline-offset:2px; }
.panel[hidden] { display:none; }
@media (max-width:640px) { .hometabs { flex-wrap:wrap; overflow:visible; gap:6px; }
  .hometabs .tab { font-size:13px; padding:7px 12px; }
  .hometabs .tab.story { flex:1 1 100%; justify-content:center; font-size:15px; padding:12px 16px; } }

.wrap { max-width:1180px; margin:0 auto; }
.topbar { display:flex; align-items:center; gap:18px; margin:0 0 22px; }
.topbar .brand { font-weight:700; font-size:17px; color:var(--fg); text-decoration:none; display:flex; align-items:center; gap:8px; }
.topbar .brand img { width:24px; height:24px; } .topbar .sp { flex:1; }
.topbar a.nav { color:var(--fg2); text-decoration:none; font-size:14px; } .topbar a.nav:hover { color:var(--fg); }
.topbar .who { color:var(--muted); font-size:14px; }
section h2 { font-size:13px; text-transform:uppercase; letter-spacing:.08em; color:var(--fg2); margin:34px 0 10px; font-weight:600; }
.cap { color:var(--muted); font-size:13px; margin:-4px 0 10px; }

#live-banner a { display:flex; flex-wrap:wrap; align-items:center; gap:10px; margin:0 0 16px; padding:14px 18px; border-radius:14px;
  text-decoration:none; color:var(--fg); background:color-mix(in srgb, var(--accent) 16%, var(--card)); border:1px solid var(--accent); }
#live-banner a span:last-child { margin-left:auto; color:var(--accent); font-weight:700; }
#live-banner .dot { width:10px; height:10px; border-radius:50%; background:var(--accent); animation:pulse-dot .8s infinite alternate; }

/* the latest game */
.hero { --glow:#3987e5; position:relative; overflow:hidden; display:grid; grid-template-columns:minmax(0, 1.35fr) minmax(0, 1fr);
  gap:28px; padding:30px 32px; border-radius:22px; border:1px solid var(--line);
  background:radial-gradient(120% 140% at 0% 0%, color-mix(in srgb, var(--glow) 34%, transparent) 0%, transparent 55%),
             radial-gradient(90% 120% at 100% 100%, color-mix(in srgb, var(--glow) 16%, transparent) 0%, transparent 60%), var(--card); }
.hero.won { --glow:#3fa95c; } .hero.lost { --glow:#e0703a; } .hero.draw { --glow:#8a8fa0; }
.hero.empty { grid-template-columns:1fr; }
.hero .eyebrow { margin:0 0 10px; color:var(--fg2); font-size:12.5px; text-transform:uppercase; letter-spacing:.08em; }
.hero h1 { font-size:clamp(30px, 4.4vw, 50px); line-height:1.05; margin:0 0 12px; letter-spacing:-.02em; }
.hero h1 .verdict { color:var(--glow); }
.hero .headline { font-size:clamp(19px, 2vw, 24px); font-weight:650; margin:0 0 6px; color:var(--fg); max-width:38ch; }
.hero .detail { margin:0 0 12px; color:var(--fg2); font-size:15px; max-width:56ch; }
.hcurve { margin:6px 0 14px; } .hcurve span { display:block; font-size:12px; color:var(--muted); margin-top:2px; }
.spark { width:100%; height:56px; display:block; } .spark.big { height:90px; }
.spark .area { fill:color-mix(in srgb, var(--glow, var(--accent)) 18%, transparent); }
.spark .mid { stroke:var(--muted); stroke-dasharray:3 4; stroke-width:1; vector-effect:non-scaling-stroke; }
.spark .ln { fill:none; stroke:var(--glow, var(--accent)); stroke-width:2.2; vector-effect:non-scaling-stroke; stroke-linejoin:round; }
.chips { display:flex; flex-wrap:wrap; gap:8px; margin:0 0 20px; }
.chip { background:color-mix(in srgb, var(--fg) 6%, transparent); border:1px solid var(--line); border-radius:12px; padding:7px 12px;
  font-size:12.5px; color:var(--fg2); line-height:1.25; }
.chip b { display:block; font-size:20px; color:var(--fg); font-variant-numeric:tabular-nums; }
.chip.up b { color:var(--good); } .chip.down b { color:var(--warn); }
.cta { display:flex; flex-wrap:wrap; gap:10px; }
.btn { display:inline-flex; align-items:center; gap:8px; padding:12px 22px; border-radius:12px; font-weight:700; text-decoration:none; font-size:16px; }
.btn.play { background:var(--glow); color:#fff; box-shadow:0 8px 28px -8px var(--glow); animation:pulse 2.6s ease-in-out 1.4s infinite; }
.btn.play:hover { filter:brightness(1.1); }
.btn.ghost { border:1px solid var(--line); color:var(--fg); background:color-mix(in srgb, var(--fg) 4%, transparent); }
.btn.ghost:hover { border-color:var(--glow); }
.hside { display:flex; flex-direction:column; gap:14px; justify-content:center; }
figure.hfig { margin:0; } .hfig .brd svg { width:100%; max-width:360px; height:auto; display:block; margin:0 auto; border-radius:10px;
  box-shadow:0 18px 40px -18px rgba(0,0,0,.6); }
.hfig figcaption { text-align:center; color:var(--fg2); font-size:12.5px; margin-top:8px; }
.focus { border-radius:14px; padding:14px 16px; background:color-mix(in srgb, var(--glow) 12%, var(--card)); border:1px solid color-mix(in srgb, var(--glow) 40%, transparent); }
.focus .eyebrow { margin:0 0 4px; font-size:11.5px; } .focus h3 { margin:0 0 4px; font-size:17px; } .focus p { margin:0; color:var(--fg2); font-size:14px; }
@keyframes rise { from { opacity:0; transform:translateY(14px); } to { opacity:1; transform:none; } }
@keyframes draw { from { stroke-dashoffset:1; } to { stroke-dashoffset:0; } }
@keyframes pulse { 0%, 100% { box-shadow:0 8px 28px -8px var(--glow); } 50% { box-shadow:0 8px 40px -2px var(--glow); } }
.hero .htext > *, .hero .hside > * { animation:rise .6s cubic-bezier(.2,.7,.2,1) both; }
.hero .htext > :nth-child(2) { animation-delay:.08s; } .hero .htext > :nth-child(3) { animation-delay:.16s; }
.hero .htext > :nth-child(4) { animation-delay:.24s; } .hero .htext > :nth-child(5) { animation-delay:.32s; }
.hero .htext > :nth-child(6) { animation-delay:.40s; } .hero .hside > * { animation-delay:.3s; } .hero .hside > :nth-child(2) { animation-delay:.45s; }
.spark.big .ln { stroke-dasharray:1; animation:draw 1.6s ease-out .4s both; }

/* your form */
.focuscheck { margin:26px 0 8px; } .focuscheck h2 { margin:0 0 4px; }
.fgrid { display:grid; grid-template-columns:repeat(auto-fit, minmax(min(100%, 300px), 1fr)); gap:12px; margin-top:10px; }
.fcard { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:14px 16px; }
.fcard .ftop { display:flex; justify-content:space-between; gap:10px; align-items:baseline; margin:0 0 8px; }
.fbadge { font-size:11.5px; font-weight:700; padding:2px 9px; border-radius:20px; background:var(--soft, rgba(127,127,127,.15)); white-space:nowrap; }
.fcard.up .fbadge { background:color-mix(in srgb, #2f9e44 30%, transparent); }
.fcard.down .fbadge { background:color-mix(in srgb, #e03131 26%, transparent); }
.fmeter { display:block; height:6px; border-radius:3px; background:var(--soft, rgba(127,127,127,.15)); overflow:hidden; margin:0 0 8px; }
.fmeter span { display:block; height:100%; background:var(--fg2); } .fcard.up .fmeter span { background:#2f9e44; }
.fcard.down .fmeter span { background:#e03131; }
.fcard.lean .fbadge { background:color-mix(in srgb, #2f9e44 15%, transparent); }
.fcard.slip .fbadge { background:color-mix(in srgb, #e03131 13%, transparent); }
.fwhat { margin:0 0 8px; font-size:12.5px; color:var(--muted); }
.fbar { display:grid; grid-template-columns:52px 1fr 96px; gap:8px; align-items:center; margin:4px 0; font-size:12.5px; }
.fbar .fl { color:var(--fg2); } .fbar .fv { text-align:right; } .fbar .fv small { color:var(--muted); }
.ftrk { height:8px; border-radius:4px; background:var(--line); overflow:hidden; display:block; }
.ftrk i { display:block; height:100%; border-radius:4px; background:var(--fg2); opacity:.55; }
.ftrk i.b1 { opacity:1; } .ftrk i.good { background:#2f9e44; }
.ftext { margin:0; font-size:13.5px; color:var(--fg2); }
.tiles { display:grid; grid-template-columns:repeat(auto-fit, minmax(122px, 1fr)); gap:8px; }
.tile { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:12px 14px; display:flex; flex-direction:column; gap:2px;
  animation:rise .5s ease-out both; }
.tile .lab { color:var(--fg2); font-size:12px; line-height:1.3; } .tile b { font-size:24px; font-variant-numeric:tabular-nums; line-height:1.15; }
.tile b small { font-size:16px; color:var(--fg2); font-weight:600; } .tile .delta { font-size:12px; color:var(--muted); }
.tile.up .delta { color:var(--good); } .tile.down .delta { color:var(--warn); }
.tile .hint { font-size:11.5px; color:var(--muted); line-height:1.3; }
svg.trend { width:100%; height:30px; margin-top:4px; } .trend polyline { fill:none; stroke:var(--accent); stroke-width:2; }
.trend circle { fill:var(--accent); stroke:var(--card); stroke-width:2; }

/* every game */
.chart { } .chartwrap { position:relative; background:var(--card); border:1px solid var(--line); border-radius:14px; padding:10px 8px 2px; }
svg.games { width:100%; height:clamp(120px, 22vh, 200px); display:block; }
svg.games line, svg.games polyline { vector-effect:non-scaling-stroke; } .games .ax { fill:var(--muted); font-size:11px; }
.games .grid { stroke:var(--line); } .games .base { stroke:var(--muted); }
.games .bar { fill:var(--s1); } .games .lost .bar { fill:var(--s2); } .games .draw .bar { fill:var(--bar-tt); }
.games .hit { fill:transparent; } .games .col:hover .bar { filter:brightness(1.25); }
.games .avg { fill:none; stroke:var(--fg); stroke-width:2; stroke-linejoin:round; opacity:.85; }
.tip { position:absolute; pointer-events:none; background:var(--bg); border:1px solid var(--line); border-radius:8px; padding:6px 9px;
  font-size:12.5px; max-width:280px; box-shadow:0 8px 20px -10px rgba(0,0,0,.5); }
.legend { display:flex; flex-wrap:wrap; gap:14px; font-size:12.5px; color:var(--fg2); margin:8px 2px 0; align-items:center; }
.legend .sw { display:inline-block; width:11px; height:11px; border-radius:3px; margin-right:5px; vertical-align:-1px; }
.sw.won { background:var(--s1); } .sw.lost { background:var(--s2); } .sw.draw { background:var(--bar-tt); }
.sw.avg { background:var(--fg); height:3px; border-radius:2px; vertical-align:3px; } .legend .muted { color:var(--muted); }
.grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(230px, 1fr)); gap:10px; }
.gcard { --glow:var(--s1); position:relative; display:block; background:var(--card); border:1px solid var(--line); border-radius:14px;
  padding:12px 14px; color:var(--fg); text-decoration:none; transition:transform .15s, border-color .15s; }
.gcard:hover { transform:translateY(-2px); border-color:var(--glow); }
.gcard.lost { --glow:var(--s2); } .gcard.draw { --glow:var(--bar-tt); }
.gtop { display:flex; align-items:center; gap:8px; margin:0 0 6px; font-size:14px; }
.gtop b { flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; } .gdate { color:var(--muted); font-size:12px; }
.res { width:24px; height:24px; border-radius:7px; display:grid; place-items:center; font-weight:800; font-size:12px; color:#fff; background:var(--glow); }
.gmeta { margin:6px 0 0; font-size:12px; color:var(--fg2); }
.gtype { display:inline-block; margin:4px 0 2px; padding:2px 9px; border-radius:20px; font-size:11.5px; font-weight:700;
  letter-spacing:.02em; background:var(--soft, rgba(127,127,127,.15)); color:var(--fg); }
.gtype.big { font-size:14px; padding:4px 12px; margin:0 0 12px; }
.gtype.big small { font-weight:400; color:var(--fg2); margin-left:8px; font-size:13px; }
.gtype.swindle, .gtype.stomp { background:color-mix(in srgb, #2f9e44 28%, transparent); }
.gtype.swindled, .gtype.stomped { background:color-mix(in srgb, #e03131 26%, transparent); }
.gtype.crazy { background:color-mix(in srgb, #f08c00 30%, transparent); }
.gplay { display:block; width:max-content; margin:6px 0 0 auto; font-size:12px; font-weight:700; color:var(--glow); }  /* its own line: never on top of the meta */
.gcard.pending .res { background:var(--muted); } .gcard .wait { color:var(--fg2); font-size:13px; margin:8px 0 0; }
.gcard.running .wait::after { content:""; display:inline-block; width:8px; height:8px; margin-left:8px; border-radius:50%;
  background:var(--accent); animation:pulse-dot 1s infinite alternate; }
@keyframes pulse-dot { from { opacity:.3; } to { opacity:1; } }
@media (max-width:600px) { .topbar { flex-wrap:wrap; gap:6px 14px; } .topbar .who { display:none; }
  .topbar .brand { white-space:nowrap; } .topbar .sp { flex-basis:100%; height:0; } }
@media (max-width:860px) { .hero { grid-template-columns:1fr; padding:22px 18px; } .hfig .brd svg { max-width:300px; } }
@media (prefers-reduced-motion: reduce) { .hero *, .tile, .btn.play, .spark.big .ln { animation:none !important; } }
"""

JS = """
(() => {
  // Home page tabs: one section at a time; the address remembers which (#games...).
  const tabs = [...document.querySelectorAll('.hometabs .tab')], panels = [...document.querySelectorAll('.panel')];
  const open = (k, push) => {
    if (!tabs.some(t => t.dataset.tab === k)) k = 'story';
    tabs.forEach(t => t.setAttribute('aria-selected', String(t.dataset.tab === k)));
    panels.forEach(p => { p.hidden = p.dataset.panel !== k; });
    if (push) history.replaceState(null, '', k === 'story' ? location.pathname : '#' + k);
  };
  tabs.forEach(t => t.addEventListener('click', () => open(t.dataset.tab, true)));
  document.querySelector('.hometabs')?.addEventListener('keydown', (e) => {
    const i = tabs.findIndex(t => t.getAttribute('aria-selected') === 'true');
    const j = e.key === 'ArrowRight' ? i + 1 : e.key === 'ArrowLeft' ? i - 1 : null;
    if (j === null || !tabs[(j + tabs.length) % tabs.length]) return;
    const t = tabs[(j + tabs.length) % tabs.length]; t.focus(); open(t.dataset.tab, true);
  });
  if (tabs.length) open(location.hash.slice(1) || 'story', false);
  // Chart tooltips, and the "story" shortcut inside each game card.
  document.querySelectorAll('.chartwrap').forEach((wrap) => {
    const tip = wrap.querySelector('.tip');
    wrap.querySelectorAll('[data-tip]').forEach((el) => {
      el.addEventListener('mousemove', (e) => {
        const r = wrap.getBoundingClientRect();
        tip.hidden = false; tip.textContent = el.dataset.tip;
        const x = Math.min(e.clientX - r.left + 12, r.width - tip.offsetWidth - 6);
        tip.style.left = Math.max(6, x) + 'px'; tip.style.top = (e.clientY - r.top - tip.offsetHeight - 10) + 'px';
      });
      el.addEventListener('mouseleave', () => { tip.hidden = true; });
    });
  });
  document.querySelectorAll('.gplay').forEach((s) => s.addEventListener('click', (e) => {
    e.preventDefault(); e.stopPropagation(); location.href = s.dataset.href;
  }));
  // Hosted: a review running right now gets a banner to its live page.
  const banner = document.getElementById('live-banner');
  if (banner) {
    const u = banner.dataset.user;
    fetch(`/api/u/${u}/active`, { cache: 'no-store' }).then((r) => r.json()).then(({ active }) => {
      if (!active || !active.length) return;
      const a = active[0], f = a.facts || {};
      const esc = (x) => String(x).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
      banner.innerHTML = `<a href="/live?u=${u}&g=${a.game_id}"><span class="dot"></span><b>Your game${f.opp ? ' vs ' + esc(f.opp) : ''} is being reviewed right now.</b><span>Watch it come together &rarr;</span></a>`;
      banner.hidden = false;
    }).catch(() => {});
  }
  // Count the hero's numbers up once.
  if (!matchMedia('(prefers-reduced-motion: reduce)').matches) {
    document.querySelectorAll('[data-count]').forEach((el) => {
      const m = el.textContent.match(/^([0-9.]+)(%?)$/); if (!m) return;
      const end = parseFloat(m[1]), dec = (m[1].split('.')[1] || '').length, t0 = performance.now() + 300;
      const step = (t) => { const k = Math.min(1, Math.max(0, (t - t0) / 900)), v = end * (1 - Math.pow(1 - k, 3));
        el.textContent = v.toFixed(dec) + m[2]; if (k < 1) requestAnimationFrame(step); };
      el.textContent = (0).toFixed(dec) + m[2]; requestAnimationFrame(step);
    });
  }
})();
"""

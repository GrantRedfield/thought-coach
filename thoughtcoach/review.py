"""The game review page: one synchronized board and a coaching workspace around it.

  summary     the coaching summary (coach.py): one focus for the next game, what went well, the decisions
              that mattered, and the headline numbers with their denominators
  workspace   one board for the whole page (left on desktop, sticky): move navigation, critical moments, the
              evaluation, arrows grouped by category; and beside it what the review says about the selected
              position: coaching, recorded thinking, engine analysis, the move list
  report      the Thinking Report: the review's findings by subject, every row opening its position on the board
  analysis    deeper material: the game chart, time, the questions with their positions, how the numbers are made

Every element that refers to a position sets the same board (data-pos). Everything is read from the gap report,
the engine analysis and the recording: no engine work and no judgement here beyond what those files hold.
"""

from __future__ import annotations

import html
import json
import re

import chess
import chess.svg

from . import coach, questions, sharing, timing
from .critical import pick
from .dashboard import CSS as DASH_CSS, JS as DASH_JS, SHORT, game_chart, time_card
from .fmt import THEME_BUTTON, THEME_HEAD, game_url, home_url, pct

CATS = [  # key, label, colour, dash, on by default
    ("cand", "Your candidates", "#15781B", "", True),
    ("idea", "Their ideas you noted", "#e8590c", "7 5", True),
    ("best", "Engine's best move", "#3987e5", "", True),
    ("reply", "Their best reply", "#c92a2a", "2 4", True),
    ("threat", "Their threat before your move", "#a61e4d", "1 5", False),
]


def _e(x) -> str:
    return html.escape(str(x), quote=True)


def _sans(line: str) -> list[str]:
    return [w for w in (re.sub(r"^\d+\.+", "", w) for w in (line or "").split()) if w]


def _line(fen: str, line_san: str, limit: int = 10) -> list[dict]:
    """A variation as positions: [{"san", "fen", "uci"}], from the position before the first move."""
    board, out = chess.Board(fen), []
    for san in _sans(line_san)[:limit]:
        try:
            mv = board.parse_san(san)
        except ValueError:
            break
        label = (f"{board.fullmove_number}." if board.turn == chess.WHITE else f"{board.fullmove_number}...") + san
        board.push(mv)
        out.append({"san": label, "fen": board.fen(), "uci": mv.uci()})
    return out


def _said(window: dict | None, tone: dict | None) -> list[str]:
    out = []
    for s in (window or {}).get("shapes", []):
        u = (s.get("utterance") or "").strip()
        if u and u not in out:
            out.append(u)
    q = (tone or {}).get("quote")
    if q and q not in out:
        out.insert(0, q)
    return out[:6]


def _shapes(window: dict | None, kind: str) -> list[list[str]]:
    out = []
    for s in (window or {}).get("shapes", []):
        if s.get("kind") == kind and s.get("from") and s.get("to") and [s["from"], s["to"]] not in out:
            out.append([s["from"], s["to"]])
    return out


def _circles(window: dict | None) -> list[str]:
    out = []
    for s in (window or {}).get("shapes", []):
        if s.get("kind") in ("key_square", "worst_piece") and s.get("from") and s["from"] not in out:
            out.append(s["from"])
    return out


def decision(m: dict, rec: dict, window: dict | None, tone: dict | None, white: bool, crit: dict | None) -> dict:
    """Everything the page shows for one of your moves (the position before it)."""
    label = f"{m['move_number']}{'.' if white else '...'} {m['move_san']}"
    # the moves that matter most first: good ones and the move played, then the most drawn (the board shows the top few)
    legal = [d for d in m.get("drawn_candidates") or [] if d.get("legal") and d.get("move_uci")]
    legal.sort(key=lambda d: (not d.get("engine_candidate"), not d.get("played"), -(d.get("times_drawn") or 1)))
    cands = [[d["move_uci"][:2], d["move_uci"][2:4]] for d in legal]
    engine = [{"san": c["move_san"], "wp": c.get("wp"), "line": _line(m["fen"], c.get("line_san") or c["move_san"])}
              for c in (rec.get("candidates") or [])[:3]]
    best_uci = (rec.get("candidates") or [{}])[0].get("move_uci")
    played = rec.get("move_uci") or ""
    reply = m.get("reply") or {}
    threat = (rec.get("threat") or {})
    q3 = m.get("q3") or {}
    drawn_sans = {d["move_san"] for d in m.get("drawn_candidates") or [] if d.get("legal")}
    loss = m.get("wp_loss") or 0
    # The coaching for this position, from what was recorded and what the engine found.
    lines = []
    went = f"Win chance {pct(m['wp_before'])} → {pct(m['wp_after'])} (engine estimate)."
    if m.get("mistake"):
        lines.append(("what", f"{label} cost {pct(loss)} of your win chance. {went}"))
        if m.get("gap") == "time_trouble":
            lines.append(("why", "Played in time trouble: the review doesn't count this against your thinking."))
        if m["best_move_san"] in drawn_sans:
            lines.append(("good", f"The engine's best move, {m['best_move_san']}, was among your candidates."))
        elif m.get("best_move_san"):
            lines.append(("gap", f"The engine's best move, {m['best_move_san']}, isn't in your recorded thinking."))
        if reply.get("san"):
            lines.append(("why", f"Their best reply was {reply['san']}: " + (
                "it is in your recorded thinking, and the move you chose didn't answer it." if reply.get("recorded")
                else "it isn't in your recorded thinking (you may still have seen it).")))
        lesson = {"wrongly_rejected": coach.FOCUS["keep_best"][1], "opponent_idea": coach.FOCUS["their_idea"][1],
                  "missed_candidate": coach.FOCUS["widen"][1], "no_thoughts": coach.FOCUS["widen"][1],
                  "time_trouble": coach.FOCUS["clock"][1]}.get(m.get("gap"), coach.FOCUS["test_reply"][1])
        if reply.get("recorded"):
            lesson = coach.FOCUS["test_reply"][1]
        lines.append(("lesson", lesson))
    elif crit and crit.get("critical") and not crit.get("obvious"):
        lines.append(("good", f"Only one move held here, {crit['best']}, and you found it. {went}"))
    else:
        lines.append(("what", f"{label} held. {went}"))
        if m["best_move_san"] in drawn_sans and m["move_san"] == m["best_move_san"]:
            lines.append(("good", "The engine's best move: you drew it and played it."))
    if q3.get("status") in ("spotted", "handled", "not_drawn"):
        rec_txt = "is in your recorded thinking" if q3["status"] == "spotted" else "isn't in your recorded thinking"
        res_txt = "your move dealt with it" if (q3["status"] == "handled" or not m.get("mistake")) else "your move didn't hold against it"
        lines.append(("threat", f"Their threat before your move, {q3['threat_san']}, {rec_txt}; {res_txt}."))
    return {
        "ply": m["ply"], "label": label, "played": played, "best": best_uci, "cands": cands,
        "ideas": _shapes(window, "opponent_idea"), "circles": _circles(window),
        "threat": threat.get("move_uci") if q3.get("status") in ("spotted", "handled", "not_drawn") else None,
        "threat_san": q3.get("threat_san"), "reply": reply or None, "said": _said(window, tone),
        "drawn": [d["move_san"] for d in m.get("drawn_candidates") or [] if d.get("legal")],
        "noted": m.get("drawn_opponent_ideas") or [],
        "wp_before": m["wp_before"], "wp_after": m["wp_after"], "loss": loss, "costly": bool(m.get("mistake")),
        "gap": SHORT.get(m.get("gap"), "") if m.get("mistake") else "",
        "time": m.get("time_spent_pgn"), "engine": engine,
        "played_line": _line(m["fen"], rec.get("played_line_san") or ""),
        "coach": lines,
    }


def _positions(game, analysis: dict) -> tuple[list[str], list[str], list[str]]:
    """FENs after 0..N plies, the SAN labels and the UCI moves of the whole game."""
    if game is not None:
        board = game.board()
        fens, sans, ucis = [board.fen()], [], []
        for mv in game.mainline_moves():
            sans.append((f"{board.fullmove_number}." if board.turn == chess.WHITE else f"{board.fullmove_number}...") + board.san(mv))
            ucis.append(mv.uci())
            board.push(mv)
            fens.append(board.fen())
        return fens, sans, ucis
    # (no game record: the positions the analysis has, in order)
    recs = [r for r in analysis.get("moves", []) if r.get("fen") and r.get("move_uci")]
    fens, sans, ucis = [], [], []
    for r in recs:
        b = chess.Board(r["fen"])
        fens.append(r["fen"]); sans.append(f"{r['move_number']}{'.' if b.turn else '...'}{r['move_san']}"); ucis.append(r["move_uci"])
    if recs:
        b = chess.Board(recs[-1]["fen"]); b.push_uci(recs[-1]["move_uci"]); fens.append(b.fen())
    return fens, sans, ucis


def _stats_html(stats: list[dict]) -> str:
    return "".join(
        f"<div class='st' title='{_e(s.get('tip', ''))}'><b>{_e(s['value'])}</b><span>{_e(s['label'])}"
        + ("<em>small sample</em>" if s.get("small") else "") + "</span></div>" for s in stats)


def _report_section(key: str, title: str, stat: str, rows: list[tuple[int | None, str]], intro: str = "", open_: bool = False) -> str:
    body = (f"<p class='intro'>{intro}</p>" if intro else "") + ("".join(
        f"<button type='button' class='row' data-pos='{p}'>{t}</button>" if p is not None else f"<p class='row'>{t}</p>"
        for p, t in rows) if rows else "<p class='none'>Nothing to show for this game.</p>")
    return (f"<details class='rs' id='r-{key}'{' open' if open_ else ''}><summary><span class='rt'>{_e(title)}</span>"
            f"<span class='rv'>{stat}</span></summary><div class='rb'>{body}</div></details>")


def thinking_report(report: dict, analysis: dict, decisions: dict, white: bool) -> str:
    s, moves = report["summary"], report["moves"]
    lab = lambda m: f"{m['move_number']}{'.' if white else '...'} {m['move_san']}"  # noqa: E731
    out = []
    # Their threats: recognition and resolution, apart.
    real = [m for m in moves if (m.get("q3") or {}).get("status") in ("spotted", "handled", "not_drawn")]
    rec_n = sum(1 for m in real if m["q3"]["status"] == "spotted")
    held = coach._held(report)
    rows = []
    for m in real:
        st = m["q3"]["status"]
        rows.append((m["ply"] - 1, f"<b>{_e(lab(m))}</b> threat {_e(m['q3']['threat_san'])}: "
                     + ("in your recorded thinking" if st == "spotted" else "not in your recorded thinking")
                     + "; " + ("your move dealt with it" if st == "handled" or not m.get("mistake") else "your move didn't hold")))
    out.append(_report_section("threats", "Opponent threats", f"recorded {coach.ratio(rec_n, len(real))} · held {coach.ratio(held, len(real))}",
                               rows, "Two separate questions: was their threat in your arrows or words, and did the move you played deal with it?"))
    # Candidates.
    k, n = s.get("q4_best_considered", 0), s.get("q4_positions", 0)
    rej = [m for m in moves if m.get("mistake") and m.get("gap") == "wrongly_rejected"]
    rows = [(m["ply"] - 1, f"<b>{_e(lab(m))}</b>: the engine's best ({_e(', '.join(m['q4']['wrongly_rejected'][:2]))}) was among your "
                           f"candidates, and the move played cost {pct(m['wp_loss'])}") for m in rej]
    rows += [(m["ply"] - 1, f"<b>{_e(lab(m))}</b>: best move {_e(m['best_move_san'])}, among your {len(decisions[m['ply'] - 1]['drawn'])} candidates")
             for m in moves if m["ply"] - 1 in decisions and m["best_move_san"] in decisions[m["ply"] - 1]["drawn"]
             and not m.get("mistake")][:4]
    out.append(_report_section("cands", "Candidate moves", f"best among yours {coach.ratio(k, n)}" + (f" · rejected {len(rej)}" if rej else ""), rows))
    # Weaknesses and worst pieces (Aagaard's questions 2 and 4).
    w_rows, p_rows = [], []
    for m in moves:
        for w in (m.get("q1") or {}).get("weaknesses") or []:
            w_rows.append((m["ply"] - 1, f"<b>{_e(lab(m))}</b>: {_e(w.get('kind', 'weakness'))} on {_e(w.get('square'))} ({_e(w.get('side', ''))})"
                           + (" · in your recorded thinking" if w.get("you") else " · not in your recorded thinking")))
        wp = (m.get("q2") or {}).get("worst")
        if wp:
            p_rows.append((m["ply"] - 1, f"<b>{_e(lab(m))}</b>: your {_e(wp.get('piece'))} on {_e(wp.get('square'))} reached {pct(wp.get('activity') or 0)} "
                           f"of its squares; the engine's way: {_e(wp.get('engine_line', ''))}"))
    out.append(_report_section("weak", "Positional weaknesses",
                               coach.ratio(s.get("q1_found", 0), s.get("q1_weaknesses", 0)) + " recorded" if s.get("q1_weaknesses") else "none found",
                               w_rows[:6]))
    out.append(_report_section("pieces", "Worst-placed pieces",
                               coach.ratio(s.get("q2_found", 0), s.get("q2_positions", 0)) + " recorded" if s.get("q2_positions") else "none found",
                               p_rows[:6]))
    # Psychology: what was said, set against the engine (insights.py), each at its move.
    ins = [i for i in report.get("insights") or [] if i.get("kind") in ("reaction", "rosy", "gloomy", "gave_up", "inner", "hindsight", "instinct", "read_plan", "planned", "right_piece")]
    rows = [(i["ply"] - 1 if i.get("ply") else None, f"<b>{_e(i['head'])}</b> {_e(i['body'])}") for i in ins]
    out.append(_report_section("mind", "Psychology and confidence", f"{len(ins)} observation{'s' * (len(ins) != 1)}" if ins else "none",
                               rows, "Read from what you said and drew, set against the engine. A mood word is evidence, not a diagnosis."))
    # Time.
    t = timing.game(analysis, report) if analysis.get("moves") else None
    rows = []
    if t:
        for p in t["rushed"][:3]:
            rows.append((p["ply"] - 1, f"<b>{_e(timing.label(p, white))}</b> {p['time']:.0f} s: only {_e(p['best'])} held"))
        for p in t["lingered"][:2]:
            rows.append((p["ply"] - 1, f"<b>{_e(timing.label(p, white))}</b> {p['time']:.0f} s with {p['good']} moves about equally good"))
    stat = (f"{t['crit_median']:.0f} s on critical moves ({t['critical']}) · {t['other_median']:.0f} s elsewhere" if t else "not enough data")
    out.append(_report_section("time", "Time management", stat, rows, _e(timing.sentence(t)) if t else ""))
    # Critical decisions.
    chosen = pick(report, analysis, 4) if analysis.get("moves") else []
    rows = [(c["move"]["ply"] - 1, f"<b>{_e(lab(c['move']))}</b> " + (f"{pct(c['move']['wp_before'])} → {pct(c['move']['wp_after'])}"
            + (f" · {_e(SHORT.get(c['move']['gap'], ''))}" if c["move"].get("gap") else "") if c["why"] == "costly" else "only one move held"))
            for c in chosen]
    out.append(_report_section("critical", "Critical decisions", f"{len(chosen)}", rows))
    return "".join(out)


METHOD = """<p><b>Win chance</b> is an engine estimate (Stockfish evaluation, mapped to the chance of winning the way lichess
does), from your side. A change of 40% → 8% is 32 percentage points.</p>
<p><b>Costly</b> means a move that lowered your win chance by 5 points or more. Moves played with under 30 seconds on the clock are put
down to time trouble and not counted against your thinking.</p>
<p><b>Recorded thinking</b> is what you drew (arrows, circles) and said, tied to the move you were on. A move or threat that isn't in it
may still have been in your head: the review says "not in your recorded thinking", never "you missed it".</p>
<p><b>Their threat</b> is the move they would play if it were their turn before your move. <b>Their best reply</b> is the engine's answer
to the move you actually played. They are different: a costly move is put down to a threat only when the threat is their best reply.</p>
<p><b>Small sample</b>: rates over fewer than 5 cases are shown as counts, not as percentages to trust.</p>"""


def render(report: dict, analysis: dict, thinking: dict | None, n: int = 3, line_for=None, game=None, player: str | None = None) -> str:
    """`player`: whose game this is, when it isn't the reader's own (the public sample): the page says so up front."""
    g, s = report["game"], report["summary"]
    white = g["my_color"] == "white"
    windows = {w["ply"]: w for w in (thinking or {}).get("windows", [])}
    recs = {r["ply"]: r for r in analysis.get("moves", []) if r.get("ply") is not None}
    tone = {t["ply"]: t for t in (report.get("tone") or {}).get("moves", [])}
    crit = {p["ply"]: p for p in timing.positions(analysis, report)} if analysis.get("moves") else {}
    fens, sans, ucis = _positions(game, analysis)
    decisions = {}
    for m in report["moves"]:
        rec = recs.get(m["ply"]) or {}
        if m.get("fen"):
            decisions[m["ply"] - 1] = decision(m, rec, windows.get(m["ply"]), tone.get(m["ply"]), white, crit.get(m["ply"]))
    # your win chance at each position (from your side), carried forward where a position wasn't reviewed
    wp, last = [], 0.5
    after = {m["ply"]: m["wp_after"] for m in report["moves"]}
    before = {m["ply"] - 1: m["wp_before"] for m in report["moves"]}
    for i in range(len(fens)):
        last = before.get(i, after.get(i, last))
        wp.append(round(last, 3))
    summ = coach.summary(report, analysis)
    chosen = pick(report, analysis, n) if analysis.get("moves") else []
    moments = [{"pos": c["move"]["ply"] - 1, "label": f"{c['move']['move_number']}{'.' if white else '...'} {c['move']['move_san']}",
                "costly": c["why"] == "costly"} for c in chosen]
    focus_pos = (summ["focus"]["start"] - 1) if summ["focus"].get("start") else (moments[0]["pos"] if moments else 0)
    T = coach.third if player else (lambda x: x)  # someone else's game: "he", "his"
    if player:
        summ["focus"]["why"] = T(summ["focus"]["why"])
        for w in summ["well"]:
            w["text"] = T(w["text"])
        for st_ in summ["stats"]:
            st_["label"], st_["tip"] = T(st_["label"]), T(st_.get("tip", ""))
        for d_ in decisions.values():
            d_["coach"] = [(k, t if k == "lesson" else T(t)) for k, t in d_["coach"]]
    data = {"fens": fens, "sans": sans, "ucis": ucis, "wp": wp, "white": white, "dec": decisions, "moments": moments, "third": bool(player),
            "start": focus_pos, "cats": [{"key": k, "label": lb, "color": c, "dash": d, "on": on} for k, lb, c, d, on in CATS]}
    blob = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    pieces = "".join(chess.svg.PIECES[sym].replace('id="', 'id="p-', 1) for sym in "PNBRQKpnbrqk")

    me, them = (g["white"], g["black"]) if white else (g["black"], g["white"])
    res = g.get("result", "*")
    outcome = "Draw" if res == "1/2-1/2" else ("Win" if (res == "1-0") == white else "Loss") if res in ("1-0", "0-1") else "Unfinished"
    f = summ["focus"]
    well = "".join(f"<li>{_e(w['text'])}{' <em>small sample</em>' if w.get('small') else ''}</li>" for w in summ["well"])
    dec = "".join(f"<button type='button' class='mchip {d['kind']}' data-pos='{d['ply'] - 1}' data-cats='cand,idea,best,reply'>"
                  f"<b>{_e(d['label'])}</b><small>{_e(d['text'])}</small></button>" for d in summ["decisions"])
    mom = "".join(f"<button type='button' class='mchip {'costly' if m['costly'] else 'good'}' data-pos='{m['pos']}'>"
                  f"<span class='n'>{i + 1}</span>{_e(m['label'])}</button>" for i, m in enumerate(moments))
    legend = "".join(
        f"<label class='cat'><input type='checkbox' data-cat='{k}'{' checked' if on else ''}><svg viewBox='0 0 28 10' aria-hidden='true'>"
        f"<line x1='1' y1='5' x2='27' y2='5' stroke='{c}' stroke-width='3' stroke-dasharray='{d}'/></svg>{_e(lb)}</label>"
        for k, lb, c, d, on in CATS)
    sample = f"<p class='note'><b>This is {_e(player)}'s game</b>, shared with his permission.</p>" if player else ""
    voice_off = sample + ("<p class='note'>Voice was off this game: the thinking here comes from your arrows and circles only.</p>"
                 if report.get("voice") == "off" else "")
    chart = game_chart(report, analysis, [c["move"]["ply"] for c in chosen], "." if white else "...") if analysis.get("moves") else ""
    aagaard = questions.render(questions.compute([{"id": g["id"], "report": report}], examples=6),
                               title="The four questions, position by position", link="lichess",
                               sub="Every position where the engine found something to see, and whether it is in your recorded thinking.")
    story_url = game_url(g["id"], story=True)

    return f"""<!doctype html>
<html lang="en" data-theme="dark"><head><meta charset="utf-8"><meta name="robots" content="noindex"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Thought Coach · {_e(g['white'])} vs {_e(g['black'])}</title>{THEME_HEAD}<style>{DASH_CSS}{questions.CSS}{sharing.CSS}{CSS}</style></head><body>{THEME_BUTTON}
<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs>{pieces}
{''.join(f"<marker id='ah-{k}' viewBox='0 0 10 10' refX='5' refY='5' markerWidth='3.2' markerHeight='3.2' orient='auto-start-reverse'><path d='M0 0 10 5 0 10z' fill='{c}'/></marker>" for k, _, c, _, _ in CATS)}
<marker id='ah-var' viewBox='0 0 10 10' refX='5' refY='5' markerWidth='3.2' markerHeight='3.2' orient='auto'><path d='M0 0 10 5 0 10z' fill='#9c36b5'/></marker></defs></svg>
<div class="review">
  <header class="top">
    <a class="homelink" href="{_e(home_url())}">{'&larr; About this sample' if player else '&larr; All your games'}</a>
    <div class="gi"><h1>{_e(g['white'])} <span>vs</span> {_e(g['black'])}</h1>
      <p>{outcome} ({_e(res)}) · {_e(coach.human_tc(g.get('time_control')))} · {'he' if player else 'you'} played {_e(g['my_color'])} · {s['moves_reviewed']} moves reviewed,
        {s['moves_with_thoughts']} with recorded thinking</p></div>
    {'' if player else sharing.BUTTON}<nav class="modes" aria-label="Review modes"><a class="mode story" href="{_e(story_url)}">&#9654; Game Story</a>
      <a class="mode" href="#report">Thinking Report</a><a class="mode" href="#workspace">Deep Analysis</a></nav>
  </header>
  {voice_off}
  <section class="summary" aria-label="Your coaching summary">
    <div class="focus"><p class="k">{T('Your focus for the next game')}</p><h2>{_e(f['title'])}</h2><p>{_e(f['why'])}</p>
      <p class="habit"><b>Practice habit:</b> {_e(f['habit'])}</p></div>
    <div class="side">
      {f"<div class='well'><p class='k'>What went well</p><ul>{well}</ul></div>" if well else ""}
      <div class="stats">{_stats_html(summ['stats'])}</div>
      {f"<div class='dec'><p class='k'>Decisions that mattered</p><div class='chips'>{dec}</div></div>" if dec else ""}
    </div>
  </section>
  <a class="storybtn" href="{_e(story_url)}">&#9654; {'Start with his Game Story' if player else 'Start with your Game Story'} <small>a 2-minute guided lesson</small></a>

  <section class="ws" id="workspace" aria-label="The board and the selected position">
    <div class="boardcol">
      {f"<div class='moments'><span class='k'>Critical moments</span>{mom}</div>" if mom else ""}
      <div class="pl them"><span class="dotc {'b' if white else 'w'}"></span>{_e(them)}</div>
      <div class="boardwrap"><div class="evalbar" aria-hidden="true"><i></i><span></span></div>
        <svg class="board" viewBox="0 0 360 360" role="img" aria-label="Chess board"></svg></div>
      <div class="pl me"><span class="dotc {'w' if white else 'b'}"></span>{_e(me)}</div>
      <div class="varbar" hidden><span class="vt"></span><button type="button" class="vback">&#8617; Back to the game</button></div>
      <div class="nav" role="group" aria-label="Move navigation">
        <button type="button" data-go="first" aria-label="First position">&#9198;</button><button type="button" data-go="prev" aria-label="Previous move">&#9664;</button>
        <span class="mv" aria-live="polite"></span>
        <button type="button" data-go="next" aria-label="Next move">&#9654;</button><button type="button" data-go="last" aria-label="Last position">&#9197;</button>
        <button type="button" data-go="flip" aria-label="Flip the board">&#8645;</button></div>
      <details class="cats"><summary>Arrows on the board</summary><div class="legend">{legend}</div>
        <label class="cat all"><input type="checkbox" data-all> Show every recorded arrow (the board shows the most relevant few)</label>
        <p class="hint">Line style tells them apart as well as colour. Purple shows an engine variation you're exploring.</p></details>
    </div>
    <div class="ctxcol">
      <div class="ctxhead"><h2 class="cl"></h2><p class="cw"></p></div>
      <details class="ctx" open><summary>Coaching</summary><div class="c-coach"></div></details>
      <details class="ctx" open><summary>Recorded thinking</summary><div class="c-think"></div></details>
      <details class="ctx"><summary>Engine analysis</summary><div class="c-engine"></div></details>
      <details class="ctx"><summary>Move history</summary><div class="c-moves"></div></details>
    </div>
  </section>

  <section class="report" id="report"><h2>Thinking Report</h2>
    <p class="sub">{T('Your thinking through this game, by subject.')} Each row opens its position on the board.</p>
    {T(thinking_report(report, analysis, decisions, white))}
  </section>

  <section class="deep" id="analysis"><h2>Deep Analysis</h2>
    <details class="rs" open><summary><span class="rt">The game, move by move</span><span class="rv">win chance, clocks, think time</span></summary>
      <div class="rb chartbox">{chart}</div></details>
    <details class="rs"><summary><span class="rt">Time where it mattered</span><span class="rv">median think time</span></summary><div class="rb">{time_card(report, analysis) if analysis.get("moves") else ""}</div></details>
    <details class="rs"><summary><span class="rt">The four questions, position by position</span><span class="rv">boards from this game</span></summary><div class="rb">{aagaard}</div></details>
    <details class="rs"><summary><span class="rt">How the numbers are made</span><span class="rv">methodology</span></summary><div class="rb method">{METHOD}</div></details>
  </section>
  <section class="practice"><p class="k">{'What to take from his game' if player else 'Before your next game'}</p><p><b>{_e(f['title'])}.</b> {_e(f['habit'])}</p></section>
</div>
{'' if player else sharing.PANEL}<div id="tip"></div>
<script type="application/json" id="rvdata">{blob}</script>
<script>{DASH_JS}</script><script>{JS}</script>{'' if player else f"<script>{sharing.JS}</script>"}
</body></html>"""


CSS = """
body { overflow:auto; }
.review { max-width:1400px; margin:0 auto; padding:12px 18px 40px; }
.review .k { font-size:11.5px; letter-spacing:.08em; text-transform:uppercase; color:var(--muted); font-weight:700; margin:0 0 6px; }
.review em { font-style:normal; font-size:11px; color:var(--warn); border:1px solid currentColor; border-radius:6px; padding:0 5px; margin-left:6px; white-space:nowrap; }
header.top { display:grid; grid-template-columns:minmax(0, 1fr) auto auto; gap:4px 12px; align-items:end; margin-bottom:12px; }
header.top .homelink { grid-column:1 / -1; }
header.top h1 { margin:0; font-size:20px; } header.top h1 span { color:var(--muted); font-weight:400; }
header.top .gi p { margin:2px 0 0; color:var(--fg2); font-size:13.5px; }
.modes { display:flex; border:1px solid var(--line); border-radius:10px; overflow:hidden; }
.modes .mode { padding:9px 14px; color:var(--fg2); text-decoration:none; font-size:14px; font-weight:600; border-left:1px solid var(--line); min-height:40px; display:flex; align-items:center; }
.modes .mode:first-child { border-left:0; } .modes .mode:hover { background:var(--s1-soft); color:var(--fg); }
.modes .mode.story { background:var(--accent); color:#fff; }
.note { margin:0 0 12px; padding:8px 12px; border:1px solid var(--line); border-radius:10px; font-size:14px; color:var(--fg2); }
.summary { display:grid; grid-template-columns:minmax(0, 1.1fr) minmax(0, 1fr); gap:14px; margin-bottom:14px; align-items:start; }
.focus { background:var(--card); border:1px solid color-mix(in srgb, var(--accent) 45%, var(--line)); border-left:4px solid var(--accent); border-radius:14px; padding:16px 18px; }
.focus h2 { margin:0 0 8px; font-size:21px; line-height:1.2; } .focus p { margin:0 0 10px; color:var(--fg2); font-size:14.5px; line-height:1.5; }
.focus .habit { margin:0; padding:10px 12px; border-radius:10px; background:color-mix(in srgb, var(--good) 10%, transparent); border-left:3px solid var(--good); color:var(--fg); }
.side { display:grid; gap:10px; align-content:start; }
.well ul { margin:0; padding-left:18px; color:var(--fg2); font-size:14px; } .well li { margin:3px 0; } .well li::marker { content:"✓  "; color:var(--good); }
.stats { display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:8px; }
.st { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:8px 10px; cursor:help; }
.st b { display:block; font-size:17px; font-variant-numeric:tabular-nums; } .st span { color:var(--fg2); font-size:12.5px; }
.chips { display:flex; flex-wrap:wrap; gap:6px; }
.mchip { all:unset; box-sizing:border-box; cursor:pointer; display:inline-flex; align-items:center; gap:8px; min-height:40px; padding:6px 11px;
  border-radius:10px; border:1px solid var(--line); background:var(--card); font-size:13.5px; }
.mchip small { color:var(--muted); font-size:12px; } .mchip .n { display:inline-grid; place-items:center; width:20px; height:20px; border-radius:50%;
  background:var(--accent); color:#fff; font-size:11.5px; font-weight:700; }
.mchip.costly { border-left:3px solid var(--s2); } .mchip.good { border-left:3px solid var(--good); }
.mchip:hover, .mchip.on { border-color:var(--accent); background:var(--s1-soft); }
.mchip:focus-visible, .row:focus-visible, .nav button:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
.storybtn { display:none; }
.ws { display:grid; grid-template-columns:minmax(320px, 560px) minmax(0, 1fr); gap:18px; align-items:start; margin:6px 0 24px; }
.boardcol { position:sticky; top:10px; display:grid; gap:8px; }
.moments { display:flex; flex-wrap:wrap; gap:6px; align-items:center; } .moments .k { margin:0 4px 0 0; }
.pl { display:flex; align-items:center; gap:8px; font-size:14px; font-weight:600; }
.dotc { width:12px; height:12px; border-radius:50%; border:1px solid var(--muted); } .dotc.w { background:#f0f0f0; } .dotc.b { background:#222; }
.boardwrap { display:grid; grid-template-columns:14px minmax(0, 1fr); gap:6px; }
.board { width:100%; height:auto; aspect-ratio:1; display:block; border-radius:6px; box-shadow:0 6px 20px #0005; touch-action:pan-y; }
.evalbar { position:relative; border-radius:4px; background:#2b2b2b; overflow:hidden; }
.evalbar i { position:absolute; left:0; right:0; bottom:0; background:#eee; transition:height .25s; }
.evalbar span { position:absolute; left:50%; top:4px; transform:translateX(-50%); writing-mode:vertical-rl; font-size:10px; color:#888; }
.varbar { display:flex; align-items:center; justify-content:space-between; gap:8px; padding:8px 10px; border-radius:10px;
  background:color-mix(in srgb, #9c36b5 16%, transparent); border:1px solid #9c36b5; font-size:13.5px; }
.varbar .vt b { color:#e599f7; } .varbar[hidden] { display:none; }
.vback { all:unset; cursor:pointer; font-weight:700; min-height:36px; display:flex; align-items:center; }
.nav { display:flex; align-items:center; gap:6px; }
.nav button { all:unset; box-sizing:border-box; cursor:pointer; min-width:48px; height:44px; display:grid; place-items:center; border-radius:10px;
  border:1px solid var(--line); background:var(--card); font-size:16px; }
.nav button:hover { border-color:var(--accent); }
.nav .mv { flex:1; text-align:center; font-weight:700; font-variant-numeric:tabular-nums; font-size:14px; }
.cats summary { cursor:pointer; font-size:13px; color:var(--fg2); min-height:32px; display:flex; align-items:center; }
.legend { display:flex; flex-wrap:wrap; gap:6px 14px; } .cat { display:inline-flex; align-items:center; gap:6px; font-size:13px; color:var(--fg2); min-height:32px; cursor:pointer; }
.cat svg { width:28px; height:10px; } .hint { margin:6px 0 0; font-size:12px; color:var(--muted); }
.ctxcol { display:grid; gap:10px; min-width:0; }
.ctxhead h2 { margin:0; font-size:19px; } .ctxhead p { margin:2px 0 0; color:var(--fg2); font-size:14px; }
.ctx { background:var(--card); border:1px solid var(--line); border-radius:12px; }
.ctx > summary { cursor:pointer; padding:10px 14px; font-weight:700; font-size:14px; min-height:44px; display:flex; align-items:center; }
.ctx > div { padding:0 14px 12px; font-size:14px; color:var(--fg2); }
.cl-line { margin:0 0 8px; padding-left:10px; border-left:3px solid var(--line); line-height:1.45; }
.cl-line.good { border-color:var(--good); } .cl-line.gap, .cl-line.why { border-color:var(--warn); } .cl-line.lesson { border-color:var(--accent); color:var(--fg); }
.cl-line.threat { border-color:#a61e4d; } .cl-line b { color:var(--fg); }
.tag { display:inline-block; font-size:12px; padding:2px 8px; border-radius:999px; border:1px solid var(--line); margin:2px 4px 2px 0; }
.quote { font-style:italic; color:var(--fg); margin:6px 0; padding:6px 10px; border-left:3px solid var(--s2); background:var(--s1-soft); border-radius:0 8px 8px 0; }
.eline { all:unset; box-sizing:border-box; cursor:pointer; display:block; width:100%; padding:8px 10px; margin:4px 0; border:1px solid var(--line); border-radius:8px; font-size:13.5px; }
.eline:hover { border-color:#9c36b5; } .eline b { color:var(--fg); } .eline small { color:var(--muted); display:block; }
.movelist { display:grid; grid-template-columns:42px 1fr 1fr; gap:2px; max-height:360px; overflow-y:auto; font-variant-numeric:tabular-nums; }
.movelist .no { color:var(--muted); padding:5px 4px; font-size:13px; }
.movelist button { all:unset; cursor:pointer; padding:5px 8px; border-radius:6px; font-size:13.5px; min-height:36px; display:flex; align-items:center; }
.movelist button.cur { background:var(--accent); color:#fff; } .movelist button.costly { color:var(--s2); font-weight:700; }
.movelist button.crit::after { content:" !"; color:var(--good); }
.report, .deep { margin-top:8px; } .report h2, .deep h2 { font-size:20px; margin:20px 0 4px; } .report .sub { color:var(--fg2); margin:0 0 12px; font-size:14px; }
details.rs { background:var(--card); border:1px solid var(--line); border-radius:12px; margin:0 0 8px; }
details.rs > summary { cursor:pointer; list-style:none; display:grid; grid-template-columns:minmax(0, 1fr) auto; gap:4px 12px; align-items:center;
  padding:12px 14px; min-height:48px; }
details.rs > summary::-webkit-details-marker { display:none; }
details.rs > summary::after { content:"+"; color:var(--muted); font-size:18px; grid-column:2; grid-row:1; }
details.rs[open] > summary::after { content:"–"; }
.rt { font-weight:700; font-size:15px; } .rv { grid-column:1; color:var(--muted); font-size:13px; }
.rb { padding:0 14px 12px; } .rb .intro { color:var(--muted); font-size:13px; margin:0 0 6px; }
.row { all:unset; box-sizing:border-box; display:block; width:100%; padding:9px 10px; border-top:1px solid var(--line); font-size:14px; color:var(--fg2); cursor:pointer; line-height:1.45; }
p.row { cursor:default; } .row b { color:var(--fg); } button.row:hover { background:var(--s1-soft); }
.none { color:var(--muted); font-size:13.5px; } .method p { color:var(--fg2); font-size:14px; line-height:1.5; }
.chartbox .timeline { width:100%; height:auto; cursor:pointer; }
.practice { margin-top:18px; padding:14px 16px; border-radius:12px; border:1px solid color-mix(in srgb, var(--good) 45%, var(--line));
  background:color-mix(in srgb, var(--good) 8%, transparent); } .practice p { margin:0; } .practice .k { color:var(--good); margin-bottom:4px; }
#tip { position:fixed; }
@media (max-width:1100px) { .ws { grid-template-columns:minmax(0, 1fr) minmax(0, 1fr); } }
@media (max-width:860px) {
  .review { padding:10px 14px 32px; }
  header.top { grid-template-columns:1fr; } .modes { width:100%; } .modes .mode { flex:1; justify-content:center; padding:8px 6px; font-size:13px; text-align:center; }
  .summary { grid-template-columns:1fr; } .stats { grid-template-columns:1fr 1fr; }
  .storybtn { display:flex; flex-direction:column; align-items:center; justify-content:center; min-height:52px; margin:0 0 14px; padding:10px; border-radius:12px;
    background:var(--accent); color:#fff; text-decoration:none; font-weight:700; } .storybtn small { font-weight:400; opacity:.85; }
  .modes .mode.story { display:none; }
  .ws { grid-template-columns:minmax(0, 1fr); } .boardcol { position:static; min-width:0; }
  .nav button { flex:1 1 0; min-width:0; }
  .board { max-width:min(100%, 560px); margin:0 auto; }
  .ctx > summary { min-height:48px; }
  .movelist { max-height:none; } .movelist button { min-height:44px; } .cats summary { min-height:44px; }
  header.top .homelink { display:inline-flex; align-items:center; min-height:44px; }
}
@media (max-width:360px) { .nav .mv { display:none; } }
@media (max-width:860px) { .theme-toggle { position:absolute !important; top:8px; right:10px; bottom:auto !important; } }
"""

JS = """
(() => {
  const D = JSON.parse(document.getElementById('rvdata').textContent);
  const svg = document.querySelector('svg.board'), NS = 'http://www.w3.org/2000/svg';
  const st = { pos: 0, flip: !D.white, v: null, all: false, cats: Object.fromEntries(D.cats.map(c => [c.key, c.on])) };
  const PIECE = { p: 'pawn', n: 'knight', b: 'bishop', r: 'rook', q: 'queen', k: 'king' };
  const sq = (s) => { const f = s.charCodeAt(0) - 97, r = +s[1] - 1; return st.flip ? [7 - f, r] : [f, 7 - r]; };
  const el = (t, a) => { const e = document.createElementNS(NS, t); for (const k in a) e.setAttribute(k, a[k]); return e; };
  const esc = (s) => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  function draw(fen, arrows, last, circles) {
    svg.textContent = '';
    for (let y = 0; y < 8; y++) for (let x = 0; x < 8; x++)
      svg.append(el('rect', { x: x * 45, y: y * 45, width: 45, height: 45, fill: (x + y) % 2 ? '#b58863' : '#f0d9b5' }));
    if (last) for (const s of [last.slice(0, 2), last.slice(2, 4)]) { const [x, y] = sq(s);
      svg.append(el('rect', { x: x * 45, y: y * 45, width: 45, height: 45, fill: '#cdd26a', opacity: .55 })); }
    const rows = fen.split(' ')[0].split('/');
    rows.forEach((row, ri) => { let fi = 0; for (const ch of row) { if (/\\d/.test(ch)) { fi += +ch; continue; }
      const name = (ch === ch.toUpperCase() ? 'white-' : 'black-') + PIECE[ch.toLowerCase()];
      const s = String.fromCharCode(97 + fi) + (8 - ri), [x, y] = sq(s);
      svg.append(el('use', { href: '#p-' + name, transform: `translate(${x * 45} ${y * 45})` })); fi++; } });
    for (const s of circles || []) { const [x, y] = sq(s);
      svg.append(el('circle', { cx: x * 45 + 22.5, cy: y * 45 + 22.5, r: 19, fill: 'none', stroke: '#e8590c', 'stroke-width': 3, 'stroke-dasharray': '7 5', opacity: .85 })); }
    for (const a of arrows) { const [x1, y1] = sq(a.f), [x2, y2] = sq(a.t);
      const cx1 = x1 * 45 + 22.5, cy1 = y1 * 45 + 22.5, cx2 = x2 * 45 + 22.5, cy2 = y2 * 45 + 22.5, len = Math.hypot(cx2 - cx1, cy2 - cy1) || 1;
      const ex = cx2 - (cx2 - cx1) / len * 10, ey = cy2 - (cy2 - cy1) / len * 10;
      svg.append(el('line', { x1: cx1, y1: cy1, x2: ex, y2: ey, stroke: a.c, 'stroke-width': 7, 'stroke-dasharray': a.d || '', 'stroke-linecap': a.d ? 'butt' : 'round',
        opacity: .82, 'marker-end': `url(#ah-${a.k})` })); }
  }
  const cat = (k) => D.cats.find(c => c.key === k);
  function arrowsFor(pos) {
    const out = [], add = (k, u) => { if (!st.cats[k] || !u) return; const c = cat(k); out.push({ f: u.slice(0, 2), t: u.slice(2, 4), c: c.color, d: c.dash, k }); };
    const d = D.dec[pos];
    if (d) { (st.all ? d.cands : d.cands.slice(0, 5)).forEach(c => add('cand', c[0] + c[1])); (st.all ? d.ideas : d.ideas.slice(0, 4)).forEach(c => add('idea', c[0] + c[1])); add('best', d.best); add('threat', d.threat); }
    const prev = D.dec[pos - 1];  // after your move: their best reply to it
    if (prev && prev.reply && prev.reply.uci) add('reply', prev.reply.uci);
    return out;
  }
  const pctx = (x) => Math.round(x * 100) + '%';
  const T = (s) => !D.third ? s : s.replace(/\\byou'd\\b/g, "he'd").replace(/\\bYour\\b/g, 'His').replace(/\\byour\\b/g, 'his')
    .replace(/\\bYou\\b/g, 'He').replace(/\\byou\\b/g, 'he');
  function render() {
    let fen, last, arrows, circles = [];
    if (st.v) {
      const step = st.v.line[st.v.i]; fen = step ? step.fen : D.fens[st.v.pos]; last = step ? step.uci : null;
      arrows = []; const nxt = st.v.line[st.v.i + 1];
      if (nxt) arrows.push({ f: nxt.uci.slice(0, 2), t: nxt.uci.slice(2, 4), c: '#9c36b5', d: '', k: 'var' });
      document.querySelector('.varbar').hidden = false;
      document.querySelector('.varbar .vt').innerHTML = '<b>Variation, not played:</b> ' + st.v.line.slice(0, st.v.i + 1).map(s => esc(s.san)).join(' ');
    } else {
      fen = D.fens[st.pos]; last = st.pos ? D.ucis[st.pos - 1] : null; arrows = arrowsFor(st.pos);
      const d = D.dec[st.pos]; if (d && st.cats.idea) circles = d.circles;
      document.querySelector('.varbar').hidden = true;
    }
    draw(fen, arrows, last, circles);
    const w = D.wp[st.pos] ?? .5, bar = document.querySelector('.evalbar i');
    bar.style.height = ((D.white ? w : 1 - w) * 100) + '%';
    document.querySelector('.evalbar span').textContent = T('You ') + pctx(w);
    document.querySelector('.nav .mv').textContent = st.pos ? D.sans[st.pos - 1] : 'Start';
    context();
    document.querySelectorAll('[data-pos]').forEach(b => b.classList.toggle('on', +b.dataset.pos === st.pos));
    document.querySelectorAll('.movelist button').forEach(b => b.classList.toggle('cur', +b.dataset.pos === st.pos));
  }
  function context() {
    const d = D.dec[st.pos], h = document.querySelector('.ctxhead');
    const coach = document.querySelector('.c-coach'), think = document.querySelector('.c-think'), eng = document.querySelector('.c-engine');
    if (!d) {
      const prev = D.dec[st.pos - 1];
      h.querySelector('.cl').textContent = st.pos ? 'After ' + D.sans[st.pos - 1] : 'The starting position';
      h.querySelector('.cw').textContent = T('Your win chance: about ') + pctx(D.wp[st.pos] ?? .5) + ' (engine estimate).';
      coach.innerHTML = prev ? (prev.reply && prev.reply.san ? `<p class='cl-line why'>Their best reply to ${esc(prev.label)} was <b>${esc(prev.reply.san)}</b>`
        + T(prev.reply.recorded ? ', and it is in your recorded thinking.' : ', not in your recorded thinking.') + '</p>' : '')
        + `<p class='cl-line'>${esc(D.sans[st.pos] || '')} ${D.sans[st.pos] ? 'was their move.' : ''}</p>`
        : "<p class='cl-line'>" + T('Not one of your reviewed moves.') + " Use the arrows, or pick a critical moment.</p>";
      think.innerHTML = "<p class='cl-line'>" + T('Recorded thinking is shown on the position before each of your moves.') + "</p>"; eng.innerHTML = '';
      return;
    }
    h.querySelector('.cl').textContent = T('Your move: ') + d.label + (d.costly ? ' · costly' : '');
    h.querySelector('.cw').textContent = `Win chance ${pctx(d.wp_before)} → ${pctx(d.wp_after)} (engine estimate)` + (d.time ? ` · ${Math.round(d.time)} s` : '');
    coach.innerHTML = d.coach.map(([k, t]) => `<p class='cl-line ${k}'>${esc(t)}</p>`).join('');
    think.innerHTML = (d.said.length ? `<p class='quote'>“${esc(d.said.map(q => q.replace(/[,.\\s]+$/, '')).join(' … '))}”</p>` : "<p class='cl-line'>Nothing said at this move.</p>")
      + `<p><b>Candidates drawn or said:</b> ${d.drawn.length ? d.drawn.map(s => `<span class='tag'>${esc(s)}</span>`).join('') : 'none recorded'}</p>`
      + `<p><b>Their ideas you noted:</b> ${d.noted.length ? d.noted.map(s => `<span class='tag'>${esc(s)}</span>`).join('') : 'none recorded'}</p>`;
    let e = '';
    const sameAsBest = d.engine[0] && d.engine[0].line[0] && d.played_line[0] && d.engine[0].line[0].uci === d.played_line[0].uci;
    if (d.played_line.length && !sameAsBest) e += `<button type='button' class='eline' data-line='played'><b>Played: ${esc(d.played_line.map(s => s.san).slice(0, 5).join(' '))}</b><small>${T('after your move')}, ${pctx(d.wp_after)} · step through it</small></button>`;
    d.engine.forEach((c, i) => { if (c.line.length) e += `<button type='button' class='eline' data-line='${i}'><b>${i ? '' : 'Best: '}${esc(c.line.map(s => s.san).slice(0, 5).join(' '))}</b><small>${c.wp != null ? pctx(c.wp) : ''} · step through it</small></button>`; });
    if (d.threat_san) e += `<p class='cl-line threat'>Their threat before your move: <b>${esc(d.threat_san)}</b></p>`;
    if (d.reply && d.reply.san) e += `<p class='cl-line why'>${T('Their best reply to your move:')} <b>${esc(d.reply.san)}</b>${d.reply.recorded ? T(' (in your recorded thinking)') : ''}</p>`;
    eng.innerHTML = e || "<p class='cl-line'>No engine lines saved for this move.</p>";
    eng.querySelectorAll('.eline').forEach(b => b.onclick = () => {
      const line = b.dataset.line === 'played' ? d.played_line : d.engine[+b.dataset.line].line;
      st.v = { pos: st.pos, line, i: 0 }; render(); });
  }
  function go(p, cats) {
    st.v = null; st.pos = Math.max(0, Math.min(D.fens.length - 1, p));
    if (cats) { const on = cats.split(','); D.cats.forEach(c => st.cats[c.key] = on.includes(c.key));
      document.querySelectorAll('[data-cat]').forEach(i => i.checked = st.cats[i.dataset.cat]); }
    history.replaceState(null, '', '#pos-' + st.pos); render();
  }
  function step(k) {
    if (st.v) { const n = st.v.i + k; if (n < 0) { st.v = null; render(); return; } st.v.i = Math.min(st.v.line.length - 1, n); render(); return; }
    go(st.pos + k);
  }
  document.querySelector('.vback').onclick = () => { st.v = null; render(); };
  document.querySelectorAll('[data-go]').forEach(b => b.onclick = () => {
    const g = b.dataset.go;
    if (g === 'flip') { st.flip = !st.flip; render(); } else if (g === 'prev') step(-1); else if (g === 'next') step(1);
    else if (g === 'first') go(0); else go(D.fens.length - 1); });
  document.querySelectorAll('[data-cat]').forEach(i => i.onchange = () => { st.cats[i.dataset.cat] = i.checked; render(); });
  document.querySelector('[data-all]').onchange = (e) => { st.all = e.target.checked; render(); };
  document.addEventListener('click', (e) => { const b = e.target.closest('[data-pos]'); if (!b) return;
    go(+b.dataset.pos, b.dataset.cats);
    if (matchMedia('(max-width:860px)').matches) document.querySelector('.boardcol').scrollIntoView({ block: 'start', behavior: 'smooth' }); });
  document.addEventListener('keydown', (e) => { if (/input|textarea|select/i.test(document.activeElement.tagName)) return;
    if (e.key === 'ArrowLeft') { e.preventDefault(); step(-1); } else if (e.key === 'ArrowRight') { e.preventDefault(); step(1); }
    else if (e.key === 'Escape' && st.v) { st.v = null; render(); } });
  let sx = null;  // a horizontal swipe on the board steps through the game (the buttons do the same)
  svg.addEventListener('touchstart', (e) => { sx = e.touches[0].clientX; }, { passive: true });
  svg.addEventListener('touchend', (e) => { if (sx == null) return; const dx = e.changedTouches[0].clientX - sx; sx = null;
    if (Math.abs(dx) > 40) step(dx < 0 ? 1 : -1); });
  // the move list
  const ml = document.querySelector('.c-moves'), list = document.createElement('div'); list.className = 'movelist';
  const crit = new Set(D.moments.filter(m => !m.costly).map(m => m.pos + 1));
  D.sans.forEach((s, i) => { if (i % 2 === 0) { const no = document.createElement('span'); no.className = 'no'; no.textContent = (i / 2 + 1) + '.'; list.append(no); }
    const b = document.createElement('button'); b.type = 'button'; b.dataset.pos = i + 1; b.textContent = s.replace(/^\\d+\\.+/, '');
    const d = D.dec[i]; if (d && d.costly) b.classList.add('costly'); if (crit.has(i + 1)) b.classList.add('crit'); list.append(b); });
  ml.append(list);
  // the game chart: a click opens the nearest move
  const chart = document.querySelector('.chartbox svg.timeline');
  if (chart) chart.addEventListener('click', (e) => {
    const pts = JSON.parse(chart.nextElementSibling.textContent);
    const p = new DOMPoint(e.clientX, e.clientY).matrixTransform(chart.getScreenCTM().inverse());
    let best = pts[0]; for (const q of pts) if (Math.abs(q.x - p.x) < Math.abs(best.x - p.x)) best = q;
    const i = D.sans.findIndex(s => s.replace(/\\s/g, '') === best.label.replace(/\\s/g, '')); if (i >= 0) { go(i); document.getElementById('workspace').scrollIntoView({ behavior: 'smooth' }); } });
  const m = /^#pos-(\\d+)$/.exec(location.hash);
  st.pos = m ? +m[1] : D.start; render();
})();
"""

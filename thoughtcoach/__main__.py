"""Command line:
    python -m thoughtcoach review RECORDING.json        everything for a recorded game (Milestones 1-4)
    python -m thoughtcoach analyse GAME.pgn --me NAME   engine analysis only (Milestone 1)
    python -m thoughtcoach report out/GAMEID.json       re-print an engine report
    python -m thoughtcoach arrows RECORDING.json        what a recording contains (Milestone 2)
    python -m thoughtcoach align RECORDING.json         thinking windows (Milestone 3)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

import chess
import chess.pgn

from .align import align
from .align import render as render_windows
from .analyse import Rules, analyse_pgn_game, game_id, live_move, opponent_clocks, player_color
from .arrows import load_recordings
from .dashboard import render as render_dashboard
from .aagaard import annotate as annotate_aagaard
from .insights import build as build_insights, verdict
from .tone import analyse as analyse_tone
from .strike import find as find_strike, first_break, threats as strike_threats
from .arrows import summarise as summarise_arrows
from .engine import WP_MODELS, Engine, EngineSettings, find_stockfish
from .gaps import Thresholds, build, engine_evaluator
from .gaps import render as render_gaps
from .progress import report as report_progress
from .report import render
from .voice import ollama_available, transcribe, utterances, voice_events, warm_up


# ---------------------------------------------------------------- shared helpers


def add_engine_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--stockfish", help="path to the Stockfish binary (else $STOCKFISH_PATH or PATH)")
    p.add_argument("--nodes", type=int, default=250_000, help="nodes per search (default 250,000)")
    p.add_argument("--threads", type=int, default=1, help="engine threads; >1 is not reproducible")
    p.add_argument("--hash", type=int, default=256, help="hash size in MB (default 256)")
    p.add_argument("--multipv", type=int, default=5, help="lines searched for candidates (default 5)")
    p.add_argument("--book-moves", type=int, default=8, help="skip your moves up to this move number")
    p.add_argument("--book", help="optional Polyglot opening book (.bin)")
    p.add_argument("--decided", type=float, default=0.05, help="skip if WP stays < x or > 1-x (default 0.05)")
    p.add_argument("--margin", type=float, default=0.05, help="candidate WP margin (default 0.05)")
    p.add_argument(
        "--wp-model",
        choices=WP_MODELS,
        default="lichess",
        help="win probability: lichess human-fitted curve (default) or Stockfish's WDL",
    )


def engine_settings(args: argparse.Namespace) -> EngineSettings:
    return EngineSettings(nodes=args.nodes, threads=args.threads, hash_mb=args.hash, multipv=args.multipv)


def analysis_rules(args: argparse.Namespace) -> Rules:
    return Rules(
        book_moves=args.book_moves,
        book_path=args.book,
        decided=args.decided,
        candidate_margin=args.margin,
        wp_model=args.wp_model,
    )


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def pgn_finished(path: Path) -> bool:
    """The PGN has a result (a game still in progress is exported with Result "*")."""
    game = read_pgn(path)
    return game is not None and game.headers.get("Result", "*") != "*"


def fetch_pgn(game_id: str, folder: Path) -> Path:
    """The game's PGN from lichess (public game export), cached in `folder` once the game is over."""
    path = folder / f"{game_id}.pgn"
    if not path.exists() or not pgn_finished(path):
        url = f"https://lichess.org/game/export/{game_id}?clocks=true&opening=true"
        # lichess rejects some default client User-Agents; send a generic one.
        request = urllib.request.Request(url, headers={"User-Agent": "thoughtcoach/0.1"})
        with urllib.request.urlopen(request, timeout=30) as response:
            text = response.read().decode("utf-8")
        write(path, text)
        print(f"downloaded {path}", file=sys.stderr)
    return path


def read_pgn(path: Path) -> chess.pgn.Game:
    with open(path, encoding="utf-8") as f:
        return chess.pgn.read_game(f)


def progress_printer(gid: str):
    def progress(ply: int, total: int) -> None:
        print(f"\r{gid}: engine, ply {ply}/{total}", end="", file=sys.stderr, flush=True)

    return progress


# ---------------------------------------------------------------- commands


def cmd_analyse(args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    pgn_path = Path(args.pgn)
    written = 0
    with Engine(find_stockfish(args.stockfish), engine_settings(args)) as engine, open(pgn_path, encoding="utf-8") as f:
        index = 0
        while (game := chess.pgn.read_game(f)) is not None:
            index += 1
            fallback = f"{pgn_path.stem}-{index}"
            gid = game_id(game, fallback)
            if game.errors:
                print(f"{gid}: PGN errors, skipping: {game.errors}", file=sys.stderr)
                continue
            me = player_color(game, args.me, args.color)
            if me is None:
                print(f"{gid}: '{args.me}' is not a player in this game, skipping", file=sys.stderr)
                continue
            data = analyse_pgn_game(game, me, engine, analysis_rules(args), fallback, progress_printer(gid))
            print(file=sys.stderr)
            write(out_dir / f"{data['game']['id']}.json", json.dumps(data, indent=2))
            write(out_dir / f"{data['game']['id']}.txt", render(data))
            print(f"wrote {out_dir / data['game']['id']}.json/.txt")
            written += 1
    if written == 0:
        print("No games analysed.", file=sys.stderr)
        return 1
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    print(render(json.loads(Path(args.json).read_text(encoding="utf-8"))))
    return 0


def cmd_arrows(args: argparse.Namespace) -> int:
    for game in load_recordings(args.json):
        print(summarise_arrows(game))
        print()
    return 0


def cmd_align(args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    for recording in load_recordings(args.recording):
        gid = recording["game_id"]
        game = read_pgn(Path(args.pgn) if args.pgn else fetch_pgn(gid, Path(args.games)))
        color = args.color or recording["my_color"]
        result = align(game, recording, chess.WHITE if color == "white" else chess.BLACK)
        write(out_dir / f"{gid}.thinking.json", json.dumps(result, indent=2))
        write(out_dir / f"{gid}.thinking.txt", render_windows(result, dict(game.headers)))
        print(f"wrote {out_dir / gid}.thinking.json/.txt")
    return 0


def voice_for(recording: dict, out_dir: Path, args: argparse.Namespace) -> list[dict]:
    """Transcribe the recording's audio (cached per game and model) and tag what was said."""
    gid = recording["game_id"]
    path = out_dir / f"{gid}.transcript.json"
    transcript = None
    if path.exists() and not args.retranscribe:
        cached = json.loads(path.read_text(encoding="utf-8"))
        transcript = cached if cached.get("model") == args.whisper_model else None
    llm = None if args.no_llm else args.llm_model
    if transcript is None and not recording.get("audio"):
        transcript = json.loads(path.read_text(encoding="utf-8"))  # the audio is gone: this is all there is
    if transcript is None:
        if llm and ollama_available(args.ollama_url):
            warm_up(llm, args.ollama_url, args.llm_gpu)  # load the model while Whisper runs
        print(f"{gid}: transcribing voice ({args.whisper_model}) ...", file=sys.stderr)
        report_progress("voice", audio_chunks=len(recording.get("audio") or []))
        transcript = transcribe(recording, args.whisper_model)
        write(path, json.dumps(transcript, indent=2))
    if llm and not ollama_available(args.ollama_url):
        print(f"{gid}: Ollama not running, tagging speech with keyword rules", file=sys.stderr)
    if llm and ollama_available(args.ollama_url):
        print(f"{gid}: tagging speech with {llm} ...", file=sys.stderr)
    start_t = recording["events"][0]["t"] if recording.get("events") else 0
    said = [{"s": round((c["t"] - start_t) / 1000), "text": c["text"]} for c in utterances(transcript) if len(c["text"].split()) >= 4]
    report_progress("voice", said=len(utterances(transcript)), quotes=said[:: max(1, len(said) // 8)][:8])
    report_progress("tagging")
    tags_path = out_dir / f"{gid}.tags.json"
    cache = json.loads(tags_path.read_text(encoding="utf-8")) if tags_path.exists() else {}  # keyed by the words, so new audio just misses
    started = time.time()
    events = voice_events(transcript, llm, args.ollama_url, args.llm_gpu, cache)
    report_progress("tagging", mentions=len(events))
    write(tags_path, json.dumps(cache, indent=1))
    if llm:
        print(f"{gid}: speech tagged in {time.time() - started:.0f}s", file=sys.stderr)
    start = recording["events"][0]["t"] if recording["events"] else 0
    lines = [f"Transcript of {gid} ({args.whisper_model}); time = seconds since recording started", ""]
    for clause in utterances(transcript):
        lines.append(f"{(clause['t'] - start) / 1000:7.1f}s  {clause['text']}")
    lines += ["", "Chess mentions found (tag, said, tagger):"]
    for e in events:
        lines.append(f"{(e['t'] - start) / 1000:7.1f}s  {e['tag']:<14} \"{e['said']}\"  [{e['tagger']}]")
    write(out_dir / f"{gid}.transcript.txt", "\n".join(lines))
    return events


def write_story(report: dict, analysis: dict, thinking: dict | None, game, out_dir: Path, games: Path) -> str:
    """The story page; the focus for next game also weighs what keeps coming back across games."""
    from . import profile, story

    try:
        # patterns across games: rapid and slower only (blitz shows too little thinking to measure)
        loaded = [g for g in profile.load(out_dir, games) if profile.serious(g)]
        history = story.history_from_profile(profile.compute(loaded))
        from . import masters  # how far from masters you are on each habit, across your games
        history["yours"] = masters.your_rates(loaded)
        history["vs_masters"] = masters.gaps(history["yours"])
        history["search"] = masters.search([m for g in loaded for m in g["report"]["moves"]])
        from . import splits  # how your thinking changes with the game, and where that showed in this one
        history["splits"] = splits.findings(splits.of_games(loaded))
        this = next((g for g in loaded if g["id"] == report["game"]["id"]), None)
        history["split_moments"] = splits.moments(this, history["splits"]) if this else []
    except Exception:  # no other games yet, or an old saved review: this game alone
        loaded, history = [], None
    # Last game's focus, and how this game went on it (habits.py).
    from . import habits

    gid = report["game"]["id"]
    ids = [g["id"] for g in loaded]
    if history is not None and gid in ids and ids.index(gid) > 0:
        i = ids.index(gid)
        prev = out_dir / f"{ids[i - 1]}.story.json"
        try:
            focus = json.loads(prev.read_text(encoding="utf-8")).get("focus") or {}
        except (OSError, ValueError):
            focus = {}
        line = habits.this_game(focus.get("key"), loaded[i], loaded[:i])  # before: every earlier game
        if line:
            history["last_focus"] = {"key": focus["key"], "title": focus.get("title"), "line": line}
            stories = {}
            for g in loaded[: i + 1]:
                try:
                    stories[g["id"]] = json.loads((out_dir / f"{g['id']}.story.json").read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    pass
            check = next((e for e in habits.compute(loaded[: i + 1], stories) if e["key"] == focus["key"]), None)
            if check and check["games"] >= habits.MIN_GAMES and check["p"] is not None:
                from .home import focus_state
                history["last_focus"]["check"] = f"Over the {check['games']} games since you got it: {focus_state(check)[2]}"
    built = story.build(report, analysis, thinking, game, history)
    # The home page's "latest game" card reads this: the headline, the key position and the focus.
    write(out_dir / f"{built['id']}.story.json", json.dumps(story.summary(built)))
    return story.render(built)


def cmd_review(args: argparse.Namespace) -> int:
    """Engine analysis (cached), alignment and the gap report for each recorded game."""
    out_dir = Path(args.out)
    rules = analysis_rules(args)
    thresholds = Thresholds(mistake=args.mistake, threat_swing=args.threat_swing, margin=args.margin,
                            time_trouble=args.time_trouble)
    with Engine(find_stockfish(args.stockfish), engine_settings(args)) as engine:
        for recording in load_recordings(args.recording):
            gid = recording["game_id"]
            pgn_path = Path(args.pgn) if args.pgn else fetch_pgn(gid, Path(args.games))
            game = read_pgn(pgn_path)
            color = args.color or recording["my_color"]
            me = chess.WHITE if color == "white" else chess.BLACK
            events = recording.get("events") or []
            report_progress("game", moves=len(list(game.mainline())),
                            arrows=sum(1 for e in events if e.get("type") == "shape_add"),
                            pickups=sum(1 for e in events if e.get("type") == "pick_up"),
                            audio_chunks=len(recording.get("audio") or []))

            # 1. Engine analysis, reused if it exists with the same settings.
            analysis_path = out_dir / f"{gid}.json"
            analysis = None
            if analysis_path.exists() and not args.reanalyse:
                cached = json.loads(analysis_path.read_text(encoding="utf-8"))
                same = cached["game"]["my_color"] == color and all(
                    cached["settings"].get(k) == v for k, v in {**vars(engine.settings), **vars(rules)}.items()
                )
                analysis = cached if same else None
            if analysis is not None:
                report_progress("engine", done=1, total=1, moves=[live_move(r) for r in analysis["moves"]])
            if analysis is None:
                analysis = analyse_pgn_game(game, me, engine, rules, gid, progress_printer(gid))
                print(file=sys.stderr)
                write(analysis_path, json.dumps(analysis, indent=2))
                write(out_dir / f"{gid}.txt", render(analysis))

            if any("opp_clock" not in r for r in analysis["moves"]):
                opp = opponent_clocks(game, me)
                for r in analysis["moves"]:  # analyses saved before opp_clock existed
                    r.setdefault("opp_clock", opp.get(r["ply"]))

            # 2. Voice (if the recording has audio): transcript (cached) -> tagged mentions.
            spoken = None
            # A recording reviewed before keeps its transcript after the audio is removed (hosted
            # service): reviewing it again uses that transcript.
            if recording.get("audio") or (out_dir / f"{gid}.transcript.json").exists():
                spoken = voice_for(recording, out_dir, args)

            # 3. Thinking windows (arrows + voice).
            report_progress("thinking")
            thinking = align(game, recording, me, spoken)
            report_progress("thinking", moves_with_thoughts=sum(1 for w in thinking["windows"] if w.get("shapes")))
            write(out_dir / f"{gid}.thinking.json", json.dumps(thinking, indent=2))
            write(out_dir / f"{gid}.thinking.txt", render_windows(thinking, dict(game.headers)))

            # 4. Gap report (moves you had that aren't engine candidates get their own search).
            report_progress("report")
            report = build(analysis, thinking, thresholds, engine_evaluator(engine, me, rules.wp_model))
            annotate_aagaard(report, analysis, engine, me)  # Q1 weaknesses, Q2 worst piece
            # Voice switched off in the extension (extension 0.8.11 on says so): the review says the
            # thinking came from arrows only, and that talking would give it more to work with.
            report["voice"] = recording.get("voice") or ("on" if spoken is not None else None)
            transcript_path = out_dir / f"{gid}.transcript.json"
            transcript = json.loads(transcript_path.read_text(encoding="utf-8")) if spoken is not None and transcript_path.exists() else None
            # The local model reads the sentences the word lists flag (tone.py): mood and giving up.
            import thoughtcoach.tone as tone_mod
            tone_mod.LLM = ({"model": args.llm_model, "url": args.ollama_url, "gpu": args.llm_gpu}
                            if not args.no_llm and ollama_available(args.ollama_url) else None)
            report["insights"] = build_insights(game, me, report, thinking, recording, transcript, utterances)
            report["verdict"] = verdict(report, analysis, report["insights"])
            report["tone"] = analyse_tone(game, me, report, recording, transcript, utterances)  # mood against play
            report_progress("strike")
            report["strike"] = find_strike(game, analysis, me, strike_threats(engine, game, analysis, me, rules.wp_model))
            brk = first_break(game, analysis, me)
            st = report["strike"]
            if brk and not (st.get("side") and brk["ply"] <= st["ply"] <= brk["end"]):
                st["break"] = brk  # a break that runs straight into the strike adds nothing
            write(out_dir / f"{gid}.gaps.json", json.dumps(report, indent=2))
            write(out_dir / f"{gid}.gaps.txt", render_gaps(report))

            # 5. Dashboard: every observation on one screen, critical moments on boards.
            report_progress("story", costly=sum(1 for m in report["moves"] if m.get("mistake")))
            page = out_dir / f"{gid}.html"
            def line_for(fen: str, uci: str) -> str:
                board = chess.Board(fen)
                info = engine.analyse(board, root_moves=[chess.Move.from_uci(uci)])[0]
                return board.variation_san(info.get("pv", [])[:4])

            write(page, render_dashboard(report, analysis, thinking, args.critical, line_for))

            # 6. The story: the game's standout moments as tap-through cards, and a focus for next game.
            write(out_dir / f"{gid}.story.html", write_story(report, analysis, thinking, game, out_dir, Path(args.games)))
            print(f"wrote {out_dir / gid}.gaps.txt (and .json, plus engine and thinking files)")
            print(f"dashboard: {page.resolve().as_uri()}")
    return 0


# ---------------------------------------------------------------- argument parsing


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="thoughtcoach", description="Thought Coach")
    sub = parser.add_subparsers(dest="command", required=True)

    rv = sub.add_parser("review", help="full review of a recorded game: engine + thinking windows + gaps")
    rv.add_argument("recording", help="userscript export (single game or 'export all' bundle)")
    rv.add_argument("--pgn", help="the game's PGN (default: downloaded from lichess by game id)")
    rv.add_argument("--games", default="games", help="folder for downloaded PGNs (default: games)")
    rv.add_argument("--color", choices=["white", "black"], help="override your colour")
    rv.add_argument("--out", default="out", help="output folder (default: out)")
    rv.add_argument("--reanalyse", action="store_true", help="redo the engine analysis even if cached")
    rv.add_argument("--mistake", type=float, default=0.05, help="WP loss that counts as a costly move (0.05)")
    rv.add_argument("--time-trouble", type=float, default=30.0,
                    help="seconds on your clock below which costly moves count as time trouble (30)")
    rv.add_argument("--critical", type=int, default=3, help="critical moments to show on boards (default 3)")
    rv.add_argument("--threat-swing", type=float, default=0.10, help="null-move swing for a real threat (0.10)")
    rv.add_argument("--whisper-model", default="small.en", help="faster-whisper model (default small.en)")
    rv.add_argument("--retranscribe", action="store_true", help="transcribe the voice again even if cached")
    rv.add_argument("--llm-model", default="qwen2.5:3b", help="Ollama model that tags speech (default qwen2.5:3b; qwen2.5:7b is about half as fast)")
    rv.add_argument("--no-llm", action="store_true", help="tag speech with keyword rules only")
    rv.add_argument("--ollama-url", default="http://127.0.0.1:11434", help="Ollama server")
    rv.add_argument("--llm-gpu", action="store_true", help="let Ollama use the GPU (needs a recent NVIDIA driver)")
    add_engine_args(rv)
    rv.set_defaults(func=cmd_review)

    a = sub.add_parser("analyse", help="engine analysis of your moves in a PGN (one or many games)")
    a.add_argument("pgn", help="PGN file with %%clk comments (lichess export)")
    who = a.add_mutually_exclusive_group(required=True)
    who.add_argument("--me", help="your username as it appears in the White/Black headers")
    who.add_argument("--color", choices=["white", "black"], help="your side (single-game PGNs)")
    a.add_argument("--out", default="out", help="output folder (default: out)")
    add_engine_args(a)
    a.set_defaults(func=cmd_analyse)

    r = sub.add_parser("report", help="print the text report for an analysis JSON")
    r.add_argument("json")
    r.set_defaults(func=cmd_report)

    ar = sub.add_parser("arrows", help="show what an arrow recording (userscript export) contains")
    ar.add_argument("json")
    ar.set_defaults(func=cmd_arrows)

    al = sub.add_parser("align", help="thinking windows: match an arrow recording to the game's PGN")
    al.add_argument("recording", help="userscript export (single game or 'export all' bundle)")
    al.add_argument("--pgn", help="the game's PGN (default: downloaded from lichess by game id)")
    al.add_argument("--games", default="games", help="folder for downloaded PGNs (default: games)")
    al.add_argument("--color", choices=["white", "black"], help="override your colour")
    al.add_argument("--out", default="out", help="output folder (default: out)")
    al.set_defaults(func=cmd_align)

    sv = sub.add_parser("serve", help="local server: the browser extension uploads games, reviews run automatically")
    sv.add_argument("--port", type=int, default=8770, help="port on 127.0.0.1 (default 8770)")
    sv.add_argument("--recordings", default="recordings", help="where uploaded recordings are saved")
    sv.add_argument("--out", default="out", help="output folder (default: out)")
    sv.add_argument("--games", default="games", help="folder for downloaded PGNs (default: games)")
    sv.set_defaults(func=cmd_serve)

    wk = sub.add_parser("worker", help="hosted review worker: takes jobs from the queue (settings from the environment)")
    wk.set_defaults(func=lambda args: __import__("thoughtcoach.worker", fromlist=["run"]).run())
    return parser


def cmd_serve(args: argparse.Namespace) -> int:
    from .server import serve

    return serve(args.port, Path(args.recordings), Path(args.out), Path(args.games))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

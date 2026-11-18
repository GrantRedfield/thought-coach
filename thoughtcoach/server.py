"""Local Thought Coach server: the browser extension uploads each game when it ends, and the
review runs here automatically (the same pipeline as `python -m thoughtcoach review`).

Fair play: a recording is only accepted once lichess says the game is over (its PGN has a
result), whatever the extension says. Nothing is analysed while a game is in progress.

Runs on 127.0.0.1 only, with no login: it is for your own machine.

    POST /api/recordings     a recording (JSON, as the extension or userscript exports it)
    GET  /api/games          every game and its review status
    GET  /api/games/<id>     one game's status
    GET  /health             is the server up
    GET  /                   the list of reviewed games
    GET  /games/<id>         a game's dashboard
    GET  /games/<id>/story   a game's story (its standout moments, tap through)
"""

from __future__ import annotations

import html
import json
import queue
import re
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


GAME_ID = re.compile(r"^[A-Za-z0-9]{8}$")
MAX_BODY = 64 * 1024 * 1024
FORMAT = "thoughtcoach-arrows/2"


class GameInProgress(Exception):
    pass


def richness(recording: dict) -> int:
    """How much thinking a recording holds: shapes drawn, and audio (a voice clip counts for a lot)."""
    shapes = sum(1 for e in recording.get("events") or [] if e.get("type") == "shape_add")
    return shapes + 1000 * len(recording.get("audio") or [])


def lichess_finished(game_id: str, games: Path) -> bool:
    """Ask lichess (public game export) whether the game is over."""
    from .__main__ import fetch_pgn, pgn_finished, read_pgn

    path = fetch_pgn(game_id, games)
    if pgn_finished(path):
        return True
    game = read_pgn(path)
    if game is not None and game.headers.get("Termination") in ("Abandoned", "Aborted"):
        raise ValueError("lichess says this game was abandoned: there is nothing to review")
    return False


def run_review(recording: Path, out: Path, games: Path) -> None:
    from .__main__ import build_parser, cmd_review

    args = build_parser().parse_args(["review", str(recording), "--out", str(out), "--games", str(games)])
    cmd_review(args)


class Reviews:
    """Saved recordings and a single worker that reviews them one at a time (engine and voice
    models are heavy, so reviews never run side by side)."""

    def __init__(self, recordings: Path, out: Path, games: Path, finished=lichess_finished, review=run_review):
        self.recordings, self.out, self.games = recordings, out, games
        self.finished, self.review = finished, review
        self.status: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.jobs: queue.Queue[str] = queue.Queue()
        for path in sorted(recordings.glob("*.json")):
            gid = path.stem
            if GAME_ID.match(gid):
                done = (out / f"{gid}.html").exists()
                self.status[gid] = {"game_id": gid, "state": "done" if done else "not reviewed"}

    # ------------------------------------------------------------ uploads

    def accept(self, recording: dict) -> dict:
        """Save a recording and queue its review. Raises ValueError (bad upload) or GameInProgress."""
        if not isinstance(recording, dict) or recording.get("format") != FORMAT:
            raise ValueError(f"not a Thought Coach recording (format {FORMAT})")
        gid = str(recording.get("game_id", ""))
        if not GAME_ID.match(gid):
            raise ValueError("bad game id")
        if not isinstance(recording.get("events"), list):
            raise ValueError("recording has no events")
        if not self.finished(gid, self.games):
            raise GameInProgress(gid)

        path = self.recordings / f"{gid}.json"
        text = json.dumps(recording)
        same = path.exists() and path.read_text(encoding="utf-8") == text
        with self.lock:
            state = self.status.get(gid, {}).get("state")
            if same and state in ("queued", "running", "done"):
                return self.status[gid]  # the same upload again: nothing new to do
            if path.exists() and not same and richness(recording) < richness(json.loads(path.read_text(encoding="utf-8"))):
                # Never replace a recording with a poorer one (e.g. the finished game opened again
                # later, with no arrows or voice): keep what we have.
                return {**self.status.get(gid, {"game_id": gid, "state": "done"}), "kept": "earlier recording"}
            self.recordings.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            if not same:
                (self.out / f"{gid}.transcript.json").unlink(missing_ok=True)  # new audio, new transcript
            self.status[gid] = {"game_id": gid, "state": "queued", "queued_at": time.time()}
        self.jobs.put(gid)
        return self.status[gid]

    def get(self, gid: str) -> dict | None:
        with self.lock:
            s = self.status.get(gid)
            done = s is not None and s["state"] == "done"
            return None if s is None else {**s, "dashboard": f"/games/{gid}" if done else None,
                                           "story": f"/games/{gid}/story" if done else None}

    def all(self) -> list[dict]:
        with self.lock:
            ids = list(self.status)
        return [self.get(g) for g in sorted(ids, key=lambda g: -self._mtime(g))]

    def _mtime(self, gid: str) -> float:
        for path in (self.recordings / f"{gid}.json", self.out / f"{gid}.gaps.json"):
            if path.exists():
                return path.stat().st_mtime
        return 0

    # ------------------------------------------------------------ worker

    def work_one(self) -> None:
        gid = self.jobs.get()
        with self.lock:
            self.status[gid].update(state="running", started_at=time.time())
        try:
            self.review(self.recordings / f"{gid}.json", self.out, self.games)
            state = {"state": "done", "error": None}
        except Exception as err:  # keep serving; report the failure on the game
            traceback.print_exc()
            state = {"state": "error", "error": f"{type(err).__name__}: {err}"}
        with self.lock:
            self.status[gid].update(finished_at=time.time(), **state)
        self.jobs.task_done()

    def start(self) -> None:
        def loop():
            while True:
                self.work_one()

        threading.Thread(target=loop, daemon=True, name="reviews").start()


# ---------------------------------------------------------------- HTTP


LIST_CSS = """
:root { color-scheme: light; --bg:#f4f3ef; --card:#fcfcfb; --fg:#0b0b0b; --fg2:#52514e; --muted:#8a8984; --line:#e4e3dd; --accent:#2a78d6;
  --s1:#2a78d6; --s1-soft:#2a78d622; --s2:#eb6834; --s1-light:#86b6ef; --bar-tt:#b4b3ad; --good:#1e8e3e; --warn:#c26a00; }
@media (prefers-color-scheme: dark) { :root:where(:not([data-theme="light"])) { color-scheme: dark; --bg:#121211; --card:#1a1a19;
  --fg:#f0efec; --fg2:#c3c2b7; --muted:#8f8e86; --line:#2e2e2b; --accent:#3987e5; --s1:#3987e5; --s1-soft:#3987e533; --s2:#d95926; --s1-light:#184f95;
  --bar-tt:#5d5c57; --good:#3fa95c; --warn:#d98a1c; } }
:root[data-theme="dark"] { color-scheme: dark; --bg:#121211; --card:#1a1a19; --fg:#f0efec; --fg2:#c3c2b7; --muted:#8f8e86; --line:#2e2e2b;
  --accent:#3987e5; --s1:#3987e5; --s1-soft:#3987e533; --s2:#d95926; --s1-light:#184f95; --bar-tt:#5d5c57; --good:#3fa95c; --warn:#d98a1c; }
body { background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; margin:0; padding:24px 16px; }
main { max-width:1100px; margin:0 auto; } h1 { font-size:22px; margin:0 0 4px; } p.sub { color:var(--fg2); margin:0 0 18px; }
table { width:100%; border-collapse:collapse; background:var(--card); border:1px solid var(--line); border-radius:10px; overflow:hidden; }
td, th { padding:8px 12px; border-bottom:1px solid var(--line); text-align:left; } th { color:var(--fg2); font-weight:600; font-size:13px; }
a { color:var(--accent); } .state { color:var(--fg2); } .error { color:#c0392b; font-size:13px; }
"""


def list_page(reviews: Reviews, owner: str | None = None) -> str:
    """The home page: your latest game's story up front, your form, every game, the patterns.
    owner (hosted service): whose games, with links to setup and sign out."""
    from . import home, profile
    from .fmt import THEME_BUTTON, THEME_HEAD

    loaded, prof, stories = [], None, {}
    try:
        loaded = profile.load(reviews.out, reviews.games)
        prof = profile.compute(loaded) if loaded else None
        for g in loaded:
            path = reviews.out / f"{g['id']}.story.json"
            if path.exists():
                stories[g["id"]] = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # a broken saved review must not take the home page down
        traceback.print_exc()
    reviewed = {g["id"] for g in loaded}
    waiting = {g["game_id"]: g for g in reviews.all()
               if g["state"] in ("queued", "running", "error", "done") and g["game_id"] not in reviewed}
    try:
        main = home.render(loaded, prof, stories, waiting)
    except Exception:
        traceback.print_exc()
        main = "<p>Your games could not be shown right now.</p>"
    busy = any(g["state"] in ("queued", "running") for g in waiting.values())
    refresh = "<meta http-equiv='refresh' content='10'>" if busy else ""
    nav = ("<a class='nav' href='https://lichess.org/'>Play on lichess</a>" if not owner else
           f"<span class='who'>{html.escape(owner)}</span><a class='nav' href='https://lichess.org/'>Play on lichess</a>"
           "<a class='nav' href='/start'>Setup</a><a class='nav' href='/api/logout'>Sign out</a>")
    return f"""<!doctype html><html lang="en" data-theme="dark"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">{refresh}<title>Thought Coach</title>
<link rel="icon" href="/favicon.png">{THEME_HEAD}<style>{LIST_CSS}{profile.CSS}{home.CSS}</style></head><body>{THEME_BUTTON}
<main class="wrap"><nav class="topbar"><a class="brand" href="/">&#9822; Thought Coach</a><span class="sp"></span>{nav}</nav>
{main}
</main><script>{home.JS}</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    server_version = "ThoughtCoach/0.1"
    reviews: Reviews  # set by make_server

    def _send(self, code: int, body: bytes, kind: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj) -> None:
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json")

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0].rstrip("/") or "/"
        if path == "/health":
            return self._json(200, {"ok": True, "app": "thoughtcoach"})
        if path == "/":
            return self._send(200, list_page(self.reviews).encode("utf-8"), "text/html; charset=utf-8")
        if path == "/api/games":
            return self._json(200, self.reviews.all())
        m = re.fullmatch(r"/api/games/([A-Za-z0-9]{8})", path)
        if m:
            s = self.reviews.get(m.group(1))
            return self._json(200, s) if s else self._json(404, {"error": "unknown game"})
        m = re.fullmatch(r"/games/([A-Za-z0-9]{8})/story", path)
        if m:
            page = self.reviews.out / f"{m.group(1)}.story.html"
            if page.exists():
                return self._send(200, page.read_bytes(), "text/html; charset=utf-8")
        m = re.fullmatch(r"/games/([A-Za-z0-9]{8})", path)
        if m:
            page = self.reviews.out / f"{m.group(1)}.html"
            if page.exists():
                return self._send(200, page.read_bytes(), "text/html; charset=utf-8")
        return self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?")[0] != "/api/recordings":
            return self._json(404, {"error": "not found"})
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= MAX_BODY:
            return self._json(413, {"error": "empty or too large"})
        try:
            recording = json.loads(self.rfile.read(length))
            status = self.reviews.accept(recording)
        except GameInProgress:
            return self._json(409, {"error": "lichess says this game is still in progress; try again when it is over"})
        except (ValueError, json.JSONDecodeError) as err:
            return self._json(400, {"error": str(err)})
        except OSError as err:  # lichess could not be reached
            return self._json(502, {"error": f"could not check the game on lichess: {err}"})
        return self._json(202, {**status, "status_url": f"/api/games/{status['game_id']}",
                                "dashboard_url": f"/games/{status['game_id']}"})

    def log_message(self, fmt: str, *args) -> None:
        print(f"[serve] {self.address_string()} {fmt % args}", file=sys.stderr)


def make_server(port: int, reviews: Reviews) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"reviews": reviews})
    return ThreadingHTTPServer(("127.0.0.1", port), handler)


def serve(port: int, recordings: Path, out: Path, games: Path) -> int:
    reviews = Reviews(recordings, out, games)
    reviews.start()
    httpd = make_server(port, reviews)
    print(f"Thought Coach server on http://127.0.0.1:{port}  (Ctrl+C to stop)", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0

import json
import threading
import urllib.error
import urllib.request

from thoughtcoach.server import Reviews, make_server


def recording(gid="abcdEFGH"):
    return {"format": "thoughtcoach-arrows/2", "game_id": gid, "my_color": "white", "events": [], "audio": []}


def setup(tmp_path, finished=True):
    reviewed = []

    def review(path, out, games):
        reviewed.append(path.stem)
        (out / f"{path.stem}.html").write_text("<html>dashboard</html>", encoding="utf-8")

    out = tmp_path / "out"
    out.mkdir()
    reviews = Reviews(tmp_path / "rec", out, tmp_path / "games", finished=lambda gid, games: finished, review=review)
    return reviews, reviewed


def post(port, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/recordings", data=data,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read())


def get(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as r:
        return r.status, r.read().decode()


def run_server(reviews):
    httpd = make_server(0, reviews)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def test_upload_review_and_dashboard(tmp_path):
    reviews, reviewed = setup(tmp_path)
    httpd, port = run_server(reviews)
    try:
        code, body = post(port, recording())
        assert code == 202 and body["state"] == "queued" and body["dashboard_url"] == "/games/abcdEFGH"
        reviews.work_one()
        assert reviewed == ["abcdEFGH"]
        assert json.loads(get(port, "/api/games/abcdEFGH")[1])["state"] == "done"
        assert "dashboard" in get(port, "/games/abcdEFGH")[1]
        assert "abcdEFGH" in get(port, "/")[1]
        # The same upload again doesn't queue another review.
        assert post(port, recording())[1]["state"] == "done"
        assert reviews.jobs.empty()
    finally:
        httpd.shutdown()


def test_refuses_games_in_progress_and_bad_uploads(tmp_path):
    reviews, _ = setup(tmp_path, finished=False)
    httpd, port = run_server(reviews)
    try:
        code, body = post(port, recording())
        assert code == 409 and "in progress" in body["error"]
        assert not (tmp_path / "rec" / "abcdEFGH.json").exists()
        assert post(port, {"format": "other"})[0] == 400
        assert post(port, recording(gid="../../etc"))[0] == 400
    finally:
        httpd.shutdown()


def test_failed_review_is_reported(tmp_path):
    reviews, _ = setup(tmp_path)

    def broken(path, out, games):
        raise RuntimeError("engine missing")

    reviews.review = broken
    reviews.accept(recording())
    reviews.work_one()
    status = reviews.get("abcdEFGH")
    assert status["state"] == "error" and "engine missing" in status["error"]


def test_abandoned_game_is_rejected_not_retried(tmp_path, monkeypatch):
    import pytest

    from thoughtcoach import __main__ as main
    from thoughtcoach.server import lichess_finished

    pgn = tmp_path / "k99d80SW.pgn"
    pgn.write_text('[Result "*"]\n[Termination "Abandoned"]\n\n1. e4 *\n')
    monkeypatch.setattr(main, "fetch_pgn", lambda gid, folder: pgn)
    with pytest.raises(ValueError, match="abandoned"):
        lichess_finished("k99d80SW", tmp_path)
    pgn.write_text('[Result "*"]\n[Termination "Unterminated"]\n\n1. e4 *\n')
    assert lichess_finished("k99d80SW", tmp_path) is False


def test_a_poorer_upload_never_replaces_a_recording(tmp_path):
    from thoughtcoach.server import Reviews

    rec_dir, out, games = tmp_path / "rec", tmp_path / "out", tmp_path / "games"
    for d in (rec_dir, out, games):
        d.mkdir()
    reviews = Reviews(rec_dir, out, games, finished=lambda gid, g: True, review=lambda *a: None)
    full = {"format": "thoughtcoach-arrows/2", "game_id": "ISBvb7Ts", "audio": [{"t": 0}],
            "events": [{"type": "shape_add"}] * 50}
    reviews.accept(full)
    reviews.work_one()
    poor = {"format": "thoughtcoach-arrows/2", "game_id": "ISBvb7Ts", "events": [{"type": "game_end"}] * 50}
    status = reviews.accept(poor)
    assert status["kept"] == "earlier recording"
    import json
    assert json.loads((rec_dir / "ISBvb7Ts.json").read_text())["audio"]  # the full one is still there

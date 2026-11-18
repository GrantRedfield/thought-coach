import chess

from thoughtcoach.aagaard import _credit, activity, annotate, structural, weaknesses, worst_piece

START = "4k3/pp3npp/8/8/3P4/8/5PPP/R5K1 b - - 0 1"
PV = [chess.Move.from_uci(u) for u in ("f7d6", "a1c1", "d6f5", "c1c4", "f5d4")]  # the knight goes for d4


def test_structural_weaknesses():
    # White: isolated d4, doubled f-pawns (with a g-pawn beside them).
    board = chess.Board("4k3/pp4pp/8/8/3P4/5P2/5PP1/4K3 w - - 0 1")
    weak = structural(board, chess.WHITE)
    assert weak[chess.D4] == "isolated pawn"
    assert weak[chess.F3] == "doubled pawn" and weak[chess.F2] == "doubled pawn"
    assert weak[chess.C4] == "hole"  # no white b- or d-pawn behind c4 can ever cover it
    assert chess.E4 not in weak  # f3 covers e4
    assert chess.A3 not in weak  # edge files are not counted as holes


def test_only_weaknesses_the_engine_uses_count():
    board = chess.Board(START)
    found = weaknesses(board, chess.BLACK, PV, None)
    assert {"square": "d4", "kind": "isolated pawn", "side": "theirs"} in found  # the knight takes it
    assert not any(w["square"] == "e4" and w["side"] == "theirs" for w in found)  # a hole nobody goes to


def test_worst_piece_is_the_least_active_and_shows_the_engine_plan():
    # White's bishop on c1 is buried behind b2/d2; the knight on f3 is free.
    board = chess.Board("4k3/8/8/8/8/5N2/1P1P4/2B1K3 w - - 0 1")
    assert activity(board, chess.C1) == 0
    w = worst_piece(board, chess.WHITE, [chess.Move.from_uci("d2d4"), chess.Move.from_uci("e8e7"),
                                         chess.Move.from_uci("c1g5")])
    assert (w["piece"], w["square"], w["engine_move"], w["engine_move_no"]) == ("bishop", "c1", "Bg5+", 2)
    assert worst_piece(chess.Board("4k3/8/8/8/3N4/8/8/4K3 w - - 0 1"), chess.WHITE, []) is None  # nothing passive


def test_credit_depends_on_whose_weakness():
    assert _credit("theirs", {"my arrow"}) == "your arrow"  # going for their weakness
    assert _credit("yours", {"my arrow"}) is None  # moving onto your own hole isn't seeing it
    assert _credit("yours", {"their arrow"}) == "their arrow"
    assert _credit("yours", {"marked"}) == "marked"


class FakeEngine:
    def __init__(self, pv):
        self.pv = pv

    def analyse(self, board, multipv=1, root_moves=None, nodes=None):
        return [{"pv": self.pv if board.fen() == START else []}]



def test_annotate_counts_each_weakness_once_and_credits_earlier_marks():
    pv = PV
    move = lambda n, marks: {"ply": 2 * n, "move_number": n, "fen": START, "skipped": None, "clock_before": 120,  # noqa: E731
                             "key_squares": marks, "drawn_candidates": [], "drawn_opponent_ideas": []}
    report = {"thresholds": {"time_trouble": 30}, "summary": {},
              "moves": [move(10, ["d4"]), move(11, []), move(12, [])]}
    analysis = {"moves": [{"ply": m["ply"], "fen": START, "threat": None} for m in report["moves"]]}
    annotate(report, analysis, FakeEngine(pv), chess.BLACK)
    s = report["summary"]
    d4 = [w for w in s["q1_list"] if w["square"] == "d4" and w["side"] == "theirs"]
    assert len(d4) == 1 and d4[0]["you"] == "marked" and d4[0]["you_move"] == 10
    assert s["q1_found"] >= 1 and s["q1_weaknesses"] == len(s["q1_list"])


def test_recapture_is_not_using_a_weakness():
    # White just played dxe5 (doubling e-pawns); 1...Nxe5 only wins the pawn back.
    board = chess.Board("r1bqk2r/pppp1ppp/2n2n2/4P3/4P3/2N5/PPP2PPP/R1BQKBNR b KQkq - 0 5")
    found = weaknesses(board, chess.BLACK, [chess.Move.from_uci("c6e5")], None, last_capture=chess.E5)
    assert not any(w["square"] == "e5" for w in found)


def test_winning_a_piece_on_a_hole_is_a_loose_piece_not_a_hole():
    # White's bishop on d3 is loose; Black's queen takes it and nothing takes back.
    board = chess.Board("4k3/8/8/8/8/3B4/6PP/q5K1 b - - 0 1")
    found = weaknesses(board, chess.BLACK, [chess.Move.from_uci("a1d1"), chess.Move.from_uci("g1f2"),
                                            chess.Move.from_uci("d1d3"), chess.Move.from_uci("h2h3")], None)
    assert {"square": "d3", "kind": "loose bishop", "side": "theirs"} in found
    assert not any(w["kind"] == "hole" and w["square"] == "d3" for w in found)


def test_no_weaknesses_once_the_game_is_decided():
    from thoughtcoach.aagaard import check_move

    move = {"drawn_candidates": [], "drawn_opponent_ideas": [], "key_squares": []}
    q1, _ = check_move(FakeEngine(PV), move, {"fen": START, "wp_before": 0.12, "threat": None}, chess.BLACK)
    assert q1["weaknesses"] == []
    q1, _ = check_move(FakeEngine(PV), move, {"fen": START, "wp_before": 0.5, "threat": None}, chess.BLACK)
    assert q1["weaknesses"]


def test_a_move_that_takes_the_guard_off_a_piece_creates_a_loose_piece():
    from thoughtcoach.aagaard import created

    # 19...b5 takes the b6 pawn's guard off the knight on c5; White trades on d8 and takes it.
    record = {"fen": "2rr2k1/1b2qpp1/pp2p2p/2n5/3R4/Q3PN1P/PP2BPP1/3R2K1 b - - 0 19", "move_uci": "b6b5",
              "wp_loss": 0.41, "played_line_san": "19...b5 20. Rxd8+ Rxd8 21. Rxd8+ Qxd8 22. Qxc5 Qa5 23. Bd3"}
    made = created(record, chess.BLACK)
    assert [(w["square"], w["kind"], w["created_by"]) for w in made] == [("c5", "loose knight", "b5")]
    assert created({**record, "wp_loss": 0.01}, chess.BLACK) == []  # a move that cost nothing isn't flagged


def test_follow_a_worst_piece_until_it_gets_going():
    from thoughtcoach.aagaard import follow
    # White's rook on a1 behind its own pawns; Ra1-d1 at move 10 and it is active from move 11.
    start = chess.Board("4k3/8/8/8/8/8/PPP2PPP/R3K3 w - - 0 10")
    after = start.copy(); after.push_uci("a1d1"); after.push_uci("e8f8")
    records = {19: {"ply": 19, "move_number": 10, "fen": start.fen(), "move_uci": "a1d1"},
               21: {"ply": 21, "move_number": 11, "fen": after.fen(), "move_uci": "e1e2"}}
    f = follow({"square": "a1", "piece": "rook", "move": 10}, records, chess.WHITE)
    assert f["kind"] == "active" and f["move"] == 11 and f["by"] == "10. Rd1"
    stuck = {19: {**records[19], "move_uci": "e1e2"}}
    assert follow({"square": "a1", "piece": "rook", "move": 10}, stuck, chess.WHITE)["kind"] == "stuck"

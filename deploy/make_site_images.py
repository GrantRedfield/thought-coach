"""Draw the board pictures on the landing page (deploy/site/img/), with python-chess like the
reviews do, so they are real positions, not drawings. Run from the repository root:

    python deploy/make_site_images.py
"""

from pathlib import Path

import chess
import chess.svg

OUT = Path(__file__).parent / "site" / "img"
COLORS = {"square light": "#f0d9b5", "square dark": "#b58863", "margin": "#00000000"}
BLUE, GREEN, RED, PLAYED = "#3987e5cc", "#2f9e44dd", "#e03131cc", "#1f4fa8dd"


def save(name: str, board: chess.Board, orientation=chess.WHITE, **kw) -> None:
    OUT.mkdir(exist_ok=True)
    svg = chess.svg.board(board, orientation=orientation, coordinates=False, colors=COLORS, size=400, **kw)
    (OUT / f"{name}.svg").write_text(svg, encoding="utf-8")


def sq(name: str) -> int:
    return chess.parse_square(name)


# The hero: a real moment. Black has just been hit by Nxc6, plays Nxc3+ (dark blue) in 12 seconds;
# only Nb6 (green) held the position.
hero = chess.Board("4r2k/5pp1/2N2p1p/1pqn4/8/p1P2P2/PP1Q2PP/1K1R4 b - - 0 27")
save("hero", hero, chess.BLACK, arrows=[chess.svg.Arrow(sq("d5"), sq("c3"), color=PLAYED),
                                        chess.svg.Arrow(sq("d5"), sq("b6"), color=GREEN)])

# What it captures: your arrows (candidates), their idea (red), and a circled weak square.
caught = chess.Board("r4r1k/5pp1/2p1np1p/1p1q4/p7/2P2N2/PPQ2PPP/1K2R2R w - - 2 21")
save("arrows", caught, arrows=[chess.svg.Arrow(sq("e1"), sq("e6"), color=BLUE),
                               chess.svg.Arrow(sq("h1"), sq("d1"), color=BLUE),
                               chess.svg.Arrow(sq("d5"), sq("b3"), color=RED),
                               chess.svg.Arrow(sq("e6"), sq("e6"), color=GREEN)])

# The review: the engine's best line against yours.
save("review", hero, chess.BLACK, arrows=[chess.svg.Arrow(sq("d5"), sq("c3"), color=PLAYED)],
     fill={sq("c3"): "#e0313155", sq("b6"): "#2f9e4455"})
print("wrote", sorted(p.name for p in OUT.glob("*.svg")))

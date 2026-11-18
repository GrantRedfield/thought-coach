"""Thin wrapper around Stockfish: start it, search a position, turn results into win probability."""

from __future__ import annotations

import math
import os
import shutil
from dataclasses import dataclass

import chess
import chess.engine


def find_stockfish(path: str | None = None) -> str:
    """Locate the Stockfish binary: explicit path, then $STOCKFISH_PATH, then PATH."""
    for candidate in (path, os.environ.get("STOCKFISH_PATH"), shutil.which("stockfish")):
        if candidate and os.path.isfile(candidate):
            return candidate
    raise FileNotFoundError(
        "Stockfish not found. Pass --stockfish PATH or set the STOCKFISH_PATH environment variable "
        "(see README)."
    )


WP_MODELS = ("lichess", "sf-wdl")

# Lichess "win%" curve, fitted to human games on lichess (lichess WinPercent.scala):
# win% = 50 + 50 * (2 / (1 + exp(-k * cp)) - 1), cp clamped to +-1000, mate = +-1000.
LICHESS_K = 0.00368208
LICHESS_CP_CAP = 1000


def win_prob(info: chess.engine.InfoDict, color: chess.Color, model: str = "lichess") -> float:
    """Win probability (0-1) for `color` from one engine result.

    model="lichess": lichess's logistic curve on centipawns, fitted to human games.
    model="sf-wdl":  Stockfish's own WDL expectation (W + D/2), calibrated on engine play,
                     so it is much sharper (e.g. +2.6 is already ~1.0).
    """
    score = info["score"].pov(color) if "score" in info else None
    if score is not None and score.is_mate() and score.mate() == 0:  # checkmate on the board: the game is over
        return 1.0 if score == chess.engine.MateGiven else 0.0
    if model == "lichess":
        cp = score.score(mate_score=100_000)
        cp = max(-LICHESS_CP_CAP, min(LICHESS_CP_CAP, cp))
        return 1 / (1 + math.exp(-LICHESS_K * cp))
    if model == "sf-wdl":
        wdl = info.get("wdl")
        if wdl is None:
            wdl = info["score"].wdl(model="sf")
        return wdl.pov(color).expectation()
    raise ValueError(f"unknown win-probability model {model!r}; choose from {WP_MODELS}")


@dataclass
class EngineSettings:
    nodes: int = 250_000
    threads: int = 1
    hash_mb: int = 256
    multipv: int = 5


class Engine:
    """One Stockfish process. Use as a context manager so the process is always closed."""

    def __init__(self, path: str, settings: EngineSettings):
        self.settings = settings
        self._engine = chess.engine.SimpleEngine.popen_uci(path)
        options = {"Threads": settings.threads, "Hash": settings.hash_mb}
        if "UCI_ShowWDL" in self._engine.options:
            options["UCI_ShowWDL"] = True
        self._engine.configure(options)
        self.name = self._engine.id.get("name", "unknown engine")

    def analyse(
        self,
        board: chess.Board,
        multipv: int = 1,
        root_moves: list[chess.Move] | None = None,
        nodes: int | None = None,
    ) -> list[chess.engine.InfoDict]:
        """Search `board` for a fixed node budget. Always returns a list (one entry per PV line).

        A fresh `game` object makes python-chess send `ucinewgame`, which clears the hash table,
        so every search is independent and (with Threads=1) reproducible.
        """
        return self._engine.analyse(
            board,
            chess.engine.Limit(nodes=nodes or self.settings.nodes),
            multipv=multipv,
            root_moves=root_moves,
            game=object(),
        )

    def close(self) -> None:
        self._engine.quit()

    def __enter__(self) -> "Engine":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

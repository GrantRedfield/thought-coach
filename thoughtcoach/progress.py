"""Progress of a review while it runs, for the hosted service's live page.

    progress.report("engine", done=12, total=41, move={...})

Does nothing unless a hook is set (the hosted worker sets one that publishes the review's status).
Stages, in order (STAGES); each report says which stage is running and what it has found so far.
"""

from __future__ import annotations

from typing import Callable

STAGES = [
    ("game", "Fetching your game from lichess"),
    ("engine", "Stockfish checks every move you played"),
    ("voice", "Transcribing what you said"),
    ("tagging", "Picking out the moves you talked about"),
    ("thinking", "Lining up your arrows and words with each move"),
    ("report", "Comparing your thinking with the engine"),
    ("strike", "Finding who struck first"),
    ("story", "Writing your game story"),
]

HOOK: Callable[[str, dict], None] | None = None


def report(stage: str, **data) -> None:
    if HOOK is not None:
        try:
            HOOK(stage, data)
        except Exception:  # progress must never break a review
            pass

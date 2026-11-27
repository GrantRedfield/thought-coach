"""What a player said while solving a puzzle, as moves: the hosted worker's puzzle job.

The puzzle page records the player thinking out loud while they solve, then sends the audio with the
position, their move and their arrows. This turns the speech into the same kinds of thoughts a review
finds (moves they considered, what they thought the opponent wanted, squares they named), resolved on the
puzzle's board, so the page can compare them with the master's.

Privacy: the worker deletes the audio once this has run; only the text and the moves are kept.
"""

from __future__ import annotations

import chess

from .align import resolve_voice, with_turn
from .voice import ollama_available, transcribe, utterances, voice_events

WHISPER_MODEL, LLM_MODEL, OLLAMA = "small.en", "qwen2.5:3b", "http://127.0.0.1:11434"


def thoughts(fen: str, events: list[dict], played: str | None = None) -> dict:
    """Spoken mentions resolved on the puzzle's position: {candidates, ideas, squares}, each in the order said."""
    board = chess.Board(fen)
    me = board.turn
    move = chess.Move.from_uci(played) if played else None
    them = with_turn(board, not me)
    out: dict[str, list] = {"candidates": [], "ideas": [], "squares": []}
    for ev in events:
        r = resolve_voice(ev, me, board, board, move)
        if r["kind"] == "candidate" and r["move_san"] and r["legal"]:
            mv = board.parse_san(r["move_san"])
            item = {"san": r["move_san"], "uci": mv.uci(), "said": ev.get("utterance", "")}
            if item["uci"] not in [x["uci"] for x in out["candidates"]]:
                out["candidates"].append(item)
        elif r["kind"] == "opponent_idea" and r["move_san"] and them is not None:
            try:
                mv = them.parse_san(r["move_san"])
            except ValueError:
                continue
            item = {"san": r["move_san"], "uci": mv.uci(), "said": ev.get("utterance", "")}
            if item["uci"] not in [x["uci"] for x in out["ideas"]]:
                out["ideas"].append(item)
        elif r["kind"] == "key_square" and r["from"] and r["from"] not in out["squares"]:
            out["squares"].append(r["from"])
    return out


def process(job: dict) -> dict:
    """One recorded solve -> {transcript, said, candidates, ideas, squares}."""
    recording = {"game_id": job["id"], "audio": job["audio"]}
    transcript = transcribe(recording, WHISPER_MODEL)
    llm = LLM_MODEL if ollama_available(OLLAMA) else None
    events = voice_events(transcript, llm, OLLAMA)
    said = [u["text"].strip() for u in utterances(transcript) if u["text"].strip()]
    return {"transcript": " ".join(s["text"].strip() for s in transcript["segments"]).strip(), "said": said,
            "tagger": "llm" if llm else "rules", **thoughts(job["fen"], events, job.get("uci"))}

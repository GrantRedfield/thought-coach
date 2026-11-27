"""Milestone 5: voice. Transcribe the audio in a recording and find the chess ideas in it.

Pipeline (all local):
  1. transcribe   faster-whisper with word timestamps; times become wall-clock ms using each audio
                  segment's recorded start time, so they line up with the board positions
  2. mentions     rule-based: piece names, squares, "takes", "castles" -> structured mentions
                  (whisper's usual mishearings like "night" for knight are normalised first)
  3. tags         what each mention is about: my candidate, the opponent's idea, a weakness/target
                  square or my worst-placed piece. A local LLM (Ollama) does this when it is running;
                  otherwise keyword rules do. The LLM only labels what I said; it never judges moves.
Turning a mention into an actual move happens in align.py, against the position on screen.
"""

from __future__ import annotations

import base64
import json
import re
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

PIECES = {"king": "K", "queen": "Q", "rook": "R", "bishop": "B", "knight": "N", "pawn": "P"}
TAGS = ["candidate", "opponent_idea", "weakness", "worst_piece", "other"]

# Bias whisper towards chess vocabulary.
WHISPER_PROMPT = (
    "Chess thinking out loud. Knight f3, bishop takes e5, queen to h4, rook d1, pawn d4, "
    "castles kingside, he wants to play knight g4, the d5 square is weak, my worst piece is the bishop."
)


# ---------------------------------------------------------------- 1. transcription


def audio_files(recording: dict, folder: Path) -> list[tuple[int, Path]]:
    """Write the recording's audio segments to files: [(segment start ms, path)]."""
    files = []
    for i, seg in enumerate(recording.get("audio") or []):
        mime = seg.get("mime") or "webm"
        ext = ".webm" if "webm" in mime else ".m4a" if "mp4" in mime or "aac" in mime else ".ogg"  # Safari records mp4
        path = folder / f"{recording['game_id']}-{i}{ext}"
        path.write_bytes(base64.b64decode(seg["data_base64"]))
        files.append((seg["start_t"], path))
    return files


def decode_16k_mono(path: Path):
    """Audio file -> float32 numpy array at 16 kHz mono (what whisper expects).

    Done here rather than by faster-whisper, whose own decoder calls PyAV with an argument that
    newer PyAV versions no longer accept.
    """
    import av
    import numpy as np

    resampler = av.AudioResampler(format="flt", layout="mono", rate=16000)
    chunks = []
    with av.open(str(path)) as container:
        for frame in container.decode(audio=0):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None):  # flush
            chunks.append(out.to_ndarray().reshape(-1))
    return np.concatenate(chunks).astype(np.float32) if chunks else np.zeros(0, dtype=np.float32)


def transcribe(recording: dict, model_size: str = "small.en", device: str = "cpu") -> dict:
    """Words and segments with wall-clock times (ms since 1970)."""
    from faster_whisper import WhisperModel  # imported here: only needed when there is audio

    model = WhisperModel(model_size, device=device, compute_type="int8" if device == "cpu" else "float16")
    words, segments = [], []
    with tempfile.TemporaryDirectory() as tmp:
        for start_t, path in audio_files(recording, Path(tmp)):
            parts, _info = model.transcribe(
                decode_16k_mono(path), word_timestamps=True, vad_filter=True, initial_prompt=WHISPER_PROMPT,
                language="en",
            )
            for part in parts:
                seg_words = [
                    {"t": start_t + round(w.start * 1000), "end_t": start_t + round(w.end * 1000), "word": w.word.strip()}
                    for w in part.words or []
                ]
                words.extend(seg_words)
                segments.append(
                    {
                        "t": start_t + round(part.start * 1000),
                        "end_t": start_t + round(part.end * 1000),
                        "text": part.text.strip(),
                        "words": seg_words,
                    }
                )
    return {"model": model_size, "words": words, "segments": segments}


# ---------------------------------------------------------------- 2. mentions

# Common mishearings -> canonical words. Applied word by word, case-insensitive.
NORMALISE = {
    "night": "knight", "nights": "knight", "nite": "knight", "knights": "knight", "horse": "knight",
    "bishops": "bishop", "rooks": "rook", "brook": "rook", "rock": "rook", "rocks": "rook", "castle's": "castles",
    "queens": "queen", "kings": "king", "pawns": "pawn", "porn": "pawn", "prawn": "pawn",
    "take": "takes", "taking": "takes", "captures": "takes", "capture": "takes", "x": "takes",
    "castle": "castles", "castling": "castles", "short": "kingside", "long": "queenside",
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
    "seven": "7", "eight": "8",
}
FILE_WORDS = {"ay": "a", "bee": "b", "be": "b", "see": "c", "sea": "c", "dee": "d", "ee": "e", "ef": "f",
              "gee": "g", "aitch": "h"}


def tokens(text: str) -> list[str]:
    """Lower-case word tokens with mishearings normalised and squares joined ('e 4' -> 'e4')."""
    raw = re.findall(r"[a-zA-Z']+|\d", text.lower().replace("-", " "))
    words = [NORMALISE.get(w, w) for w in raw]
    out: list[str] = []
    i = 0
    while i < len(words):
        w = words[i]
        nxt = words[i + 1] if i + 1 < len(words) else ""
        f = FILE_WORDS.get(w, w)
        if len(f) == 1 and f in "abcdefgh" and nxt.isdigit() and nxt in "12345678":
            out.append(f + nxt)  # "e 4" / "ee four" -> "e4"
            i += 2
            continue
        if len(w) == 2 and w[0] in "abcdefgh" and w[1] in "abcdefgh" and nxt.isdigit() and nxt in "12345678":
            out += ["file:" + w[0], w[1] + nxt]  # "rook ae1" -> which rook, then the square
            i += 2
            continue
        if w in ("king", "queen") and nxt == "side":
            out.append(w + "side")
            i += 2
            continue
        out.append(w)
        i += 1
    return out


SQUARE = re.compile(r"^[a-h][1-8]$")


def find_mentions(text: str) -> list[dict]:
    """Moves and squares mentioned in one utterance, in order.

    Each mention: piece (K/Q/R/B/N/P or None), from/to squares, capture, castle ('K'/'Q'),
    and square_only=True for a bare square ("the d5 square", "f7 is weak").
    """
    toks = tokens(text)
    mentions = []
    i = 0
    while i < len(toks):
        t = toks[i]
        if t == "castles":
            side = "Q" if "queenside" in toks[i + 1 : i + 3] else "K"
            mentions.append({"piece": "K", "from": None, "to": None, "capture": False, "castle": side,
                             "square_only": False, "said": " ".join(toks[i : i + 2])})
            i += 1
            continue
        if t in PIECES:
            # piece [fillers] square [(to|takes) square]
            j = i + 1
            squares, capture, from_file = [], False, None
            while j < len(toks) and toks[j] in ("to", "on", "takes", "goes", "from", "into", "in", "the", "on"):
                capture = capture or toks[j] == "takes"
                j += 1
            # "rook ae1" / "rook a e1" / "knight b d2": which piece, by its file
            f = toks[j][5:] if j < len(toks) and toks[j].startswith("file:") else FILE_WORDS.get(toks[j], toks[j]) if j < len(toks) else ""
            if len(f) == 1 and f in "abcdefgh" and j + 1 < len(toks) and SQUARE.match(toks[j + 1]):
                from_file = f
                j += 1
            if j < len(toks) and SQUARE.match(toks[j]):
                squares.append(toks[j])
                j += 1
                # A second square only directly after "to"/"takes" (or right away): "bishop c1 to e3".
                k = j
                while k < len(toks) and toks[k] in ("to", "takes"):
                    k += 1
                if k < len(toks) and SQUARE.match(toks[k]) and k - j <= 1:
                    capture = capture or "takes" in toks[j:k]
                    squares.append(toks[k])
                    j = k + 1
            if squares:
                frm, to = (squares[0], squares[1]) if len(squares) == 2 else (None, squares[0])
                mentions.append({"piece": PIECES[t], "from": frm, "to": to, "capture": capture, "castle": None,
                                 "square_only": False, "said": " ".join(toks[i:j]).replace("file:", ""),
                                 **({"from_file": from_file} if from_file else {})})
                i = j
                continue
            mentions.append({"piece": PIECES[t], "from": None, "to": None, "capture": capture, "castle": None,
                             "square_only": False, "piece_only": True, "said": " ".join(toks[i:j])})
            i += 1
            continue
        if SQUARE.match(t):
            # square [takes|to] square  -> a move given by squares ("e2 e4", "d4 takes e5")
            j = i + 1
            capture = False
            while j < len(toks) and toks[j] in ("takes", "to"):
                capture = capture or toks[j] == "takes"
                j += 1
            if j < len(toks) and SQUARE.match(toks[j]) and j <= i + 2:
                mentions.append({"piece": None, "from": t, "to": toks[j], "capture": capture, "castle": None,
                                 "square_only": False, "said": " ".join(toks[i : j + 1])})
                i = j + 1
                continue
            if i + 1 < len(toks) and toks[i + 1] == "takes":  # "d4 takes" without a target: skip
                i += 1
                continue
            prev = toks[i - 1] if i else ""
            pawn_push = prev in ("push", "play", "plays", "playing", "pawn")
            takes = "takes" in toks[max(0, i - 2) : i]  # "takes e5", "takes on e5"
            mentions.append({"piece": "P" if pawn_push else None, "from": None, "to": t, "capture": takes,
                             "castle": None, "square_only": not pawn_push and not takes,
                             "said": " ".join(toks[max(0, i - (2 if takes else 1)) : i + 1])})
        i += 1
    return mentions


# ---------------------------------------------------------------- 3. tags

OPPONENT_WORDS = {"he", "his", "him", "they", "their", "them", "opponent", "threat", "threatens", "threatening",
                  "wants", "idea", "attacking", "attacks", "coming", "danger", "dangerous", "worried", "scared"}
WEAKNESS_WORDS = {"weak", "weakness", "weaknesses", "hole", "holes", "target", "targets", "outpost", "backward",
                  "isolated", "loose", "hanging", "undefended"}
WORST_WORDS = {"worst", "passive", "misplaced", "badly", "useless", "bad", "improve", "reroute", "rerouting",
               "sucks", "terrible", "awful", "horrible", "ugly", "stuck", "buried", "dead", "inactive", "trapped"}


def tag_by_rules(utterance: str, mention: dict) -> str:
    words = set(tokens(utterance))
    if words & WEAKNESS_WORDS and mention["square_only"]:
        return "weakness"
    if words & WORST_WORDS and (mention.get("piece_only") or "worst" in words):
        return "worst_piece"
    if words & OPPONENT_WORDS:
        return "opponent_idea"
    if mention["square_only"]:
        return "weakness" if words & WEAKNESS_WORDS else "other"
    return "candidate"


LLM_INSTRUCTIONS = """You label what a chess player said while thinking aloud during their own game.
For each numbered mention, answer which of these it is, from the speaker's point of view:
  candidate      a move the speaker considers playing
  opponent_idea  a move or plan the speaker thinks the OPPONENT wants (he/they/threat/wants)
  weakness       a weak square, weak pawn or target (for either side)
  worst_piece    the speaker's own worst-placed piece
  other          anything else
Do not judge whether moves are good. Reply with JSON only: {"tags": ["candidate", ...]} with one
tag per mention, in order."""


def guard(tag: str, utterance: str, mention: dict) -> str:
    """'Worst piece' is decided by the words, not the small model: it called a bare "rook" someone's
    worst piece, and "The bishop sucks." a candidate. Without words like "worst", "passive" or
    "sucks" it is never a worst piece; with them, a piece named on its own always is."""
    words = set(tokens(utterance))
    if tag == "worst_piece" and not words & WORST_WORDS:
        return tag_by_rules(utterance, mention)
    if tag != "worst_piece" and words & WORST_WORDS and mention.get("piece_only"):
        return "worst_piece"
    return tag


def ollama_available(url: str) -> bool:
    try:
        with urllib.request.urlopen(f"{url}/api/tags", timeout=2):
            return True
    except Exception:
        return False


# Ollama is reached at 127.0.0.1, not "localhost": on Windows "localhost" tries IPv6 first and
# every call waited about 2 s for that to fail. The model is kept loaded between calls, and the
# instructions come first so Ollama reuses them from its cache: only the clause is new each time.
KEEP_ALIVE = "30m"


GPU_FAILED = False  # Ollama's GPU runner crashed once (e.g. a CUDA build the driver can't run): CPU from then on


def _post(url: str, body: dict, timeout: float) -> dict:
    """POST to Ollama. If the model can't load on the GPU, the request is made again on the CPU, and so
    is every later one (else each would wait ~30 s for the GPU runner to crash again)."""
    global GPU_FAILED
    if GPU_FAILED and "options" in body:
        body = {**body, "options": {**body["options"], "num_gpu": 0}}
    request = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        if GPU_FAILED or "options" not in body or body["options"].get("num_gpu") == 0 or not any(
                w in detail for w in ("CUDA", "llama-server", "terminated")):
            raise
        GPU_FAILED = True
        print("the language model won't load on the GPU: using the CPU from here on", flush=True)
        return _post(url, body, timeout)


def warm_up(model: str, url: str, gpu: bool = False) -> threading.Thread:
    """Load the model in the background (about 30 s from disk) while something else runs."""
    def load():
        try:
            _post(f"{url}/api/generate", {"model": model, "keep_alive": KEEP_ALIVE,
                                          "options": {} if gpu else {"num_gpu": 0}}, 300)
        except Exception:
            pass
    thread = threading.Thread(target=load, daemon=True, name="llm-warm-up")
    thread.start()
    return thread


def tag_with_llm(utterance: str, mentions: list[dict], model: str, url: str, gpu: bool = False) -> list[str] | None:
    """Tags from a local Ollama model, or None if it fails or answers in the wrong shape."""
    listing = "\n".join(f"{i + 1}. \"{m['said']}\"" for i, m in enumerate(mentions))
    body = {
        "model": model,
        "prompt": f"{LLM_INSTRUCTIONS}\n\nThe player said: \"{utterance}\"\nMentions:\n{listing}",
        "format": "json",
        "stream": False,
        "keep_alive": KEEP_ALIVE,
        "options": {"temperature": 0, **({} if gpu else {"num_gpu": 0})},
    }
    try:
        answer = json.loads(_post(f"{url}/api/generate", body, 120)["response"])
        tags = answer["tags"]
        if len(tags) == len(mentions) and all(t in TAGS for t in tags):
            return tags
    except Exception:
        pass
    return None


# ---------------------------------------------------------------- putting it together


CLAUSE_END = re.compile(r"[.,;:!?]$")


PAUSE_MS = 1500


def utterances(transcript: dict) -> list[dict]:
    """Clauses of what was said (text, start time, words).

    Whisper segments can hold several sentences, so they are split at punctuation: "I could play
    knight f3, he wants queen h4" is two clauses, and "he" only colours the second one. A pause of
    more than PAUSE_MS also ends a clause.
    """
    clauses = []
    for seg in transcript["segments"]:
        words = seg.get("words") or []
        if not words:
            if seg["text"]:
                clauses.append({"text": seg["text"], "t": seg["t"], "words": []})
            continue
        current: list[dict] = []
        for w in words:
            if current and current[-1].get("end_t") and w["t"] - current[-1]["end_t"] > PAUSE_MS:
                clauses.append({"text": " ".join(x["word"] for x in current), "t": current[0]["t"], "words": current})
                current = []  # a long pause also ends a thought
            current.append(w)
            if CLAUSE_END.search(w["word"]):
                clauses.append({"text": " ".join(x["word"] for x in current), "t": current[0]["t"], "words": current})
                current = []
        if current:
            clauses.append({"text": " ".join(x["word"] for x in current), "t": current[0]["t"], "words": current})
    return [c for c in clauses if c["text"].strip()]


def voice_events(
    transcript: dict, llm_model: str | None = None, llm_url: str = "http://127.0.0.1:11434", llm_gpu: bool = False,
    cache: dict | None = None,
) -> list[dict]:
    """Every mention as a `voice` event (time-stamped), ready to merge with the recording's events.

    `cache` (clause and mentions -> LLM tags) is read and filled in, so a game reviewed again
    doesn't ask the model about the same words twice."""
    use_llm = bool(llm_model) and ollama_available(llm_url)
    cache = {} if cache is None else cache
    events = []
    for utt in utterances(transcript):
        mentions = find_mentions(utt["text"])
        if not mentions:
            continue
        key = f"{llm_model}|{utt['text']}|" + "|".join(m["said"] for m in mentions)
        tags = cache.get(key)
        if tags is None and use_llm:
            tags = tag_with_llm(utt["text"], mentions, llm_model, llm_url, llm_gpu)
            if tags:
                cache[key] = tags
        tagger = "llm" if tags else "rules"
        tags = tags or [tag_by_rules(utt["text"], m) for m in mentions]
        tags = [guard(t, utt["text"], m) for t, m in zip(tags, mentions)]
        for mention, tag in zip(mentions, tags):
            events.append(
                {
                    "type": "voice",
                    "t": _mention_time(utt, mention),
                    "tag": tag,
                    "tagger": tagger,
                    "utterance": utt["text"],
                    **mention,
                }
            )
    return events


def _mention_time(utt: dict, mention: dict) -> int:
    """Time of the first word of the mention (falls back to the utterance start)."""
    target = mention["said"].split()[0] if mention["said"] else ""
    for w in utt.get("words", []):
        if tokens(w["word"])[:1] == [target] or (target and target in tokens(w["word"])):
            return w["t"]
    return utt["t"]

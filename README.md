# Thought Coach

Free, open-source chess coaching tool. It compares what you were **thinking** during a game
(lichess arrows, voice notes) with Stockfish's analysis of the same positions, to find gaps
in your thinking.

**Fair play:** nothing here runs during a game. All analysis happens after the game is over.

**Use it without installing anything but the extension:** go to <https://thoughtcoach.org/start>,
sign in with lichess, and follow the three steps there (install the extension, it connects itself,
play). Reviews show up at `thoughtcoach.org/u/<your lichess name>/`. Hosting it yourself on AWS:
[deploy/README.md](deploy/README.md). Everything below is for running it on your own PC.

Status:
- **Milestone 1, engine analysis.** Given a PGN, for each of your moves it outputs the
  position, your move, win probability before/after, the opponent's idea found with a null move,
  and the engine's candidate moves.
- **Milestone 2, arrow capture.** A read-only userscript records the arrows and circles you
  draw during lichess games and exports them as JSON when the game ends.
- **Milestone 3, alignment.** Matches a recording to the game's PGN and builds one thinking
  window per move of yours, with each arrow read as a candidate, an opponent's idea or a key square.
- **Milestone 4, gap report.** Compares what you drew with the engine for Q3 (opponent's idea)
  and Q4 (candidates), and attributes each mistake to the gap that explains it.
- **Milestone 5, voice.** The userscript also records your voice while you play. `review`
  transcribes it locally with faster-whisper, finds the moves and squares you mention, tags each one
  (candidate, opponent's idea, weakness, worst piece) with a local LLM, and merges them with your
  arrows.

## Setup (Windows)

1. **Python 3.11+** (tested with 3.13).

2. **Stockfish**
   - Download the Windows build from <https://stockfishchess.org/download/>. The
     `x86-64-avx2` build suits most CPUs from 2015 on. If it crashes, use the plain `x86-64` build.
   - Unzip it somewhere permanent, e.g. `C:\Tools\stockfish\`.
   - Tell Thought Coach where it is (PowerShell, then open a new terminal):
     ```powershell
     setx STOCKFISH_PATH "C:\Tools\stockfish\stockfish-windows-x86-64-avx2.exe"
     ```
     Or pass `--stockfish PATH` on every run.
   - macOS: `brew install stockfish`. Debian/Ubuntu: `sudo apt install stockfish`.
     Both put `stockfish` on PATH, so no variable is needed.

3. **Python packages**, from the project folder:
   ```powershell
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

4. **Voice (optional): Ollama**, the local LLM that tags what you said. Install it from
   <https://ollama.com/download> (or `winget install Ollama.Ollama`), then:
   ```powershell
   ollama pull qwen2.5:3b
   ```
   Without it, speech is tagged with simple keyword rules. The whisper speech model (`small.en`,
   about 480 MB) downloads automatically the first time a recording with voice is reviewed.
   On this machine Ollama runs on the CPU, about 1.2 s per spoken clause with the 3B model
   (2.4 s with `qwen2.5:7b`). Over 9 test games the 3B and 7B models gave identical reviews, once
   "worst piece" is decided by the words said ("worst", "passive", "sucks"...) rather than by the
   model (`guard` in `voice.py`). Tags are cached per game in `out/<id>.tags.json`.

5. **Check it works:**
   ```powershell
   pytest
   ```
   The engine test is skipped (not failed) if Stockfish can't be found.

## Usage

Export a game from lichess (game page → share/export → PGN, with **clock comments** on), then:

```powershell
python -m thoughtcoach analyse my_game.pgn --me YourLichessName
```

This writes `out\<gameid>.json` (all data) and `out\<gameid>.txt` (readable report).
The PGN can contain many games. Games you didn't play in are skipped.
To print the report again from the JSON:

```powershell
python -m thoughtcoach report out\<gameid>.json
```

### Options

| option | default | meaning |
|---|---|---|
| `--me NAME` / `--color white\|black` | required | which side is you |
| `--nodes` | 250,000 | Stockfish nodes per search (3 searches per move) |
| `--threads` | 1 | more threads = faster but results are no longer reproducible |
| `--multipv` | 5 | lines searched for candidate moves |
| `--margin` | 0.05 | candidates = lines within this win probability of the best |
| `--book-moves` | 8 | your moves up to this move number count as book |
| `--book FILE` | – | optional Polyglot `.bin` book: your move found in it = book |
| `--decided` | 0.05 | skip if the best line *and* your move both stay < 0.05 (or both > 0.95) |
| `--wp-model` | `lichess` | `lichess` (human-fitted curve) or `sf-wdl` (Stockfish WDL) |
| `--out` | `out` | output folder |

## Easiest: browser extension + local server

The extension records each of your lichess games (arrows, pieces you pick up, and your voice if
you want), and sends the recording to the Thought Coach server on your own computer when the game
is over. The server reviews it and the extension shows a notification with the dashboard link.

**Once:**
1. Start the server (keep its window open while you play):
   ```
   python -m thoughtcoach serve
   ```
   It listens on http://127.0.0.1:8770 (only your own computer can reach it). Your reviewed
   games are listed there.
2. In Chrome, open `chrome://extensions`, turn on **Developer mode** (top right), click
   **Load unpacked** and pick the [`extension`](extension) folder. Pin the Thought Coach icon.
3. If you used the Tampermonkey userscript before, turn it off so the game isn't recorded twice.

**Then just play.** The first time, lichess asks for the microphone (voice can be turned off in
the extension's popup). When a game ends you get "review ready" about 1–4 minutes later (the
first review also loads the speech model).

**The home page** (http://127.0.0.1:8770) lists your games and, from two games on, shows
**patterns across your games**: things one game is too small to show. Strong and weak points
(each with the number of moves behind it; under 30 moves it is marked as an early signal), and
charts: how often moves go wrong by think time, by your candidate moves (how many, and whether a
good move was among them), and by game phase; your clock against your opponent's by move number;
win % lost per move in each game, oldest to newest; where the win % went; and costly moves by
piece. Time-trouble moves are kept out of the thinking patterns and counted on their own.
Everything comes from the saved reviews (`thoughtcoach/profile.py`), no new engine work.

**Your game story** (`/games/<id>/story`, `thoughtcoach/story.py`): each game as a short run of
full-screen cards you tap through (tap or → for next, ← back, space to pause; they also play on
their own). It opens with the matchup and how much you drew and said, then shows only the moments
that stand out in that game, at most six, in the order they happened. They are picked from the
turning point, a good move you drew and dropped, a threat you missed or saw (Aagaard 3), a weakness
your move made, you found or missed (Aagaard 1), a stuck or named worst piece (Aagaard 2), your
thoughts (called it, read their plan, reactions), the first strike, and the clock. Each card shows
the position with your arrows and what you said. It ends with the whole game's win % (the moments
numbered on it) and **one focus for your next game**: the cause that cost the most win chance this
game, weighted towards what keeps coming back across your games, with a habit to try. The
extension's "review ready" notification opens the story; the dashboard is one tap away.

**The mental game** (`thoughtcoach/psych.py`), from your voice and arrows against the engine:
*you called it lost too early* ("I'm lost", "busted", "white's winning" while the engine still gave
you 25%+, and whether the game came back); *the tide turned* (the opponent move that handed the
game back, whether anything you said showed you noticed, and whether you took it); *instinct vs
calculation* (System 1 / System 2: how often your first arrow was already good and you talked
yourself out of it, and how often more thinking rescued a poor first idea); and *the inner game*
(after Gallwey: judging remarks about yourself, and how the next moves went). Also *seen right
after moving*: the better idea named out loud in the 25 s after a costly move. Each can be a story
card, and "Play it out", "Notice, don't judge" and "Look once more before you let go" can be the
focus for next game.

**Who struck first** (`thoughtcoach/strike.py`, no extra engine work): the moment a balanced game
breaks open into tactics. It is the first run of captures and checks (not starting with a
recapture) that begins with both sides between 20% and 80% and matters: the win chance moves 15%
or more, or someone comes out 2+ pawns of material ahead. Even trades don't count. The side that
starts it struck first; if the other side's move just before lost 8% or more, that move "opened
the door". Each game says who struck, with what, whether it worked, and what opened it; the
dashboard shows it as a third "In short" card and a marker on the win % chart, and the home page
counts who struck first and how those games ended.

**Aagaard's questions** get their own section on the home page (`thoughtcoach/questions.py`):
where are the weaknesses, which is your worst piece, what does your opponent want, and was the
best move among your candidates. Each shows how often you saw it, what that depends on (their
weaknesses vs your own, which piece, spotted vs stopped by luck, best move rejected vs never
considered), and three positions from your games (the worst misses and one you saw), linking to
the game. Question 3 counts only threats you drew; ones your move happened to stop are shown but
not counted as seen.

Each game's dashboard has the same four panels below the one-screen summary (scroll down, or
click the "Aagaard's three questions" tile), with up to six positions per question from that game;
a board opens that position on lichess.

The popup shows whether the server is connected, your recent games and their review status, and
lets you send the current game again or download its recording as a file.

**Fair play:** the extension shows nothing while you play and only sends a game once it's over.
The server checks with lichess too: a game lichess still shows as in progress is refused (the
extension tries again a minute later). Nothing is analysed during a game.

**Privacy:** recordings and voice go only to the server address set in the popup (your own
computer by default). After handing a game to the extension's upload queue, the recorder deletes
its own copy of the audio from the browser. Recordings are saved in `recordings/`, results in `out/`.

## Capturing your thinking (lichess userscript: arrows + voice)

### Install (once)
1. Install the **Tampermonkey** extension for your browser (Chrome, Edge, Firefox or Brave).
2. Open the Tampermonkey dashboard, click **+** (new script), delete the template, paste in the
   whole of [`userscript/thoughtcoach-arrows.user.js`](userscript/thoughtcoach-arrows.user.js),
   then **File → Save**.
3. In Chrome/Edge, Tampermonkey may need **Developer mode** turned on (or "Allow user scripts"
   on the extension's details page) before userscripts run.

### During a game
Just use lichess's default drawing. No colours or modifier keys are needed:

- **Arrow** (right-click drag): a move you're considering, yours or your opponent's.
- **Circle** (right-click a square): a square that matters, such as a weakness or target.

What an arrow means is worked out after the game, from the position at that moment
(planned for Milestone 3):

| what you drew | read as |
|---|---|
| arrow starting on **your** piece | your candidate move |
| arrow starting on your **opponent's** piece | the opponent's idea or threat you saw |
| circle | weakness or target square |

If you do use lichess's other colours (Shift, Alt), they're recorded, but nothing depends on them.

**Voice:** just think out loud. The first time, the browser asks lichess.org for microphone access;
allow it. Recording only runs while you are **playing** (never when watching games) and stops when
the game ends. Speak the way you think, e.g. "knight f3 or bishop g5", "he wants queen h4",
"the d5 square is weak", "my worst piece is the bishop on c1". Moves are recognised by piece and
square ("knight takes e5", "bishop c1 to e3", "castle short"), and checked against the position on
the board at the moment you said them. Turn voice off or on from the Tampermonkey menu.

**Picking up pieces:** nothing to do. Every time you pick up a piece (click it, or start dragging
it) and let go again, the script notes which square, read from lichess's own highlight of the
selected square (needs script 0.5.0). The review lists the pieces you picked up but didn't move, and
flags when one of them was the piece the engine's best move needed.

The script records each arrow or circle when you finish drawing it, plus when it disappears,
and the board position after each move, with both clocks. Recordings are saved in Tampermonkey's storage as you go,
so reloading the page loses nothing.

### After the game
When the game ends, `thoughtcoach-arrows-<gameid>.json` downloads automatically. It is one file
holding the arrows and the voice recording (about 0.25 MB per minute of audio).
The Tampermonkey menu (click the extension icon on a lichess page) has:
- **Export arrows + voice for this game**: download it again.
- **Status**: what the script sees (game, board, clocks, whether voice is recording).
- **Turn voice recording on/off**.
- **Export all recorded games**: one bundle file.
- **Delete all recorded games (and voice)**.

To check what was captured:
```powershell
python -m thoughtcoach arrows D:\Downloads\thoughtcoach-arrows-<gameid>.json
```
(Use whatever folder your browser saves downloads to.)

### Fair play and privacy
The script only **reads** the board (and records your microphone while you play). It computes
nothing, shows nothing, adds nothing to the page, makes no network requests and never touches your
moves. Voice is kept in this browser's storage until the game ends and is then put into the
downloaded file; it is never uploaded anywhere. Transcription and tagging run on your own computer. Lichess game URLs you play from can contain
a secret 4-character player token after the 8-character game id. The script only ever stores the
8-character id.

### Limits
- Moves are recorded as **board positions**, read from where the pieces are drawn, not from the
  move list, which lichess deliberately scrambles. Milestone 3 matches each position to the PGN.
  When a premove makes two moves land at once, one position is skipped, but nothing after it is
  shifted. This works on both the 2D and 3D boards, from either side.
- Lichess clears your arrows when you left-click the board (e.g. to move a piece). Those removals
  are recorded too, so you can see how long each idea stayed on the board.

## Reviewing a recorded game (all steps)

```powershell
python -m thoughtcoach review D:\Downloads\thoughtcoach-arrows-<gameid>.json
```
This runs everything for the game: it downloads the PGN, runs the engine analysis (reused next
time if the settings haven't changed), transcribes and tags your voice if there is any
(`out\<gameid>.transcript.txt` shows what it heard), builds the thinking windows and writes the gap
report `out\<gameid>.gaps.txt`, and the **dashboard** `out\<gameid>.html` (open it in a
browser; the command prints its link). It takes a few minutes per game, mostly the speech tagging.
It takes the same engine options as `analyse` (e.g. `--book-moves 3` for short test games), plus:

| option | default | meaning |
|---|---|---|
| `--mistake` | 0.05 | WP loss that counts as a costly move |
| `--critical` | 3 | critical moments to show on boards |
| `--time-trouble` | 30 | seconds on your clock below which costly moves count as time trouble |
| `--threat-swing` | 0.10 | null-move swing that counts as a real threat |
| `--reanalyse` | off | redo the engine analysis even if a cached one exists |
| `--whisper-model` | small.en | speech model (`base.en` is faster, `medium.en` more accurate) |
| `--retranscribe` | off | transcribe again even if a cached transcript exists |
| `--llm-model` | qwen2.5:3b | Ollama model that tags speech (`qwen2.5:7b`: same results, half the speed) |
| `--no-llm` | off | tag speech with keyword rules only |
| `--llm-gpu` | off | let Ollama use the GPU (needs a recent NVIDIA driver) |

### What the gap report says
Win probabilities are shown as percentages (0.53 -> 53%), and a loss is in percentage points
(53% -> 25% is "-28%"). Command-line thresholds still take fractions: `--mistake 0.05` means 5%.

For each move:
- **Q4, candidates:** the engine's candidates (within 0.05 of the best) compared with what you drew
  or played: **found**, **wrongly rejected** (you drew a good move but played one outside the
  margin), **missed** (neither drawn nor played), and moves you drew that aren't engine candidates,
  each with the WP it gives up.
- **Q3, opponent's idea:** the engine's null-move threat. It counts as real if its swing is ≥ 0.10,
  and as **drawn** if you drew an arrow with the same squares.
- **Q1, weaknesses** (Aagaard's first question), for both sides, only what the engine's main line
  actually uses within 8 plies:
  - a weak pawn (isolated, doubled, backward) it captures, but not as a recapture: taking back on
    the square the other side just captured on is an exchange (10...Nxe5 after dxe5), not a weakness;
  - a hole (a square that side's pawns can never cover again; not on the a/h-files) where an enemy
    piece settles: it goes there without capturing a piece and isn't taken straight back;
  - a loose piece: a knight, bishop, rook or queen the line captures while nothing defends it, that
    isn't taken back, without the taker ending up behind on material (winning the bishop that
    stands on a hole is this, not the hole).
  Your own weak pawns and holes are also taken from the opponent's threat line (pieces it would
  win are threats, Q3). Nothing is counted once the game is decided (your win chance under 20% or
  over 80%). A weakness counts once per game.
  **Created by your own move:** after a move of yours that cost 3% or more, your weaknesses that the
  opponent's best reply uses and that weren't there before the move (19...b5 taking the b6 pawn's
  guard off the knight on c5) are flagged "created by b5". These count as seen only if you pointed
  at the square while thinking about that move. It's **seen** if you circled or named the square within
  10 moves before it showed up (or while it lasted); for their weakness an arrow of yours going
  there also counts, for yours an arrow of theirs going there.
- **Q2, worst piece** (Aagaard's second question): your least active knight, bishop, rook or queen,
  if it reaches at most 25% of the squares that kind of piece can (not counting squares with your
  own pieces or covered by enemy pawns). Rooks and the queen still on their starting square before
  move 15 are left out. This one is read from the board; the engine's line is shown next to it:
  the move its plan improves that piece with, if any (a bad bishop behind its own pawns often
  has none). It's **seen** if you called that piece your worst ("the bishop sucks") or drew a move
  with it. Each piece on its square counts once per game.
  Both are in the dashboard's "Aagaard's three questions" tile, in each critical moment's replay
  (brown squares: weaknesses; pink: worst piece) and in the summary and insights.

Each **costly move** goes to the first gap that explains it: **time trouble** (under 30 s on your
clock, where there's no time to draw or talk, so it says nothing about your thinking), the opponent's idea not seen (a real threat
you didn't draw, and your move did about as badly as ignoring it), a good move drawn then rejected,
good moves not drawn, nothing drawn at all, or other. The summary adds up the WP lost per gap.
An idea you didn't draw isn't proof you never had it, so read "not drawn" literally.
A real threat you didn't draw counts as **handled** when your move kept you more than 0.05 clear of
what ignoring it would cost (taking the attacker, a recapture), whether or not that move was the best.

### Dashboard
`out\<gameid>.html` (the command prints its link) puts every observation on one screen:
- **In short:** two cards at the top, one thing that **went well** and one to **watch out for**,
  each one sentence from fixed rules (`verdict` in `thoughtcoach/insights.py`, no LLM).
  - *Went well* picks the strongest of: threats you drew before they happened (at least half of
    them), opponent moves you predicted, only-moves you found, the engine's best move among your
    ideas in half your moves or more, a long run with no costly move, reaching a winning position,
    or (last resort) how many moves cost under 2%.
  - *Watch out for* takes the gap that cost the most and tells it through its worst move: the
    clock (and when you dropped under 30 s), trusting a good candidate you drew, checking their
    idea first, or drawing a second candidate. Click it to open that moment's board.
  The same two lines open the gap report as "IN SHORT". With this summary the takeaways strip is
  left out; its "right piece, other move" line is now one of the insights below.
- **Header:** WP lost in total and in costly moves you had time for, real threats seen or stopped,
  and how often the engine's best move was among the ones you considered.
- **Takeaways:** the gaps that cost the most, in plain words with the moves, plus the threat count.
- **Critical moments** (tabs): the costliest moves you had time to think about (then the sharpest
  positions, then time-trouble moves), each played back one idea at a time: the position, the
  opponent ideas you saw (purple) and squares you marked, each move you considered (light blue)
  with its engine line and win probability, the move you played (dark blue), the engine's best
  (green), good moves you missed (orange), and the opponent's threat (red). Pause, step, or jump
  to an idea with the buttons under the board.
- **The game, move by move:** three charts on one move axis. Your win % (costly moves as dots
  sized by what they cost, numbered when they're a critical moment: click to open), both clocks
  (you in blue, your opponent in orange, with the 30 s line), and how long you thought about each
  move (costly moves dark). Time trouble is shaded through all three. Hover anywhere for that
  move's win %, candidates drawn, both clocks and think time.
- **Headline numbers:** costly moves with time and in time trouble, plus two small bars: threats
  (drawn / dealt with by your move / missed) and how often the best move was among yours.
- **Where the WP went:** WP lost per gap (only gaps that cost something), in one row.
- **From your thoughts:** up to four insights that pair something you said or drew with what the
  engine and the game did (also in the gap report as "FROM YOUR THOUGHTS"). Each one is a fixed
  rule (`thoughtcoach/insights.py`), not an LLM opinion:
thoughtcoach/server.py    local server: takes uploads from the extension, reviews them one at a time
extension/                Chrome extension: records games and uploads them when they end
  - *You called it:* a square you called weak (said with a word like "weak", "hole" or "target",
    or marked) is where the opponent landed right after one of your costly moves, within 8 moves.
  - *One idea at the key moment:* a costly move (0.10+) where the move you played was the only
    one you drew or said.
  - *Your reaction / Feeling good / Second-guessing / Doubt:* something you exclaimed ("oh man",
    "jackpot", "should have"), with the engine's view of that moment and how your next 3 moves
    went (WP lost, seconds per move against your game average). Only shown when the board backs
    it up: a swing or loss of 0.10+, or confidence in a position under 0.55.
  - *You saw the clock coming:* the first time you mention the clock ("hustling me on time",
    "21 seconds"), with both clocks then, your pace after it, and what time trouble later cost.
  - *You read their plan:* opponent moves you drew before they were played.
  - *Thinking ahead:* your own move drawn a move or more before you played it.

On a small window the layout stacks and scrolls instead. `--critical 5` shows more moments.

## Thinking windows (alignment)

```powershell
python -m thoughtcoach align D:\Downloads\thoughtcoach-arrows-<gameid>.json
```
This downloads the game's PGN from lichess into `games\`, unless you pass `--pgn`. It writes
`out\<gameid>.thinking.json` and `out\<gameid>.thinking.txt`.

For each of your moves:
- **own time:** from when the opponent's move appeared on your board to when yours did, from
  the recording. The PGN clock time is shown next to it.
- **opponent time:** how long the opponent thought before that. Arrows you drew then count towards
  this move, with a negative "drawn" time.
- **shapes:** every arrow and circle, placed by the **position on the board** when you drew it
  (matched to the PGN), not by timestamps alone. Each one is read by what stands on its starting
  square:

| shape | read as | also recorded |
|---|---|---|
| arrow from your piece | **candidate** | the move in SAN, whether it's legal, whether you played it |
| arrow from the opponent's piece | **opponent's idea** | their move in SAN, as if they could move again (null move) |
| arrow from an empty square | **route** (e.g. a plan's second step) | |
| circle | **key square** | |

A circle drawn on an arrow's target square within 1 second of the arrow is counted as part of
that arrow (`target_circled`, shown as `o` in the report), not as a separate key square.
Each shape also records how long it stayed on the board. Repeated drawings of the same idea are
kept as they are; Milestone 4 will group them.

## What is computed (per move of yours)

All win probabilities (WP) are **yours**, 0–1. By default they come from the
**lichess win% curve**, a logistic curve on Stockfish's centipawn score fitted to human
games on lichess: `WP = 1 / (1 + exp(-0.00368208 · cp))`, with cp capped at ±1000 and
mate counted as ±1000. So +1.00 ≈ 0.59, +2.60 ≈ 0.72, and the 0.95 "decided" line is about +8.
`--wp-model sf-wdl` uses Stockfish's own WDL expectation (W + D/2) instead. That model is
calibrated on engine play and far sharper: +2.6 is already about 1.0.

- **wp_before**: WP of the engine's best line in the position you faced.
- **wp_after / wp_loss**: a search restricted to the move you played (`root_moves`),
  so it's directly comparable with the best line.
- **candidates**: MultiPV lines within `--margin` of the best.
  `candidates_may_be_truncated` means every line made the cut, so more good moves may exist.
  `played_within_margin` / `played_in_candidates` say whether your move was good enough
  and whether it was in the list.
- **threat** (opponent's idea): the opponent's best move if you passed (null move).
  `swing` = wp_before − WP if you ignored it. Not computed when you're in check.
- **time_spent**: previous clock − clock after the move + increment (from `%clk`).
- **skipped**: `book`, `forced` (only one legal move) or `decided`. Skipped moves are kept
  in the JSON with the reason. A position only counts as `decided` if your move keeps it
  decided, so throwing away a won game is always analysed.

## Project layout

```
thoughtcoach/engine.py    Stockfish wrapper, WDL -> win probability
thoughtcoach/analyse.py   per-move analysis
thoughtcoach/report.py    text report
thoughtcoach/arrows.py    load and summarise arrow recordings
thoughtcoach/align.py     thinking windows: recording + PGN
thoughtcoach/gaps.py      gap report and mistake attribution
thoughtcoach/voice.py     transcription, spoken-move parsing, tagging
thoughtcoach/critical.py  critical moments: picking them, idea-by-idea board animation
thoughtcoach/dashboard.py one-screen dashboard
thoughtcoach/insights.py  "from your thoughts": what you said and drew, checked against the engine
thoughtcoach/aagaard.py   Aagaard Q1 weaknesses and Q2 worst piece, compared with what you marked and said
thoughtcoach/profile.py   patterns across all your reviewed games (the home page)
thoughtcoach/questions.py Aagaard's questions across games, with example positions
userscript/               Tampermonkey userscript (arrow capture)
thoughtcoach/__main__.py  command line
tests/                    pytest + a sample PGN
```

## License

Apache License 2.0. See [LICENSE](LICENSE). Stockfish is a separate program under GPL-3.0.
Thought Coach only talks to it over UCI and does not include it.

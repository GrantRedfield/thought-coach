"""How win probabilities are shown: as percentages (stored as 0-1 fractions everywhere else)."""
import math


def pct(x: float | None) -> str:
    """0.534 -> '53%'; below 1% one decimal ('0.4%'). Differences are percentage points."""
    if x is None:
        return "?"
    v = x * 100
    if v != 0 and abs(v) < 1:
        return f"{v:.1f}%"
    return f"{v:.0f}%"


def went(before: float | None, after: float | None) -> str:
    """How a win chance changed, shown as where it went: '80% → 16%' (never as a difference)."""
    return f"{pct(before)} → {pct(after)}"


def move_went(m: dict) -> str:
    """'80% → 16%' for one move (anything with wp_before / wp_after)."""
    return went(m.get("wp_before"), m.get("wp_after"))


# ---------------------------------------------------------------- accuracy
# How well a set of moves kept your winning chances, on the same scale as lichess's accuracy:
# 100% = no move gave any away. Each move's score comes from the win chance it lost (lichess's
# AccuracyPercent curve); a set's is the average of the plain and the harmonic mean of those, so one
# blunder pulls it down more than a plain average would (lichess blends the same two means with its
# own weighting, so the numbers are close to lichess's but not always identical).
ACCURACY_EXPLAIN = ("How well your moves kept your winning chances, like lichess's accuracy score. "
                    "100% = no move gave any away; one blunder pulls it down a lot.")


def move_accuracy(loss: float) -> float:
    """One move's accuracy (0-100) from the win chance it lost (0-1)."""
    a = 103.1668 * math.exp(-0.04354 * max(0.0, loss) * 100) - 3.1669 + 1  # lichess adds 1 for engine noise
    return max(0.0, min(100.0, a))


def accuracy(losses) -> float | None:
    """Accuracy (0-1) of a set of moves, from the win chance each lost."""
    accs = [move_accuracy(x) for x in losses if x is not None]
    if not accs:
        return None
    plain = sum(accs) / len(accs)
    harmonic = len(accs) / sum(1 / max(a, 1.0) for a in accs)
    return (plain + harmonic) / 2 / 100


def acc(x: float | None) -> str:
    """0.873 -> '87%'."""
    return "?" if x is None else f"{x * 100:.0f}%"


def avg_went(moves: list[dict]) -> str:
    """A set of moves as its accuracy: '87% accuracy'."""
    return f"{acc(accuracy(m.get('wp_loss') for m in moves))} accuracy"


def spct(x: float | None, minus: str = "-") -> str:
    """Signed: +3%, -28%, 0% (minus can be '&minus;' or '−' for HTML)."""
    if x is None:
        return "?"
    if round(x * 1000) == 0:
        return "0%"
    return ("+" if x > 0 else minus) + pct(abs(x))


# ---------------------------------------------------------------- light / dark
# Pages open dark (data-theme="dark" on <html>, so it holds even where scripts don't run); the
# corner button switches to light and back, remembered per browser.
THEME_HEAD = """<script>try { var t = localStorage.getItem("tc-theme"); if (t) document.documentElement.dataset.theme = t; } catch (e) {}</script>"""
THEME_BUTTON = """<button class="theme-toggle" type="button" aria-label="Switch light or dark" title="Light / dark"
 onclick="var r=document.documentElement, t=r.dataset.theme==='light'?'dark':'light'; r.dataset.theme=t; try{localStorage.setItem('tc-theme',t)}catch(e){}">&#9680;</button>
<style>.theme-toggle { position:fixed; right:12px; bottom:12px; z-index:50; width:34px; height:34px; border-radius:50%; cursor:pointer;
 border:1px solid var(--line); background:var(--card); color:var(--fg); font-size:17px; line-height:1; opacity:.85; }
.theme-toggle:hover { opacity:1; }</style>"""


# ---------------------------------------------------------------- page links
# Where the pages live: "" on your own PC (/games/<id>); the hosted service puts each player's
# pages under "/u/<lichess id>" and sets this before rendering.
SITE = {"base": ""}


def home_url() -> str:
    return SITE["base"] + "/"


def game_url(gid: str, story: bool = False) -> str:
    return f"{SITE['base']}/games/{gid}" + ("/story" if story else "")

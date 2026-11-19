"""How win probabilities are shown: as percentages (stored as 0-1 fractions everywhere else)."""


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


def avg_went(moves: list[dict]) -> str:
    """Your average move, as where it went: '52% → 49%'."""
    ms = [m for m in moves if m.get("wp_before") is not None and m.get("wp_after") is not None]
    if not ms:
        return "?"
    return went(sum(m["wp_before"] for m in ms) / len(ms), sum(m["wp_after"] for m in ms) / len(ms))


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

"""A small ask for support once a player has had real use out of Thought Coach (AFTER reviewed games).

Donations go through a Stripe Payment Link ("customers choose what to pay"): only its public URL is
here; no keys and no payment handling on our side. Without a URL nothing is shown.
"""

from __future__ import annotations

import html

URL = "https://buy.stripe.com/7sYfZh1Qo7dIdHl65f8ww00"  # the Stripe Payment Link; empty: no ask
AFTER = 5  # reviewed games before asking
AMOUNTS = (5, 10)  # suggested, in dollars (prefilled on the payment page when Stripe allows it)


def card(reviewed: int, url: str | None = None) -> str:
    url = URL if url is None else url
    if not url or reviewed < AFTER:
        return ""
    u = html.escape(url, quote=True)
    sep = "&amp;" if "?" in url else "?"
    btns = "".join(f"<a class='dgo' href='{u}{sep}__prefilled_amount={a * 100}' target='_blank' rel='noopener' "
                   f"data-donate>${a}</a>" for a in AMOUNTS)
    return f"""<aside class="donate" hidden>
<div><p class="eyebrow">{reviewed} games reviewed</p>
<h2>Help keep Thought Coach running</h2>
<p>Thought Coach is free and has no ads. Every review runs Stockfish and transcribes your voice on AWS servers,
and donations cover those costs and pay for new features. If it has helped your chess, $5 or $10 goes a long way.
Any amount helps.</p></div>
<div class="dbtn">{btns}<a class="dgo alt" href="{u}" target="_blank" rel="noopener" data-donate>Other amount</a>
<button type="button" class="dlater">Not now</button></div>
</aside>"""


CSS = """
.donate { display:grid; grid-template-columns:minmax(0, 1fr) auto; gap:14px 22px; align-items:center; margin:0 0 18px;
  padding:16px 20px; border-radius:16px; background:var(--card); border:1px solid color-mix(in srgb, var(--accent) 45%, var(--line)); }
.donate h2 { margin:2px 0 6px; font-size:18px; } .donate p { margin:0; color:var(--fg2); font-size:14px; max-width:62ch; }
.donate .eyebrow { color:var(--muted); font-size:12px; }
.donate .dbtn { display:flex; flex-wrap:wrap; gap:8px; align-items:center; justify-content:flex-end; }
.donate .dgo { display:inline-block; padding:9px 18px; border-radius:999px; font-weight:700; text-decoration:none;
  background:var(--accent); color:var(--accent-fg, #fff); border:1px solid var(--accent); }
.donate .dgo:hover { filter:brightness(1.08); }
.donate .dgo.alt { background:transparent; color:var(--fg); border-color:var(--line); }
.donate .dlater { background:none; border:0; color:var(--muted); font:inherit; font-size:13px; cursor:pointer; padding:6px; }
.donate .dlater:hover { color:var(--fg); }
@media (max-width:640px) { .donate { grid-template-columns:1fr; } .donate .dbtn { justify-content:flex-start; } }
"""

# Hidden until the script says this browser hasn't put it off: "Not now" for 30 days, a click on
# a donate button for a year.
JS = """
(() => {
  const box = document.querySelector('.donate');
  if (!box) return;
  const KEY = 'tc-donate-until', DAY = 864e5;
  let until = 0;
  try { until = +localStorage.getItem(KEY) || 0; } catch (e) {}
  if (Date.now() < until) return;
  box.hidden = false;
  const later = (days) => { try { localStorage.setItem(KEY, String(Date.now() + days * DAY)); } catch (e) {} };
  box.querySelector('.dlater').addEventListener('click', () => { later(30); box.hidden = true; });
  box.querySelectorAll('[data-donate]').forEach(a => a.addEventListener('click', () => later(365)));
})();
"""

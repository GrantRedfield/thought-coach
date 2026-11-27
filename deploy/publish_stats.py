"""Fill the published research figures into the pages from one file (deploy/site/research/stats.json, written by the
research tools), so the homepage and the research page can't disagree.

    python deploy/publish_stats.py        # rewrites the marked numbers in place; run before deploying the site

Marked places: <tag data-stat="key">...</tag> (the content is replaced), <!--stat:measures-->...<!--/stat:measures-->
(the benchmark table rows), and "N rapid and classical games" in meta descriptions.
"""

import json
import re
from pathlib import Path

SITE = Path(__file__).resolve().parent / "site"
PAGES = [SITE / "index.html", SITE / "research" / "index.html", SITE / "questions" / "index.html"]


def fmt(v) -> str:
    if isinstance(v, list):
        return f"{fmt(v[0])} to {fmt(v[1])}"
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return f"{v:,}" if isinstance(v, int) else str(v)


def fill(text: str, s: dict) -> tuple[str, list[str]]:
    missing = []
    s = {**s, **{f"m_{r['key']}": r["rate"] for r in s.get("measures", [])}}  # one measure's rate: data-stat="m_candidates"

    def one(m):
        key = m.group(3)
        if key.endswith("_cap"):
            base = s.get(key[:-4]); return m.group(1) + (str(base)[:1].upper() + str(base)[1:] if base is not None else m.group(4)) + m.group(5)
        if key not in s or s[key] is None:
            missing.append(key); return m.group(0)
        return m.group(1) + fmt(s[key]) + m.group(5)

    text = re.sub(r'(<(\w+)[^>]*\bdata-stat="([a-z_]+)"[^>]*>)(.*?)(</\2>)', one, text, flags=re.S)
    rows = "".join(f"<tr><td>{r['label']}</td><td class=\"n\">{r['rate']}%</td><td class=\"n\">{r['low']}–{r['high']}%</td></tr>"
                   for r in s.get("measures", []))
    text = re.sub(r"(<!--stat:measures-->).*?(<!--/stat:measures-->)", lambda m: m.group(1) + rows + m.group(2), text, flags=re.S)
    text = re.sub(r"\d[\d,]* rapid and classical games by strong players", f"{s['games']:,} rapid and classical games by strong players", text)
    return text, missing


def main() -> None:
    s = json.loads((SITE / "research" / "stats.json").read_text(encoding="utf-8"))
    for page in PAGES:
        text, missing = fill(page.read_text(encoding="utf-8"), s)
        page.write_text(text, encoding="utf-8")
        print(page.relative_to(SITE), "filled", "(missing: " + ", ".join(sorted(set(missing))) + ")" if missing else "")


if __name__ == "__main__":
    main()

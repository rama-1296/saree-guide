#!/usr/bin/env python3
"""
Resham Room
Pulls silk sarees under MAX_PRICE from tathastu.fashion and hastakalaethnic.com
and writes resham-room.html: an image-only gallery with colour, silk, weave
and shop filters. No prices are shown.

Run:   python3 resham_room.py
Then open resham-room.html in your browser. Run again any time to refresh.

Optional: `pip install pillow` lets the script guess colours from the photos
for sarees whose names don't mention a colour (most Tathastu listings).
"""

import colorsys
import html
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date

MAX_PRICE = 8500
OUTPUT = os.environ.get("OUTPUT", "resham-room.html")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "application/json,text/html,*/*",
}

try:
    from PIL import Image
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False

# ---------------------------------------------------------------- vocabulary

# plain colour family -> (fancy name, swatch hex)
COLOURS = {
    "Red":    ("Sindoor", "#B3202A"),
    "Pink":   ("Rani", "#D63C83"),
    "Orange": ("Kesar", "#E27B26"),
    "Yellow": ("Haldi", "#E8B923"),
    "Gold":   ("Zari", "#C09A45"),
    "Green":  ("Mehendi", "#4E7A2E"),
    "Teal":   ("Mor", "#127A7A"),
    "Blue":   ("Neel", "#1F3F8F"),
    "Purple": ("Jamun", "#5E2B6E"),
    "Brown":  ("Kattha", "#7A4A2A"),
    "Black":  ("Kajal", "#1A1A1A"),
    "Grey":   ("Dhuan", "#8A8A8A"),
    "White":  ("Chandan", "#EDE3CF"),
    "Silver": ("Chandi", "#C4C7CC"),
    "Multi":  ("Rangoli", "conic-gradient(#B3202A,#E8B923,#4E7A2E,#1F3F8F,#B3202A)"),
}

COLOUR_WORDS = [
    (r"rose gold|copper|coffee|chocolate|brown|tan", "Brown"),
    (r"maroon|wine|red|cherry|crimson|ruby|sindoor", "Red"),
    (r"pink|rani|magenta|fuchsia|onion|blush", "Pink"),
    (r"orange|rust|peach|saffron|kesari|coral", "Orange"),
    (r"yellow|mustard|muster|lemon|haldi", "Yellow"),
    (r"gold|golden", "Gold"),
    (r"teal|turquoise|firozi|peacock|sea green|rama green", "Teal"),
    (r"green|pista|parrot|bottle|mehendi|mehndi|olive|mint|emerald|lime", "Green"),
    (r"blue|navy|sky|ink", "Blue"),
    (r"purple|violet|lavender|lavendar|lilac|mauve|plum|jamuni", "Purple"),
    (r"black", "Black"),
    (r"grey|gray|ash|steel|smoke", "Grey"),
    (r"white|cream|beige|ivory|off white|offwhite|sandal", "White"),
    (r"silver", "Silver"),
    (r"multi ?colou?r|multi", "Multi"),
]

SILK_TYPES = {  # plain -> fancy
    "Pure silk": "Shuddh resham",
    "Art silk": "Kala resham",
    "Silk": "Resham",
}

WEAVES = [
    (r"paithani", "Paithani"),
    (r"kanjeevaram|kanjivaram|kanchipuram|kanjeevram", "Kanjeevaram"),
    (r"banarasi|benarasi", "Banarasi"),
    (r"brocade", "Brocade"),
    (r"tissue", "Tissue"),
    (r"katan", "Katan"),
    (r"tussar|tussore|tasar", "Tussar"),
    (r"soft silk", "Soft silk"),
    (r"patola", "Patola"),
    (r"irkal|ilkal", "Irkal"),
    (r"chanderi", "Chanderi"),
    (r"organza", "Organza"),
    (r"apurva", "Apurva"),
    (r"gadwal", "Gadwal"),
    (r"nalli", "Nalli"),
    (r"dola", "Dola"),
    (r"pashmina", "Pashmina"),
    (r"narayanpet", "Narayanpet"),
    (r"printed", "Printed"),
]

NOT_SAREE = re.compile(r"crop top|dress|blouse|lehenga|shela|dupatta|kurti|one piece|stole")


def find_all(patterns, text):
    found = []
    for pat, label in patterns:
        if re.search(r"\b(?:%s)\b" % pat, text) and label not in found:
            found.append(label)
    return found


def silk_type(text):
    if re.search(r"\bpure\b", text):
        return "Pure silk"
    if re.search(r"\bart\b|\bsemi\b|\bfancy\b", text):
        return "Art silk"
    return "Silk"


# ---------------------------------------------------------------- fetching

def get(url, as_json=True, tries=3):
    for attempt in range(tries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read()
                return json.loads(body) if as_json else body
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):
                return None
            if attempt == tries - 1:
                raise
        except Exception:
            if attempt == tries - 1:
                raise
        time.sleep(1.5 * (attempt + 1))


def pull_tathastu():
    items, page = [], 1
    while True:
        url = ("https://tathastu.fashion/wp-json/wc/store/v1/products"
               f"?per_page=100&page={page}")
        batch = get(url)
        if not batch:
            break
        for p in batch:
            name = html.unescape(p.get("name", ""))
            cats = " ".join(html.unescape(c.get("name", "")) for c in p.get("categories", []))
            tags = " ".join(html.unescape(t.get("name", "")) for t in p.get("tags", []))
            attrs = " ".join(t.get("name", "") for a in p.get("attributes", [])
                             for t in a.get("terms", []))
            core = f"{name} {cats}".lower()
            if "silk" not in core or "saree" not in core or NOT_SAREE.search(name.lower()):
                continue
            if not p.get("is_in_stock", True) or not p.get("images"):
                continue
            pr = p.get("prices", {})
            minor = 10 ** int(pr.get("currency_minor_unit", 0))
            raw = (pr.get("price_range") or {}).get("min_amount") or pr.get("price") or 0
            price = int(raw) / minor
            if not (0 < price < MAX_PRICE):
                continue
            img = p["images"][0]
            items.append({
                "shop": "Tathastu",
                "name": name.title(),
                "url": p.get("permalink"),
                "img": img.get("src"),
                "thumb": img.get("thumbnail") or img.get("src"),
                "colours": find_all(COLOUR_WORDS, f"{name} {attrs}".lower()),
                "silk": silk_type(core),
                "weaves": find_all(WEAVES, core),
            })
        print(f"  Tathastu page {page}: {len(items)} matches so far")
        page += 1
        time.sleep(0.3)
    return items


def pull_hastakala():
    items, page = [], 1
    while True:
        data = get(f"https://hastakalaethnic.com/products.json?limit=250&page={page}")
        prods = (data or {}).get("products", [])
        if not prods:
            break
        for p in prods:
            title = p.get("title", "")
            tags = p.get("tags", [])
            tags = " ".join(tags) if isinstance(tags, list) else str(tags)
            opts = " ".join(v for o in p.get("options", []) for v in o.get("values", []))
            text = f"{title} {tags} {p.get('product_type', '')}".lower()
            if "silk" not in text or NOT_SAREE.search(title.lower()):
                continue
            variants = [v for v in p.get("variants", []) if v.get("available", True)]
            if not variants or not p.get("images"):
                continue
            price = min(float(v.get("price") or 0) for v in variants)
            if not (0 < price < MAX_PRICE):
                continue
            src = p["images"][0]["src"]
            sep = "&" if "?" in src else "?"
            items.append({
                "shop": "Hastakala",
                "name": title,
                "url": f"https://hastakalaethnic.com/products/{p.get('handle')}",
                "img": f"{src}{sep}width=700",
                "thumb": f"{src}{sep}width=160",
                "colours": find_all(COLOUR_WORDS, f"{title} {opts}".lower()),
                "silk": silk_type(f"{title} {tags}".lower()),
                "weaves": find_all(WEAVES, title.lower()),
            })
        print(f"  Hastakala page {page}: {len(items)} matches so far")
        page += 1
        time.sleep(0.3)
    return items


# ---------------------------------------------------------------- colour guess

def hue_family(r, g, b):
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    if v < 0.18:
        return "Black"
    if s < 0.18:
        return "White" if v > 0.75 else "Grey"
    d = h * 360
    if d < 15 or d >= 345:
        return "Red"
    if d < 42:
        return "Brown" if v < 0.55 else "Orange"
    if d < 52:
        return "Gold" if s < 0.6 else "Yellow"
    if d < 68:
        return "Yellow"
    if d < 160:
        return "Green"
    if d < 195:
        return "Teal"
    if d < 255:
        return "Blue"
    if d < 290:
        return "Purple"
    return "Pink"


def guess_colour(item):
    try:
        raw = get(item["thumb"], as_json=False, tries=2)
        im = Image.open(io.BytesIO(raw)).convert("RGB")
        w, h = im.size
        im = im.crop((int(w * .2), int(h * .15), int(w * .8), int(h * .85))).resize((24, 32))
        counts = {}
        for px in im.getdata():
            fam = hue_family(*px)
            counts[fam] = counts.get(fam, 0) + 1
        # photos often sit on pale backgrounds, so prefer a real colour if it's substantial
        ranked = sorted(counts.items(), key=lambda kv: -kv[1])
        for fam, n in ranked:
            if fam not in ("White", "Grey") or n > 0.6 * sum(counts.values()):
                return fam
        return ranked[0][0]
    except Exception:
        return None


# ---------------------------------------------------------------- page

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Resham Room</title>
<style>
:root{--bg:#fbfbfa;--ink:#221d26;--muted:#77707c;--line:#e4e1e6;--on:#6b1330;--on-ink:#fff}
@media (prefers-color-scheme:dark){:root{--bg:#18161a;--ink:#ece8ee;--muted:#9a93a0;--line:#332f36;--on:#e7b6c6;--on-ink:#18161a}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
header{padding:28px 20px 8px;max-width:1400px;margin:0 auto}
h1{font:400 34px/1.1 "Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;margin:0 0 4px;letter-spacing:.01em}
.sub{color:var(--muted);margin:0}
.filters{max-width:1400px;margin:0 auto;padding:12px 20px 4px;display:grid;gap:10px}
.group{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.group h2{font:italic 400 16px/1 "Iowan Old Style",Palatino,Georgia,serif;margin:0 8px 0 0;min-width:72px}
.group h2 small{display:block;font:12px system-ui,sans-serif;color:var(--muted);margin-top:3px}
button.chip{font:inherit;font-size:13px;border:1px solid var(--line);background:transparent;color:var(--ink);
  padding:5px 11px;border-radius:999px;cursor:pointer;display:inline-flex;align-items:center;gap:6px}
button.chip[aria-pressed=true]{background:var(--on);border-color:var(--on);color:var(--on-ink)}
button.chip:focus-visible{outline:2px solid var(--on);outline-offset:2px}
.dot{width:11px;height:11px;border-radius:50%;border:1px solid rgba(0,0,0,.15);flex:none}
.n{color:var(--muted);font-size:12px}
button.chip[aria-pressed=true] .n{color:inherit;opacity:.75}
.bar{max-width:1400px;margin:0 auto;padding:6px 20px 14px;display:flex;gap:14px;color:var(--muted);font-size:13px}
.bar button{font:inherit;background:none;border:0;color:var(--ink);text-decoration:underline;cursor:pointer;padding:0}
main{max-width:1400px;margin:0 auto;padding:0 20px 40px;display:grid;gap:10px;
  grid-template-columns:repeat(auto-fill,minmax(180px,1fr))}
main a{display:block;aspect-ratio:5/7;overflow:hidden;background:var(--line)}
main img{width:100%;height:100%;object-fit:cover;display:block}
.empty{grid-column:1/-1;color:var(--muted);padding:40px 0}
@media (max-width:520px){main{grid-template-columns:repeat(2,1fr);gap:6px}.group h2{min-width:100%}}
</style>
</head>
<body>
<header>
  <h1>Resham Room</h1>
  <p class="sub">Silk sarees within budget from Tathastu and Hastakala. Pulled __DATE__. Tap a saree to open it in its shop.</p>
</header>
<section class="filters" id="filters"></section>
<div class="bar"><span id="count"></span><button id="clear" hidden>Clear filters</button></div>
<main id="grid"></main>
<script>
const ITEMS = __ITEMS__;
const COLOURS = __COLOURS__;
const SILKS = __SILKS__;
const GROUPS = [
  {key:"colours", title:"Rang", hint:"colour", many:true,
   order:Object.keys(COLOURS), label:k=>COLOURS[k][0], swatch:k=>COLOURS[k][1]},
  {key:"silk", title:"Resham", hint:"silk", order:Object.keys(SILKS), label:k=>SILKS[k]},
  {key:"weaves", title:"Bunai", hint:"weave", many:true, label:k=>k},
  {key:"shop", title:"Dukaan", hint:"shop", label:k=>k},
];
const picked = Object.fromEntries(GROUPS.map(g=>[g.key,new Set()]));
const vals = (it,g) => g.many ? it[g.key] : [it[g.key]];
const matches = (it, skip) => GROUPS.every(g =>
  g.key===skip || !picked[g.key].size || vals(it,g).some(v=>picked[g.key].has(v)));

function renderFilters(){
  const box = document.getElementById("filters"); box.innerHTML="";
  for (const g of GROUPS){
    const counts = {};
    ITEMS.filter(it=>matches(it,g.key)).forEach(it=>vals(it,g).forEach(v=>counts[v]=(counts[v]||0)+1));
    const all = new Set(ITEMS.flatMap(it=>vals(it,g)));
    const keys = (g.order||[...all].sort()).filter(k=>all.has(k));
    if (!keys.length) continue;
    const row = document.createElement("div"); row.className="group";
    row.innerHTML = `<h2>${g.title}<small>${g.hint}</small></h2>`;
    for (const k of keys){
      const b = document.createElement("button"); b.className="chip";
      b.setAttribute("aria-pressed", picked[g.key].has(k));
      b.title = k;
      b.innerHTML = (g.swatch?`<span class="dot" style="background:${g.swatch(k)}"></span>`:"")
        + `${g.label(k)} <span class="n">${counts[k]||0}</span>`;
      b.onclick = ()=>{ picked[g.key].has(k)?picked[g.key].delete(k):picked[g.key].add(k); render(); };
      row.appendChild(b);
    }
    box.appendChild(row);
  }
}
function render(){
  renderFilters();
  const shown = ITEMS.filter(it=>matches(it));
  const grid = document.getElementById("grid");
  grid.innerHTML = shown.length ? shown.map(it=>
    `<a href="${it.url}" target="_blank" rel="noopener" title="${it.name.replace(/"/g,'&quot;')}">`+
    `<img src="${it.img}" alt="${it.name.replace(/"/g,'&quot;')}" loading="lazy"></a>`).join("")
    : `<p class="empty">No sarees match all of these. Remove a filter to see more.</p>`;
  document.getElementById("count").textContent = `${shown.length} of ${ITEMS.length} sarees`;
  document.getElementById("clear").hidden = !GROUPS.some(g=>picked[g.key].size);
}
document.getElementById("clear").onclick = ()=>{ GROUPS.forEach(g=>picked[g.key].clear()); render(); };
render();
</script>
</body>
</html>
"""


def build_page(items):
    for it in items:
        it.pop("thumb", None)
    page = (PAGE
            .replace("__DATE__", date.today().strftime("%-d %B %Y") if sys.platform != "win32"
                     else date.today().strftime("%d %B %Y"))
            .replace("__ITEMS__", json.dumps(items, ensure_ascii=False))
            .replace("__COLOURS__", json.dumps(COLOURS))
            .replace("__SILKS__", json.dumps(SILK_TYPES)))
    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write(page)


def main():
    items = []
    for label, fn in (("Tathastu", pull_tathastu), ("Hastakala", pull_hastakala)):
        print(f"Pulling {label}...")
        try:
            items += fn()
        except Exception as e:
            print(f"  Couldn't reach {label} ({e}). Skipping it this run.")

    missing = [it for it in items if not it["colours"]]
    if missing and HAVE_PIL:
        print(f"Guessing colours from photos for {len(missing)} sarees...")
        with ThreadPoolExecutor(max_workers=8) as ex:
            for it, fam in zip(missing, ex.map(guess_colour, missing)):
                if fam:
                    it["colours"] = [fam]
    elif missing:
        print(f"{len(missing)} sarees have no colour in their name. "
              "Run `pip install pillow` and re-run to guess them from photos.")

    if not items:
        print("Nothing came back from either shop, so no page was written.")
        sys.exit(1)
    build_page(items)
    print(f"Done: {len(items)} sarees written to {OUTPUT}")


if __name__ == "__main__":
    main()

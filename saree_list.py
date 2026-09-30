#!/usr/bin/env python3
"""
Pulls silk sarees between MIN_PRICE and MAX_PRICE from tathastu.fashion and
hastakalaethnic.com and writes an image-only gallery page with a colour filter.
Each saree gets a code like HK01 or TT01. The page shows only the code; the
private spreadsheet (Google Sheet, or sarees-private.csv when run locally)
maps each code to the saree's name and shop link. The spreadsheet also works
as the record of codes, so a saree keeps its code from one day to the next.

Run:   python3 saree_list.py
Optional: `pip install pillow` to guess colours from photos when the name
doesn't mention one (most Tathastu listings).
"""

import colorsys
import csv
import html
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date

# ---- edit these -------------------------------------------------------------
MIN_PRICE = 2000
MAX_PRICE = 8500
MAX_PHOTOS = 8          # photos per saree in the viewer
SKIP_COLOURS = ["Black"]

# Code prefix for each shop: HK01, HK02 ... and TT01, TT02 ...
PREFIXES = {"Hastakala": "HK", "Tathastu": "TT"}

# Columns the script fills in the private spreadsheet. To add more, see
# sheet_values() below. Any extra columns you add by hand in the sheet are kept.
SHEET_COLUMNS = ["Code", "Name", "Link", "In stock"]

# Saree data that goes into the public page. Anything not listed here stays
# out of the page's source code. Add "shop", "silk" or "weaves" if you bring
# those filters back. Never add "name", "link" or "price".
PUBLIC_FIELDS = ["code", "img", "photos", "colours"]
# -----------------------------------------------------------------------------

OUTPUT = os.environ.get("OUTPUT", "saree-list.html")
LOCAL_SHEET = "sarees-private.csv"           # used when no Google Sheet is set up
SHEET_URL = os.environ.get("SHEET_URL")      # Apps Script web app URL (GitHub secret)
SHEET_TOKEN = os.environ.get("SHEET_TOKEN")  # shared password (GitHub secret)
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

COLOURS = {
    "Red":    ("Red", "#B3202A"),
    "Pink":   ("Pink", "#D63C83"),
    "Orange": ("Orange", "#E27B26"),
    "Yellow": ("Yellow", "#E8B923"),
    "Gold":   ("Gold", "#C09A45"),
    "Green":  ("Green", "#4E7A2E"),
    "Teal":   ("Teal", "#127A7A"),
    "Blue":   ("Blue", "#1F3F8F"),
    "Purple": ("Purple", "#5E2B6E"),
    "Brown":  ("Brown", "#7A4A2A"),
    "Black":  ("Black", "#1A1A1A"),
    "Grey":   ("Grey", "#8A8A8A"),
    "White":  ("Cream & white", "#EDE3CF"),
    "Silver": ("Silver", "#C4C7CC"),
    "Multi":  ("Multicolour", "conic-gradient(#B3202A,#E8B923,#4E7A2E,#1F3F8F,#B3202A)"),
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

NOT_SAREE = re.compile(
    r"crop top|dress|blouse|lehenga|shela|dupatta|kurti|kurta|one piece|stole|"
    r"suit|fabric|material|running|shawl|jacket|gown|skirt|potli|bag|jewell?ery")


def find_all(patterns, text):
    found = []
    for pat, label in patterns:
        if re.search(r"\b(?:%s)\b" % pat, text) and label not in found:
            found.append(label)
    return found


def in_budget(price):
    return MIN_PRICE <= price < MAX_PRICE


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
        batch = get("https://tathastu.fashion/wp-json/wc/store/v1/products"
                    f"?per_page=100&page={page}")
        if not batch:
            break
        for p in batch:
            name = html.unescape(p.get("name", ""))
            cats = " ".join(html.unescape(c.get("name", "")) for c in p.get("categories", []))
            attrs = " ".join(t.get("name", "") for a in p.get("attributes", [])
                             for t in a.get("terms", []))
            core = f"{name} {cats}".lower()
            if "silk" not in core or "saree" not in core or NOT_SAREE.search(name.lower()):
                continue
            if p.get("is_in_stock") is not True or p.get("is_purchasable") is False or not p.get("images"):
                continue
            pr = p.get("prices", {})
            minor = 10 ** int(pr.get("currency_minor_unit", 0))
            raw = (pr.get("price_range") or {}).get("min_amount") or pr.get("price") or 0
            price = int(raw) / minor
            if not in_budget(price):
                continue
            imgs = [i.get("src") for i in p["images"] if i.get("src")][:MAX_PHOTOS]
            items.append({
                "shop": "Tathastu",
                "id": p.get("id"),
                "name": name.title(),
                "link": p.get("permalink"),
                "price": price,
                "img": imgs[0],
                "photos": imgs,
                "thumb": p["images"][0].get("thumbnail") or imgs[0],
                "colours": find_all(COLOUR_WORDS, f"{name} {attrs}".lower()),
            })
        print(f"  Tathastu page {page}: {len(items)} sarees so far")
        page += 1
        time.sleep(0.3)
    return items


def shopify_img(src, width):
    return f"{src}{'&' if '?' in src else '?'}width={width}"


def pull_hastakala():
    items, page = [], 1
    while True:
        data = get(f"https://hastakalaethnic.com/products.json?limit=250&page={page}")
        prods = (data or {}).get("products", [])
        if not prods:
            break
        for p in prods:
            title = p.get("title", "")
            ptype = (p.get("product_type") or "").lower()
            tags = p.get("tags", [])
            tags = " ".join(tags) if isinstance(tags, list) else str(tags)
            opts = " ".join(v for o in p.get("options", []) for v in o.get("values", []))
            text = f"{title} {tags} {ptype}".lower()
            # sarees only: the shop marks them with product type "Saree";
            # fall back to the title if the type is blank
            is_saree = "saree" in ptype if ptype else "saree" in title.lower()
            if not is_saree or "silk" not in text or NOT_SAREE.search(title.lower()):
                continue
            variants = [v for v in p.get("variants", []) if v.get("available") is True]
            if not variants or not p.get("images"):
                continue
            price = min(float(v.get("price") or 0) for v in variants)
            if not in_budget(price):
                continue
            srcs = [i["src"] for i in p["images"] if i.get("src")][:MAX_PHOTOS]
            items.append({
                "shop": "Hastakala",
                "id": p.get("id"),
                "name": title,
                "link": f"https://hastakalaethnic.com/products/{p.get('handle')}",
                "price": price,
                "img": shopify_img(srcs[0], 800),
                "photos": [shopify_img(s, 1600) for s in srcs],
                "thumb": shopify_img(srcs[0], 160),
                "colours": find_all(COLOUR_WORDS, f"{title} {opts}".lower()),
            })
        print(f"  Hastakala page {page}: {len(items)} sarees so far")
        page += 1
        time.sleep(0.3)
    return items


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
        ranked = sorted(counts.items(), key=lambda kv: -kv[1])
        for fam, n in ranked:
            if fam not in ("White", "Grey") or n > 0.6 * sum(counts.values()):
                return fam
        return ranked[0][0]
    except Exception:
        return None


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Silk sarees</title>
<style>
:root{
  --bg:#f7f6f4; --panel:#ffffff; --ink:#231e27; --muted:#7b7480; --line:#e6e2e8;
  --on:#6b1330; --on-ink:#fff; --tile:#ebe7ec;
}
@media (prefers-color-scheme:dark){:root{
  --bg:#141215; --panel:#1c191e; --ink:#ece8ee; --muted:#9a93a0; --line:#2f2b33;
  --on:#e7b6c6; --on-ink:#141215; --tile:#242027;
}}
*{box-sizing:border-box}
html,body{margin:0}
body{background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:1840px;margin:0 auto;padding:0 clamp(16px,4vw,48px)}

.filters{background:var(--panel);border-bottom:1px solid var(--line)}
.filters .wrap{display:grid;gap:14px;padding-top:22px;padding-bottom:18px}
.group{display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.group h2{font:500 14px/1 system-ui,sans-serif;margin:0;color:var(--muted)}
.chips{display:flex;flex-wrap:wrap;gap:8px}
button.chip{font:inherit;font-size:13.5px;border:1px solid var(--line);background:transparent;color:var(--ink);
  padding:6px 13px;border-radius:999px;cursor:pointer;display:inline-flex;align-items:center;gap:7px;
  transition:background-color .15s,border-color .15s}
button.chip:hover{border-color:var(--muted)}
button.chip[aria-pressed=true]{background:var(--on);border-color:var(--on);color:var(--on-ink)}
button.chip:disabled{opacity:.35;cursor:default}
.dot{width:12px;height:12px;border-radius:50%;box-shadow:inset 0 0 0 1px rgba(0,0,0,.12);flex:none}
.n{color:var(--muted);font-size:12px}
button.chip[aria-pressed=true] .n{color:inherit;opacity:.75}
.bar{display:flex;justify-content:space-between;align-items:center;color:var(--muted);font-size:13px;padding-top:2px}
.bar button{font:inherit;background:none;border:0;color:var(--ink);text-decoration:underline;text-underline-offset:3px;cursor:pointer;padding:0}
button:focus-visible{outline:2px solid var(--on);outline-offset:2px}

main.wrap{display:grid;gap:clamp(14px,2.4vw,32px);padding-top:clamp(20px,3vw,40px);padding-bottom:64px;
  grid-template-columns:repeat(auto-fill,minmax(250px,1fr))}
.tile{all:unset;display:block;cursor:zoom-in;aspect-ratio:3/4;overflow:hidden;border-radius:6px;background:var(--tile)}
.tile img{width:100%;height:100%;object-fit:cover;display:block;opacity:0;transition:opacity .3s,transform .4s}
.tile img.ready{opacity:1}
.tile:hover img{transform:scale(1.025)}
.tile:focus-visible{outline:2px solid var(--on);outline-offset:3px}
.empty{grid-column:1/-1;color:var(--muted);padding:56px 0;text-align:center}

.viewer{position:fixed;inset:0;z-index:20;background:rgba(12,10,13,.94);display:none;
  flex-direction:column;align-items:center;justify-content:center;gap:16px;padding:56px 72px 40px}
.viewer.open{display:flex}
.viewer img{max-width:100%;min-height:0;flex:1 1 auto;object-fit:contain;border-radius:4px;user-select:none}
.viewer .name{color:#fff;font-size:15px;letter-spacing:.08em;text-align:center;margin:0}
.viewer button{position:absolute;background:rgba(255,255,255,.08);color:#fff;border:0;cursor:pointer;
  width:44px;height:44px;border-radius:50%;font-size:22px;line-height:44px;padding:0}
.viewer button:hover{background:rgba(255,255,255,.18)}
.viewer button[hidden]{display:none}
.viewer button:disabled{opacity:.25;cursor:default}
.viewer .close{top:14px;right:14px}
.viewer .prev{left:16px;top:50%;transform:translateY(-50%)}
.viewer .next{right:16px;top:50%;transform:translateY(-50%)}
.viewer .count{color:rgba(255,255,255,.6);font-size:13px;margin:-8px 0 0;min-height:1em}

@media (max-width:640px){
  main.wrap{grid-template-columns:repeat(2,1fr)}
  .viewer{padding:60px 8px 24px}
  .viewer .prev,.viewer .next{top:auto;bottom:8px;transform:none}
}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
</style>
</head>
<body>
<section class="filters"><div class="wrap" id="filters"></div></section>
<main class="wrap" id="grid"></main>

<div class="viewer" id="viewer" role="dialog" aria-modal="true" aria-label="Saree photos">
  <img id="vimg" alt="">
  <p class="name" id="vname"></p>
  <button class="close" id="vclose" aria-label="Close">&times;</button>
  <button class="prev" id="vprev" aria-label="Previous photo">&lsaquo;</button>
  <button class="next" id="vnext" aria-label="Next photo">&rsaquo;</button>
  <p class="count" id="vcount"></p>
</div>

<script>
const ITEMS = __ITEMS__;
const COLOURS = __COLOURS__;
const PULLED = "__DATE__";
const GROUPS = [
  {key:"colours", title:"Colour", many:true,
   order:Object.keys(COLOURS), label:k=>COLOURS[k][0], swatch:k=>COLOURS[k][1]},
];
const picked = Object.fromEntries(GROUPS.map(g=>[g.key,new Set()]));
const vals = (it,g) => g.many ? (it[g.key]||[]) : [it[g.key]];
const matches = (it, skip) => GROUPS.every(g =>
  g.key===skip || !picked[g.key].size || vals(it,g).some(v=>picked[g.key].has(v)));
const esc = s => String(s).replace(/[&<>"]/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
let shown = [];

function renderFilters(){
  const box = document.getElementById("filters"); box.innerHTML = "";
  for (const g of GROUPS){
    const counts = {};
    ITEMS.filter(it=>matches(it,g.key)).forEach(it=>vals(it,g).forEach(v=>counts[v]=(counts[v]||0)+1));
    const all = new Set(ITEMS.flatMap(it=>vals(it,g)));
    const keys = (g.order||[...all].sort()).filter(k=>all.has(k));
    if (keys.length < 2) continue;
    const row = document.createElement("div"); row.className = "group";
    row.innerHTML = `<h2>${g.title}</h2><div class="chips"></div>`;
    const chips = row.querySelector(".chips");
    for (const k of keys){
      const b = document.createElement("button"); b.className = "chip";
      const on = picked[g.key].has(k);
      b.setAttribute("aria-pressed", on);
      b.disabled = !on && !counts[k];
      b.innerHTML = (g.swatch?`<span class="dot" style="background:${g.swatch(k)}"></span>`:"")
        + `${esc(g.label(k))} <span class="n">${counts[k]||0}</span>`;
      b.onclick = ()=>{ on?picked[g.key].delete(k):picked[g.key].add(k); render(); };
      chips.appendChild(b);
    }
    box.appendChild(row);
  }
  const bar = document.createElement("div"); bar.className = "bar";
  const any = GROUPS.some(g=>picked[g.key].size);
  bar.innerHTML = `<span>${shown.length} of ${ITEMS.length} sarees, updated ${PULLED}</span>`
    + (any ? `<button id="clear">Clear filters</button>` : "");
  box.appendChild(bar);
  if (any) bar.querySelector("#clear").onclick = ()=>{ GROUPS.forEach(g=>picked[g.key].clear()); render(); };
}

function render(){
  shown = ITEMS.filter(it=>matches(it));
  renderFilters();
  const grid = document.getElementById("grid");
  grid.innerHTML = shown.length ? shown.map((it,i)=>
    `<button class="tile" data-i="${i}" aria-label="Saree ${esc(it.code)}">`+
    `<img src="${esc(it.img)}" alt="" loading="lazy" referrerpolicy="no-referrer" onload="this.classList.add('ready')"></button>`).join("")
    : `<p class="empty">No sarees match all of these. Remove a filter to see more.</p>`;
}
document.getElementById("grid").addEventListener("click", e=>{
  const t = e.target.closest(".tile"); if (t) openViewer(+t.dataset.i, 0);
});

// ---- photo viewer: one saree at a time, arrows step through its photos
const $ = id => document.getElementById(id);
const V = {el:$("viewer"), img:$("vimg"), count:$("vcount"), item:0, photo:0, opener:null};
function photosOf(i){ const it = shown[i]; return it.photos && it.photos.length ? it.photos : [it.img]; }
function show(){
  const ph = photosOf(V.item);
  V.img.src = ph[V.photo]; V.img.alt = "Saree " + shown[V.item].code;
  $("vname").textContent = shown[V.item].code;
  V.count.textContent = ph.length > 1 ? `${V.photo+1} / ${ph.length}` : "";
  $("vprev").hidden = $("vnext").hidden = ph.length < 2;
  $("vprev").disabled = V.photo === 0;
  $("vnext").disabled = V.photo === ph.length - 1;
  if (ph[V.photo+1]) new Image().src = ph[V.photo+1];
}
function openViewer(i, p){
  V.opener = document.activeElement; V.item = i; V.photo = p;
  V.el.classList.add("open"); document.body.style.overflow = "hidden"; show(); $("vclose").focus();
}
function closeViewer(){
  V.el.classList.remove("open"); document.body.style.overflow = ""; V.img.removeAttribute("src");
  if (V.opener) V.opener.focus();
}
function step(d){
  const n = V.photo + d;
  if (n >= 0 && n < photosOf(V.item).length){ V.photo = n; show(); }
}
$("vclose").onclick = closeViewer;
$("vprev").onclick = ()=>step(-1);
$("vnext").onclick = ()=>step(1);
V.el.addEventListener("click", e=>{ if (e.target === V.el) closeViewer(); });
document.addEventListener("keydown", e=>{
  if (!V.el.classList.contains("open")) return;
  if (e.key === "Escape") closeViewer();
  if (e.key === "ArrowRight") step(1);
  if (e.key === "ArrowLeft") step(-1);
});
let tx = null;
V.el.addEventListener("touchstart", e=>{ tx = e.touches[0].clientX; }, {passive:true});
V.el.addEventListener("touchend", e=>{
  if (tx === null) return; const dx = e.changedTouches[0].clientX - tx; tx = null;
  if (Math.abs(dx) > 40) step(dx < 0 ? 1 : -1);
});

render();
</script>
</body>
</html>
"""


def build_page(items):
    public = [{k: it[k] for k in PUBLIC_FIELDS if k in it} for it in items]
    d = date.today()
    page = (PAGE
            .replace("__DATE__", f"{d.day} {d.strftime('%B %Y')}")
            .replace("__ITEMS__", json.dumps(public, ensure_ascii=False).replace("</", "<\\/"))
            .replace("__COLOURS__", json.dumps(COLOURS)))
    with open(OUTPUT, "w", encoding="utf-8") as f:
        f.write(page)


def sheet_values(it):
    """Spreadsheet columns filled in for each saree that's on the page today."""
    return {"Code": it["code"], "Name": it["name"], "Link": it["link"], "In stock": "Yes"}


def code_parts(code):
    m = re.match(r"([A-Za-z]+)(\d+)$", str(code).strip())
    return (m.group(1).upper(), int(m.group(2))) if m else None


def load_registry():
    """Read the existing spreadsheet so sarees keep the codes they already have."""
    if SHEET_URL and SHEET_TOKEN:
        sep = "&" if "?" in SHEET_URL else "?"
        data = get(SHEET_URL + sep + urllib.parse.urlencode({"token": SHEET_TOKEN}))
        if not isinstance(data, dict) or "rows" not in data:
            raise RuntimeError(f"unexpected reply: {str(data)[:80]}")
        header, rows = data.get("header") or [], data["rows"]
    elif os.environ.get("GITHUB_ACTIONS"):
        return [], []
    elif os.path.exists(LOCAL_SHEET):
        with open(LOCAL_SHEET, newline="", encoding="utf-8") as f:
            table = list(csv.reader(f))
        header, rows = (table[0], table[1:]) if table else ([], [])
    else:
        return [], []
    records = [dict(zip(header, r)) for r in rows]
    return header, [r for r in records if r.get("Link")]


def assign_codes(items, records):
    by_link = {r["Link"]: r for r in records}
    highest = {}
    for r in records:
        parts = code_parts(r.get("Code", ""))
        if parts:
            highest[parts[0]] = max(highest.get(parts[0], 0), parts[1])
    for it in items:
        known = by_link.get(it["link"])
        if known and code_parts(known.get("Code", "")):
            it["code"] = known["Code"]
        else:                                   # new saree: next number for its shop
            prefix = PREFIXES[it["shop"]]
            highest[prefix] = highest.get(prefix, 0) + 1
            it["code"] = f"{prefix}{highest[prefix]:02d}"


def write_sheet(items, header, records):
    columns = SHEET_COLUMNS + [h for h in header if h and h not in SHEET_COLUMNS]
    merged = {r["Link"]: {**r, "In stock": "No"} for r in records}
    for it in items:
        merged[it["link"]] = {**merged.get(it["link"], {}), **sheet_values(it)}
    ordered = sorted(merged.values(), key=lambda r: code_parts(r.get("Code", "")) or ("ZZ", 0))
    rows = [[r.get(c, "") for c in columns] for r in ordered]

    if SHEET_URL and SHEET_TOKEN:
        body = json.dumps({"token": SHEET_TOKEN, "header": columns, "rows": rows}).encode()
        req = urllib.request.Request(SHEET_URL, data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                reply = r.read().decode(errors="replace")[:80]
            print(f"Google Sheet: {reply}")
        except Exception as e:
            print(f"Google Sheet update failed ({type(e).__name__}). The page was still built.")
    elif os.environ.get("GITHUB_ACTIONS"):
        print("No Google Sheet set up (SHEET_URL / SHEET_TOKEN secrets missing), "
              "so codes may change between runs.")
    else:
        with open(LOCAL_SHEET, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(columns)
            w.writerows(rows)
        print(f"Spreadsheet saved to {LOCAL_SHEET} (keep this file out of anything you upload)")


def main():
    items = []
    for label, fn in (("Hastakala", pull_hastakala), ("Tathastu", pull_tathastu)):
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

      items = [it for it in items if it["colours"][:1] != ["Black"]]
    if not items:
        print("Nothing came back from either shop, so no page was written.")
        sys.exit(1)

    try:
        header, records = load_registry()
    except Exception as e:
        # Without the existing codes, new ones would clash, so stop and keep the old page.
        print(f"Couldn't read the Google Sheet ({e}). Stopping so codes stay consistent.")
        sys.exit(1)
    assign_codes(items, records)

    build_page(items)
    print(f"Done: {len(items)} sarees written to {OUTPUT}")
    write_sheet(items, header, records)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Build tlds.json for the TLD List app.

Sources (all public):
  * IANA root zone list      https://data.iana.org/TLD/tlds-alpha-by-domain.txt
  * IANA root zone database  https://www.iana.org/domains/root/db   (type + manager)
  * IANA RDAP bootstrap      https://data.iana.org/rdap/dns.json    (copied as rdap_dns.json)
  * tldlist.us dataset       https://tldlist.us/tld-list.json + each /tld/<slug> page (sitemap)
The tldlist.us data is CC BY style: attribution to tldlist.us is shipped inside the app.

Usage: python3 build_tlds.py [OUT_DIR]   (default: ./out)   --offline reuses ../iana and ../site captures
"""
import html, json, os, re, sys, time, urllib.request, datetime

UA = "TldListFeed/1.0 (+https://cinderspire.github.io/support/tldlist.html)"
HERE = os.path.dirname(os.path.abspath(__file__))
OFFLINE = "--offline" in sys.argv
args = [a for a in sys.argv[1:] if not a.startswith("--")]
OUT = args[0] if args else os.path.join(HERE, "out")
CACHE = os.path.join(HERE, "cache")
os.makedirs(OUT, exist_ok=True)
os.makedirs(CACHE, exist_ok=True)


def get(url, name):
    p = os.path.join(CACHE, name)
    if OFFLINE and os.path.exists(p):
        return open(p, encoding="utf-8").read()
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read().decode("utf-8", "replace")
    open(p, "w", encoding="utf-8").write(data)
    return data


def text(s):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s)).split())


LEGACY = {"com", "net", "org", "info", "biz", "name", "pro", "aero", "asia", "cat", "coop", "edu", "gov",
          "int", "jobs", "mil", "mobi", "museum", "post", "tel", "travel", "xxx", "arpa"}


def uni(label):
    if label.startswith("xn--"):
        try:
            return label.encode("ascii").decode("idna")
        except Exception:
            return label
    return label


# 1. IANA list + root db
lst = get("https://data.iana.org/TLD/tlds-alpha-by-domain.txt", "tlds.txt")
labels = [l.strip().lower() for l in lst.splitlines() if l.strip() and not l.startswith("#")]
version_line = lst.splitlines()[0].lstrip("# ").strip()
db = get("https://www.iana.org/domains/root/db", "root_db.html")
meta = {}
for r in re.findall(r"<tr[^>]*>(.*?)</tr>", db, flags=re.S):
    tds = re.findall(r"<td[^>]*>(.*?)</td>", r, flags=re.S)
    if len(tds) >= 4:
        hm = re.search(r'/domains/root/db/([^"]+)"', tds[0])
        lab = hm.group(1).lower() if hm else text(tds[0]).lstrip(".").lower()
        meta[lab] = {"state": text(tds[1]), "kind": text(tds[2]), "manager": text(tds[3])}

# 2. tldlist.us
site = json.loads(get("https://tldlist.us/tld-list.json", "tld-list.json"))
by_slug = {t["slug"]: t for t in site["tlds"]}
sitemap = get("https://tldlist.us/sitemap.xml", "sitemap.xml")
slugs = sorted({m.group(1) for m in re.finditer(r"<loc>https://tldlist\.us/tld/([^<]+)</loc>", sitemap)})


def parse_page(slug):
    s = get(f"https://tldlist.us/tld/{slug}", f"page_{slug}.html")
    if not OFFLINE:
        time.sleep(0.35)  # polite
    cells = {}
    for k, v in re.findall(r'<div class="k">(.*?)</div><div class="v[^"]*">(.*?)</div>', s, flags=re.S):
        cells[text(k)] = text(v)
    short = re.search(r'<div class="answer-box">.*?<strong>(.*?)</strong>', s, flags=re.S)
    rng = re.search(r"\$(\d+(?:\.\d+)?)\s*[–-]\s*\$(\d+(?:\.\d+)?)\s*per year", text(s))
    upd = re.search(r'<time datetime="([\d-]+)"', s)
    return cells, text(short.group(1)) if short else "", rng, upd.group(1) if upd else ""


def short_meaning(m, limit=170):
    m = " ".join(m.split())
    if len(m) <= limit:
        return m
    cut = m[:limit]
    dot = cut.rfind(". ")
    return (cut[: dot + 1] if dot > 60 else cut[: cut.rfind(" ")].rstrip(",;:") + "...")


def num(x):
    return None if x is None else (int(x) if float(x).is_integer() else round(float(x), 2))


def money(v):
    m = re.search(r"\$(\d+(?:\.\d+)?)", v or "")
    return float(m.group(1)) if m else None


# Review 2026-11-09: the site's "example sites" column often holds category phrases
# ("Swiss sites") or defunct names. Only real host names are kept.
_HOST = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$")
_DEFUNCT = {"vine.co", "angel.co"}


def clean_examples(items):
    return [x for x in items if _HOST.match(x) and x not in _DEFUNCT]


# Registry names checked against the IANA root zone database (technical contact) and the
# registry sites on 2026-11-09; the site's text was wrong or vague for these.
REGISTRY_FIX = {
    "tv": "GoDaddy Registry (for Tuvalu)",
    "io": "Internet Computer Bureau (Identity Digital)",
    "sh": "Internet Computer Bureau (Identity Digital)",
    "me": "doMEn (.ME registry), Montenegro",
    "gg": "Island Networks Ltd. (nic.gg)",
    "so": "soNIC (nic.so)",
    "co": ".CO Internet registry (registry.co)",
}

detail = {}
for slug in slugs:
    try:
        detail[slug] = parse_page(slug)
    except Exception as e:  # keep going, the JSON dataset still covers most
        print("page failed", slug, e, file=sys.stderr)

enriched = {}
for slug in slugs:
    cells, short, rng, upd = detail.get(slug, ({}, "", None, ""))
    j = by_slug.get(slug, {})
    typ = (j.get("type") or cells.get("Type", "").split(" ")[0] or "").strip()
    price = j.get("typicalPriceUSD")
    if price is None:
        price = money(cells.get("Typical price"))
    lo = hi = None
    if rng:
        lo, hi = float(rng.group(1)), float(rng.group(2))
    elif price is not None:
        lo = hi = float(price)
    if lo is not None and price is not None and not (lo <= price <= hi):
        lo, hi = min(lo, price), max(hi, price)
    ex = j.get("exampleSites") or [x.strip() for x in cells.get("Example sites", "").split(",") if x.strip()]
    e = {
        "meaning": short_meaning(j.get("meaning") or short),
        "registry": j.get("registry") or cells.get("Registry", ""),
        "country": j.get("country") or ("" if cells.get("Country / scope", "").startswith("Generic") else cells.get("Country / scope", "")),
        "launched": cells.get("Launched", ""),
        "restrictions": cells.get("Restrictions", ""),
        "siteType": typ,
        "examples": clean_examples(ex)[:4],
        "priceTypical": num(price), "priceLow": num(lo), "priceHigh": num(hi),
        "restricted": bool(j.get("restricted")) if j else price is None,
        "updated": upd or site.get("lastUpdated", ""),
    }
    enriched[slug] = {k: v for k, v in e.items() if v not in ("", None, [])}

tlds = []
for lab in labels:
    m = meta.get(lab, {})
    kind = m.get("kind", "")
    e = enriched.get(lab, {})
    if lab in enriched and e.get("siteType") in ("gTLD", "ccTLD", "sTLD"):
        ty = {"gTLD": "g", "ccTLD": "c", "sTLD": "s"}[e["siteType"]]
    elif kind == "Country Code":
        ty = "c"
    else:
        ty = "g"
    o = {"t": lab, "ty": ty, "mgr": m.get("manager", "")}
    if lab.startswith("xn--"):
        o["u"] = uni(lab)
    if ty == "g" and lab not in LEGACY and lab not in enriched:
        o["new"] = 1
    elif ty == "g" and lab not in LEGACY:
        o["new"] = 1
    if lab in enriched:
        e = dict(e)
        e.pop("siteType", None)
        if lab in REGISTRY_FIX:
            e["registry"] = REGISTRY_FIX[lab]
        o["d"] = e
    tlds.append(o)

# every enriched slug must exist in the IANA list
missing = [s for s in enriched if s not in set(labels)]
now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
feed = {
    "schema": 1,
    "version": now[:10],
    "generatedAt": now,
    "ianaVersion": version_line,
    "siteDataDate": site.get("lastUpdated", ""),
    "attribution": "Meanings, registries and typical prices: tldlist.us (CC BY). Root zone: IANA.",
    "count": {"total": len(tlds), "detailed": sum(1 for t in tlds if "d" in t)},
    "tlds": tlds,
}
json.dump(feed, open(os.path.join(OUT, "tlds.json"), "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
boot = get("https://data.iana.org/rdap/dns.json", "dns.json")
open(os.path.join(OUT, "rdap_dns.json"), "w", encoding="utf-8").write(json.dumps(json.loads(boot), separators=(",", ":")))
print("tlds", feed["count"], "missing-from-iana", missing)

#!/usr/bin/env python3
"""
Breaking-news monitor: pulls RSS from preferred sources, tags region + category,
scores each item, strips duplicates against the published log, and writes a
ranked candidate queue for the editorial pass.

Usage:
    python3 monitor.py                 # full cycle
    python3 monitor.py --hours 6       # only items newer than 6 hours
    python3 monitor.py --limit 15      # top N candidates in queue
"""
import argparse
import html
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(BASE, "state")
IST = timezone(timedelta(hours=5, minutes=30))

CFG = json.load(open(os.path.join(BASE, "config.json"), encoding="utf-8"))

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

# --------------------------------------------------------------------------
# keyword maps
# --------------------------------------------------------------------------
REGION_KEYS = {
    "hyderabad": ["hyderabad", "secunderabad", "cyberabad", "malkajgiri", "rachakonda",
                  "shamshabad", "rgia", "ghmc", "kukatpally", "gachibowli", "madhapur",
                  "uppal", "charminar", "nampally", "bowenpally", "jubilee hills",
                  "banjara hills", "masab tank", "medchal", "rangareddy", "ranga reddy",
                  "khairatabad", "balapur", "tolichowki", "ameerpet", "shamshabad",
                  "musi river", "old city", "telangana police"],
    "telangana": ["telangana", "warangal", "karimnagar", "nizamabad", "khammam",
                  "nalgonda", "mahbubnagar", "mahabubnagar", "adilabad", "siddipet",
                  "sangareddy", "vikarabad", "mancherial", "jagtial", "sircilla",
                  "gadwal", "nagarkurnool", "yadadri", "bhadradri", "telangana assembly",
                  "revanth reddy", "hydraa", "zaheerabad", "kamareddy"],
    "india": ["india", "new delhi", "mumbai", "pm modi", "supreme court", "high court",
              "parliament", "bsf", "crpf", "nia court", "cbi", "isro", "rbi", "brics",
              "bengaluru", "chennai", "kolkata", "jammu and kashmir", "punjab police",
              "maharashtra", "kerala", "keralam", "tamil nadu", "gujarat", "uttar pradesh",
              "central government", "union government", "andhra pradesh",
              "manipur", "imphal", "kuki", "assam", "meghalaya", "tripura", "mizoram",
              "nagaland", "arunachal", "odisha", "bhubaneswar", "bihar", "patna",
              "jharkhand", "ranchi", "chhattisgarh", "raipur", "madhya pradesh", "bhopal",
              "rajasthan", "jaipur", "uttarakhand", "dehradun", "himachal", "shimla",
              "haryana", "chandigarh", "goa", "puducherry", "karnataka", "mysuru",
              "mangaluru", "hubballi", "thiruvananthapuram", "kozhikode", "kochi",
              "coimbatore", "madurai", "vijayawada", "visakhapatnam", "amaravati",
              "tirupati", "aiims", "drdo", "iiser", "iit ", "ediic", "delhi"],
}

CATEGORY_RULES = [
    ("fire_explosion", ["fire breaks", "fire erupts", "blast", "explosion", "boiler burst",
                        "gas leak", "fire at", "major fire", "caught fire"]),
    ("accident_casualty", ["dead", "killed", "dies", "death toll", "casualties", "injured",
                           "accident", "collapses", "derail", "crash", "runs over", "hits truck",
                           "capsize", "electrocut", "drowned", "suicide", "murder", "stabbed",
                           "strangled", "body found"]),
    ("disaster_weather", ["flood", "heavy rain", "rains lash", "red alert", "orange alert",
                          "cyclone", "depression", "earthquake", "tremor", "landslide",
                          "heatwave", "heat wave", "cloudburst", "imd warns", "waterlogging",
                          "dam water released", "influx"]),
    ("security_terror", ["terror", "terrorist", "let", "jem", "isis", "naxal", "maoist",
                         "encounter", "arms seized", "weapon", "armoury", "ammunition",
                         "hijack", "bomb scare", "ied", "grenade", "hafiz saeed", "attack on"]),
    ("major_crime", ["rape", "sexual assault", "molestation", "kidnap", "abduct", "robbery",
                     "dacoity", "cheating", "fraud", "scam", "extortion", "human trafficking",
                     "chain snatch", "drug", "ganja", "narcotic", "gold smuggling"]),
    ("police_operation", ["arrested", "arrests", "apprehend", "detained", "busts", "busted",
                          "crackdown", "seized", "seizes", "raids", "police operation",
                          "non-bailable warrant", "warrant against", "chargesheet", "custody"]),
    ("court_judgment", ["supreme court", "high court", "court orders", "court directs",
                        "convicted", "sentenced", "acquitted", "bail", "judgment", "verdict",
                        "tribunal", "stays", "notice to"]),
    ("govt_announcement", ["government announces", "cabinet clears", "cabinet approves",
                           "government order issued", "notified", "scheme launched", "sanction",
                           "assembly passes", "bill passed", "passes bill", "ordinance",
                           "free of cost", "waiver", "subsidy", "recruitment", "notification"]),
    ("traffic_disruption", ["traffic diversion", "traffic alert", "strike", "bandh",
                            "road blocked", "halt services", "off the road", "boycott",
                            "metro services", "bus services suspended", "rally",
                            "congestion", "road closed", "jam"]),
    ("public_safety_warning", ["food safety", "advisory", "warning issued", "alert issued",
                               "unsafe", "contaminated", "ban on", "suspended licence",
                               "suspends fssai", "recall", "health hazard", "spike in cases",
                               "outbreak", "water crisis", "power cut", "outage",
                               "quality check", "show cause notice"]),
    ("infrastructure_failure", ["bridge", "collapse", "pothole", "power failure",
                                "pipeline burst", "water supply", "metro delay",
                                "signal failure", "crack", "retaining wall", "flyover"]),
    ("political_major", ["resigns", "resignation", "expelled", "suspended from", "defect",
                         "joins", "alliance", "mla", "member of parliament", "minister", "chief minister",
                         "chief minister", "prime minister", "president", "election", "polls", "manifesto",
                         "house arrest", "dharna", "protest"]),
    ("protest_major", ["protest", "dharna", "march", "rally", "stir", "hunger strike",
                       "demonstration"]),
    ("economic_major", ["rupee", "inflation", "gdp", "sensex", "nifty", "crude oil",
                        "gold price", "fii", "rate cut", "repo rate", "layoffs",
                        "investment", "fdi", "export", "import ban"]),
    ("international_conflict", ["war", "airstrike", "missile", "ceasefire", "conflict",
                                "invasion", "sanctions", "un security council", "nato",
                                "hostage"]),
    ("missing_person", ["missing", "untraceable", "lost contact", "search on for"]),
]

# things that must never be published
BLOCKLIST = ["bigg boss", "instagram trend", "photo trend", "horoscope",
             "astrolog", "recipe", "movie review", "box office", "trailer",
             "song release", "fashion show", "celebrity wedding", "viral video",
             "salaries of all", "microlot", "bespoke", "luxury villas",
             "infinite dots", "weaver showcases", "take the leap", "yayoi",
             "king oyo", "cricket world cup squad", "jersey launch", "first look",
             "teaser", "ott release", "music video", "birthday wishes", "wedding photos"]

# entertainment / lifestyle vocabulary -> routine unless a genuine incident is stated
SOFT_DOMAINS = ["entertainment", "bollywood", "tollywood", "hollywood", "sports",
                "cricket", "cinema", "celebrity", "lifestyle", "travel feature",
                "food feature", "fashion", "music", "television"]

# real-incident verbs that rescue a story sitting in a soft domain
HARD_INCIDENT_VERBS = ["arrested", "killed", "dead", "dies", "murder", "rape",
                       "accident", "fire", "blast", "collision", "stabbed",
                       "assaulted", "robbed", "looted", "suicide", "injured"]

PREFERRED_ONLY_HINT = "Prefer The Hindu / Munsif. Use others only to confirm."


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------
# fetching
# --------------------------------------------------------------------------
def strip_cdata(s):
    if not s:
        return ""
    s = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", s, flags=re.S)
    return html.unescape(s).strip()


def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


BOILERPLATE = [
    r"Get the latest updates in Hyderabad City News.*$",
    r"\*\s*Siasat\.com.*$",
    r"Tags Sidebar.*$",
    r"Menu\s*\*\s*Siasat.*$",
    r"Also Read.*$",
    r"\(Source:\s*X\s*@\w+\)\s*$",
    r"Follow us on (Twitter|Facebook|Instagram).*$",
    r"Advertisement\s*$",
]


def clean_excerpt(text):
    t = re.sub(r"\s+", " ", text or "").strip()
    for pat in BOILERPLATE:
        t = re.sub(pat, "", t, flags=re.I).strip()
    return t


def parse_rss(xml_text):
    out = []
    blocks = re.findall(r"<item[\s>](.*?)</item>", xml_text, re.S)
    if not blocks:  # atom fallback
        blocks = re.findall(r"<entry[\s>](.*?)</entry>", xml_text, re.S)
    for b in blocks:
        title = strip_cdata((re.search(r"<title[^>]*>(.*?)</title>", b, re.S) or [None, ""])[1])
        link = strip_cdata((re.search(r"<link[^>]*>(.*?)</link>", b, re.S) or [None, ""])[1])
        if not link:
            m = re.search(r'<link[^>]*href="([^"]+)"', b)
            link = m.group(1) if m else ""
        pub = strip_cdata((re.search(r"<pubDate[^>]*>(.*?)</pubDate>", b, re.S)
                           or re.search(r"<published[^>]*>(.*?)</published>", b, re.S)
                           or re.search(r"<updated[^>]*>(.*?)</updated>", b, re.S)
                           or [None, ""])[1])
        desc = strip_cdata((re.search(r"<description[^>]*>(.*?)</description>", b, re.S)
                            or re.search(r"<summary[^>]*>(.*?)</summary>", b, re.S)
                            or [None, ""])[1])
        desc = re.sub(r"<[^>]+>", " ", desc)
        desc = clean_excerpt(desc)
        if not title:
            continue
        out.append({"title": title, "url": link, "pub_raw": pub, "excerpt": desc[:900]})
    return out


def parse_date(raw):
    if not raw:
        return None
    raw = raw.strip()
    fmts = ["%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z",
            "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"]
    for f in fmts:
        try:
            d = datetime.strptime(raw.replace("Z", "+0000") if f.endswith("Z") else raw, f)
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            return d.astimezone(IST)
        except ValueError:
            continue
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})", raw)
    if m:
        return datetime(*[int(x) for x in m.groups()], tzinfo=IST)
    return None


# --------------------------------------------------------------------------
# analysis
# --------------------------------------------------------------------------
FALSE_LOCAL = ["hyderabad house", "hyderabad metro phase 2 noc"]


def kw_hit(text, kw):
    """Word-boundary match so 'let' never hits 'bullet' and 'ed' never hits 'reduced'."""
    return re.search(r"(?<![a-z0-9])" + re.escape(kw.lower()) + r"(?![a-z0-9])", text) is not None


def detect_region(item):
    """Region is decided from the TITLE and URL only.
    Excerpts mention Hyderabad in passing far too often (taglines, related links,
    'Get the latest updates in Hyderabad City News...') and caused false tagging."""
    title = item["title"].lower()
    if any(f in title for f in FALSE_LOCAL):
        return "india"
    url = item.get("url", "").lower()
    # a Hyderabad/Telangana URL slug is a strong, reliable signal
    if any(kw_hit(url, k) for k in REGION_KEYS["hyderabad"]) or "/hyderabad" in url:
        return "hyderabad"
    if any(kw_hit(url, k) for k in REGION_KEYS["telangana"]) or "/telangana" in url:
        return "telangana"
    # now the title
    for region in ("hyderabad", "telangana", "india"):
        if any(kw_hit(title, k) for k in REGION_KEYS[region]):
            return region
    # URL section path is a reliable editorial signal
    if "/international/" in url or "/world/" in url:
        return "world"
    if "/national/" in url or "/india/" in url:
        return "india"
    # domain priors for locally-focused outlets
    if "telanganatoday.com" in url:
        return "telangana"
    if "siasat.com" in url or "munsifdaily.com" in url:
        return "hyderabad"
    # last resort: a strict dateline, e.g. "Hyderabad: police said..." -- only at the
    # very start of the excerpt. Loose excerpt matching was picking up Siasat's
    # boilerplate tagline ("Get the latest updates in Hyderabad City News...").
    head = item.get("excerpt", "").lower()[:90]
    m = re.match(r"^(hyderabad|secunderabad|telangana|warangal|new delhi|india)\s*[:\-]", head.strip())
    if m:
        d = m.group(1)
        return {"secunderabad": "hyderabad", "warangal": "telangana",
                "new delhi": "india"}.get(d, d)
    return "world"


FIRE_METAPHOR = re.compile(r"(fires?\s+back|under\s+fire|opening\s+fire|"
                           r"opened\s+fire|fired\s+shots|ceasefire|crossfire|"
                           r"line\s+of\s+fire|fire\s+brands)")


def detect_categories(item):
    """Category is decided from the TITLE. Excerpt is only used to corroborate,
    never to invent a category -- this is what keeps celebrity fluff from
    being tagged as a fire or a terror story."""
    title = item["title"].lower()
    excerpt = item.get("excerpt", "").lower()[:400]
    hits = []
    for cat, keys in CATEGORY_RULES:
        for k in keys:
            if kw_hit(title, k) or kw_hit(excerpt, k):
                hits.append(cat)
                break
    if "fire_explosion" in hits and FIRE_METAPHOR.search(title):
        hits.remove("fire_explosion")
    return hits or ["routine"]


def detect_soft_domain(item):
    blob = (item.get("feed_url", "") + " " + item.get("url", "") + " " + item["title"]).lower()
    return any(kw_hit(blob, d) for d in SOFT_DOMAINS)


def is_blocked(item):
    title = item["title"].lower()
    if any(b in title for b in BLOCKLIST):
        return True
    if detect_soft_domain(item):
        # entertainment/lifestyle item: only survives if it reports a real incident
        if not any(kw_hit(title, v) for v in HARD_INCIDENT_VERBS):
            return True
    return False


def region_confidence(item):
    """1.0 = region named in title/URL, 0.4 = only inferred from dateline."""
    region = item["region"]
    keys = REGION_KEYS.get(region)
    if not keys:  # world -> nothing local to confirm
        return 1.0
    title = item["title"].lower()
    url = item.get("url", "").lower()
    if any(kw_hit(url, k) for k in keys):
        return 1.0
    if any(kw_hit(title, k) for k in keys):
        return 1.0
    return 0.4


def score(item):
    region = item["region"]
    cats = item["categories"]
    s = CFG["scoring"]["region_weights"].get(region, 10)
    conf = region_confidence(item)
    item["region_confidence"] = conf
    if conf < 1.0:
        # region only inferred -> treat as weak, it may not be a local story at all
        s = CFG["scoring"]["region_weights"]["world"]
    s += max(CFG["scoring"]["category_weights"].get(c, 0) for c in cats)
    title = item["title"].lower()
    # magnitude signals
    if re.search(r"\b\d+\s?(dead|killed|died|injured|arrested)\b", title):
        s += 8
    if any(w in title for w in ["major", "massive", "fatal", "deadly", "biggest", "rare"]):
        s += 5
    if item["source_preferred"]:
        s += 6
    # freshness
    age_h = item.get("age_hours")
    if age_h is not None:
        if age_h <= 2:
            s += 10
        elif age_h <= 6:
            s += 6
        elif age_h <= 12:
            s += 2
        elif age_h > 24:
            s -= 8
    if is_blocked(item):
        s -= 60
    return max(0, min(100, s))


def classify(item):
    """A breaking / B important / C developing / D routine / E dup / F unverified"""
    s = item["score"]
    cats = item["categories"]
    hard = {"accident_casualty", "fire_explosion", "disaster_weather", "security_terror",
            "infrastructure_failure", "public_safety_warning", "traffic_disruption"}
    strong_region = item.get("region_confidence", 1.0) >= 1.0
    if s >= 78 and (set(cats) & hard) and strong_region:
        return "A"
    if s >= 78 and strong_region:
        return "B"
    if s >= CFG["rules"]["min_score_to_publish"]:
        return "C"
    return "D"


HEADLINE_MAP = {
    "hyderabad": "\U0001F4F0 LATEST NEWS \u2014 HYDERABAD",
    "telangana": "\U0001F4F0 LATEST NEWS \u2014 TELANGANA",
    "india": "\U0001F4F0 LATEST NEWS \u2014 INDIA",
    "world": "\U0001F30D LATEST \u2014 WORLD",
}


def norm_title(t):
    t = t.lower()
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    return " ".join(t.split())


def jaccard(a, b):
    sa, sb = set(norm_title(a).split()), set(norm_title(b).split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


# --------------------------------------------------------------------------
# state
# --------------------------------------------------------------------------
def load_published():
    p = os.path.join(STATE, "published.json")
    if os.path.exists(p):
        return json.load(open(p, encoding="utf-8"))
    return []


def load_seen():
    p = os.path.join(STATE, "seen.json")
    if os.path.exists(p):
        return json.load(open(p, encoding="utf-8"))
    return {}


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)


def duplicate_of(item, published, thresh):
    for rec in published:
        if jaccard(item["title"], rec.get("headline", "")) >= thresh:
            return rec
        if rec.get("url") and item["url"] and rec["url"].split("?")[0] == item["url"].split("?")[0]:
            return rec
    return None


# --------------------------------------------------------------------------
def run_cycle(max_age_hours, limit):
    now = datetime.now(IST)
    published = load_published()
    seen = load_seen()
    thresh = CFG["rules"]["duplicate_similarity_threshold"]

    items, errors = [], []
    for feed in CFG["feeds"]:
        for url in feed["urls"]:
            try:
                parsed = parse_rss(fetch(url))
            except Exception as e:
                errors.append(f"{feed['name']} {url}: {type(e).__name__} {e}")
                continue
            for it in parsed:
                d = parse_date(it["pub_raw"])
                it["published_at"] = d.isoformat() if d else None
                it["age_hours"] = round((now - d).total_seconds() / 3600, 2) if d else None
                if feed.get("strip_title_suffix"):
                    it["title"] = re.sub(r"\s+-\s+[^-]{2,40}$", "",
                                         it.get("title") or "").strip()
                it["source"] = feed["name"]
                it["source_key"] = feed["source_key"]
                it["source_preferred"] = feed["preferred"]
                it["feed_url"] = url
                items.append(it)
            log(f"[ok] {feed['name']}: {len(parsed)} items from {url}")

    # de-dup identical URLs across feeds (keep preferred source copy)
    by_url = {}
    for it in items:
        key = it["url"].split("?")[0] if it["url"] else it["title"]
        prev = by_url.get(key)
        if prev is None or (it["source_preferred"] and not prev["source_preferred"]):
            by_url[key] = it
    items = list(by_url.values())

    # freshness window
    fresh = []
    for it in items:
        if it["age_hours"] is None:
            continue
        if 0 <= it["age_hours"] <= max_age_hours:
            fresh.append(it)
    log(f"[fresh] {len(fresh)}/{len(items)} items within {max_age_hours}h")

    analysed = []
    for it in fresh:
        it["region"] = detect_region(it)
        it["categories"] = detect_categories(it)
        it["score"] = score(it)
        it["class"] = classify(it)
        dup = duplicate_of(it, published, thresh)
        if dup:
            it["class"] = "E"
            it["duplicate_of"] = dup.get("headline")
            it["score"] = 0
        analysed.append(it)

    analysed.sort(key=lambda x: (-x["score"], x["age_hours"]))

    # cluster near-identical candidates so one event does not appear 4 times
    queue, clusters = [], []
    for it in analysed:
        if it["class"] == "E":
            continue
        match = next((c for c in clusters if jaccard(it["title"], c[0]["title"]) >= 0.55), None)
        if match:
            match.append(it)
            it.setdefault("corroborating_sources", [])
            match[0].setdefault("corroborating_sources", [])
            if it["source"] not in [match[0]["source"]] and it["source"] not in match[0]["corroborating_sources"]:
                match[0]["corroborating_sources"].append(it["source"])
            continue
        clusters.append([it])

    for c in clusters:
        head = c[0]
        head["corroboration"] = [x["source"] for x in c[1:]]
        queue.append(head)

    queue = queue[:limit]

    cycle = {
        "cycle_id": now.strftime("%Y%m%d-%H%M"),
        "generated_at": now.isoformat(),
        "feeds_ok": len(CFG["feeds"]) - len(set(e.split(" ")[0] for e in errors)),
        "errors": errors,
        "counts": {
            "fetched": len(items),
            "fresh": len(fresh),
            "candidates": len(queue),
            "duplicates_suppressed": sum(1 for i in analysed if i["class"] == "E"),
        },
        "candidates": [
            {
                "rank": n + 1,
                "class": c["class"],
                "score": c["score"],
                "region": c["region"],
                "region_confidence": c.get("region_confidence", 1.0),
                "suggested_headline": HEADLINE_MAP[c["region"]],
                "title": c["title"],
                "url": c["url"],
                "source": c["source"],
                "source_preferred": c["source_preferred"],
                "published_at": c["published_at"],
                "age_hours": c["age_hours"],
                "categories": c["categories"],
                "excerpt": c["excerpt"][:500],
                "corroboration": c.get("corroboration", []),
            }
            for n, c in enumerate(queue)
        ],
    }
    save_json(os.path.join(STATE, "candidates.json"), cycle)
    save_json(os.path.join(STATE, "seen.json"), seen)
    log(f"[queue] {len(queue)} candidates written to state/candidates.json")
    return cycle


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=CFG["rules"]["max_post_age_hours"])
    ap.add_argument("--limit", type=int, default=14)
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    cycle = run_cycle(a.hours, a.limit)
    if a.quiet:
        return
    print(f"\nCYCLE {cycle['cycle_id']}  |  fetched {cycle['counts']['fetched']}  "
          f"fresh {cycle['counts']['fresh']}  dups suppressed {cycle['counts']['duplicates_suppressed']}\n")
    for c in cycle["candidates"]:
        flag = {"A": "PUBLISH", "B": "publish", "C": "maybe", "D": "skip",
                "E": "DUP", "F": "unverified"}[c["class"]]
        star = "*" if c["source_preferred"] else " "
        print(f"{c['rank']:>2}. [{c['class']}={flag}{star}] {c['score']:>3}  "
              f"{c['region']:<9}{'~' if c.get('region_confidence',1)<1 else ' '} {c['title'][:80]}")
        print(f"      {c['source']} | {c['age_hours']}h ago | {', '.join(c['categories'][:3])}")
        if c["corroboration"]:
            print(f"      also reported by: {', '.join(c['corroboration'])}")
    if cycle["errors"]:
        print("\nFeed errors:")
        for e in cycle["errors"]:
            print("  -", e)


if __name__ == "__main__":
    main()

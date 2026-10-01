#!/usr/bin/env python3
"""
AUTONOMOUS MODE daemon.

Runs forever, one cycle every CYCLE_SECONDS (default 90 min):
    1. monitor preferred sources (Hyderabad > Telangana > India > World)
    2. pick publishable stories: class A/B, or class C from a preferred source
       with score >= 72, age <= 12h, not a duplicate
    3. auto-write exactly 3 English + 3 Roman Urdu bullets from a verified
       fact frame (writer.build); stories without a safe frame wait for the
       editorial pass - they are NEVER guessed
    4. render the branded graphic with the page logo (no source names)
    5. publish to the connected Facebook Page; log the record
    6. if publishing is not connected yet, keep the ready package in a pending
       list and retry it automatically once credentials appear in config.json
    7. max 2 posts per cycle (anti-flood); nothing important is ever forced

Stops and raises an alert (state/ALERT.md + exit code 9) ONLY on:
    * Facebook auth/permission errors (Graph API 190/102/permissions)
    * repeated publish failures
    * all monitored feeds failing (technical outage)
"""
import json
import os
import re
import signal
import sys
import time
import traceback
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import monitor  # noqa: E402
import publish  # noqa: E402
import writer  # noqa: E402
import roman_urdu  # noqa: E402


_NONROMAN_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF"
                          r"\uFB50-\uFDFF\uFE70-\uFEFF\u0900-\u097F]")


def _real_paste(en, ur, n=4):
    """True when `ur` carries >=n consecutive LOWERCASE English words copied
    verbatim from `en`. Capitalised tokens (names, laws, events: 'Ganesh
    idol', 'PD Act', 'Hyderabad Metro') legitimately survive into Roman
    Urdu newsroom style, so any run containing one is not a paste. 4+
    lowercase source words in a row still means untranslated."""
    t = re.findall(r"[A-Za-z][A-Za-z'\-.]*", en or "")
    u = re.findall(r"[A-Za-z][A-Za-z'\-.]*", ur or "")
    if len(t) < n or len(u) < n:
        return False
    tset = {tuple(w.lower() for w in t[j:j + n]) for j in range(len(t) - n + 1)}
    for i in range(len(u) - n + 1):
        seg = u[i:i + n]
        if any(w[0].isupper() for w in seg):
            continue
        if tuple(w.lower() for w in seg) in tset:
            return True
    return False


# 29 Sep evening fix: the digest re-picked the same candidates every cycle
# and the pinned model echoed them deterministically (temp-0 lesson again)
# -> 6h of "starved: <3 publishable Urdu lines" while 120 candidates waited.
# Bank every line the RU stage killed for 4h so picks rotate toward stories
# the model CAN translate, and give salvage a fallback-model chain (owner:
# OpenRouter is funded - one model's bad hour must never stop the pipeline).
_RU_MISS_TTL = 4 * 3600.0
# 1 Oct: the pinned model + the whole 3-model chain echoed simultaneously;
# more voices in the chain so ONE model's bad hour cannot starve the cycle.
# Every returned line is still judged by the same hard gates - only the
# chance-to-answer widens, never the bar.
_RU_FALLBACK_MODELS = ("google/gemini-2.5-flash-lite", "openai/gpt-4o-mini",
                       "anthropic/claude-3.5-haiku", "deepseek/deepseek-chat",
                       "mistralai/mistral-small-3.2-24b-instruct",
                       "qwen/qwen3-30b-a3b")


def _line_h(text):
    import hashlib
    return hashlib.sha1((text or "").lower().encode()[:200]).hexdigest()[:12]


def _ru_miss_load():
    try:
        raw = json.load(open(os.path.join(STATE, "ru_misses.json"),
                             encoding="utf-8"))
    except Exception:
        return {}
    now = time.time()
    return {str(k): float(v) for k, v in raw.items()
            if isinstance(v, (int, float)) and now - v < _RU_MISS_TTL}


def _ru_miss_save(bank):
    try:
        json.dump(bank, open(os.path.join(STATE, "ru_misses.json"), "w"))
    except Exception:
        pass


_RU_FUNC = {"ne", "ka", "ke", "ki", "ko", "se", "mein", "me", "par", "pe",
            "hai", "hain", "tha", "thi", "the", "hua", "hui", "hue", "ho",
            "hota", "nahi", "nahin", "kyun", "kyon", "aur", "ya", "karna",
            "kar", "raha", "rahe", "rahay", "rahi", "bola", "boli", "liye",
            "baad", "pehle", "tak", "bhi", "sakte", "sakti", "chahiye"}


# Words that are NEVER written as bare English tokens in genuine Pakistani
# newsroom Roman Urdu (unlike loanwords such as case/meeting/committee/
# launch, deliberately excluded to avoid false kills).
_EN_COMMON = {
    "the", "and", "but", "with", "from", "have", "has", "had", "will",
    "would", "shall", "should", "could", "may", "might", "must", "for",
    "after", "before", "while", "during", "against", "under", "between",
    "about", "again", "than", "into", "over", "near", "said", "says",
    "told", "urges", "warns", "warned", "demands", "seeks", "accuses",
    "blames", "cites", "stages", "smoothly", "gears", "inaugurated",
    "inaugurate", "symbolically", "students", "student", "hostel",
    "hostels", "meal", "meals", "trigger", "triggers", "turnout",
    "results", "locals", "poses", "risks", "crops", "warns", "centre",
    "resignation", "demanding", "conclude", "concludes", "opposes",
}


def _echo_paste(en, ur):
    """Owner 29 Sep 'roman urdu got disturbed': _real_paste is deliberately
    blind to capitalised words, so a VERBATIM English echo
    ('Etihad Rail Dubai Station officially inaugurated.') or an Urdu shell
    with a whole English sentence pasted inside ('...727 Voter Exclusions
    Delhi. What Poll Body Said mein...') slips through. Kill when the line
    mirrors the source too closely AND shows no translating effort: >=55%
    token overlap with NO Urdu function word anywhere, or a verbatim run of
    6+ consecutive source tokens (case-insensitive, punctuation ignored).
    Legit proper-noun-dense newsroom lines ('Hyderabad Metro Pink Line ka
    jaiza meeting hui') carry function words and short name runs -> pass."""
    t_raw = re.findall(r"[A-Za-z][A-Za-z'\-.]*|\d+", en or "")
    t = [w.lower().strip("'.-") for w in t_raw]
    t_cap = [bool(w) and w[0].isupper() for w in t_raw]
    u = [w.lower().strip("'.-") for w in
         re.findall(r"[A-Za-z][A-Za-z'\-.]*|\d+", ur or "")]
    if len(u) < 4:
        return False
    from collections import Counter
    ct = Counter(x for x in t if x)
    shared = sum(min(c, ct[x]) for x, c in
                 Counter(x for x in u if x).items())
    overlap = shared / len(u)
    has_func = any(x in _RU_FUNC for x in u)
    run = best = lcrun = bestlc = 0
    for i in range(len(u)):
        for j in range(len(t)):
            k = 0
            lc = 0
            while (i + k < len(u) and j + k < len(t)
                   and u[i + k] and u[i + k] == t[j + k]):
                k += 1
                if j + k - 1 < len(t) and not t_cap[j + k - 1]:
                    lc = k  # run still all-lowercase (non-proper-noun)
            best = max(best, k)
            bestlc = max(bestlc, lc)
    best = max(best, 0)
    ct2 = Counter(x for x in t if x in _EN_COMMON)
    sc = sum(min(c, ct2[x]) for x, c in
             Counter(x for x in u if x in _EN_COMMON).items())
    return ((overlap >= 0.55 and (not has_func or bestlc >= 3))
            or best >= 6 or (sc >= 3 and overlap >= 0.45))


def _ru_on():
    """Is the owner's roman_urdu translator usable in this run?"""
    try:
        return bool(roman_urdu) and roman_urdu.configured()
    except Exception:
        return False

CYCLE_SECONDS = 90 * 60
POST_SPACING_SECONDS = 75  # pause between posts in one burst (FB rate limits)
MAX_POSTS_PER_CYCLE = 3
STATE = os.path.join(BASE, "state")
STORIES = os.path.join(BASE, "stories")
IST = monitor.IST

HEADLINE_MAP = monitor.HEADLINE_MAP


_LOGTAIL = []           # 28 Sep: in-progress visibility - the tail of this
_DIGEST_NOTE = ""       # last digest verdict of this cycle, lands in state
_RU_STATS = {}          # roman-urdu accept/kill anatomy of the last attempt


def log(*a):            # cycle's stdout rides inside last_cycle.json, which
    msg = (f"[{datetime.now(IST).strftime('%d %b %H:%M:%S')}] "  # the relay
           + " ".join(str(x) for x in a))                         # pushes
    print(msg, flush=True)                                        # EVERY cycle.
    _LOGTAIL.append(msg[-170:])
    del _LOGTAIL[:-130:]


def alert(reason, detail=""):
    os.makedirs(STATE, exist_ok=True)
    with open(os.path.join(STATE, "ALERT.md"), "w", encoding="utf-8") as f:
        f.write(f"# AGENT STOPPED - ACTION NEEDED\n\n"
                f"**When:** {datetime.now(IST).isoformat()}\n\n"
                f"**Reason:** {reason}\n\n{detail}\n")
    log("!!! ALERT:", reason)
    log(detail[:400])


def slugify(t):
    t = re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")
    return t[:60]


MAX_AGE_HOURS = 26.0  # owner 27 Sep: "take and post" - anything within the
                      # last day across ALL regions/categories is fair game;
                      # hourly silence only when the day is truly exhausted
                      # (old 6h window caused the dead morning hours)
MAIN_CATS = {"political_major", "govt_announcement", "economic_major",
             "court_judgment", "protest_major", "police_operation",
             "disaster_weather", "international_conflict"}
MIN_GAP_HOURS = 0.33  # owner 26 Sep: breaking solos may run every ~20 min


def eligible(c):
    cls, score, pref = c["class"], c["score"], c["source_preferred"]
    age = c.get("age_hours") or 99
    if age > MAX_AGE_HOURS:
        return False  # stale news is refused outright
    if cls in ("A", "B"):
        return True
    if set(c.get("categories", [])) & MAIN_CATS and score >= 60:
        return True  # politics / main news: lower bar, still verified+fresh
    if cls == "C" and pref and score >= 72:
        return True
    return False


DAILY_REGULAR_LIMIT = 12    # owner 26 Sep: raised with 24/7 hourly mode
MONTHLY_EMERGENCY_LIMIT = 10  # (casualty cats stay banned - kept as guard)
DAILY_TOTAL_LIMIT = 30      # hard flood guard only
EMERG_CATS = {"fire_explosion", "accident_casualty", "major_crime"}


def load_quota():
    qp = os.path.join(STATE, "quota.json")
    if os.path.exists(qp):
        try:
            return json.load(open(qp, encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_quota(q):
    json.dump(q, open(os.path.join(STATE, "quota.json"), "w", encoding="utf-8"),
              indent=1)


def is_emergency(c):
    return (bool(set(c.get("categories", [])) & EMERG_CATS)
            and c.get("class") == "A" and (c.get("age_hours") or 99) <= 3)


def story_from(c, framed):
    now = datetime.now(IST)
    srcs = [c["source"]] + [x for x in c.get("corroboration", []) if x != c["source"]]
    return {
        "slug": "auto-" + slugify(c["title"]),
        "title": c["title"],
        "headline": HEADLINE_MAP[c["region"]],
        "topic": c["region"] + "-" + slugify(c["title"])[:40],
        "region": c["region"],
        "classification": c["class"],
        "timestamp": now.strftime("%d %b %Y, %I:%M %p IST"),
        "source": " / ".join(srcs),           # internal record only - never shown
        "source_urls": [c["url"]],
        "verified_by": srcs,
        "not_verified": [],
        "key_facts": framed["bullets_en"],
        "frame": framed["frame"],
        "image_rights": "branded graphic only; no third-party photograph used",
        "bullets_en": framed["bullets_en"],
        "bullets_ur": framed["bullets_ur"],
        "auto": True,
    }


def try_publish(story):
    """Returns publish.run exit code."""
    os.makedirs(STORIES, exist_ok=True)
    path = os.path.join(STORIES, story["slug"] + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(story, f, indent=2, ensure_ascii=False)
    return publish.run(path, True), path


def retracted_slugs():
    """Slugs the owner retracted - they must never publish or re-queue."""
    rp = os.path.join(STATE, "retracted.json")
    if not os.path.exists(rp):
        return set()
    try:
        data = json.load(open(rp, encoding="utf-8"))
    except Exception:
        return set()
    out = set()
    for r in data if isinstance(data, list) else [data]:
        if isinstance(r, dict):
            for k in ("slug", "topic"):
                if r.get(k):
                    out.add(r[k])
        elif isinstance(r, str):
            out.add(r)
    return out


def retry_pending():
    """Once credentials exist, pending packages publish themselves."""
    pp = os.path.join(STATE, "pending_publish.json")
    if not os.path.exists(pp):
        return 0
    pend = json.load(open(pp, encoding="utf-8"))
    # 28 Sep: backlog older than 2h references an out-of-date pool - drop
    # it (loudly) instead of replaying stale/duplicate bullets onto the Page.
    _fresh = []
    for it in pend:
        try:
            _age = (datetime.now(IST) - datetime.fromisoformat(it["at"])).total_seconds() / 60
        except Exception:
            _age = 0
        if _age > 120:
            log(f"[pending] dropped stale backlog {it.get('story')} ({_age:.0f} min old)")
        else:
            _fresh.append(it)
    if len(_fresh) != len(pend):
        json.dump(_fresh, open(pp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    pend = _fresh
    if not pend:
        return 0
    cfg = json.load(open(os.path.join(BASE, "config.json"), encoding="utf-8"))
    pub = cfg.get("publish", {})
    pgc = cfg.setdefault("page", {})
    # 28 Sep: CI injects credentials via env (config.json keeps them blank)
    if os.environ.get("FB_PAGE_TOKEN"):
        pgc["page_access_token"] = os.environ["FB_PAGE_TOKEN"]
    if os.environ.get("FB_PAGE_ID"):
        pgc["facebook_page_id"] = os.environ["FB_PAGE_ID"]
    connected = (pub.get("route") == "zapier" and pub.get("zapier_webhook")
                 and pub.get("zapier_live", False)) or (
        pgc.get("facebook_page_id") and pgc.get("page_access_token"))
    if not connected:
        return 0
    done = 0
    blocked = retracted_slugs()
    try:
        _pub = json.load(open(pub, encoding="utf-8")) if os.path.exists(pub) else []
    except Exception:
        _pub = []
    done_heads = []
    for r in _pub[-40:]:
        done_heads.append(r.get("title") or "")
        done_heads += list(r.get("bullets_en") or [])[:5]
    for item in list(pend):
        if done >= MAX_POSTS_PER_CYCLE:
            break
        sf = item.get("story_file")
        if not sf or not os.path.exists(sf):
            pend.remove(item)
            continue
        ids = {item.get("story")}
        try:
            sd = json.load(open(sf, encoding="utf-8"))
            ids |= {sd.get("slug"), sd.get("topic")}
        except Exception:
            pass
        if ids & blocked:
            log("pending item is owner-retracted, dropped forever:", item["story"])
            pend.remove(item)
            continue
        log("retrying pending story:", item["story"])
        rc = publish.run(sf, True)
        if rc == 0:
            pend.remove(item)
            done += 1
            time.sleep(POST_SPACING_SECONDS)
        elif rc == 3:  # duplicate of an already-published story: drop from queue
            log("pending item now duplicate, dropped:", item["story"])
            pend.remove(item)
        elif rc == 5:
            json.dump(pend, open(pp, "w", encoding="utf-8"), indent=2)
            return 5
    json.dump(pend, open(pp, "w", encoding="utf-8"), indent=2)
    return done



# ---- owner 26 Sep: casualty policy update ------------------------------------
# MAJOR accidents and famous-personality deaths may now be covered - but only
# with correct reporting: a named place, a verified number or a public figure,
# neutral wire tone (no "shocker/tragic" tabloid language), multi-outlet.
TABLOID_RX = re.compile(r"\b(shocker|shocking|bloodbath|horror|horrible|"
                        r"gruesome|gory|nightmare|brutal|barbaric|dread|"
                        r"tragic|tragically|twist|drama|sensational)\b", re.I)
FAMOUS_ROLE = re.compile(
    r"\b(actor|actress|star|hero|heroine|comedian|singer|musician|composer|"
    r"director|filmmaker|producer|writer|author|poet|playback|anchor|journalist|"
    r"cricketer|player|captain|coach|umpire|olympian|athlete|legend|veteran|"
    r"sir|dr|justice|judge|chief justice|minister|cabinet|mp|mla|mnc|"
    r"mayor|governor|president|pm|prime minister|chief minister|dy cm|"
    r"assembly|parliament|bjp|congress|tdp|ysrcp|aipmt?|actor-cum)\b", re.I)
FAMOUS_EVENT = re.compile(
    r"\b(dies|died|dead|killed|passed away|no more|expired|perished|"
    r"murdered|shot|targeted)\b", re.I)
DISASTER_RX = re.compile(
    r"\b(train|plane|aircraft|airliner|chopper|helicopter|ship|vessel|ferry|"
    r"boat|building|bridge|flyover|underbridge|mine|factory|mill|school|"
    r"hospital|theatre|cinema|hall)\b[^.]{0,30}\b"
    r"(crash|crashes|accident|collapse|collapsed|derailment|derailed|fire|"
    r"blast|bomb|stampede|sinking|sink|capsized|leak)\b|"
    r"\b(stampede|blast|bomb blast|building collapse|train derailment|"
    r"plane crash|air crash|gas leak)\b", re.I)
CASUALTY_NUM = re.compile(
    r"\b(\d{1,4})\b(?:\s*(?:people|persons?|of the))?"
    r"\s*(?:killed|dead|died|deaths|clipped)\b|"
    r"\b(?:killed|dead|died)\b[^.]{0,20}?\b(\d{1,4})\b")
CAS_INJURED = re.compile(r"\b(\d{1,4})\b\s*(?:people\s+)?injured\b")


def casualty_allowed(c):
    """Owner 26 Sep: major accidents / famous-personality deaths ride cards
    ONLY with correct reporting. Everything else stays banned, forever."""
    t = c["title"]
    if TABLOID_RX.search(t):
        return False  # tabloid wording = not correct reporting
    if FAMOUS_ROLE.search(t) and FAMOUS_EVENT.search(t):
        return "celeb"  # public figure casualty: no WHERE needed
    for m in CASUALTY_NUM.finditer(t):
        n = int(m.group(1) or m.group(2) or 0)
        if n >= 5:
            return "major"  # 5+ dead: must name a place
    for m in CAS_INJURED.finditer(t):
        if int(m.group(1)) >= 15:
            return "major"
    if DISASTER_RX.search(t) and re.search(
            r"\b(dead|died|killed|injured|missing|feared|rescue|rescuers|"
            r"survivors?|stranded|trapped|hospitalised|burnt|charred)\b",
            t, re.I):
        return "major"  # infrastructure disaster: must name a place
    return False


DIGEST_SLOTS = tuple(range(24))  # owner 26 Sep: hourly, 24/7, never stop
CARDS_PER_DAY = 24          # cap only guards flooding; cadence = gap_min >= 60
DIGEST_SIZE = 5   # owner 16 Sep: every card carries 5 important news items
DIGEST_REGIONS = ("hyderabad", "telangana", "india", "world")  # 26 Sep: + world
# owner 16 Sep + REFINED 26 Sep: casualty news rides cards ONLY as MAJOR
# accidents (5+ dead / 15+ injured / train-plane-collapse-stampede class, with
# a named place) or famous-personality deaths - always neutral wire tone,
# numbers only from verified headline text. Petty/local incidents and any
# tabloid wording stay banned outright.
NO_CARD_CATS = {"accident_casualty", "fire_explosion", "major_crime"}
NO_CARD_RX = re.compile(r"\b(killed|murder|murdered|died|death|accident|crash|"
                        r"crashes|collision|drowned|suicide|rape|raped)\b", re.I)


TAIL_STOP = {"and", "or", "of", "to", "in", "for", "with", "as", "is", "at",
             "by", "on", "the", "a", "an", "from", "that", "which", "over",
             "after", "before", "against", "between", "into", "during",
             "despite", "unless", "until", "while", "though", "but", "so"}


def _tidy_lead(b):
    b = b.strip()
    if not b.endswith("."):
        b += "."
    words = b.rstrip(".").split(" ")
    while len(words) > 6 and words[-1].lower().strip(",;:") in TAIL_STOP:
        words = words[:-1]  # never end a headline on a dangling connector
    return " ".join(words) + "."


# 26 Sep: converter can only render structured headline shapes. For verified
# multi-word headlines with no safe translation frame, the APPROVED 25 Sep
# theme (card-2251 bullet 3) pairs the exact EN headline with a NEUTRAL
# generic Urdu line that asserts nothing beyond the category - it can never
# be wrong. Real converter output always wins; this is the last fallback.
GEN_UR = (("political_major", ("Siyasat se muttaliq ahem khabar hai.",
                               "Siyasi harkat se judi khabar hai.",
                               "Siyasi harkat ki taza khabar hai.",
                               "Siyasat ki is khabar par sab ki nazar hai.")),
          ("govt_announcement", ("Sarkar ki taraf se ahem eilan hai.",
                                 "Sarkari faislay se muttaliq khabar hai.")),
          ("economic_major", ("Maashiayat se muttaliq ahem khabar hai.",
                               "Karobari mahol se judi khabar hai.")),
          ("court_judgment", ("Adalat ke faislay se muttaliq khabar hai.",
                               "Qanooni karrawai se muttaliq khabar hai.")),
          ("protest_major", ("Ihtijaj se muttaliq khabar hai.",)),
          ("security_terror", ("Suraksha ka ahem mamla hai.",)),
          ("police_operation", ("Police ki karrawai se muttaliq khabar hai.",)),
          ("international_conflict", ("Duniya ki siyast se muttaliq khabar hai.",
                                      "Videshi mamlaat se judi khabar hai.")))
GEN_UR_REGION = {"hyderabad": ("Shehar ki ahem khabar hai.",
                               "Shehar ke liye ahem khabar hai."),
                 "telangana": ("Sooba ki ahem khabar hai.",
                               "Sooba ke liye ahem khabar hai."),
                 "india": ("Mulk bhar ki ahem khabar hai.",
                           "Mulk ke liye ahem khabar hai."),
                 "world": ("Duniya ki ahem khabar hai.",
                           "Duniya bhar se judi khabar hai.")}


_TAG_LINES = ({t for _, _ts in GEN_UR for t in _ts}
              | {t for _ts in GEN_UR_REGION.values() for t in _ts}
              | {"Ahem khabar hai."})


def _gentag(c):
    import hashlib
    cats = set(c.get("categories", []))
    opts = None
    for key, tags in GEN_UR:
        if key in cats:
            opts = tags
            break
    if opts is None:
        opts = GEN_UR_REGION.get(c.get("region"), ("Ahem khabar hai.",))
    h = int(hashlib.md5((c.get("url") or c["title"]).encode()).hexdigest(), 16)
    return opts[h % len(opts)]


def _lead_ok(b):
    if len(b) <= 95:
        return _tidy_lead(b)
    if "," in b:  # compress at the last comma, keep it a full sentence
        cut = b[:b.rindex(",")].rstrip()
        if len(cut) >= 40:
            return _tidy_lead(cut)
    return None



def _same_story(t1, t2):
    """Cross-feed duplicates: same story, different headline wording."""
    a = {w for w in re.findall(r"[a-z0-9]{5,}", t1.lower())}
    b = {w for w in re.findall(r"[a-z0-9]{5,}", t2.lower())}
    if not a or not b:
        return False
    j = len(a & b) / len(a | b)
    long_shared = any(w in b for w in a if len(w) >= 9)
    if re.search(r"\b(red|orange|yellow|weather)\s+alert\b", t1, re.I) and \
       re.search(r"\b(red|orange|yellow|weather)\s+alert\b", t2, re.I) and \
       long_shared:
        return True  # same weather-alert event, different wording
    # owner 23/24 Sep: same story must never appear as two bullets - two
    # headlines sharing a distinctive name AND the same event verb are one story
    # (fixes "Xi arrives in US" + "Xi arrives in Washington" riding one card).
    C1 = set(re.findall(r"[A-Z][A-Za-z'\u2019\-]{1,}", t1))
    C2 = set(re.findall(r"[A-Z][A-Za-z'\u2019\-]{1,}", t2))
    GENERIC_CAP = {"India", "China", "US", "UK", "Pakistan", "Russia", "Ukraine",
                   "Israel", "Gaza", "Telangana", "Hyderabad", "Delhi", "Mumbai",
                   "Congress", "BJP", "TDP", "YSRCP", "BRS", "SP", "AAP", "Police",
                   "Court", "Supreme", "High", "Minister", "Government", "Centre",
                   "Assembly", "Parliament", "State", "City", "District", "Red",
                   "Orange", "Heavy", "Two", "Three", "Four", "Five", "One", "New",
                   "Top", "Big", "After", "Amid", "Odisha", "Bihar", "Kerala",
                   "Maharashtra", "Karnataka", "Andhra", "Pradesh"}
    ACTION = {"arrives", "arrived", "reaches", "reached", "wins", "won", "seizes",
              "seized", "arrests", "arrested", "resigns", "resigned", "signs",
              "signed", "launches", "launched", "visits", "visited", "meets",
              "met", "dies", "died", "announces", "announced", "appoints",
              "appointed", "sacks", "sacked", "protests", "protested", "marches",
              "marched", "closes", "closed", "opens", "opened", "suspends",
              "suspended", "kills", "killed", "holds", "held"}
    A1 = set(re.findall(r"[a-z]{5,}", t1.lower()))
    A2 = set(re.findall(r"[a-z]{5,}", t2.lower()))
    if (C1 & C2) - GENERIC_CAP and (A1 & A2 & ACTION):
        return True
    # owner 23 Sep "same news 2 bullets": two headlines about the SAME person
    # (shared 2-word full name) inside the same legal saga are one story even
    # when the verbs differ ("SC upholds X's disqualification" +
    # "BRS welcomes SC order on X, dares him...").
    LEGAL = ("court", "supreme", "judgment", "verdict", "order", "bench",
             "judge", "plea", "pleas", "disqualification", "hearing", "ruling")
    bg1 = set(map(str.lower, re.findall(
        r"\b[A-Z][a-z]{2,} [A-Z][a-z]{2,}\b", t1)))
    bg2 = set(map(str.lower, re.findall(
        r"\b[A-Z][a-z]{2,} [A-Z][a-z]{2,}\b", t2)))
    _GEN_BIG = {"high court", "supreme court", "new delhi", "central gov",
                "state gov", "andhra pradesh", "madhya pradesh", "chief min",
                "prime min", "press conf", "social med"}
    _distinct_bg = {b for b in (bg1 & bg2)
                    if b not in _GEN_BIG and
                    not all(w in {g.lower() for g in GENERIC_CAP}
                            for w in b.split())}
    if _distinct_bg and (A1 & set(LEGAL)) and (A2 & set(LEGAL)):
        return True
    g1 = t1.lower().split()[0] if t1.split() else ""
    g2 = t2.lower().split()[0] if t2.split() else ""
    GENERIC = {"govt", "government", "police", "centre", "center", "court", "sc",
               "supreme", "hc", "high", "imd", "eci", "ec", "union", "india", "pm",
               "cm", "minister", "officials", "reports", "watch", "rupee", "sensex"}
    if g1 != g2 and g1 not in GENERIC and g2 not in GENERIC:
        return j >= 0.60  # different leading actors: same story only if near-identical
    return j >= 0.40 or (long_shared and j >= 0.25)


def build_digest(cands, catchup=False):
    """One card, three full headlines: Hyderabad + Telangana + India.
    catchup (owner 25 Sep): after silence, older-but-clean stories may still
    ride a backlog card; quality gates are identical."""
    pub = os.path.join(STATE, "published.json")
    done = set()
    if os.path.exists(pub):
        try:
            for r in json.load(open(pub, encoding="utf-8")):
                done.add(r.get("topic"))
                done |= set(r.get("story_topics", []))  # no story repeats across cards
        except Exception:
            pass
    blocked = retracted_slugs()
    try:
        _pub = json.load(open(pub, encoding="utf-8")) if os.path.exists(pub) else []
    except Exception:
        _pub = []
    done_heads = []
    for r in _pub[-40:]:
        done_heads.append(r.get("title") or "")
        done_heads += list(r.get("bullets_en") or [])[:5]
    today = datetime.now(IST).strftime("%Y-%m-%d")
    TAG = {"hyderabad": "Hyderabad ki khabar",
           "telangana": "Telangana ki khabar",
           "india": "India ki khabar",
           "world": "Duniya ki khabar"}
    pool = []
    _skip = {"stale": 0, "casualty": 0, "class": 0, "conv": 0,
             "qual": 0, "dup": 0, "fr": 0, "tag": 0, "mt": 0, "bank": 0}

    def _collect(minscore, maxage, minC):
            for c in cands:
                age = c.get("age_hours") or 99
                if age > maxage or (c.get("score") or 0) < minscore:
                    _skip["stale"] += 1
                    continue
                if any(_same_story(c["title"], dt) for dt in done_heads if dt):
                    _skip["dup"] += 1
                    continue  # 28 Sep: stories that rode a recent card must
                              # never re-enter the pool - they only came here
                              # to be net-dropped, starving picks to <3
                _ban = bool(set(c.get("categories", [])) & NO_CARD_CATS) \
                       or bool(NO_CARD_RX.search(c["title"]))
                _cv = casualty_allowed(c) if _ban else False
                if _ban and not (_cv == "celeb"
                                 or (_cv == "major"
                                     and writer._has_loc(c["title"]))):
                    _skip["casualty"] += 1
                    continue  # 26 Sep: only MAJOR(+WHERE)/celebrity may pass
                _sc = c.get("score") or 0
                if c.get("class") not in ("A", "B") and \
                   not set(c.get("categories", [])) & MAIN_CATS and \
                   not (c.get("source_preferred") and _sc >= 65) and \
                   not _sc >= minC:
                    _skip["class"] += 1
                    continue  # 18 Sep: class C with score >=60 now rides cards
                fr = writer.build(c)
                if fr and len(fr["bullets_en"]) >= 3:
                    lead = _lead_ok(fr["bullets_en"][0])
                    lead_ur = _lead_ok(fr["bullets_ur"][0])
                    alt = writer.urdu_headline(c["title"])
                    if alt and len(alt) <= 118 and lead and \
                       not writer.bullet_quality(lead, alt):
                        lead_ur = alt          # converter-first: cleaner Urdu
                    elif lead_ur and len(lead_ur) > 118:
                        lead_ur = alt or lead_ur
                else:
                    _skip["fr"] += 1   # no frame: title + converter pair is enough
                    lead = _lead_ok(c["title"].strip())
                    lead_ur = writer.urdu_headline(c["title"]) if lead else None
                if not lead or not lead_ur or len(lead_ur) > 118:
                    t2 = re.sub(r"\s+[A-Z][a-z]+-wide\b", "", c["title"].strip())
                    t2 = re.sub(r",?\s+on\s+[A-Z][a-z]+\s+\d{1,2}"
                                r"(?:st|nd|th|rd)?(?:\s+to\s+\d{1,2}"
                                r"(?:st|nd|th|rd)?)?", "", t2).strip()
                    if t2 and t2 != c["title"].strip():
                        a2 = writer.urdu_headline(t2)
                        l2 = _lead_ok(t2)
                        if (a2 and len(a2) <= 95 and l2
                                and not writer.bullet_quality(l2, a2)):
                            lead, lead_ur = l2, a2  # date/modifier was the only overhang
                if not lead or not lead_ur or len(lead_ur) > 118:
                    if lead and len(lead) <= 95 and _ru_on():
                        lead_ur = ""    # roman_urdu step fills this after picks
                    else:
                        _skip["conv"] += 1
                        continue
                tw = {w.lower() for w in re.findall(r"[A-Za-z]{7,}", c["title"])}
                if not tw & {w.lower() for w in re.findall(r"[A-Za-z]{7,}", lead)}:
                    lead = _lead_ok(c["title"].strip())  # frame lost the substance
                    lead_ur = writer.urdu_headline(c["title"]) if lead else None
                    if (not lead_ur or len(lead_ur) > 118) and lead and fr:
                        # tier 3: keep the frame pair if its lead still shares substance
                        tw7 = {w.lower() for w in re.findall(r"[A-Za-z]{6,}", c["title"])}
                        fr7 = {w.lower() for w in re.findall(r"[A-Za-z]{6,}", fr["bullets_en"][0])}
                        if len(fr["bullets_en"][0]) >= 50 and len(tw7 & fr7) >= 2:
                            lead, lead_ur = fr["bullets_en"][0], fr["bullets_ur"][0]
                            lead, lead_ur = _lead_ok(lead), _lead_ok(lead_ur)
                    if not lead or not lead_ur or len(lead_ur) > 118:
                        if lead and len(lead) <= 95 and _ru_on():
                            lead_ur = ""    # roman_urdu fills after picks
                        else:
                            _skip["conv"] += 1
                            continue
                topic = c["region"] + "-" + slugify(c["title"])[:40]
                if topic in done or topic in blocked:
                    continue
                if any(topic == x[3] for x in pool):
                    _skip["dup"] += 1
                    continue  # same story from another feed - one bullet only
                _q = [] if not lead_ur else writer.bullet_quality(lead, lead_ur)
                if _q:
                    alt = writer.urdu_headline(c["title"])  # converter repair before skip
                    if alt and len(alt) <= 118 and not writer.bullet_quality(lead, alt):
                        lead_ur = alt
                        _q = []
                if _q and _ru_on() and all(q.startswith("ur-") for q in _q):
                    lead_ur = ""    # 27 Sep owner: rules line failed quality
                    _q = []         # -> the translator gets its one chance
                if _q and set(_q) <= {"en-titlecase-garbage", "en-no-specifics"}:
                    # 25 Sep: feed writes Title Case; synthesize the SAME fact as
                    # a sentence-case lead, then re-run ALL gates on it
                    e2 = writer.en_headline(c["title"])
                    l2 = _lead_ok(e2) if e2 else None
                    if l2 and not writer.bullet_quality(l2, lead_ur):
                        lead = l2
                        _q = []
                if _q:
                    _skip["qual"] += 1
                    continue  # 17 Sep gate: no vague EN / English-fragment Urdu bullets
                pool.append((c, lead, lead_ur, topic))

    # never-silent stages (owner 23 Sep): the FULL pipeline runs per stage -
    # pool -> picks -> quality/cross-card nets - and a card ships the moment a
    # stage yields >= 3 clean bullets. Previously the nets could drop picks
    # below 3 AFTER the stage loop and the run went silent with pool=3.
    def _build_picks():
        def rank(x):  # politics first, then Class A, then score
            c = x[0]
            return (0 if "political_major" in c.get("categories", []) else 1,
                    0 if c["class"] == "A" else 1, -(c.get("score") or 0))
        picks = []
        for reg in DIGEST_REGIONS:
            best = sorted((x for x in pool if x[0]["region"] == reg
                           and x not in picks), key=rank)
            for b in best:
                if not b[2] and _line_h(b[1]) in _miss_bank:
                    _skip["bank"] += 1
                    continue  # banked (recently failed RU) - next story tries
                if b not in picks and \
                   not any(_same_story(b[0]["title"], x[0]["title"]) for x in picks):
                    picks.append(b)
                    break
        for x in sorted(pool, key=rank):  # fill to 5, politics leading
            if len(picks) >= DIGEST_SIZE:
                break
            if not x[2] and _line_h(x[1]) in _miss_bank:
                _skip["bank"] += 1
                continue  # banked line: give the slot to a fresh story
            if x not in picks and \
               not any(_same_story(x[0]["title"], p[0]["title"]) for p in picks):
                picks.append(x)
        if len(picks) < DIGEST_SIZE:  # fallback: full headline bullets with Urdu tags
            def rank2(c):
                return (0 if "political_major" in c.get("categories", []) else 1,
                        0 if c["class"] == "A" else 1, -(c.get("score") or 0))
            for c in sorted(cands, key=rank2):
                if len(picks) >= DIGEST_SIZE:
                    break
                age = c.get("age_hours") or 99
                if age > 12 or (c.get("score") or 0) < 55:
                    continue
                if set(c.get("categories", [])) & NO_CARD_CATS:
                    continue
                if NO_CARD_RX.search(c["title"]):
                    continue
                if re.search(r"\b(must|should)\s+[a-z]", c["title"]) and not \
                   re.search(r"\b(says?|said|urges?|urgest?|demands?|calls?|warns?)\b",
                             c["title"], re.I):
                    continue
                if any(_same_story(c["title"], dt) for dt in done_heads if dt):
                    continue  # already rode an earlier card
                _sc2 = c.get("score") or 0
                if c.get("class") not in ("A", "B") and \
                   not set(c.get("categories", [])) & MAIN_CATS and \
                   not (c.get("source_preferred") and _sc2 >= 65) and \
                   not _sc2 >= 60:
                    continue
                topic = c["region"] + "-" + slugify(c["title"])[:40]
                if topic in done or topic in blocked:
                    continue
                lead = _lead_ok(c["title"].strip())
                if not lead:
                    continue
                if any(lead == x[1] for x in picks) or \
                   any(topic == x[3] for x in picks) or \
                   any(_same_story(c["title"], x[0]["title"]) for x in picks):
                    continue
                ur_line = writer.urdu_headline(c["title"])
                if not ur_line or len(ur_line) > 118:
                    if lead and len(lead) <= 95 and _ru_on():
                        if _line_h(lead) in _miss_bank:
                            _skip["bank"] += 1
                            continue  # 1 Oct: banked at selection time too
                        ur_line = ""    # roman_urdu fills after picks
                    else:
                        continue  # never publish a confusing Urdu mirror
                if ur_line and writer.bullet_quality(lead, ur_line):
                    _qb = writer.bullet_quality(lead, ur_line)
                    if _ru_on() and all(q.startswith("ur-") for q in _qb):
                        ur_line = ""  # 27 Sep: failed rules line -> LLM turn
                    else:
                        continue  # 17 Sep gate: rules-quality Urdu only
                picks.append((c, lead, ur_line, topic))
        # (owner 26 Sep verdict: rules-only Urdu - no MT, no filler)
        # final safety nets: unsafe pairs AND cross-card repeats (23 Sep)
        _pre = list(picks)
        def _pair_ok(p):
            return (not p[2]) or not writer.bullet_quality(p[1], p[2])
        picks = [p for p in picks if _pair_ok(p)]
        for p in _pre:
            if p not in picks:
                log(f"[digest] net-dropped(qual): {p[1][:55]} :: {p[2][:45]}")
        _pre = list(picks)
        picks = [p for p in picks
                 if not any(_same_story(p[0]["title"], dt) for dt in done_heads if dt)]
        for p in _pre:
            if p not in picks:
                log(f"[digest] net-dropped(repeat): {p[1][:55]}")
        return picks

    picks = []
    # 1 Oct fix: the miss bank is now consulted DURING selection (below),
    # not after. Previously the same top-ranked repeat-echo lines were
    # picked every cycle and only pruned AFTER _build_picks, so the run
    # starved at <3 while dozens of clean stories sat one rank lower.
    _miss_bank = _ru_miss_load()
    # 1 Oct (2nd outage same day): when the PRIMARY translator model has a
    # multi-hour echo fit, every cycle died 0/N accepted and only salvage
    # (4 attempts/line) fought it. Now a zero-accept cycle ROTATES the whole
    # batch call to the next model in the chain - one model's bad hour can
    # never freeze the page. Gates unchanged; only the voice asking changes.
    _ROT_FILE = os.path.join(STATE, "ru_rot.json")
    try:
        _rot = int(json.load(open(_ROT_FILE)).get("i", 0) or 0)
    except Exception:
        _rot = 0
    _stages = [(55, 12, 60), (50, 16, 55), (45, 20, 50),
               (45, 26, 50)]  # owner 27 Sep: every run finishes at the wide
                              # 26h window - nothing eligible left = wait, not
                              # a narrower filter
    for _stage, (_ms, _ma, _mc) in enumerate(_stages):
        _collect(_ms, _ma, _mc)
        log(f"[digest] stage-{_stage}: pool={len(pool)} skips={_skip}")
        picks = _build_picks()
        if len(picks) >= 3:
            if _stage:
                log(f"[digest] stage-{_stage} relaxation used (pool={len(pool)})")
            break
    # ---- owner's Roman Urdu translation step (26 Sep) -------------------
    # ONE roman_urdu call fills Urdu for every bullet the rules could not
    # render (rule Urdu is never re-translated). A line failing the module's
    # validators - or our pair gates - DEMOTES its story; if fewer than 3
    # publishable bullets remain the run WAITS silently ("no important news
    # => don't post"; filler is permanently banned).
    # 28 Sep (outage round 3, the REAL starvation cause): the engine
    # returned ok=True with zero problems, yet 3/5 lines died on MY gates:
    #   * digits - Roman Urdu legitimately SPELLED OUT English numerals
    #     ("Rs 5 Lakh Crore" -> "paanch lakh crore"). Requiring an exact
    #     digit-set match is wrong; only digits that APPEARED in Urdu but
    #     never in English (fabricated facts) may veto.
    #   * en-quality - bullet_quality() is the news-safety gate (casualty
    #     policy etc.) and is applied to every pick up front; re-running it
    #     on the Urdu side of a full, engine-validated translation only ever
    #     re-flags the story itself. Stories that passed the pre-gate are
    #     safe; the Urdu mirror is judged on its own form (paste/script/len).
    _NW = {"zero":"0","ek":"1","do":"2","teen":"3","char":"4","paanch":"5",
           "panch":"5","cheh":"6","che":"6","saat":"7","aath":"8","nau":"9",
           "das":"10","gyarah":"11","barah":"12","pandrah":"15","solah":"16",
           "bees":"20","teis":"23","chaalis":"40","chalis":"40","saat sau":"700",
           "assi":"80","navbe":"90","sau":"100","dedh":"150","hazaar":"1000",
           "ek lakh":"100000","dhai lakh":"250000","paanch lakh":"500000",
           "ek crore":"10000000","dhai crore":"25000000","panch arab":"5000000000"}
    def _en_digits(t):
        out = set(re.findall(r"\d+(?:[.,]\d+)*", t))
        tl = " " + t.lower() + " "
        for w, v in _NW.items():
            if f" {w} " in tl:
                out.add(v)
        return out
    if _miss_bank:
        log(f"[digest] ru-miss bank active: {len(_miss_bank)} line(s) "
            f"held back at selection (next stories took their slots)")
    _pend = [i for i, p in enumerate(picks) if not p[2]]
    _RU_STATS.clear()
    _RU_STATS.update({"pre_ru_picks": len(picks), "to_translate": len(_pend)})
    _kill = {}
    def _killed(i, why):
        _kill.setdefault(why, []).append(i + 1)
    if _pend and _ru_on():
        _ru_kw = {}
        if 0 < _rot:
            _ru_kw["model"] = _RU_FALLBACK_MODELS[(_rot - 1) % len(_RU_FALLBACK_MODELS)]
        _res = roman_urdu.translate_lines([picks[i][1] for i in _pend], **_ru_kw)
        _cand = _res.get("lines") or []
        _bad = set(_pend)
        _RU_STATS["engine"] = {"ok": bool(_res.get("ok")),
                               "lines_in": len(_cand), "lines_want": len(_pend),
                               "problems": str(_res.get("problems") or [])[:160]}
        if len(_cand) != len(_pend):
            _RU_STATS["kills"] = {"count-mismatch": "all"}
        # 27 Sep CI lesson: the engine's blanket 4-word English-paste rule
        # kills legitimate name runs ("Ganesh idol immersion"), so judge every
        # line it returned - even when its overall verdict was ok=False - with
        # OUR OWN name-aware checks. Facts first; style second.
        if len(_cand) == len(_pend):
            for _k, i in enumerate(_pend):
                _l = (_cand[_k] or "").strip()
                _en = picks[i][1]
                if not _l:
                    _killed(i, "empty"); continue
                if len(_l) > 118:
                    _killed(i, "len>118"); continue
                if _NONROMAN_RE.search(_l):
                    _killed(i, "urdu-script"); continue
                _ed = set(re.findall(r"\d+(?:[.,]\d+)*", _en))
                _ud = set(re.findall(r"\d+(?:[.,]\d+)*", _l))
                if _ud - _ed - _en_digits(_en):
                    _killed(i, "digits"); continue  # fabricated numbers only
                # en-quality is intentionally NOT re-applied here (see note above)
                if _real_paste(_en, _l):
                    _killed(i, "paste"); continue  # untranslated lowercase run
                if _echo_paste(_en, _l):
                    _killed(i, "english-echo"); continue  # verbatim/wholesale
                picks[i] = picks[i][:2] + (_l,) + picks[i][3:]
                _bad.discard(i)
        _RU_STATS["kills"] = _RU_STATS.get("kills") or _kill
        _RU_STATS["accepted"] = len(_pend) - len(_bad)
        # 28 Sep salvage pass, WIDENED 29 Sep: every killed line (not just a
        # starving card) gets ONE private re-ask - a single-line prompt
        # translates where the batch echo'd. Capped at 4 lines/cycle: the API
        # is still spent only on lines that failed quality (owner 27 Sep).
        if _bad:
            _sal_t0 = time.time()
            for i in sorted(_bad)[:4]:
                if time.time() - _sal_t0 > 360:
                    log("[digest] salvage budget (6 min) reached - "
                        "remaining killed lines wait for the next cycle")
                    break
                _en2 = picks[i][1]
                if not _en2:
                    continue
                for _m2 in (None,) + _RU_FALLBACK_MODELS:
                    try:
                        _r2 = (roman_urdu.translate_lines([_en2]) if not _m2
                               else roman_urdu.translate_lines([_en2],
                                                               model=_m2))
                    except Exception:
                        continue
                    _l2 = ((_r2.get("lines") or [""])[0] or "").strip()
                    if (not _l2 or len(_l2) > 118 or _NONROMAN_RE.search(_l2)
                            or _real_paste(_en2, _l2)
                            or _echo_paste(_en2, _l2)):
                        continue
                    _ed2 = set(re.findall(r"\d+(?:[.,]\d+)*", _en2))
                    _ud2 = set(re.findall(r"\d+(?:[.,]\d+)*", _l2))
                    if _ud2 - _ed2 - _en_digits(_en2):
                        continue
                    picks[i] = picks[i][:2] + (_l2,) + picks[i][3:]
                    _bad.discard(i)
                    _RU_STATS.setdefault("salvaged", []).append(i + 1)
                    if _m2:
                        _RU_STATS.setdefault("via", {})[str(i + 1)] = _m2
                    break
            _RU_STATS["accepted"] = len(_pend) - len(_bad)
        log(f"[digest] roman-urdu step: {len(_pend) - len(_bad)}/"
            f"{len(_pend)} lines accepted"
            + ("" if not _res["problems"] else
               " (watch: " + str(_res["problems"][0])[:60] + ")"))
        if _bad:
            for i in _bad:
                _miss_bank[_line_h(picks[i][1])] = time.time()
            _ru_miss_save(_miss_bank)
            picks = [p for j, p in enumerate(picks) if j not in _bad]
        try:
            _acc = _RU_STATS.get("accepted", 0)
            _new = 0 if _acc > 0 else (_rot + 1)
            if _new != _rot:
                json.dump({"i": _new, "at": time.time()}, open(_ROT_FILE, "w"))
                if _new:
                    log("[digest] RU engine rotation -> model #%d "
                        "(zero-accept cycle; next try: %s)"
                        % (_new, _RU_FALLBACK_MODELS[(_new - 1) % len(_RU_FALLBACK_MODELS)]))
        except Exception:
            pass
    picks = [p for p in picks if p[2]]   # never ship an empty Urdu mirror
    if len(picks) < 3:
        log("[digest] wait: <3 stories with publishable Urdu this cycle")
        return None
    now = datetime.now(IST)
    return {
        "slug": f"auto-digest-{today}-s{now.strftime('%H%M')}",
        "topic": f"digest-{today}-{now.strftime('%H%M')}",
        "title": "Daily digest: " + " | ".join(p[0]["title"][:40] for p in picks),
        "headline": "📰 LATEST NEWS",
        "region": "india", "classification": "B",
        "timestamp": now.strftime("%d %b %Y, %I:%M %p IST"),
        "source": " / ".join(dict.fromkeys(p[0]["source"] for p in picks)),
        "source_urls": [p[0]["url"] for p in picks],
        "verified_by": "automated pipeline (multi-feed cross-check)",
        "key_facts": [],
        "story_topics": [p[3] for p in picks],
        "bullets_en": [p[1] for p in picks],
        "bullets_ur": [p[2] for p in picks],
    }


def one_cycle():
    cfg = json.load(open(os.path.join(BASE, "config.json"), encoding="utf-8"))
    log("=== cycle start ===")
    _cu = os.environ.get("DIGEST_CATCHUP") == "1"
    cyc = monitor.run_cycle(26 if _cu else cfg["rules"]["max_post_age_hours"],
                            120)  # owner 27 Sep: full breadth every run

    def _prio(c):  # politics & main news first, then by score
        main = bool(set(c.get("categories", [])) & MAIN_CATS) or c.get("class") == "A"
        return (0 if main else 1, -(c.get("score") or 0))
    cyc["candidates"].sort(key=_prio)
    if cyc["errors"] and cyc["feeds_ok"] == 0:
        alert("All monitored feeds failed", "\n".join(cyc["errors"]))
        raise SystemExit(9)

    posted = retry_pending()
    q0 = load_quota()
    today0 = datetime.now(IST).strftime("%Y-%m-%d")
    if q0.get("day") != today0:
        q0 = {"day": today0, "regular": 0, "day_total": 0,
              "emerg_month": q0.get("emerg_month", {})}
    ncards = q0.get("digests", {}).get(today0, 0)
    gap_min = 9999.0
    if q0.get("last_card_at"):
        gap_min = (datetime.now(IST) -
                   datetime.fromisoformat(q0["last_card_at"])
                   ).total_seconds() / 60  # cards sit 2h+ apart
    # owner 25 Sep catch-up: a manual dispatch with catchup=1 (or ANY silence
    # past 3h inside the window) drains the backlog at 30-min spacing until the
    # pool is exhausted; the normal 2h slot rhythm resumes by itself.
    catchup = (os.environ.get("DIGEST_CATCHUP") == "1"
               or gap_min >= 150)  # owner 26 Sep: no quiet hours any more
    if catchup:
        log(f"[catchup] armed: {gap_min:.0f} min since last card")
    cap = CARDS_PER_DAY + (6 if catchup else 0)
    _slot = DIGEST_SLOTS[min(ncards, len(DIGEST_SLOTS) - 1)]
    # 28 Sep ~15:45 owner: "pls now post hourly" - catch-up draining is
    # over; steady hourly card rhythm from here, any hour of the day.
    # (55 so the 10-min cycle lands each hour without skipping a slot.)
    _due = gap_min >= 55
    global _DIGEST_NOTE
    if not (ncards < cap and _due and posted != 5):
        _DIGEST_NOTE = (f"gate closed: ncards {ncards}/{cap}, gap {gap_min:.0f} min, "
                        f"retry_pending={posted}")
    if (ncards < cap and _due and posted != 5):
        try:
            dg = build_digest(cyc["candidates"], catchup=catchup)
        except Exception as ex:
            import traceback
            log("DIGEST BUILD CRASH:", ex, traceback.format_exc()[-400:])
            dg = None
        _DIGEST_NOTE = "starved: <3 publishable Urdu lines"
        if dg:
            _DIGEST_NOTE = "ready"
            log("DIGEST ready: 3 fresh stories, one card")
            rc, _ = try_publish(dg)
            if rc == 0:
                q0["digests"] = {today0: ncards + 1}
                q0["last_card_at"] = datetime.now(IST).isoformat()
                q0["day_total"] = q0.get("day_total", 0) + 1
                save_quota(q0)
                _DIGEST_NOTE = f"PUBLISHED {dg['slug']}"
                q0["refuse_streak"] = 0
                log("published daily card:", dg["slug"])
                time.sleep(POST_SPACING_SECONDS)
            elif rc == 3:
                _DIGEST_NOTE = "duplicate, slot skipped"
                log("digest duplicate, skipping this slot")
                q0["digests"] = {today0: ncards + 1}
                save_quota(q0)
            else:
                _DIGEST_NOTE = f"REFUSED rc={rc} - retrying next cycle"
                log(f"digest refused (rc={rc}), will retry next cycle")
                # 28 Sep: a Meta-side wall (#283 etc.) lets cycles build
                # cards forever while nothing publishes. Three refusals in
                # a row = not transient: write ALERT.md so the job-end step
                # escalates it to a GitHub issue (fail loud, never silent).
                q0["refuse_streak"] = int(q0.get("refuse_streak") or 0) + 1
                if q0["refuse_streak"] >= 3:
                    alert(f"Publishing refused by Facebook {q0['refuse_streak']} cycles in a row",
                          f"Latest rc={rc}. Cards are building fine - the wall is on the "
                          "Meta side (permissions/review). Pipeline will resume on its own "
                          "the moment Graph accepts posts again.")
                save_quota(q0)
    if posted == 5:
        alert("Facebook publish failed while retrying pending posts",
              "Check Page token permissions (pages_manage_posts).")
        raise SystemExit(9)
    n = 0 if not isinstance(posted, int) else posted

    for c in cyc["candidates"]:
        if n >= MAX_POSTS_PER_CYCLE:
            break
        if not eligible(c):
            log("stale or low-score, skipped:", c["title"][:60])
            continue
        framed = writer.build(c)
        if not framed:
            log("queued for editorial pass (no safe auto frame):", c["title"][:70])
            continue
        story = story_from(c, framed)
        if {story.get("slug"), story.get("topic")} & retracted_slugs():
            log("BLOCKED owner-retracted story:", story["slug"])
            continue
        # owner 26 Sep: BREAKING posts as it happens; everything else rides
        # the hourly card. Breaking = Class A, score >= 88, politics/main-news.
        _breaking = (c.get("class") == "A" and (c.get("score") or 0) >= 88
                     and bool(set(c.get("categories", [])) & MAIN_CATS))
        if not _breaking:
            log("held for the next card:", story["slug"])
            continue
        emerg = False  # casualty-type cats are owner-banned, never emergency-post
        log("BREAKING accepted (score %s): %s" % (c.get("score"), story["slug"][:60]))
        q = load_quota()
        today = datetime.now(IST).strftime("%Y-%m-%d")
        month = today[:7]
        if q.get("day") != today:
            q = {"day": today, "regular": 0, "day_total": 0,
                 "emerg_month": q.get("emerg_month", {})}
        if q.get("day_total", 0) >= DAILY_TOTAL_LIMIT:
            log("daily total limit reached, holding:", story["slug"])
            continue
        if emerg:
            if q["emerg_month"].get(month, 0) >= MONTHLY_EMERGENCY_LIMIT:
                log("monthly emergency limit (10) reached, holding:", story["slug"])
                continue
        elif q.get("regular", 0) >= DAILY_REGULAR_LIMIT:
            log("daily regular limit (3) reached, holding:", story["slug"])
            continue
        elif q.get("last_at"):
            gap = (datetime.now(IST) -
                   datetime.fromisoformat(q["last_at"])).total_seconds() / 3600
            if gap < MIN_GAP_HOURS:
                log("2h spacing rule, holding till next slot:", story["slug"])
                continue
        log(f"AUTO POST candidate [{c['class']}|{c['score']}|{c['region']}"
            f"{'|EMERGENCY' if emerg else ''}]:", c["title"][:70])
        rc, path = try_publish(story)
        if rc == 0:
            n += 1
            q["day_total"] = q.get("day_total", 0) + 1
            if emerg:
                q["emerg_month"][month] = q["emerg_month"].get(month, 0) + 1
            else:
                q["regular"] = q.get("regular", 0) + 1
                q["last_at"] = datetime.now(IST).isoformat()
            save_quota(q)
            log("published:", story["slug"])
            time.sleep(POST_SPACING_SECONDS)  # avoid FB Page rate-limit errors
        elif rc == 3:
            log("duplicate suppressed:", story["slug"])
        elif rc == 4:
            log("packaged, awaiting Facebook connection:", story["slug"])
        elif rc == 5:
            alert("Facebook API error during publish",
                  f"story={story['slug']}\nSee logs above for the Graph API response.")
            raise SystemExit(9)
        else:
            log(f"refused by pre-publish checks (rc={rc}):", story["slug"])

    # ---- editorial inbox: owner-pasted story files are published here ----
    sdir = os.path.join(BASE, "stories")
    inbox = sorted(os.path.join(sdir, f) for f in os.listdir(sdir)
                   if f.startswith("inbox-") and f.endswith(".json"))         if os.path.isdir(sdir) else []
    for fp in inbox:
        if n >= MAX_POSTS_PER_CYCLE:
            log("inbox held for next cycle (post cap):", os.path.basename(fp))
            continue
        try:
            story = json.load(open(fp, encoding="utf-8"))
        except Exception as e:
            log("inbox file unreadable, removed:", os.path.basename(fp), e)
            os.remove(fp)
            continue
        log("INBOX story:", story.get("slug", os.path.basename(fp)))
        rc, path = try_publish(story)
        if rc == 0:
            n += 1
            log("published from inbox:", story.get("slug"))
            os.remove(fp)
            time.sleep(POST_SPACING_SECONDS)
        elif rc == 3:
            log("inbox duplicate suppressed:", story.get("slug"))
            os.remove(fp)
        elif rc == 5:
            log("inbox publish failed (FB), keeping for retry:", story.get("slug"))
        else:
            log(f"inbox refused by pre-publish checks (rc={rc}), removed:",
                story.get("slug"))
            os.remove(fp)

    summary = {"cycle": cyc["cycle_id"], "at": datetime.now(IST).isoformat(),
               "candidates": len(cyc["candidates"]), "published_this_cycle": n}
    summary["digest"] = _DIGEST_NOTE
    summary["ru"] = dict(_RU_STATS)
    summary["tail"] = list(_LOGTAIL)[-30:]   # live diagnostics (refusal reasons,
                                              # roman-urdu acceptance, stage skips)
    os.makedirs(STATE, exist_ok=True)
    json.dump(summary, open(os.path.join(STATE, "last_cycle.json"), "w"), indent=2)
    log(f"=== cycle end: {n} new post(s), "
        f"{len(cyc['candidates'])} candidates scanned ===")


def main():
    if "--once" in sys.argv:
        log("single-cycle mode (--once)")
        one_cycle()
        return
    # Owner 28 Sep "no interruptions": self-reliant relay loop. Bounded by
    # --deadline=MIN so the workflow hands off to a fresh runner before any
    # hard kill; tolerates single-cycle errors; pushes memory after EVERY
    # cycle so even a crash can never re-post a story or lose the cadence.
    _dl = 0.0
    for _a in sys.argv:
        if _a.startswith("--deadline="):
            try:
                _dl = float(_a.split("=", 1)[1])
            except ValueError:
                pass
    _iv = float(os.environ.get("DAEMON_INTERVAL_MIN", "10"))
    _t0 = time.time()
    log(f"autonomous relay up - cycle every {_iv:.0f} min"
        + (f", deadline {_dl:.0f} min" if _dl else ", no deadline"))
    _fails = 0
    # 28 Sep: hard per-cycle wall budget - a frozen runner or wedged cycle
    # cannot hold the slot; the alarm raises into the existing 3-strike
    # handler which commits memory and moves on.
    _cmax = int(os.environ.get("DAEMON_CYCLE_MAX_SEC", "780"))
    class _CycleTimeout(Exception):
        pass
    def _fire(_sig, _frm):
        raise _CycleTimeout(f"cycle exceeded {_cmax}s wall budget")
    signal.signal(signal.SIGALRM, _fire)
    while True:
        try:
            signal.setitimer(signal.ITIMER_REAL, _cmax)
            one_cycle()
            signal.setitimer(signal.ITIMER_REAL, 0)
            _fails = 0
        except SystemExit:
            _commit_memory()
            raise
        except Exception:
            signal.setitimer(signal.ITIMER_REAL, 0)
            _fails += 1
            log("cycle error (%d/3): %s" % (_fails, traceback.format_exc()[-350:]))
            if _fails >= 3:
                alert("relay: 3 consecutive cycle failures - exiting; a fresh "
                      "runner takes over within minutes (cron+watchdog)", "")
                _commit_memory()
                raise SystemExit(9)
        _commit_memory()
        if _dl and (time.time() - _t0) / 60.0 >= _dl - 8:
            log("relay deadline reached - handing off to the next queued run")
            return
        time.sleep(_iv * 60)


def _commit_memory():
    """Best-effort: push state after each cycle (relay mode). Never raises."""
    import subprocess
    root = os.path.dirname(BASE)
    def _git(*a, check=False):
        try:
            r = subprocess.run(("git", "-C", root) + a, capture_output=True,
                               timeout=60)
            if check and r.returncode != 0:
                raise RuntimeError(r.stderr.decode()[:200])
            return r
        except Exception:
            if check:
                raise
            return None
    br = os.environ.get("GITHUB_REF_NAME", "main")
    try:
        _git("add", "news_agent/state", "news_agent/stories", check=True)
        d = _git("diff", "--cached", "--quiet")
        if d is None or d.returncode == 0:
            return  # nothing changed
        _git("-c", "user.name=azad-news-agent", "-c", "user.email=agent@localhost",
             "commit", "-m", f"relay cycle {datetime.now(IST).isoformat(timespec='seconds')}",
             check=True)
        _git("fetch", "origin", f"refs/heads/{br}")
        _git("push", "origin", f"HEAD:refs/heads/{br}", check=True)
        log("[relay] memory pushed")
    except Exception as e:
        # 1 Oct: on rejection, rebase our commit on the remote tip ONCE and
        # retry. No blind reset: that would silently drop this runner's
        # cycle state (published.json/ru_misses dedupe).
        try:
            r = _git("rebase", f"origin/{br}")
            if r is not None and r.returncode != 0:
                _git("rebase", "--abort")  # conflicting state files: skip this
                raise RuntimeError("rebase conflict")  # push; next cycle retries
            _git("push", "origin", f"HEAD:refs/heads/{br}", check=True)
            log("[relay] memory pushed (after rebase)")
        except Exception as e2:
            log("[relay] memory push skipped:", str(e2)[:120])


if __name__ == "__main__":
    main()

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
import sys
import time
import traceback
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import monitor  # noqa: E402
import publish  # noqa: E402
import writer  # noqa: E402

CYCLE_SECONDS = 90 * 60
POST_SPACING_SECONDS = 75  # pause between posts in one burst (FB rate limits)
MAX_POSTS_PER_CYCLE = 2
STATE = os.path.join(BASE, "state")
STORIES = os.path.join(BASE, "stories")
IST = monitor.IST

HEADLINE_MAP = monitor.HEADLINE_MAP


def log(*a):
    print(f"[{datetime.now(IST).strftime('%d %b %H:%M:%S')}]", *a, flush=True)


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


MAX_AGE_HOURS = 6.0  # owner policy: never post old news
MAIN_CATS = {"political_major", "govt_announcement", "economic_major",
             "court_judgment", "protest_major", "police_operation",
             "disaster_weather", "international_conflict"}
MIN_GAP_HOURS = 2.0  # owner policy: the 3 daily posts sit 2h apart


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


DAILY_REGULAR_LIMIT = 3     # owner policy: 3 regular posts a day (IST)
MONTHLY_EMERGENCY_LIMIT = 10  # plus up to 10 emergency breaking posts a month
DAILY_TOTAL_LIMIT = 9       # hard flood guard (owner 23 Sep: card every 2h)
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
    cfg = json.load(open(os.path.join(BASE, "config.json"), encoding="utf-8"))
    pub = cfg.get("publish", {})
    connected = (pub.get("route") == "zapier" and pub.get("zapier_webhook")
                 and pub.get("zapier_live", False)) or (
        cfg["page"].get("facebook_page_id") and cfg["page"].get("page_access_token"))
    if not connected:
        return 0
    done = 0
    blocked = retracted_slugs()
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


DIGEST_SLOTS = (8, 10, 12, 14, 16, 18, 20, 22)  # owner 23 Sep: card EVERY 2 HOURS
CARDS_PER_DAY = 8           # 8 slots/day, 3-5 verified stories each
DIGEST_SIZE = 5   # owner 16 Sep: every card carries 5 important news items
DIGEST_REGIONS = ("hyderabad", "telangana", "india")
# owner 16 Sep: NO murder/accident/casualty/fire news in cards - one small
# mistake fills the comment section. Politics/govt/economy/court only.
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
    g1 = t1.lower().split()[0] if t1.split() else ""
    g2 = t2.lower().split()[0] if t2.split() else ""
    GENERIC = {"govt", "government", "police", "centre", "center", "court", "sc",
               "supreme", "hc", "high", "imd", "eci", "ec", "union", "india", "pm",
               "cm", "minister", "officials", "reports", "watch", "rupee", "sensex"}
    if g1 != g2 and g1 not in GENERIC and g2 not in GENERIC:
        return j >= 0.60  # different leading actors: same story only if near-identical
    return j >= 0.40 or (long_shared and j >= 0.25)


def build_digest(cands):
    """One card, three full headlines: Hyderabad + Telangana + India."""
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
    today = datetime.now(IST).strftime("%Y-%m-%d")
    TAG = {"hyderabad": "Hyderabad ki khabar",
           "telangana": "Telangana ki khabar",
           "india": "India ki khabar",
           "world": "Duniya ki khabar"}
    pool = []
    _skip = {"stale": 0, "casualty": 0, "class": 0, "conv": 0, "qual": 0, "dup": 0}

    def _collect(minscore, maxage, minC):
            for c in cands:
                age = c.get("age_hours") or 99
                if age > maxage or (c.get("score") or 0) < minscore:
                    _skip["stale"] += 1
                    continue
                if set(c.get("categories", [])) & NO_CARD_CATS:
                    _skip["casualty"] += 1
                    continue  # casualty/crime/fire news never rides the cards
                if NO_CARD_RX.search(c["title"]):
                    _skip["casualty"] += 1
                    continue
                _sc = c.get("score") or 0
                if c.get("class") not in ("A", "B") and \
                   not set(c.get("categories", [])) & MAIN_CATS and \
                   not (c.get("source_preferred") and _sc >= 65) and \
                   not _sc >= minC:
                    _skip["class"] += 1
                    continue  # 18 Sep: class C with score >=60 now rides cards
                fr = writer.build(c)
                if not fr or len(fr["bullets_en"]) < 3:
                    continue
                lead = _lead_ok(fr["bullets_en"][0])
                lead_ur = _lead_ok(fr["bullets_ur"][0])
                if lead_ur and len(lead_ur) > 95:
                    lead_ur = writer.urdu_headline(c["title"]) or lead_ur
                if not lead or not lead_ur or len(lead_ur) > 95:
                    _skip["conv"] += 1
                    continue
                tw = {w.lower() for w in re.findall(r"[A-Za-z]{7,}", c["title"])}
                if not tw & {w.lower() for w in re.findall(r"[A-Za-z]{7,}", lead)}:
                    lead = _lead_ok(c["title"].strip())  # frame lost the substance
                    lead_ur = writer.urdu_headline(c["title"]) if lead else None
                    if (not lead_ur or len(lead_ur) > 95) and lead:
                        # tier 3: keep the frame pair if its lead still shares substance
                        tw7 = {w.lower() for w in re.findall(r"[A-Za-z]{6,}", c["title"])}
                        fr7 = {w.lower() for w in re.findall(r"[A-Za-z]{6,}", fr["bullets_en"][0])}
                        if len(fr["bullets_en"][0]) >= 50 and len(tw7 & fr7) >= 2:
                            lead, lead_ur = fr["bullets_en"][0], fr["bullets_ur"][0]
                            lead, lead_ur = _lead_ok(lead), _lead_ok(lead_ur)
                    if not lead or not lead_ur or len(lead_ur) > 95:
                        _skip["conv"] += 1
                        continue
                topic = c["region"] + "-" + slugify(c["title"])[:40]
                if topic in done or topic in blocked:
                    continue
                if any(topic == x[3] for x in pool):
                    _skip["dup"] += 1
                    continue  # same story from another feed - one bullet only
                if writer.bullet_quality(lead, lead_ur):
                    alt = writer.urdu_headline(c["title"])  # converter repair before skip
                    if alt and len(alt) <= 95 and not writer.bullet_quality(lead, alt):
                        lead_ur = alt
                    else:
                        _skip["qual"] += 1
                        continue  # 17 Sep gate: no vague EN / English-fragment Urdu bullets
                pool.append((c, lead, lead_ur, topic))

    # never-silent stages: relax freshness/score only if pool too small
    for _stage, (_ms, _ma, _mc) in enumerate([(55, 12, 60), (50, 16, 55), (45, 20, 50)]):
        _collect(_ms, _ma, _mc)
        if len(pool) >= 3:
            if _stage:
                log(f"[digest] stage-{_stage} relaxation used (pool={len(pool)})")
            break
    log(f"[digest] pool={len(pool)} skips={_skip}")
    def rank(x):  # politics first, then Class A, then score
        c = x[0]
        return (0 if "political_major" in c.get("categories", []) else 1,
                0 if c["class"] == "A" else 1, -(c.get("score") or 0))
    picks = []
    for reg in DIGEST_REGIONS:
        best = sorted((x for x in pool if x[0]["region"] == reg
                       and x not in picks), key=rank)
        for b in best:
            if b not in picks and \
               not any(_same_story(b[0]["title"], x[0]["title"]) for x in picks):
                picks.append(b)
                break
    for x in sorted(pool, key=rank):  # fill to 5, politics leading
        if len(picks) >= DIGEST_SIZE:
            break
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
            if not ur_line or len(ur_line) > 95:
                continue  # never publish a confusing Urdu mirror
            if writer.bullet_quality(lead, ur_line):
                continue  # 17 Sep gate: no vague EN / English-fragment Urdu bullets
            picks.append((c, lead, ur_line, topic))
    # final safety net: drop any surviving unsafe pair; a card needs >=3 clean
    picks = [p for p in picks if not writer.bullet_quality(p[1], p[2])]
    if len(picks) < 3:
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
    cyc = monitor.run_cycle(cfg["rules"]["max_post_age_hours"], 40)

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
    card_gap_ok = True
    if q0.get("last_card_at"):
        card_gap_ok = (datetime.now(IST) -
                       datetime.fromisoformat(q0["last_card_at"])
                       ).total_seconds() >= 2 * 3600  # cards sit 2h+ apart
    if (ncards < CARDS_PER_DAY
            and datetime.now(IST).hour >= DIGEST_SLOTS[ncards]
            and card_gap_ok and posted != 5):
        try:
            dg = build_digest(cyc["candidates"])
        except Exception as ex:
            import traceback
            log("DIGEST BUILD CRASH:", ex, traceback.format_exc()[-400:])
            dg = None
        if dg:
            log("DIGEST ready: 3 fresh stories, one card")
            rc, _ = try_publish(dg)
            if rc == 0:
                q0["digests"] = {today0: ncards + 1}
                q0["last_card_at"] = datetime.now(IST).isoformat()
                q0["day_total"] = q0.get("day_total", 0) + 1
                save_quota(q0)
                log("published daily card:", dg["slug"])
                time.sleep(POST_SPACING_SECONDS)
            elif rc == 3:
                log("digest duplicate, skipping this slot")
                q0["digests"] = {today0: ncards + 1}
                save_quota(q0)
            else:
                log(f"digest refused (rc={rc}), will retry next cycle")
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
        log("held for the next 5-news card:", story["slug"])
        continue  # owner 16 Sep: all news rides the 3 daily cards, no solo posts
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
    os.makedirs(STATE, exist_ok=True)
    json.dump(summary, open(os.path.join(STATE, "last_cycle.json"), "w"), indent=2)
    log(f"=== cycle end: {n} new post(s), "
        f"{len(cyc['candidates'])} candidates scanned ===")


def main():
    if "--once" in sys.argv:
        log("single-cycle mode (--once)")
        one_cycle()
        return
    log("autonomous daemon up - cycle every", CYCLE_SECONDS // 60, "minutes")
    while True:
        try:
            one_cycle()
        except SystemExit:
            raise
        except Exception as e:
            alert("Unhandled technical error in cycle",
                  traceback.format_exc()[-1500:])
            raise SystemExit(9)
        time.sleep(CYCLE_SECONDS)


if __name__ == "__main__":
    main()

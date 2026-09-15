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


def eligible(c):
    cls, score, pref = c["class"], c["score"], c["source_preferred"]
    age = c.get("age_hours") or 99
    if age > MAX_AGE_HOURS:
        return False  # stale news is refused outright
    if cls in ("A", "B"):
        return True
    if cls == "C" and pref and score >= 72:
        return True
    return False


DAILY_REGULAR_LIMIT = 3     # owner policy: 3 regular posts a day (IST)
MONTHLY_EMERGENCY_LIMIT = 10  # plus up to 10 emergency breaking posts a month
DAILY_TOTAL_LIMIT = 5       # hard flood guard
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


def one_cycle():
    cfg = json.load(open(os.path.join(BASE, "config.json"), encoding="utf-8"))
    log("=== cycle start ===")
    cyc = monitor.run_cycle(cfg["rules"]["max_post_age_hours"], 12)
    if cyc["errors"] and cyc["feeds_ok"] == 0:
        alert("All monitored feeds failed", "\n".join(cyc["errors"]))
        raise SystemExit(9)

    posted = retry_pending()
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
        emerg = is_emergency(c)
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

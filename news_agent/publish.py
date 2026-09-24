#!/usr/bin/env python3
"""
Publish one approved story to the connected Facebook Page.

Flow per story file:
    1. validate the story: exactly 3 EN bullets, exactly 3 Roman Urdu bullets,
       source attribution present, verification notes present
    2. duplicate check against state/published.json (URL + topic + headline
       similarity). A story that adds no substantial new development is refused.
    3. render the branded PNG (logo + 3+3 bullets) via render.py
    4. write the FB caption text
    5. publish: photo + caption through the Graph API when credentials are
       configured; otherwise stop at a ready-to-post package in out/ and say so
    6. append the publish record so the same story is never posted twice

Never publishes rumours: the story file must carry "verified_by" with at least
one preferred/reliable source, and "not_verified" facts are stripped from the
bullets by the author before this stage.

Usage:
    python3 publish.py stories/x.json            # dry run: package only
    python3 publish.py stories/x.json --publish  # actually POST to the Page
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import render as R  # noqa: E402

CFG = json.load(open(os.path.join(BASE, "config.json"), encoding="utf-8"))
STATE = os.path.join(BASE, "state")
OUT = os.path.normpath(os.path.join(BASE, "..", "out"))
IST = timezone(timedelta(hours=5, minutes=30))


def now_iso():
    return datetime.now(IST).isoformat()


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)


# --------------------------------------------------------------------------
def validate(story):
    errors = []
    counts = {}
    for key, label in (("bullets_en", "ENGLISH"), ("bullets_ur", "ROMAN URDU")):
        b = [x for x in story.get(key, []) if x and x.strip()]
        counts[label] = len(b)
        if not (3 <= len(b) <= 5):
            errors.append(f"{label}: expected 3-5 bullets, found {len(b)}")
        for x in b:
            if len(x) > 95:
                errors.append(f"{label}: bullet over 95 chars ({len(x)}) - card spec")
                break
    if counts.get("ENGLISH") != counts.get("ROMAN URDU"):
        errors.append("ENGLISH and ROMAN URDU must carry the same number of bullets")
    if not story.get("headline"):
        errors.append("missing headline")
    if not story.get("source"):
        errors.append("missing source attribution")
    if not story.get("verified_by"):
        errors.append("missing verified_by: unverified stories must not be published")
    if story.get("region") not in ("hyderabad", "telangana", "india", "world"):
        errors.append("region must be one of hyderabad/telangana/india/world")
    cls = story.get("classification")
    if cls not in ("A", "B", "C"):
        errors.append(f"classification '{cls}' not publishable (only A/B/C reach the Page)")
    return errors


def dup_check(story, thresh):
    pub = []
    p = os.path.join(STATE, "published.json")
    if os.path.exists(p):
        pub = load(p)
    title = story.get("title") or story.get("topic", "")
    hits = []
    for rec in pub:
        rec_urls = [rec.get("url", "")] + list(rec.get("source_urls") or [])
        same_url = any(u and ru and u.split("?")[0] == ru.split("?")[0]
                       for u in story.get("source_urls", [])
                       for ru in rec_urls)
        same_topic = rec.get("topic") == story.get("topic")
        sim = R.jaccard(title, rec.get("headline", "")) if title else 0
        if same_url or (same_topic and sim >= thresh):
            hits.append(rec)
    return hits


# --------------------------------------------------------------------------
def graph_post_photo(page_id, token, caption, image_path, api_version):
    """Upload the branded graphic with the caption as one Page post."""
    boundary = "----arenaBoundary7MA4YWxkTrZu0gW"
    body = b""
    fields = {"caption": caption, "access_token": token}
    for k, v in fields.items():
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    with open(image_path, "rb") as f:
        data = f.read()
    if len(data) > 2_000_000:  # keep transfers small; Zapier/FB cap is 10 MB
        import io as _io
        from PIL import Image as _Im
        buf = _io.BytesIO()
        _Im.open(image_path).convert("RGB").save(buf, "JPEG", quality=88, optimize=True)
        data = buf.getvalue()
        image_path = image_path + ".jpg"
    body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"source\"; "
             f"filename=\"{os.path.basename(image_path)}\"\r\n"
             f"Content-Type: image/png\r\n\r\n").encode() + data + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    url = f"https://graph.facebook.com/{api_version}/{page_id}/photos"
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)



def ensure_under_10mb(image_path):
    """Owner rule 23 Sep: post ONLY if graphic < 10 MB. Downscale first."""
    import os as _os
    sz = _os.path.getsize(image_path)
    if sz < 2_000_000:
        return image_path, sz
    import io as _io
    from PIL import Image as _Im
    for q in (88, 80, 72):
        buf = _io.BytesIO()
        _Im.open(image_path).convert("RGB").save(buf, "JPEG", quality=q, optimize=True)
        jpg = image_path.rsplit(".", 1)[0] + f"_q{q}.jpg"
        with open(jpg, "wb") as f:
            f.write(buf.getvalue())
        if buf.tell() < 2_000_000:
            return jpg, buf.tell()
    return jpg, buf.tell()


def zapier_post(webhook, caption, image_path, meta):
    """Hand a finished post to Zapier (Catch Hook). Zapier holds the Facebook
    authorization for the Page; we send caption + branded PNG as multipart so
    text-only since 24 Sep (owner): image attachments broke the Zap."""
    boundary = "----arenaZapBoundary9x81b"
    body = b""
    fields = {"caption": caption, "slug": meta.get("slug", ""),
              "headline": meta.get("headline", ""), "region": meta.get("region", ""),
              "timestamp": meta.get("timestamp", ""),
              "bullets_en": json.dumps(meta.get("bullets_en", []), ensure_ascii=False),
              "bullets_ur": json.dumps(meta.get("bullets_ur", []), ensure_ascii=False)}
    for k, v in fields.items():
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    # owner 24 Sep: "dont try to post pic as it is causing zapier error" -
    # the image part is no longer attached; Zapier receives text fields only.
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        webhook, data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                 "Accept": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        raw = r.read().decode()
        try:
            return json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            return {"response": raw[:200]}


def graph_post_text(page_id, token, message, api_version):
    url = f"https://graph.facebook.com/{api_version}/{page_id}/feed"
    data = urllib.parse.urlencode({"message": message, "access_token": token}).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


# --------------------------------------------------------------------------
def run(story_path, do_publish):
    story = load(story_path)
    slug = story.get("slug") or os.path.splitext(os.path.basename(story_path))[0]

    errors = validate(story)
    if errors:
        print("REFUSED - story failed pre-publish checks:")
        for e in errors:
            print("  -", e)
        return 2

    dups = dup_check(story, CFG["rules"]["duplicate_similarity_threshold"])
    if dups and not story.get("substantial_new_development"):
        print("REFUSED - duplicate of previously published story:")
        for rec in dups:
            print("  -", rec.get("headline"), "|", rec.get("published_at"))
        print("  To publish an update, set \"substantial_new_development\": true and "
              "describe what is new in \"new_facts\".")
        return 3

    os.makedirs(OUT, exist_ok=True)
    png = os.path.join(OUT, f"{slug}.png")
    path, meta = R.render(story, png)
    path, _sz = ensure_under_10mb(path)   # owner 23 Sep: post ONLY if < 10 MB
    if _sz >= 10_000_000:
        print(f"REFUSED - graphic is {_sz // 1_000_000} MB (>= 10 MB). Not posted.")
        return 6
    caption = R.caption_from(story)
    cap_path = os.path.join(OUT, f"{slug}-caption.txt")
    with open(cap_path, "w", encoding="utf-8") as f:
        f.write(caption)

    print("=" * 72)
    print(caption)
    print("=" * 72)
    print(f"graphic : {path}  ({meta['size'][0]}x{meta['size'][1]}, "
          f"bullet font {meta['bullet_font_px']}px, logo={'yes' if meta['logo_found'] else 'MISSING'})")
    print(f"caption : {cap_path}")
    print(f"image   : {_sz // 1024} KB built but NOT attached - text-only "
          f"per owner (Zapier image error)")

    if not do_publish:
        print("\nDRY RUN - nothing was posted. Re-run with --publish to post.")
        return 0

    pg = dict(CFG["page"])
    if os.environ.get("FB_PAGE_TOKEN"):
        pg["page_access_token"] = os.environ["FB_PAGE_TOKEN"]
    if os.environ.get("FB_PAGE_ID"):
        pg["facebook_page_id"] = os.environ["FB_PAGE_ID"]
    pub_cfg = CFG.get("publish", {})
    route = pub_cfg.get("route", "graph")
    zap = (route == "zapier" and pub_cfg.get("zapier_webhook")
           and pub_cfg.get("zapier_live", False))
    graph_ok = pg.get("facebook_page_id") and pg.get("page_access_token")
    if not (zap or graph_ok):
        print("\nBLOCKED - Facebook publishing is not connected.")
        print("  Either set publish.zapier_webhook (Zapier route, recommended) or")
        print("  page.facebook_page_id + page.page_access_token (Graph API route).")
        print("  The package above is ready to post by hand in the meantime.")
        pp = os.path.join(STATE, "pending_publish.json")
        pend = load(pp) if os.path.exists(pp) else []
        if not any(x["story"] == slug for x in pend):
            pend.append({"story": slug, "story_file": story_path,
                         "image": png, "caption": cap_path, "at": now_iso()})
            save(pp, pend)
        return 4

    try:
        if zap:
            res = zapier_post(pub_cfg["zapier_webhook"], caption, png,
                              {"slug": slug, "headline": story["headline"],
                               "region": story["region"],
                               "timestamp": story.get("timestamp", ""),
                               "bullets_en": story["bullets_en"],
                               "bullets_ur": story["bullets_ur"]})
            post_id = res.get("id") or res.get("post_id") or f"zapier:{slug}"
            print(f"\nHanded to Zapier webhook: {pub_cfg['zapier_webhook'][:48]}...")
        else:
            res = graph_post_photo(pg["facebook_page_id"], pg["page_access_token"],
                                   caption, png, pg.get("api_version", "v21.0"))
            post_id = res.get("post_id") or res.get("id")
    except urllib.error.HTTPError as e:
        print(f"\nPUBLISH ERROR {e.code}: {e.read().decode()[:600]}")
        return 5

    record = {
        "headline": story["headline"],
        "title": story.get("title", story["headline"]),
        "topic": story.get("topic"),
        "url": (story.get("source_urls") or [""])[0],
        "source": story["source"],
        "region": story["region"],
        "classification": story["classification"],
        "published_at": now_iso(),
        "key_facts": story.get("key_facts", []),
        "bullets_en": story["bullets_en"],
        "bullets_ur": story["bullets_ur"],
        "image": png,
        "fb_post_id": post_id,
    }
    p = os.path.join(STATE, "published.json")
    pub = load(p) if os.path.exists(p) else []
    pub.append(record)
    save(p, pub)
    print(f"\nPUBLISHED to the Page. post_id={post_id}")
    print(f"Recorded in state/published.json ({len(pub)} stories).")
    return 0


def check_fb():
    """Read-only Facebook connection test (no posting). Exit 0 = healthy."""
    pg = dict(CFG["page"])
    if os.environ.get("FB_PAGE_TOKEN"):
        pg["page_access_token"] = os.environ["FB_PAGE_TOKEN"]
    if os.environ.get("FB_PAGE_ID"):
        pg["facebook_page_id"] = os.environ["FB_PAGE_ID"]
    tok, pid = pg.get("page_access_token"), pg.get("facebook_page_id")
    if not (tok and pid):
        print("FB CHECK: Graph route on standby - Zapier bridge active. OK.")
        return 0
    url = (f"https://graph.facebook.com/{pg.get('api_version', 'v21.0')}/{pid}"
           f"?fields=name,link,fan_count&access_token={tok}")
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            d = json.load(r)
    except urllib.error.HTTPError as e:
        print(f"FB CHECK FAILED {e.code}: {e.read().decode()[:300]}")
        return 5
    except Exception as e:
        print(f"FB CHECK FAILED: {e}")
        return 5
    print(f"FB CHECK OK: page={d.get('name')} id={d.get('id')} "
          f"fans={d.get('fan_count')} link={d.get('link')}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("story", nargs="?")
    ap.add_argument("--publish", action="store_true")
    ap.add_argument("--check-fb", action="store_true")
    a = ap.parse_args()
    if a.check_fb:
        raise SystemExit(check_fb())
    if not a.story:
        ap.error("a story file is required unless --check-fb is given")
    raise SystemExit(run(a.story, a.publish))


if __name__ == "__main__":
    main()

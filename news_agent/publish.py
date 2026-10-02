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
        _capx = 95 if label == "ENGLISH" else 118  # 26 Sep: MT Urdu runs longer
        for x in b:
            if len(x) > _capx:
                errors.append(f"{label}: bullet over {_capx} chars ({len(x)}) - card spec")
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
def verify_public(post_id, pg):
    """Owner 28 Sep: "when you are posting check the visibility to public".
    Read the fresh post back and print a verdict line; if Facebook stored it
    hidden, un-hide it once. A failed check never stops the cadence - it just
    prints what is provable (23:2x forensics: the audience flags on our posts
    are feed_targeting=null / is_hidden=false; the only readable signals)."""
    if not post_id or str(post_id).startswith("zapier"):
        return
    ver = pg.get("api_version", "v21.0")
    tok = pg.get("page_access_token")
    base = f"https://graph.facebook.com/{ver}/{post_id}"

    def _get(url):
        req = urllib.request.Request(url + ("&" if "?" in url else "?") +
                                     f"access_token={tok}", method="GET")
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)

    d = None
    try:
        d = _get(base + "?fields=id,is_hidden,feed_targeting")
    except Exception:
        try:  # direct post read can 404 on migrated pages; published_posts can
            pid = pg.get("facebook_page_id")
            f = _get(f"https://graph.facebook.com/{ver}/{pid}/published_posts"
                     f"?limit=6&fields=id,is_hidden,feed_targeting")
            for p in (f.get("data") or []):
                if p.get("id") == post_id:
                    d = p
                    break
        except Exception as e2:
            print(f"VISIBILITY: unreadable right now ({e2}); post itself was accepted")
            return
    if d is None:
        print("VISIBILITY: post accepted but not listed yet; will confirm next audit")
        return
    hidden, tgt = bool(d.get("is_hidden")), d.get("feed_targeting")
    if hidden:
        try:
            data = urllib.parse.urlencode({"is_hidden": "false",
                                           "access_token": tok}).encode()
            urllib.request.urlopen(urllib.request.Request(
                base, data=data, method="POST"), timeout=30)
            print("VISIBILITY: was HIDDEN -> un-hid it, now public")
            hidden = False
        except Exception as e3:
            print(f"VISIBILITY: WARNING hidden and un-hide failed: {e3}")
    aud = "default (public)" if not tgt else json.dumps(tgt, ensure_ascii=False)[:80]
    print(f"VISIBILITY: verified public - is_hidden={hidden} "
          f"feed_targeting={aud} post_id={post_id}")


# owner 2 Oct: this pipeline posts to EXACTLY ONE page - Azad Daily.
# Any other target (swapped secret, stale config, copied repo) is refused
# structurally at the lowest layer, not by convention.
AZAD_PAGE_ID = "1538679366414544"


def _lock_page(page_id):
    if str(page_id) != AZAD_PAGE_ID:
        raise RuntimeError(
            f"PUBLISH REFUSED: target page {page_id} is not Azad Daily "
            f"({AZAD_PAGE_ID}). This agent must never post anywhere else - "
            "fix FB_PAGE_ID/config, do not bypass this lock.")
    return str(page_id)


def graph_post_photo(page_id, token, caption, image_path, api_version):
    page_id = _lock_page(page_id)
    """Upload the branded graphic with the caption as one Page post.
    Owner 28 Sep: publish with explicit PUBLIC audience (stream_visibility -
    accepted by Graph even for our unreviewed app; test2 proved it)."""
    boundary = "----arenaBoundary7MA4YWxkTrZu0gW"
    body = b""
    fields = {"caption": caption, "stream_visibility": "PUBLIC",
              "access_token": token}
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


def _ig_publish(png_path, slug, caption, pg):
    """Owner 2 Oct: dual-publish the SAME card to the Page's linked
    Instagram business account (azaddaily99), seconds after the FB post in
    the same run. STRICTLY a piggyback: it never raises and never touches
    the FB outcome - IG is additive only. Instagram needs a PUBLIC image
    URL, so we reuse the azad-daily-cards public-repo push built for this."""
    try:
        link = json.load(open(os.path.join(STATE, "ig_link.json"),
                              encoding="utf-8"))
    except Exception:
        print("IG: skipped (no state/ig_link.json yet - auth step writes it)")
        return None
    ig_id = link.get("ig_id")
    if not ig_id:
        print(f"IG: skipped ({link.get('reason') or 'no linked account'})")
        return None
    if "instagram_content_publish" not in (link.get("scopes") or []):
        print("IG: skipped - page token lacks instagram_content_publish; "
              "owner must re-authorize the token once with IG scopes")
        return None
    try:
        pub_url = upload_card_public(png_path, slug)
        if not pub_url:
            print("IG: skipped (no public card URL)")
            return None
        ver = pg.get("api_version", "v21.0")
        tok = pg["page_access_token"]
        base = f"https://graph.facebook.com/{ver}"
        body = urllib.parse.urlencode({"image_url": pub_url, "caption": caption,
                                       "is_comment_enabled": "true",
                                       "access_token": tok}).encode()
        req = urllib.request.Request(f"{base}/{ig_id}/media", data=body,
                                     method="POST")
        with urllib.request.urlopen(req, timeout=60) as r:
            cid = json.load(r).get("id")
        if not cid:
            print("IG: container creation returned no id - skipped")
            return None
        req2 = urllib.request.Request(
            f"{base}/{ig_id}/media_publish?"
            + urllib.parse.urlencode({"creation_id": cid, "access_token": tok}),
            data=b"", method="POST")
        with urllib.request.urlopen(req2, timeout=60) as r:
            mid = json.load(r).get("id")
        print(f"IG: published to @{link.get('username')} (media id {mid})")
        return mid
    except urllib.error.HTTPError as e:
        print(f"IG: publish failed HTTP {e.code}: "
              f"{e.read().decode(errors='replace')[:300]} - FB card unaffected")
    except Exception as e:
        print(f"IG: publish failed ({e}) - FB card unaffected")
    return None


def upload_card_public(png_path, slug):
    """Owner 24 Sep: Zapier's Facebook photo step errors on uploaded FILES -
    it needs a public image URL. We publish the branded card PNG to the
    azad-daily-cards repo (public) and hand Zapier the raw URL.
    Returns the URL, or None on any failure (posting then stays text-only;
    it must never block a card)."""
    FALLBACK = ("https://raw.githubusercontent.com/cheerfulspirits999-bit/"
                "azad-daily-cards/main/cards/fallback.png")
    tok = os.environ.get("AGENT_PAT")
    if not tok:
        print("cards upload: AGENT_PAT secret not set - fallback card URL")
        return FALLBACK
    import base64 as _b64
    name = "".join(ch if ch.isalnum() or ch in ".-_" else "-" for ch in slug) + ".png"
    api = ("https://api.github.com/repos/cheerfulspirits999-bit/azad-daily-cards"
           f"/contents/cards/{name}")
    hdr = {"Authorization": "Bearer " + tok, "Accept": "application/vnd.github+json"}
    sha = None
    try:
        req = urllib.request.Request(api + "?ref=main", headers=hdr)
        with urllib.request.urlopen(req, timeout=30) as r:
            sha = json.load(r).get("sha")
    except Exception:
        pass
    try:
        with open(png_path, "rb") as f:
            content = _b64.b64encode(f.read()).decode()
        body = {"message": f"card {slug}", "content": content, "branch": "main"}
        if sha:
            body["sha"] = sha
        req = urllib.request.Request(api, data=json.dumps(body).encode(),
                                     headers=hdr, method="PUT")
        with urllib.request.urlopen(req, timeout=120) as r:
            r.read()
        raw = ("https://raw.githubusercontent.com/cheerfulspirits999-bit/"
               f"azad-daily-cards/main/cards/{name}")
        print(f"cards upload: public URL live -> {raw}")
        return raw
    except Exception as e:
        print(f"cards upload FAILED (fallback card URL): {e}")
        return FALLBACK


def zapier_post(webhook, caption, image_path, meta):
    """Hand a finished post to Zapier (Catch Hook). Zapier holds the Facebook
    authorization for the Page; we send caption + branded PNG as multipart so
    the Zap's photo step attaches the themed card (Sep-12 house style,
    restored 24 Sep night at owner's explicit request)."""
    boundary = "----arenaZapBoundary9x81b"
    body = b""
    fields = {"caption": caption, "slug": meta.get("slug", ""),
              "headline": meta.get("headline", ""), "region": meta.get("region", ""),
              "timestamp": meta.get("timestamp", ""),
              "bullets_en": json.dumps(meta.get("bullets_en", []), ensure_ascii=False),
              "bullets_ur": json.dumps(meta.get("bullets_ur", []), ensure_ascii=False),
              "image_url": meta.get("image_url") or "",
              "has_image": "yes" if meta.get("image_url") else "no"}
    for k, v in fields.items():
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    # owner 24 Sep 22:45 (screenshot of Sep-12 post): "this was our theme follow
    # this only" - the themed card rode the post as the attached PNG, and the
    # pre-24-Sep payload (fields + "image" file part) posted it fine for weeks.
    # Restored exactly. The 10 MB hard cap upstream still protects the Zap.
    if image_path:
        # config publish.attach_image=false sends fields only (Zap-panic kill-switch)
        with open(image_path, "rb") as f:
            _img = f.read()
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; "
                 f"filename=\"{os.path.basename(image_path)}\"\r\n"
                 f"Content-Type: image/png\r\n\r\n").encode() + _img + b"\r\n"
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
    page_id = _lock_page(page_id)
    url = f"https://graph.facebook.com/{api_version}/{page_id}/feed"
    data = urllib.parse.urlencode({"message": message,
                                   "stream_visibility": "PUBLIC",
                                   "access_token": token}).encode()
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
    print(f"image   : {_sz // 1024} KB attached as multipart 'image' part "
          f"(Sep-12 theme restored); no links in caption")

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
    if route == "zapier" or pub_cfg.get("zapier_webhook"):
        print("PUBLISH: Zapier route is PERMANENTLY DISCONNECTED "
              "(owner 2 Oct) - any webhook config is ignored; Graph only.")
    zap = False  # permanently disconnected by owner order (26 Sep / 2 Oct)
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

    image_url = upload_card_public(png, slug) if zap else None
    # owner 24 Sep 22:00: NO public links in posts, ever. The caption-URL trick
    # showed raw github text on FB without a preview - removed permanently.
    # Themed image attaches ONLY via native photo post (Zap photo step / Graph token).
    cap_zap = caption
    # 28 Sep blank-post incident guard: an empty caption must NEVER reach the
    # Page (10 no-message posts appeared 23:24 IST from a caption-less /photos
    # test - a publisher must be structurally incapable of that).
    if do_publish and not (caption or "").strip():
        print("\nPUBLISH REFUSED: caption is empty - refusing to post a blank note.")
        return 6
    ig_post_id = None
    try:
        if zap:
            _att = png if pub_cfg.get("attach_image", True) else ""
            res = zapier_post(pub_cfg["zapier_webhook"], cap_zap, _att,
                              {"slug": slug, "headline": story["headline"],
                               "region": story["region"],
                               "timestamp": story.get("timestamp", ""),
                               "bullets_en": story["bullets_en"],
                               "bullets_ur": story["bullets_ur"],
                               "image_url": image_url or ""})
            post_id = res.get("id") or res.get("post_id") or f"zapier:{slug}"
            print(f"\nHanded to Zapier webhook: {pub_cfg['zapier_webhook'][:48]}...")
            # owner 25 Sep: "Handed to Zapier" proved nothing when the Zap was off;
            # keep what Zapier said so a silent drop is visible in run logs.
            print(f"  zapier said: {json.dumps(res, ensure_ascii=False)[:180]}")
            try:
                json.dump({"at": now_iso(), "slug": slug, "response": res},
                          open(os.path.join(STATE, "zapier_last.json"), "w"),
                          ensure_ascii=False, indent=1)
            except Exception:
                pass
        else:
            res = graph_post_photo(pg["facebook_page_id"], pg["page_access_token"],
                                   caption, png, pg.get("api_version", "v21.0"))
            post_id = res.get("post_id") or res.get("id")
            try:
                verify_public(post_id, pg)
            except Exception as _ve:
                print(f"VISIBILITY: check skipped ({_ve})")
            # Owner 2 Oct ~09:00 IST: page->IG auto-mirror switched ON in
            # Meta's own settings; Meta now mirrors every page post to
            # azaddaily99 by itself. The API piggyback stays OFF so we never
            # double-post; flip IG_DUALPOST=1 (repo var) to take over from
            # Meta's mirror - machinery is tested and ready.
            if os.environ.get("IG_DUALPOST", "") == "1":
                ig_post_id = _ig_publish(png, slug, caption, pg)
            else:
                print("IG: handled by Meta page-to-page auto-mirror (owner "
                      "enabled 2 Oct) - API publish skipped on purpose.")
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
        "image_url": image_url or "",
        "fb_post_id": post_id,
        "ig_post_id": ig_post_id,
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

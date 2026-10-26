#!/usr/bin/env python3
"""News photo for the digest card - owner 6 Oct.

The owner's other bots post cards carrying a photo; Azad Daily's digest cards
were text-only. This module resolves ONE photo per card, in priority order:

  1. the news story's own image, carried out of the RSS feed
     (<media:content> / <enclosure> / <media:thumbnail> / an <img> inside the
     description). This is the correct image because it IS the story.
  2. Pixabay free stock - ONLY when the operator sets CARD_PIXABAY=1 AND a
     PIXABAY_API_KEY exists. Off by default on owner instruction (6 Oct): a
     random stock photo on a news card misleads the reader about what the
     picture shows, so a card with no photo beats a card with the wrong one.

GOVERNING RULE, same as the logo: nothing here may break a card. Every failure
path returns None, and the renderer then draws exactly the card it drew before
- no empty frame, no placeholder, no invented artwork. A card without a photo is
always better than a card with a broken photo.

No network call here may stall the hourly cycle: every fetch has a short
timeout and every exception is swallowed.
"""
import hashlib
import io
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(BASE, "state", "photos")

TIMEOUT = 12                 # seconds - a hung image host must not eat a cycle
MAX_BYTES = 6 * 1024 * 1024  # a 6 MB download ceiling; FB caps at 10 MB anyway
MIN_SIDE = 260               # ignore logos/icons/tracking pixels masquerading as art
UA = ("Mozilla/5.0 (compatible; AzadDailyCardBot/1.0; "
      "+https://www.facebook.com/AzadDaily)")

# Words that make a useless stock-photo query. Kept short on purpose - this is
# a search hint, not a parser.
_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
    "at", "by", "from", "as", "is", "are", "was", "were", "be", "been", "after",
    "over", "under", "new", "says", "say", "said", "will", "has", "have", "had",
    "his", "her", "their", "its", "it", "he", "she", "they", "we", "you", "i",
    "that", "this", "these", "those", "not", "no", "but", "if", "than", "into",
    "amid", "during", "before", "up", "out", "off", "about", "against", "between",
    "telangana", "hyderabad", "india", "indian",
}


def _log(msg):
    print(f"[cardphoto] {msg}", file=sys_stderr())


def sys_stderr():
    import sys
    return sys.stderr


def _get(url):
    """Fetch bytes. Returns None on ANY failure - never raises, never hangs."""
    if not url or not str(url).lower().startswith(("http://", "https://")):
        return None
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": UA, "Accept": "image/*,*/*;q=0.5"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = r.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            return None
        return data or None
    except Exception:
        return None


def _open(data):
    """Bytes -> PIL image, or None. Rejects non-images and tiny/degenerate art."""
    if not data:
        return None
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        img.load()
        if min(img.size) < MIN_SIDE:
            return None
        return img.convert("RGB")
    except Exception:
        return None


def _crop_cover(img, w, h):
    """Center-crop to the w:h aspect, then resize. No distortion, ever."""
    from PIL import Image
    tw, th = w, h
    sw, sh = img.size
    target = tw / th
    source = sw / sh
    if source > target:                       # too wide -> crop the sides
        nw = max(1, int(round(sh * target)))
        x = (sw - nw) // 2
        img = img.crop((x, 0, x + nw, sh))
    else:                                     # too tall -> crop top/bottom
        nh = max(1, int(round(sw / target)))
        y = (sh - nh) // 2
        img = img.crop((0, y, sw, y + nh))
    return img.resize((tw, th), Image.LANCZOS)


def _pixabay_on():
    """Stock art is OPT-IN (owner 6 Oct).

    "i ask you to post the news with pic it had photo related to the new not
    random imade added to all the cards" - a stock scenery photo on a news card
    is worse than no photo, because it implies the picture is of the story. It
    only ever runs when the operator explicitly sets CARD_PIXABAY=1.
    """
    return os.environ.get("CARD_PIXABAY", "").strip() == "1"


def _cache_key(*parts):
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


# ---------------------------------------------------------------- feed image
def story_image(story):
    """The image the feed published with this story, if any."""
    for key in ("image_url", "image", "img", "photo"):
        v = story.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    # a digest carries several source stories - take the first that has art
    for u in (story.get("source_urls") or []):
        pass
    return None


# ------------------------------------------------------------------- pixabay
def _query(story):
    """A short, topical stock-photo query from the story's own words."""
    text = (story.get("title") or story.get("headline") or "")
    text = re.sub(r"^Daily digest:.*$", "", text)          # digest titles are lists
    words = re.findall(r"[A-Za-z][A-Za-z'-]{2,}", text)
    keep = [w for w in words if w.lower() not in _STOP]
    if not keep:
        keep = words
    q = " ".join(keep[:4]).strip()
    return q or "news"


def pixabay_image(story, key):
    """Search Pixabay free stock. Returns a URL or None."""
    if not key:
        return None
    q = _query(story)
    url = ("https://pixabay.com/api/?" + urllib.parse.urlencode({
        "key": key, "q": q, "image_type": "photo", "orientation": "horizontal",
        "safesearch": "true", "editors_choice": "false", "per_page": "6",
        "min_width": "800"}))
    data = _get(url)
    if not data:
        return None
    try:
        hits = json.loads(data.decode("utf-8", "replace")).get("hits") or []
    except Exception:
        return None
    for h in hits:
        for k in ("largeImageURL", "webformatURL", "previewURL"):
            u = h.get(k)
            if isinstance(u, str) and u.lower().startswith("http"):
                return u
    return None


# --------------------------------------------------------------------- entry
def prepare(story, w, h, use_pixabay=True):
    """Return a PIL image sized exactly w x h, or None.

    Priority: the story's own feed image, then Pixabay. Cached on disk by
    (story slug, source url) so a re-render never re-downloads.
    """
    slug = str(story.get("slug") or "card")
    cand = story_image(story)
    if not cand and use_pixabay and _pixabay_on():
        cand = pixabay_image(story, os.environ.get("PIXABAY_API_KEY", ""))
        if cand:
            _log(f"pixabay art for {slug}: {_query(story)!r}")
    if not cand:
        return None

    key = _cache_key(slug, cand, w, h)
    try:
        os.makedirs(CACHE, exist_ok=True)
        cached = os.path.join(CACHE, key + ".jpg")
        if os.path.exists(cached):
            img = _open(open(cached, "rb").read())
            if img is not None:
                return img
    except Exception:
        pass

    img = _open(_get(cand))
    if img is None:
        _log(f"unusable feed art for {slug} (download failed or too small) - "
             f"card renders without a photo")
        return None
    img = _crop_cover(img, w, h)
    try:
        img.save(os.path.join(CACHE, key + ".jpg"), "JPEG", quality=90,
                 optimize=True)
    except Exception:
        pass
    return img

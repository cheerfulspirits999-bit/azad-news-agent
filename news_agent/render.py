#!/usr/bin/env python3
"""
Branded breaking-news post graphic.

Fixed contract (never negotiable):
    * EXACTLY 3 English bullets and EXACTLY 3 Roman Urdu bullets (same 3 facts)
    * the page logo rendered at full opacity, uncropped, undistorted, recolour-free
    * every bullet stays inside the safe area and readable on a phone
    * the footer never collides with content (two-pass measure-then-draw)

If the logo file is missing a clearly-labelled empty frame is drawn instead of
inventing, redrawing or recolouring a logo.

Usage:
    python3 render.py story.json -o ../out/post.png
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime

from PIL import Image, ImageDraw, ImageFont

BASE = os.path.dirname(os.path.abspath(__file__))
CFG = json.load(open(os.path.join(BASE, "config.json"), encoding="utf-8"))
B = CFG["branding"]

GOLD = B["accent_gold"]
GOLD_BRIGHT = B["accent_gold_bright"]
BLACK = B["background_black"]
PANEL = B["panel_black"]
RED = "#C8102E"
WHITE = "#FFFFFF"
GREY = "#B4B4B4"

FONT_DIR = "/usr/share/fonts/truetype/dejavu"
F_BOLD = os.path.join(FONT_DIR, "DejaVuSans-Bold.ttf")
F_REG = os.path.join(FONT_DIR, "DejaVuSans.ttf")

PAD = 52
BULLET_SIZES = (46, 44, 42, 40, 38, 36)   # largest-first, floor 36px (owner readability spec)


def font(size, bold=True):
    return ImageFont.truetype(F_BOLD if bold else F_REG, size)


def rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def wrap(d, text, fnt, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        trial = (cur + " " + w).strip()
        if not cur or d.textlength(trial, font=fnt) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


MIN_B, MAX_B = 3, 5


def enforce_three(bullets, label):
    """3-5 bullets (owner rule: up to 5 when the story needs it)."""
    b = [x.strip() for x in (bullets or []) if x and x.strip()]
    if len(b) < MIN_B:
        raise ValueError(f"{label}: needs at least {MIN_B} bullets, got {len(b)}")
    if len(b) > MAX_B:
        print(f"[render] WARNING {label}: {len(b)} bullets -> truncated to {MAX_B}",
              file=sys.stderr)
        b = b[:MAX_B]
    return b


def jaccard(a, b):
    import re as _re
    na = " ".join(_re.sub(r"[^a-z0-9\s]", " ", (a or "").lower()).split())
    nb = " ".join(_re.sub(r"[^a-z0-9\s]", " ", (b or "").lower()).split())
    sa, sb = set(na.split()), set(nb.split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def strip_emoji(text):
    """PNG headline uses a drawn alert badge, not an emoji glyph (DejaVu has no
    emoji and Facebook renders the emoji in the caption text anyway)."""
    return re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]", "", text).strip()


def load_logo(target_h):
    path = B["logo_path"]
    if not os.path.isabs(path):
        path = os.path.normpath(os.path.join(BASE, "..", path))
    path = os.path.normpath(path)
    if not os.path.exists(path):
        return None, path, None
    img = Image.open(path).convert("RGBA")
    orig_bg = img.getpixel((0, 0))[:3]
    bbox = img.getbbox()
    if bbox:
        img = img.crop(bbox)  # transparent margin only, never artwork
    # opaque black-field logos (like this page's): trim only the EMPTY black
    # padding around the artwork so the mark renders at full presence.
    # Artwork pixels are never touched, recoloured or cropped.
    grey = img.convert("L")
    bright = grey.point(lambda v: 255 if v > 24 else 0)
    abox = bright.getbbox()
    if abox and (abox[0] > 2 or abox[1] > 2 or
                 abox[2] < img.width - 2 or abox[3] < img.height - 2):
        img = img.crop(abox)
    ratio = target_h / img.height
    out_img = img.resize((max(1, round(img.width * ratio)), target_h), Image.LANCZOS)
    return out_img, path, orig_bg


COMPACT = {"on": False}


def measure_blocks(d, items, f_size, content_w):
    """Height of one bullet block at font size f_size, plus its wrapped lines."""
    f = font(f_size)
    rows, h = [], 0
    for b in items:
        lines = wrap(d, b, f, content_w - 52)
        rows.append(lines)
        if COMPACT["on"]:
            h += len(lines) * int(f_size * 1.20) + 8
        else:
            h += len(lines) * int(f_size * 1.28) + 18
    return h, rows


def render(story, out_path):
    bullets_en = enforce_three(story["bullets_en"], "ENGLISH")
    bullets_ur = enforce_three(story["bullets_ur"], "ROMAN URDU")
    COMPACT["on"] = len(bullets_en) >= 5  # tight leading keeps 36px+ on 5-card
    headline_png = strip_emoji(story["headline"].strip())
    source = (story.get("source") or "").strip()
    stamp = story.get("timestamp") or datetime.now().strftime("%d %b %Y, %I:%M %p") + " IST"

    W = B["image_width"]
    H_base = B["image_height"]
    content_w = W - 2 * PAD

    probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))

    # ---------------- vertical budget -------------------------------------
    logo_h = 112
    fixed = {
        "top_gold": 10,
        "top_gap": 34,
        "logo": logo_h + 20,
        "rule": 24,
        "band": 86 + 18,
        "stamp": 44,
        "labels": 2 * 58,
        "pre_footer": 26,
        "footer": 84,
    }
    fixed_h = sum(fixed.values())

    # pick the largest bullet size that fits; grow the canvas if nothing fits
    chosen, rows_en, rows_ur, H = None, None, None, H_base
    for f_size in BULLET_SIZES:
        h_en, r_en = measure_blocks(probe, bullets_en, f_size, content_w)
        h_ur, r_ur = measure_blocks(probe, bullets_ur, f_size, content_w)
        if fixed_h + h_en + h_ur <= H_base:
            chosen, rows_en, rows_ur, H = f_size, r_en, r_ur, H_base
            break
    if chosen is None:
        h_en, r_en = measure_blocks(probe, bullets_en, BULLET_SIZES[-1], content_w)
        h_ur, r_ur = measure_blocks(probe, bullets_ur, BULLET_SIZES[-1], content_w)
        chosen = BULLET_SIZES[-1]
        rows_en, rows_ur = r_en, r_ur
        H = fixed_h + h_en + h_ur + 20
        print(f"[render] content tall -> canvas stretched to {W}x{H}", file=sys.stderr)

    canvas = Image.new("RGBA", (W, H), rgb(BLACK) + (255,))
    grad = Image.new("L", (1, H))
    for yy in range(H):
        grad.putpixel((0, yy), int(255 - 110 * (yy / H)))
    shade = Image.new("RGBA", (W, H), rgb(PANEL) + (255,))
    canvas = Image.composite(shade, canvas, grad.resize((W, H)))
    d = ImageDraw.Draw(canvas)
    d.rectangle([0, 0, W, fixed["top_gold"]], fill=rgb(GOLD))

    rule_y = fixed["top_gold"] + fixed["top_gap"] + fixed["logo"]
    d.rectangle([0, fixed["top_gold"], W, rule_y], fill=(0, 0, 0))  # header = pure black

    y = fixed["top_gold"] + fixed["top_gap"]

    # ---------------- header: logo + LIVE ---------------------------------
    logo, logo_path, logo_bg = load_logo(logo_h)
    if logo:
        # cap width so a very wide logo can never push the LIVE badge off-canvas
        if logo.width > W * 0.62:
            r2 = (W * 0.62) / logo.width
            logo = logo.resize((round(W * 0.62), max(1, round(logo.height * r2))), Image.LANCZOS)
        # fill behind with the logo's own background colour so an opaque
        # black-field logo shows no tile edge (artwork untouched)
        bg = logo_bg or (0, 0, 0)
        d.rectangle([PAD - 2, y + 2, PAD + logo.width + 2, y + 6 + logo.height], fill=bg)
        canvas.alpha_composite(logo, (PAD, y + 4))
        logo_found = True
    else:
        logo_found = False
        d.rounded_rectangle([PAD, y, PAD + 250, y + logo_h], radius=12,
                            outline=rgb(GOLD), width=4)
        f_ph = font(20)
        t1, t2 = "LOGO GOES HERE", "news_agent/logo/logo.png"
        d.text((PAD + 125 - d.textlength(t1, font=f_ph) / 2, y + 30), t1,
               font=f_ph, fill=rgb(GOLD_BRIGHT))
        f_ph2 = font(16, False)
        d.text((PAD + 125 - d.textlength(t2, font=f_ph2) / 2, y + 62), t2,
               font=f_ph2, fill=rgb(GREY))
        print(f"[render] LOGO NOT FOUND at {logo_path} -> empty frame drawn; "
              f"no logo invented or redrawn.", file=sys.stderr)

    # (red LIVE badge removed on owner's instruction - header keeps only the logo)

    y += fixed["logo"]
    d.line([PAD, y, W - PAD, y], fill=rgb(GOLD), width=3)
    y += fixed["rule"]

    # ---------------- headline band with drawn alert badge ----------------
    f_head = font(56)
    avail = W - 2 * PAD - 96
    while d.textlength(headline_png, font=f_head) > avail and f_head.size > 30:
        f_head = font(f_head.size - 2)
    d.rounded_rectangle([PAD - 14, y - 8, W - PAD + 14, y - 8 + fixed["band"] - 30],
                        radius=12, fill=rgb(RED))
    # drawn alert triangle (vector, no emoji glyph needed)
    ty = y - 8
    tri = [(PAD + 12, ty + 62), (PAD + 34, ty + 20), (PAD + 56, ty + 62)]
    d.polygon(tri, fill=(255, 255, 255))
    d.line([(PAD + 34, ty + 30), (PAD + 34, ty + 48)], fill=rgb(RED), width=6)
    d.ellipse([PAD + 31, ty + 52, PAD + 37, ty + 58], fill=rgb(RED))
    d.text((PAD + 74, ty + (fixed["band"] - 30 - f_head.size) / 2 - 6), headline_png,
           font=f_head, fill=(255, 255, 255))
    y += fixed["band"]

    f_ts = font(28, False)
    d.text((PAD, y), stamp.upper(), font=f_ts, fill=rgb(GOLD_BRIGHT))
    y += fixed["stamp"]

    # ---------------- bullet blocks ---------------------------------------
    def block(label, rows, y):
        f_lab = font(38)
        d.rectangle([PAD, y + 5, PAD + 10, y + 38], fill=rgb(GOLD))
        d.text((PAD + 24, y), label, font=f_lab, fill=rgb(GOLD_BRIGHT))
        y += 58
        f = font(chosen)
        lead = int(chosen * 1.28)
        for lines in rows:
            d.ellipse([PAD + 4, y + 16, PAD + 20, y + 32], fill=rgb(GOLD))
            ly = y
            for ln in lines:
                d.text((PAD + 46, ly), ln, font=f, fill=rgb(WHITE))
                ly += lead
            y = ly + 18
        return y

    y = block("ENGLISH", rows_en, y)
    y = block("ROMAN URDU", rows_ur, y)

    # ---------------- footer ----------------------------------------------
    foot_top = H - fixed["footer"]
    d.rectangle([0, foot_top - 22, W, foot_top - 18], fill=rgb(GOLD))
    f_foot = font(27, False)
    ft = B["default_footer"]
    if d.textlength(ft, font=f_foot) > W - 2 * PAD:
        ft = "Hyderabad • Telangana • India • World"
    if ft:
        d.text(((W - d.textlength(ft, font=f_foot)) / 2, foot_top + 2), ft,
               font=f_foot, fill=rgb(GREY))

    assert y <= foot_top - 18, f"content overran footer: {y} > {foot_top}"

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    canvas.convert("RGB").save(out_path, "PNG", optimize=True)
    return out_path, {"logo_found": logo_found, "logo_path": logo_path,
                      "size": [W, H], "bullet_font_px": chosen,
                      "content_bottom": y, "footer_top": foot_top}


def caption_from(story):
    """The Facebook caption text: 3 EN bullets, 3 Roman Urdu bullets, source line."""
    lines = [story["headline"].strip(), "", "ENGLISH", ""]
    lines += [f"* {b}" for b in enforce_three(story["bullets_en"], "ENGLISH")]
    lines += ["", "ROMAN URDU", ""]
    lines += [f"* {b}" for b in enforce_three(story["bullets_ur"], "ROMAN URDU")]
    # house rule: no source names on the public post - bullets + logo only
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("story")
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--caption", action="store_true",
                    help="also write the FB caption next to the PNG")
    a = ap.parse_args()
    story = json.load(open(a.story, encoding="utf-8"))
    out = a.out or os.path.join(BASE, "..", "out", f"{story.get('slug', 'post')}.png")
    path, meta = render(story, out)
    if a.caption:
        cap_path = os.path.splitext(out)[0] + "-caption.txt"
        with open(cap_path, "w", encoding="utf-8") as f:
            f.write(caption_from(story))
        meta["caption"] = cap_path
    print(json.dumps({"rendered": path, **meta}, indent=2))


if __name__ == "__main__":
    main()

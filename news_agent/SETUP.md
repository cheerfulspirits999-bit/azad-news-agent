# Breaking-News Facebook Agent — setup & operation

## What runs in one cycle (every 1–2 hours)

```
python3 news_agent/monitor.py --hours 30 --limit 12     # 1. fetch preferred sources, rank
      ... editorial pass: verify, classify A–F, de-duplicate, write story JSON
python3 news_agent/publish.py news_agent/stories/<slug>.json            # 2. dry run: graphic + caption
python3 news_agent/publish.py news_agent/stories/<slug>.json --publish  # 3. POST to the Page + log it
```

* `monitor.py` — pulls The Hindu (Hyderabad / Telangana / National / World),
  Munsif Daily + Siasat, Telangana Today, Newsmeter. Scores each item by
  region (Hyderabad 40 > Telangana 34 > India 20 > World 12) + category
  weight + freshness + preferred-source bonus. Entertainment/lifestyle items
  are suppressed unless they report a real incident. Duplicates and
  near-duplicates (title Jaccard ≥ 0.72 vs the publish log, or same URL, or
  same topic) are refused automatically. Output: `news_agent/state/candidates.json`.
* Story JSON (`news_agent/stories/*.json`) — the editorial layer: exactly
  `bullets_en[3]` + `bullets_ur[3]`, `verified_by`, `not_verified` (facts we
  will NOT state), `source`, `classification` (only A/B/C can publish).
* `publish.py` — validates 3+3 bullets, re-checks duplicates, renders
  `out/<slug>.png` (logo + headline band + 3 EN + 3 Roman Urdu bullets +
  source line), writes `out/<slug>-caption.txt`, then posts via Graph API and
  appends to `news_agent/state/published.json` (headline, URL, time, topic,
  key facts, bullets, post id) so the story can never be posted twice.
  An update to an old story needs `"substantial_new_development": true` plus a
  `new_facts` note, otherwise it is refused.

## Connecting the Facebook Page (required for automatic posting)

A share URL (`facebook.com/share/...`) cannot be posted to programmatically.
Meta requires a **Page access token**. Two supported routes:

**Route A — Meta Graph API (fully automatic)**
1. developers.facebook.com → Create App → type **Business**.
2. Add product **Facebook Login for Business** / **Pages API**; in App Review
   request: `pages_manage_posts`, `pages_read_engagement`, `pages_show_list`.
3. As a Page admin, generate a **Page access token** (Graph API Explorer:
   `GET /me/accounts` → copy the target Page's `access_token`; for production
   exchange for a long-lived token).
4. Put the Page `id` and the token in `news_agent/config.json` →
   `page.facebook_page_id` / `page.page_access_token`.
   (Keep the token out of git; the file is workspace-local.)
5. `publish.py --publish` then posts photo + caption in one call.

**Route B — Metricool (scheduled/managed)**
Upload each `out/<slug>.png` + paste `out/<slug>-caption.txt` into Metricool's
Facebook planner; Metricool holds the Page authorization and gives analytics.
Use this if you prefer not to hold a Meta token here.

Until one of these is configured, `publish.py --publish` stops safely and
leaves a ready-to-post package in `out/` plus `state/pending_publish.json`.

## Logo

Drop the gold-on-black logo at `news_agent/logo/logo.png` (PNG with
transparency preferred). The renderer never crops, distorts or recolours it:
it only trims fully-transparent margins and scales to the header band.
Until the file exists, an empty labelled frame is drawn — no substitute logo
is ever invented.

## Autonomous operation

`python3 news_agent/autod.py` is the resident autonomous agent (90-minute
cycles, max 2 posts/cycle, pending-retry on credential arrival, ALERT.md +
exit on auth/technical failure). Keep it under systemd/PM2/screen for
machine-level resilience; inside the Arena sandbox it runs as a live process.

## Continuous operation (external scheduler alternative)

The sandbox keeps no resident scheduler, so run the cycle from an external
runner every 90 minutes, e.g.:

* cron: `10 */2 * * *  cd /path/to/repo && python3 news_agent/monitor.py --hours 30 --limit 12 >> logs/monitor.log 2>&1`
  (the editorial pass + `publish.py` run where the AI agent / operator sits)
* or GitHub Actions `schedule: cron: '10 */2 * * *'`
* or `while true; do python3 news_agent/monitor.py ...; sleep 5400; done` under systemd/PM2.

## Guardrails enforced in code

* exactly 3 bullets per language (render + publish refuse anything else)
* no publish without `verified_by`; class D/E/F never reach the Page
* duplicate + same-topic updates refused without a substantial new development
* no copyrighted news photograph: graphic posts carry only the branded layout;
  a real photo may be composited later only with documented reuse rights
* neutral wording checks live in the story JSON's `not_verified` field

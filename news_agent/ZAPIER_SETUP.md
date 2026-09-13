# Zapier publishing bridge — setup (≈6 minutes)

Zapier holds the Facebook authorization for **Azad Daily**; the news daemon
hands every finished post (caption text + branded PNG with your logo) to a
Zapier webhook. No Meta app, no Page token needed on our side.

```
autod.py (every 90 min)
   monitor → verify → de-dup → write 3+3 bullets → render logo graphic
        │  publish.py --publish
        ▼
Zapier Catch Hook  ──▶  Facebook Pages: Create Page Post  ──▶  Azad Daily
   (caption + PNG file)      (Message + Image)
```

## Build the Zap

1. **zapier.com → Create → Zaps → New Zap.**
2. **Trigger:** app = `Webhooks by Zapier`, event = `Catch Hook` → Continue.
   Copy the **Custom Webhook URL** (`https://hooks.zapier.com/hooks/catch/…`).
   *Don't test yet* — paste the URL to the agent first; it will fire a real
   sample payload so all fields (caption, image, headline…) appear in the
   editor with real data.
3. **Action:** app = `Facebook Pages`, event = `Create Page Post`.
   Connect the Facebook account that is admin/editor of **Azad Daily**, then
   select Page = **Azad Daily**.
4. **Map the fields:**
   * `Message` ← trigger field `caption`
   * `Image`  ← trigger field `image` (the PNG file we upload)
   * Leave link/preview settings at default; do NOT add extra text.
   * If your Zapier plan cannot catch files: map `Image` from a URL step
     instead — set `publish.public_image_base` in `news_agent/config.json` to
     your sandbox preview base (shown in the Arena preview panel for port
     8090 after running `python3 -m http.server 8090 --directory out --bind
     0.0.0.0`), and the daemon will include `image_url` in every payload.
     Facebook copies the image at post time, so the URL only needs to live
     until the post goes out.
5. **Test the action** (Zapier will post once to Azad Daily with the sample)
   → delete that test post on the Page if you don't want it → **Turn Zap ON**.
6. Paste the webhook URL to the agent (or put it in
   `news_agent/config.json → publish.zapier_webhook`).

## What happens after that

* `publish.py` sees route=zapier + webhook → every approved story is POSTed to
  the hook as multipart (fields: caption, slug, headline, region, timestamp,
  bullets_en, bullets_ur + file `image`).
* The daemon's pending queue (cab boycott → ganja arrest → detentions)
  publishes automatically on the next cycle, max 2 posts/cycle.
* Failures: Zapier emails you on Zap errors; the daemon additionally writes
  `state/ALERT.md` and stops if the webhook returns HTTP 4xx/5xx repeatedly —
  it never silently drops or blindly retries a post.
* Duplicate protection, 3+3 bullet enforcement and logo branding all stay on
  our side; Zapier is a pure delivery pipe.

## Payload reference (what the trigger receives)

| Field | Type | Use |
|---|---|---|
| `caption` | text | Facebook post message (headline + 3 EN + 3 UR bullets) |
| `image` | file (PNG 1200×1350) | post attachment |
| `headline`, `region`, `timestamp` | text | optional logging / Sheets |
| `bullets_en`, `bullets_ur` | JSON arrays | optional archive step |
| `slug` | text | dedupe key for a Google Sheets log step (recommended) |

Optional hardening: add a **Google Sheets "Create Row"** step after the FB
action with slug/timestamp/post link — gives you a publish audit trail that
mirrors `news_agent/state/published.json`.

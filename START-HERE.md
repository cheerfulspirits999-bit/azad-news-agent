# 📰 AZAD DAILY — YOUR AUTONOMOUS NEWS AGENT (owner's copy)

This folder **is** the agent. It monitors news, verifies it, writes bilingual
posts, brands them with your gold-and-black logo, and publishes to your
Facebook Page **Azad Daily** through your Zapier Zap — every 90 minutes,
forever, without you.

## What's inside

| Path | What it does |
|---|---|
| `news_agent/autod.py` | the autonomous loop (monitor → verify → write → render → publish → record). `--once` = single cycle |
| `news_agent/monitor.py` | pulls The Hindu, Munsif/Siasat, Telangana Today, Newsmeter; scores Hyderabad > Telangana > India > World; kills duplicates & entertainment fluff |
| `news_agent/writer.py` | writes exactly 3 English + 3 Roman Urdu bullets from verified facts only; refuses stories it cannot frame safely |
| `news_agent/render.py` | draws the 1200×1350 card: your logo (never cropped/recoloured), headline band, 3+3 bullets, footer |
| `news_agent/publish.py` | pre-publish gates (3+3 bullets, verification, duplicate kill-switch) → hands caption+PNG to Zapier |
| `news_agent/config.json` | **your settings**: Zapier webhook (already filled), feeds, scoring, branding, live switch |
| `news_agent/logo/logo.png` | your gold R logo — replace this file to rebrand every future post |
| `news_agent/state/` | the agent's memory: `published.json` (never repost), `pending_publish.json` (queue), `watchlist.json` (held stories), `candidates.json`, `ALERT.md` (only if it needs you) |
| `news_agent/stories/` | every post's full record (facts, sources, bullets) |
| `out/` | the rendered cards + caption texts |
| `news_agent/deploy/` | systemd unit + GitHub Actions workflow for 24/7 uptime |
| `news_agent/SETUP.md`, `ZAPIER_SETUP.md` | full manuals |

## Run it (3 commands)

```bash
pip install Pillow          # once
cd <this folder>
python3 news_agent/autod.py # runs forever, cycle every 90 minutes
```

You'll see each cycle's decisions printed live. Press Ctrl+C to stop.

## Keep it running 24/7 (pick one)

* **Linux VPS / home server (recommended):**
  `sudo cp -r <this folder> /opt/azad-agent`
  `sudo cp /opt/azad-agent/news_agent/deploy/azad-news.service /etc/systemd/system/`
  `sudo systemctl enable --now azad-news`
* **GitHub (no server):** create a repo with this folder, copy
  `news_agent/deploy/github-actions-cycle.yml` to `.github/workflows/news-cycle.yml`.
  Every 2 hours GitHub runs one cycle and commits the memory back.
* **Any machine (Windows/Mac):** leave a terminal open with the command above,
  or use Task Scheduler / `launchd` to run `python3 news_agent/autod.py --once`
  every 90 minutes.

## ⚠️ ONE rule: run only ONE copy at a time

The agent's "already posted" memory lives in `news_agent/state/published.json`
on the machine that runs it. Two machines running simultaneously would each
think a story is new and post it twice. If you start it on your server, stop
any other copy first.

## Your safety switches (all in `news_agent/config.json`)

* `publish.zapier_live` : `true` = publishing on. Set `false` to pause posts
  while keeping monitoring.
* `publish.zapier_webhook` : your Zap address. Replace if you ever rebuild the Zap.
* `rules.max_post_age_hours`, `rules.min_score_to_publish`,
  `rules.duplicate_similarity_threshold` : strictness dials.
* `feeds` : add/remove sources (RSS URLs only).

## What the agent will NEVER do

publish rumours or single-source death/crime claims • post more than 3 bullets
per language • repost the same story without a substantial new development •
crop, distort or recolour your logo • use copyrighted news photographs •
flood the Page (max 2 posts per cycle) • retry blindly after a Facebook/Zapier
failure (it writes `state/ALERT.md` and stops, waiting for you).

## Current queue at handover

Waiting to publish on the first cycle of your run: Kukatpally ganja arrest +
1 kg seizure (verified). Held as unverified: Ameerpet RTC-bus death (single
non-preferred source) — it publishes only when a second reliable outlet
confirms it. Already on the Page: airport cab boycott + Assembly detentions
(recorded in `state/published.json`, so they can never duplicate).

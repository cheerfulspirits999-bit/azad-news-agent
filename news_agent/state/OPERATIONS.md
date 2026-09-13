
## 12 Sep 2026, ~9:15 PM IST — ownership decision
Owner decided the agent KEEPS RUNNING IN THE ARENA SANDBOX (agent-managed).
The zip handover is a BACKUP ONLY — do not run a second copy elsewhere while
the sandbox instance is live (single-instance rule: one published.json memory).
Continuity model: daemon runs as a live process; on every conversation turn
the agent verifies the daemon is up (restarts if the sandbox recycled it) and
reviews the cycle log + watchlist. Publishing continues via the Zapier Zap.


## 13 Sep — 12-hour gap incident + tab-close reality
- Daemon died again overnight (sandbox recycles background processes when the
  tab/session goes inactive). Gap 01:12 -> 13:03 IST. Revived 13:03.
- Auto-post #9 (IMD alert) carried template filler "The incident came to light
  on Sunday" -> retracted (see state/retracted.json), writer patched
  (day/place extensions skipped for non-incident categories), corrected
  editorial post published (01a099b3-5b65...). User must delete the defective
  FB post 01a099af-2f3b... manually.
- Ameerpet RTC-bus death cleared from watchlist (4-source corroboration) and
  published (01a099b3-5e3f...). Driver-arrest claim excluded (single source).
- HONEST LIMIT: this sandbox CANNOT run with the tab closed. Permanent 24/7
  requires deploying the refreshed azad-daily-news-agent.zip (GitHub Actions
  cron / always-on PC / VPS). Decision pending with owner.

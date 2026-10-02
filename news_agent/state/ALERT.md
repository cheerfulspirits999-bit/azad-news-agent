# AGENT STOPPED - ACTION NEEDED

**When:** 2026-10-03T01:59:00.366518+05:30

**Reason:** Roman-Urdu translator has no working free lane

The hourly digest cannot translate, so it publishes 0. Verified causes (see news_agent/state/free_lane_check.json, refreshed daily): OpenRouter's free tier is capped at 50 requests/day and today's are used (HTTP 429 until 00:00 UTC); the OpenAI key is valid but has $0 credits. FREE fix, ~1 minute, no card: create a key at aistudio.google.com/apikey (or console.groq.com/keys) and store it as the repo secret GEMINI_API_KEY (or GROQ_API_KEY) - the code already tries that lane first. The free OpenRouter quota also returns at 00:00 UTC (05:30 IST) with 50 calls/day. Editorial inbox cards still publish immediately - they bypass the translator entirely.

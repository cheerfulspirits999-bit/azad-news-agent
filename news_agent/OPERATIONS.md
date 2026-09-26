
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

## 13 Sep ~14:00 — card readability fix (owner request: "font very small")
- Canvas 1200x1350 -> 1200x1500 (exact FB 4:5, no feed crop); PAD 64->52;
  header/band/footer budgets trimmed; leading 1.34->1.28; bullet gap 24->18;
  BULLET_SIZES now (46..30); labels/footer/stamp fonts up.
- EDITORIAL RULE: keep bullets <=95 chars and prefer 4 per language so cards
  render at 34-44px; 5x5 long-bullet cards force 30-32px. Never drop facts to
  gain space - trim wording instead.
- Today's IMD + Ameerpet posts re-published with 36px cards (01a09a20-f9eb...,
  01a09a20-fc4f...). Older small-text versions retracted (state/retracted.json).

## 13 Sep ~16:15 IST — GITHUB DEPLOYMENT LIVE (owner: cheerfulspirits999-bit)
- Repo https://github.com/cheerfulspirits999-bit/azad-news-agent (PRIVATE),
  workflow news-cycle: cron :10 UTC (= :40 IST) hourly + manual button.
- First run 16:07 IST = success; state committed back each cycle.
- Sandbox daemon PERMANENTLY OFF — GitHub is the single running copy.
- Editorial inbox added: drop stories/inbox-<slug>.json into the repo
  (GitHub web: Add file) -> next cycle publishes it with full dup-check;
  file auto-removed afterwards. rc5 (FB error) keeps file for retry.
- Owner token to be REVOKED after deployment; never stored in workspace.

## 13 Sep ~16:30 IST — OWNER MONITORING PERIOD (2 days), then session closes
RETURNING-OWNER / RETURNING-AGENT BRIEF:
- SINGLE RUNNING COPY = GitHub repo cheerfulspirits999-bit/azad-news-agent
  (private). Hourly cron :10 UTC (:40 IST) + manual Run workflow button.
  Heartbeat check = Actions tab -> news-cycle -> one green run per hour,
  regardless of whether news posted. Silence on the Page with green runs
  = correct behaviour (no qualifying verified story).
- SANDBOX DAEMON MUST STAY OFF. Never restart autod.py loops in the sandbox
  while GitHub runs (double-post risk). Sandbox is for: code edits, test
  renders (publish.py dry run), editorial verification, zip backups.
- Owner token was REVOKED after deployment. For future pushes: owner pastes a
  fresh temporary classic PAT (scopes: repo + workflow), agent uses it
  in-memory only, never writes it to files, owner revokes after.
  Token-less alternative: owner pastes file content via GitHub web Add file.
- Editorial posts flow: agent verifies via web_search, writes story JSON,
  owner adds it in repo as news_agent/stories/inbox-<slug>.json (Add file);
  next cycle publishes with dup-check and deletes the file.
- Pending owner housekeeping from 13 Sep: delete 3 FB posts (1:03 PM flawed
  rain alert; 1:05 PM small-text rain alert; 1:05 PM small-text Ameerpet).
- Card design since 13 Sep: 1200x1500 (4:5), bullets 30-46px auto-fit,
  editorial bullets <=95 chars, prefer 4 per language.
- Strictness rules unchanged: 3-5 bullets/language equal counts, no source
  names, no hashtags, LATEST NEWS headline style, logo untouched, verify
  casualties/crime with >=2 sources, hedge developing stories, <=2 posts/cycle.
- Backups: /home/user/azad-daily-news-agent.zip (refreshed 13 Sep 16:20),
  DEPLOY-GITHUB.md, START-HERE.md. Publish log: state/published.json +
  state/retracted.json (audit trail).

## 13 Sep night — mix fix + reliability stack + Zapier pause incident
- Politics fix: category weights lifted (political 26/court 24/economic 24/
  govt 24/protest 22; crime trimmed); writer _frame_politics added (grounded
  demand/oppose/criticise/support templates, Roman-Urdu mirrors).
- Region fix earlier tonight: state-name keywords + URL-section + domain priors.
- Reliability: cron every 30 min (10,40 UTC); watchdog.yml wakes cycle if
  last_cycle.json older than 45 min; commit-back order fixed (commit before
  pull --rebase). GitHub scheduler skipped cron slots tonight - watchdog covers.
- ZAPIER PAUSE INCIDENT (~23:05-23:20 IST): webhook returned 404 "please
  unsubscribe me!" = Zap paused (auto-pause after FB step error). Owner resumed
  manually. NOTE: rc5 now raises SystemExit(9) -> red GitHub run -> owner email,
  so future pauses alert automatically. If pauses recur: check Zapier Task
  History for the FB step error (image/caption/permissions) and fix root cause.
- Posts tonight: RTI 16:26; Kerala crash + cyber refund 20:26; Manipur + auto
  20:30; BJP textbooks + TGMC raids 23:05; CURE Bill 23:27. Records: 18.
- Verify on Page: BJP + TGMC posts may have been caught by the pause window;
  if missing and Task History shows dropped tasks, re-send via fresh inbox
  slug (keep old record as audit).

== 17 Sep 2026 — DEFECTIVE CARD + ANTI-MISTAKE GATE =====================
- 16:54 IST digest card (digest-2026-09-17-1654, post 01a0af1c-8b07-9c75-
  9895-af88a8e4c62d) owner-flagged: bullet "Supreme Court issued stay." had NO
  subject of the stay (story was Delhi SIR 47-lakh-names plea) and Roman-Urdu
  bullets 1/4/5 carried English fragments ("youth to drive ...", "hands over
  ...", "'monitoring developments' of Russia ..."). RETRACTED from
  published.json (->22) and archived in retracted.json; owner deletes the FB
  post manually.
- Corrected card hand-built + verified (2-3 sources per story), published
  17:15 IST as digest-2026-09-17-1715, post 01a0af30-4649-a22b-83c8-
  d95d8d198357: HC CCTV order / KTR judicial-probe challenge / EC graduate
  rolls / SC Delhi SIR hearing / rupee 95.94.
- PERMANENT GATE (writer.bullet_quality, wired into all 3 autod digest paths
  + final safety net): rejects vague EN bullets (issued stay / gave statement
  / thin <50 chars w/o digits) and UR bullets with >=2 English function words
  or leftover English verbs (hands over, monitoring, to drive, seeks, ...).
  Unit-tested: all 4 defective pairs flagged, all 6 good pairs pass.

== 18 Sep 2026 — "ONLY ONE POST YESTERDAY" INCIDENT ====================
- Owner: yesterday delivered 1 visible card (defective 16:54 deleted by owner;
  17:15 corrected card remained; 19:00 slot NEVER fired).
- Root causes: (1) GitHub cron throttle left NO run 17:40-20:48 IST on 17 Sep,
  so the 19:00 slot had no cycle until 20:48; (2) the 20:48 cycle found pool<3
  because casualty ban + narrow converter left <3 convertible headlines.
- FIXES PUSHED 18 Sep 12:26: converter v4 (Urdu postposition layer: "for X"->
  "X ke liye" etc., quote-masked; 12 new verb templates incl. can-V, begins,
  probes/raids/veto/wishes/allows/participates/recalls/rises/falls/caught;
  tag-prefix + person-attribution stripping; "sanctions" noun-trap removed;
  dialogue-khwahish template), autod pool widened (class C score>=60),
  converter-repair before quality-skip, digest telemetry line
  "[digest] pool=N skips={...}", cron densified to 5,20,35,50 UTC.
- Offline proof: live 40-candidate pool now yields 5 clean EN/UR pairs.
- MAKEUP CARD 13:00 IST published manually (post 01a0b34f-b4a5-4b70-95f0-
  d09430918cef): SIR family documents / Cyberabad WFH / Harvard Hyd / 22-A fee
  route / UNGA Abbas - all 2-4 outlet verified. quota synced (digests=2,
  last_card_at 13:00) so auto slots shift: ~15:10 and ~19:10 cards today.
- STANDING CHECK: after any pipeline change read next 2-3 run logs for the
  "[digest] pool=" line; pool<3 two cycles in a row = widen or hand-build.

## Round 5c (23 Sep 22:25 IST) — anti-silence pipeline restructure
- Stage loop now wraps the FULL pool→picks→quality-net→cross-card-net pipeline:
  a card ships the moment any stage yields ≥3 clean bullets (fixes 22:07 silent run).
- Frame-optional pool entry: candidates without a writer frame join via
  title+converter pair (telemetry skip key `fr`).
- writer: second-verb object cut ("raid two units, SEIZE x" → clean UR);
  "offers prayers at X" → "{s} ne {X} mein duayen ki."; number-words (two/crore/Rs…)
  count as specifics; lexicon victims→mutasireen, workers→karkunon, prayers→duayen;
  UR_END_OK += maare/maari/huye/gaye/karengi/pareshan/barqarar.
- autod: net-drop telemetry (`net-dropped(qual|repeat): …`).
- REPO TRUTH: remote is cheerfulspirits999-bit/azad-news-agent (private).
- Card digest-2026-09-23-2225 published 22:25 IST (3 bullets, 131 KB, Zapier 200).

## Round 6 (24 Sep 09:46 IST) — owner feedback: text-only + ne/mein grammar
- publish.py: Zapier handoff is TEXT-ONLY (no image part) — owner: pic causes Zap error.
- writer.py gates: ur-ne-after-quote-or-comma, ur-ne-inanimate (FTA/flyover/dam/rain/...),
  ur-mein-agentive, ur-passive-mismatch (to-be-signed), ur-postposition-stack ("se ka elan"),
  ur-date-order ("16 ko December").
- Converters added: arrives-in, calls-for-removal/resignation/probe, alert-issued-for,
  march-to, rain-closes (passive), visit, criticises, to-infinitive future (main-verb guard),
  quoted says-should-be (before modal guard), to-be-signed passive future, possessive 's -> ke.
- autod.py: same-story rule now catches shared distinctive name + shared event verb
  ("Xi arrives in US" vs "Xi arrives in Washington" = one story).
- Retracted digest-2026-09-24-0923 (ne-defects) -> FB delete list now 8 posts.
- Card digest-2026-09-24-0946 published: 5 bullets, text-only, all gates green.

## Round 7 (24 Sep ~15:45 IST) — themed card via public image URL; pool widened
- Owner: cards must SHOW the branded graphic again; text-only was the wrong reading of
  "dont try to post pic" (the real Zapier fault: photo step needs an IMAGE URL, not a file).
- New: publish.upload_card_public() PUTs the card PNG to the PUBLIC repo
  cheerfulspirits999-bit/azad-daily-cards (cards/<slug>.png) via AGENT_PAT secret;
  Zapier payload carries image_url + has_image. Fallback: if upload fails, a pre-baked
  branded cards/fallback.png URL is used so the Zapier photo field is NEVER empty
  (empty field = Zap errors = auto-pause risk).
- Needle: Zap Facebook action must switch from Create Text Post to Create Photo Post,
  Image from code-step field image_url. Owner guided click-by-click.
- Feeds +3 (Deccan Chronicle, TOI top+india, India Today national) => corroboration pool
  much richer; candidate window 40 -> 60. Secrets: AGENT_PAT added (repo Actions secret).
- Verified locally: upload_card_public E2E (raw URL 200); monitor parses all 7 feeds.
- Gate hardening after local repro (same pool, junk-free): en-titlecase-garbage (raw
  TITLE-CASE headline used as bullet rejected), postposition stack now catches
  "mein ke(liye)", UR english-noun blacklist +flood/suspected/adulterated/paste/
  catchment/waterlogged/adulteration/diverted, _same_story: shared distinctive FULL NAME +
  both titles in LEGAL context = one story (SC-upholds-Danam + BRS-welcomes-order deduped),
  court converters: "upholds X's disqualification", "rejects pleas challenging/against".
  UR_END_OK +rakha./rakhi./dein./dena./rakhte. Feeds 4->7 (Deccan/TOI/IndiaToday),
  candidate window 40->60. Local: 3 clean bullets, no dupes, no junk.
- 16:30 owner: 'posted without logo/black theme card' -> the Zap is still text-action,
  and Zap-side photo mapping is unreliable on owner's phone. Fix in OUR code: caption now
  ends with the public card-PNG URL (raw.githubusercontent image/png) -> Facebook
  link-previews it as a large image under the post = themed card shows WITHOUT touching Zap.
  image_url field still sent for a future photo-action upgrade. Verified via runner logs next slot.
- 18:2x: caption-URL fix VERIFIED via isolated /tmp/payload test of the REAL publish.run
  path (zapier_post monkeypatched): last caption line = live card PNG URL, meta.image_url
  set. First themed post = next card with >=3 fresh clean stories (evening slot).
  Runner gate + dedupe confirmed working same window (18:20 run refused 1-item pool).

## Round 8 (24 Sep ~22:10 IST) — permanent fix: no links, no pasted English, no fact-inversion
- CAPTION: github image URL REMOVED permanently (owner: no links, ever). Image attaches
  only via native photo route (Zap photo step w/ image_url, or Graph Page token).
- writer.py: targets/targeted NO LONGER map to "tankeed" (aspiration != criticism =
  fact inversion; same class as 18 Sep sanctions trap). "tanqeed" spelling unified.
- New hard gates: ur-english-run (>=2 consecutive lowercase tokens that are neither
  generator vocabulary nor names shared with the EN bullet), ur-english-verb-leftover
  expanded (backs/seized/arrested/injured/... single verb = reject), postposition
  stack now also catches "ko tak".
- URDI_VOCAB auto-extracted at import from this file's UR string literals (self-
  updating: every converter word is whitelisted automatically; raw-headline paste is not).
- Verified: all 4 junk bullets from owner's 21:01 screenshot REJECT; all owner-approved
  house-style bullets PASS; digest tonight correctly pool=0-clean (strict>volume).
- Retract digest-2026-09-24-2101 (FB delete list item #9); quota gate reset to 20:10.
- 22:5x owner screenshot (Sep 12 post): THE theme = themed PNG ATTACHED to the post.
  Root insight: Sep-12..Sep-23 payloads were multipart WITH 'image' file part and the
  Zap posted images fine; the 24-Sep text-only switch (after owner's Zap-error report)
  is why posts went bare text. Restored exact Sep-12 wire format (fields + image part),
  10 MB cap retained, config kill-switch publish.attach_image added. 6h gap tonight:
  GitHub cron dropped 21:16-22:01 runs again (throttling) + strict pool; manual dispatch.
- 23:0x owner: 'you didnt posted anything' + logo re-attached (identical bytes, no change
  needed). Silence 15:54->23:06 = evening pool failed gates (correct) + GitHub cron dropped
  21:16-22:01 slots AGAIN. Volume fix WITHOUT loosening: 6 new converters (targets-by-year
  aspiration frame, meets+assures, seeks-probe-into, ready-for-bypoll, begins-debate,
  N-camps-for-X-in-Y), UR_GLOSS house translations applied inside _fin (thermal power ->
  thermal bijli, capacity -> kshamata, family properties -> xandaan ki jaidadon, ...),
  english-paste gate upgraded: TOTAL unknown-token count (frame's and->aur glue trick
  bypassed run-length), VAGUE_EN now kills 'issued stay against <place>' shape.
  ROOT CAUSE vocab under-harvest: 2-char markers (ke/ki/ka) never matched the {2,} token
  scan -> 200+ legit Urdu words missing; fixed with word-boundary marks search; new rules
  hoisted above generic 'begins' handler. Local digest: 4 clean bullets (pool=6).

## 25 Sep morning round (post-mortem of "no posts since morning")
- GitHub delivered only 2 news-cycle runs 02:12→13:08 IST (07:21, 13:08); 08/10/12 slots silently throttled. 13:08 run DID attempt: stage-2 skips = casualty 63, conv 87, qual 24, fr 102 → honest empty pool (deaths/bans + unconvertible), not a crash.
- Slot watchdog: facebook-check.yml re-armed (cron 45 2,4,6,8,10,12,14,16 * * * = 08:15..22:15 IST). It reads quota last_card_at + recent news-cycle runs; if a slot was attempted by nobody and the last card is >130 min old, it dispatches news-cycle itself → missed GitHub slots self-heal. Needs AGENT_PAT secret (already present).
- Supply: +NDTV +LiveMint feeds (reachability verified from sandbox via curl; feedburner/livemint 200s).
- 5 converters (writer.urdu_headline, hoisted): sets-up-body-for (→"khaas samiti gathit ki"), advisory-on (word-glossed idol/height/limits→murtiyon/unchai/seemaon + ki-inserts), relief-to incl. "troubled by Section N" → "dhara 22A se pareshan … ko raahat di", landslide-damage (property-only, "kai ghar kshatigraat hue"), resignation-over (trailing clause cut at comma, proper-noun case kept). URDI_VOCAB +13 words (sadak/suraksha/dhara/raahat/bhousadke/istifa…).
- autod._collect: shrink-retry before conv-skip — strip " State-wide"-type modifiers and trailing " on <Month> <dd>" date clause, reconvert, adopt only if ≤95 AND bullet_quality clean. Fixes "…protests … on September 28" class of >95 overhangs.
- VAGUE_EN trailing-dot bug FIXED: stay-against now matches titles ending with '.' (unit: "Supreme Court issued stay against the eviction drive." → vague-english ✔).
- (same round) transport frame "Ctx: Special Buses From Place" → "{ctx} ke liye {place} se khaas basen chalayi gayi hain."; local morning-pool replay now yields pool=3 (buses/22A-relief/Sikkim landslide) → cards possible on slots even in casualty-heavy mornings.
- (25 Sep night) 5th+6th converters: "issues order to register Section X properties with valid applications" → dhara/registration frame; "Boulders/Rocks crash onto <road> in <City>…" → chattane frame. Watchdog v2: skip only if cycle ran <30 min ago (or <75 min AND cadence ok) → thin-pool slots now retry every patrol; pillow installed in facebook-check (check-fb was dying on ModuleNotFoundError).
- (25 Sep ~23:00) CATCH-UP MODE (owner directive): news-cycle workflow_dispatch input `catchup: "1"` -> env DIGEST_CATCHUP: fetch window 26h (vs 6), 120 candidates, extra digest stage (45/26h/50), min gap 30 min (vs 2h), slot-hour bypass, cap +6 cards. Auto-arms on its own whenever gap since last card >= 3h inside 08-23 IST (silence cannot persist). Watchdog dispatches with inputs catchup=1 when age>=180. Also: approvals variant in order rule, protests-seeking frame rule (re.I), fetch-window widening happens only for catchup dispatches.
- (25 Sep ~23:00) en_headline() sentence-case synthesizer + rescue in _collect: when the raw-title lead is flagged ONLY en-titlecase-garbage/en-no-specifics, a same-facts sentence-case lead is synthesized and ALL gates re-run on it (no loosening). UR_PAST abstain guards: verb inside a colon label ("X Probe: ...") and comma-joined multi-clause objects can no longer fold into wrong-fact templates (Myanmar/Kerala near-misses dead; SC control unaffected). Vocab +rail/prabandhan/karyalay; _KEEP_EN proper-noun list for the normalizer. Backlog replay tonight: pool=3 at stage-0 → catchup card = advisory(EN-synth)/22A-order/CGST-arrest.
- (26 Sep) Zapier delivery diagnosis: GET to hook returns {"status":"success"} -> hook+Zap endpoint alive; owner's "didn't post" = Zap not EXECUTING (auto-off after FB errors / free-plan monthly task cap / expired FB connection -> owner checks Zap History + Tasks usage + reconnects FB). publish.py now logs `zapier said: {attempt...}` + state/zapier_last.json so accepted-but-dropped is distinguishable in run logs.
- (26 Sep 13:31) that day's catch-up card self-retracted: UR leaked "UNGA par par" + "Opposition Flags Form 6 Changes"-style runs. Gates hardened: adjacent-particle stutter rule; UR_EN_BLACK += flags/discusses/consults/resignation/opposition/changes/meets/warns/urges/asks/tells/declines/accepts/rejecting. Unit: both leaked pairs now REJECTED; 5 known-good bullets live. No collateral on controls.

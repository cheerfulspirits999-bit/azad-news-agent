#!/usr/bin/env python3
"""
AUTONOMOUS MODE daemon.

Runs forever, one cycle every CYCLE_SECONDS (default 90 min):
    1. monitor preferred sources (Hyderabad > Telangana > India > World)
    2. pick publishable stories: class A/B, or class C from a preferred source
       with score >= 72, age <= 12h, not a duplicate
    3. auto-write exactly 3 English + 3 Roman Urdu bullets from a verified
       fact frame (writer.build); stories without a safe frame wait for the
       editorial pass - they are NEVER guessed
    4. render the branded graphic with the page logo (no source names)
    5. publish to the connected Facebook Page; log the record
    6. if publishing is not connected yet, keep the ready package in a pending
       list and retry it automatically once credentials appear in config.json
    7. max 2 posts per cycle (anti-flood); nothing important is ever forced

Stops and raises an alert (state/ALERT.md + exit code 9) ONLY on:
    * Facebook auth/permission errors (Graph API 190/102/permissions)
    * repeated publish failures
    * all monitored feeds failing (technical outage)
"""
import json
import os
import re
import signal
import sys
import time
import traceback
from datetime import datetime, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import monitor  # noqa: E402
import publish  # noqa: E402
import writer  # noqa: E402
import roman_urdu  # noqa: E402


_NONROMAN_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF"
                          r"\uFB50-\uFDFF\uFE70-\uFEFF\u0900-\u097F]")


def _real_paste(en, ur, n=4):
    """True when `ur` carries >=n consecutive LOWERCASE English words copied
    verbatim from `en`. Capitalised tokens (names, laws, events: 'Ganesh
    idol', 'PD Act', 'Hyderabad Metro') legitimately survive into Roman
    Urdu newsroom style, so any run containing one is not a paste. 4+
    lowercase source words in a row still means untranslated."""
    t = re.findall(r"[A-Za-z][A-Za-z'\-.]*", en or "")
    u = re.findall(r"[A-Za-z][A-Za-z'\-.]*", ur or "")
    if len(t) < n or len(u) < n:
        return False
    tset = {tuple(w.lower() for w in t[j:j + n]) for j in range(len(t) - n + 1)}
    for i in range(len(u) - n + 1):
        seg = u[i:i + n]
        if any(w[0].isupper() for w in seg):
            continue
        if tuple(w.lower() for w in seg) in tset:
            return True
    return False


# 29 Sep evening fix: the digest re-picked the same candidates every cycle
# and the pinned model echoed them deterministically (temp-0 lesson again)
# -> 6h of "starved: <3 publishable Urdu lines" while 120 candidates waited.
# Bank every line the RU stage killed for 4h so picks rotate toward stories
# the model CAN translate, and give salvage a fallback-model chain (owner:
# OpenRouter is funded - one model's bad hour must never stop the pipeline).
_RU_MISS_TTL = 4 * 3600.0
# 1 Oct (2): the pinned model + the whole 3-model chain echoed simultaneously;
# more voices so ONE model's bad hour cannot starve the cycle. 1 Oct (3):
# wallet hit $0 (402s) - so the chain is ordered FREE-TIER FIRST (works at
# $0), then the owner's DIRECT Gemini/OpenAI keys (used only when those
# secrets exist in git; tuple = provider/model/ENV var), then paid OpenRouter
# slugs that auto-revive the premium lane the moment credits come back.
# Every returned line is still judged by the same hard gates - only the
# chance-to-answer widens, never the bar.
# sentinel marking the FreeLLMAPI gateway lane inside the fallback chain -
# it is not a model name, it is "ask the owner's gateway to choose one"
_FREELLM = "\x00freellm-gateway"


def _freellm_base():
    """Public base URL of the owner's FreeLLMAPI gateway, or "" when unset.

    Accepts the gateway root or the /v1 path and normalises to /v1, so
    https://x.onrender.com, https://x.onrender.com/ and
    https://x.onrender.com/v1 all work. A localhost value is rejected: it
    would be the runner's own loopback, never the owner's machine.
    """
    b = (os.environ.get("FREELLM_BASE_URL") or "").strip().rstrip("/")
    if not b:
        return ""
    low = b.lower()
    if "localhost" in low or "127.0.0.1" in low or low.startswith("http://0."):
        return ""
    if not low.startswith(("http://", "https://")):
        b = "https://" + b
    return b if b.lower().endswith("/v1") else b + "/v1"


_RU_FALLBACK_MODELS = (
    # 3 Oct: direct-key lanes FIRST. Proven with a raw HTTP probe (see the
    # diag-translator workflow): OpenRouter's :free tier is hard-capped at
    # 50 requests/day for a $0 wallet ("Add 10 credits to unlock 1000/day")
    # and this account has already burned today's 50 -> every :free call
    # answers 429 until 00:00 UTC. The owner refuses any top-up, so the
    # direct keys are the only lanes with a real quota (Gemini free tier is
    # ~1500/day). The rotation index picks ONE attempt per batch, so a
    # working lane must be first or the digest waits ~5 dead rotations
    # (~50 min) before it is even tried.
    # 6 Oct: gemini-2.5-flash returns HTTP 404 "no longer available to new
    # users". The owner's GEMINI_API_KEY (aistudio, free ~1500 req/day) is
    # the pipeline's primary free lane.
    #
    # 21 Nov ROOT-CAUSE FIX for the "engine.method=fallback" cards (the
    # garbled "Passenger ne Ammunition ke saath RGIA par Live ka inqiad
    # kiya" output). Every model below was run through the REAL engine in
    # CI with the owner's key on live bullets (diag run 44904443919):
    #
    #     gemini-flash-lite-latest       llm        1.3s   <- lead
    #     gemini-3.5-flash-lite          llm        1.4s
    #     gemini-3-flash-preview         llm        6.4s
    #     gemini-3.8-flash               fallback  14.4s
    #     gemini-omni-1.2-flash          fallback  14.4s
    #     gemini-3.6-flash               fallback  27.8s
    #     gemini-3.1-flash-lite          fallback  55.7s
    #     gemini-3.1-flash-lite-preview  fallback  63.0s
    #     gemini-flash-latest            fallback  (503)
    #     gemini-3.7-flash               fallback
    #     gemini-3.5-flash               fallback
    #
    # Only THREE translate. The old chain led with four of the dead ones,
    # so the batch spent its tries on English passthroughs before reaching
    # a model that answers - and _RU_LANE_DEAD is per-process while every
    # cycle is a fresh `autod.py --once`, so those four were re-walked
    # EVERY hour, ~2 min of the cycle burned to produce fallback text.
    #
    # Keep this list ranked by VERIFIED method="llm". A model name that
    # merely exists in /v1beta/models is NOT a live lane - re-run the diag
    # before adding one.
    ("gemini", "gemini-flash-lite-latest", "GEMINI_API_KEY"),
    ("gemini", "gemini-3.5-flash-lite", "GEMINI_API_KEY"),
    ("gemini", "gemini-3-flash-preview", "GEMINI_API_KEY"),
    # 21 Nov (owner: "FREELLM HAS UNIFIED KEYS, IT HAS GEMINI, GROK, OPEN AI,
    # NVIDIA, ETC"). Correct - FreeLLMAPI fronts all of those behind ONE
    # freellmapi-... token. The catch is that the token authenticates
    # against the owner's OWN gateway, not against the providers, so the
    # gateway has to have an address this runner can reach. Their own docs
    # say it of Cursor, which is in exactly our position: "your router must
    # be reachable from the internet (a tunnel or a host with a public
    # address), not just localhost".
    #
    # This lane is therefore DORMANT until FREELLM_BASE_URL is set as a repo
    # variable. Set it and the lane lights up with no code change; leave it
    # unset and nothing about the cycle changes. It sits BEHIND Gemini on
    # purpose: Gemini is measured at 1.3s and proven, so it stays the fast
    # path, and FreeLLM becomes the broad failover (Grok/NVIDIA/OpenAI/...)
    # for the day Gemini's free tier caps out - which is currently an
    # unprotected single point of failure for the whole page.
    # --- THE OLD ROUTE (owner, 21 Nov: "KEEP THE OLD ROUTE ALSO ONLY IF
    # THE QUOTE FINISHES YOU CAN USE THIS"). The OpenRouter :free lane is
    # kept and now REPAIRED. It had rotted: of the four slugs configured
    # here, "qwen/qwen3.8-27b:free" and
    # "nvidia/nemotron-3-super-120b-a12b:free" no longer exist on
    # OpenRouter at all (404 "unavailable for free"), so two of the four
    # fallbacks were addresses for nothing. Checked against OpenRouter's
    # live /models list on 21 Nov - these six all exist today.
    #
    # This route only ever runs when the Gemini lanes above are exhausted
    # or failing, which is exactly the owner's rule. It is quota-bound in
    # its own right: this account is at the free-models-per-day cap
    # ("Add 10 credits to unlock 1000"), which resets 00:00 UTC, so the
    # route is a genuine next-day backstop rather than a live lane today.
    # Instruction-tuned models first - a translation prompt needs
    # instruction-following, not raw scale.
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    # --- only once BOTH of the above are out of quota ---
    # Position matters: _RU_BATCH_TRIES is 7, so with three Gemini lanes
    # and three old-route lanes ahead of it this sentinel is the 7th and
    # last lane a batch can reach. A lane that is parked by
    # _ru_lane_failed() is dropped from the list entirely, which moves
    # this one EARLIER, never later - so it stays reachable.
    _FREELLM,
    # deeper backstops, past the batch window; the salvage pass can still
    # reach them one line at a time
    "nvidia/nemotron-3.5-lightning:free",
    "inclusionai/ling-3.2-flash:free",
    "baidu/ernie-5.5-flash-preview:free",
    ("openai", "gpt-4o-mini", "OPENAI_API_KEY"),
    "openai/gpt-4o-mini",
    "google/gemini-2.5-flash-lite",
    "anthropic/claude-3.5-haiku",
    "deepseek/deepseek-chat",
    "mistralai/mistral-small-3.2-24b-instruct",
    "qwen/qwen3-30b-a3b",
)

# 6 Oct: how many live translator lanes one cycle may try for the BATCH, and
# how many lines a card needs before the cycle is worth publishing. A single
# batch that echoes costs the whole hour, so the batch now retries on a fresh
# lane before falling back to the (much more expensive) per-line salvage pass.
#
# 16 Nov: raised 3 -> 5. Ten translator lanes are configured but only three were
# ever tried, so seven sat idle. Observed live at 20:38 and 20:58 IST: all five
# picked headlines were killed for pasting English on lanes 1, 2 AND 3, the
# per-line salvage pass then rescued only one line, and the cycle starved at
# <3 publishable Urdu lines - a 100-minute hole in the hourly cadence, which is
# exactly what the owner's "hourly" and "breaking news" instructions forbid.
# Different models echo differently, so lanes 4 and 5 are a genuine second
# chance. Nothing is weakened: every paste/echo/quality gate is unchanged, the
# extra tries only happen while lines are still bad, and the spend is still
# bounded by _RU_DAILY_HARD (24 cycles x 5 lanes = 120 in a pathological day,
# under the 150 hard ceiling). Free lanes (Gemini, OpenRouter :free) bypass the
# budget entirely.
#
# 16 Nov round 2: 5 -> 7. The diag-translator probe run at 06:11 UTC today settled
# which lanes are actually reachable:
#     GEMINI_API_KEY  set(len=53)   -> lanes 1-5 live, all free
#     OPENAI_API_KEY  MISSING       -> lane 6 is DEAD and is skipped for free
#     RU_API_KEY      set(len=73)   -> OpenRouter reachable
#     RAW HTTP PROBE, OpenRouter :free model -> HTTP 200, real completion
# so lane 7 (nvidia/nemotron-3-ultra-550b-a55b:free) is a LIVE lane of a completely
# different model family from the five Gemini variants that just echoed, and it is
# a :free slug so _ru_is_free() returns True and it bypasses the paid budget
# entirely - the two extra tries cost nothing. Measured effect of 3 -> 5: accepted
# lines went 1 -> 2, and gemini-flash-lite-latest (lane 5) started being reached.
_RU_BATCH_TRIES = int(os.environ.get("RU_BATCH_TRIES", "7"))
_RU_CARD_MIN = 3

# 21 Nov (owner: "should a failed translation REFUSE the card?" -> "Yes,
# refuse it"). The engine reports HOW a line was produced:
#   "llm"      - the model translated it on the first attempt   ACCEPT
#   "retry"    - the model translated it on a later attempt     ACCEPT
#   "fallback" - NO model answered; a word-by-word dictionary   REFUSE
#                substitution was returned instead
#   "none"     - hard error                                     REFUSE
#   "noop"     - empty input                                    REFUSE
#
# The fallback path returns ok=True whenever its dictionary output passes
# the package's own paste validator, so every gate we had - echo, paste,
# digits, Urdu-script - waved it straight through. That is precisely how
# "Passenger ne Ammunition ke saath RGIA par Live ka inqiad kiya" reached a
# published card: real English nouns glued together with Urdu connectives,
# which is not a translation and does not mean anything.
#
# Refusing it costs bullets on a bad hour (<3 publishable lines -> no card,
# and the hourly cadence rides). That is the trade the owner chose, and it
# is the same rule as the rest of the pipeline: never publish something we
# cannot stand behind.
_RU_REAL_METHODS = frozenset(("llm", "retry"))


def _ru_translated(res):
    """True only when a real model produced this batch."""
    return str((res or {}).get("method") or "") in _RU_REAL_METHODS
# 16 Nov: the owner's "cover full up election" ranking is working (5/5
# election picks), but it collides with the translator's gates. Election
# headlines are the most proper-noun-dense copy in the pool - "Talasani
# Srinivas Yadav flags low graduate voter registration", "BJP, Congress
# leaders spar in Haveri over vote chori" - and a correct Roman Urdu
# rendering necessarily carries 4+ consecutive English words (the names,
# plus "voter", "registration", "polls", "graduate" which are the natural
# Roman Urdu newsroom words). The paste gate kills those lines, so all five
# picks die identically and the cycle starves at "<3 publishable Urdu lines".
#
# Measured: 7 model families (5 Gemini, GPT-4o-mini, Nemotron) each failed
# the SAME five lines - deterministic input problem, not a model problem.
#
# So the card keeps election LEADING (the lead bullet and the majority of
# the card stay election - "full up election" is still covered) but at most
# _ELECTION_PICK_CAP of the five bullets may be election, leaving slots for
# stories the translator can actually render. Nothing is weakened: the
# ranking, the score, the paste/echo gates and _RU_CARD_MIN are untouched.
_ELECTION_PICK_CAP = 3

_RU_DAILY_CAP = int(os.environ.get("RU_DAILY_CAP", "46"))
_RU_DAILY_HARD = int(os.environ.get("RU_DAILY_HARD", "150"))


def _ru_is_free(kw):
    """True when an attempt targets one of OpenRouter's :free slugs.

    3 Oct: these cost $0, so they must never be charged against the paid
    translator budget. The gate was built to cap SPEND, but it had become the
    only thing stopping the pipeline: ru_budget sat at 220/220 while the free
    lane was answering perfectly (proved by the daily probe at 05:07 UTC:
    HTTP 200, ok=True, real Roman Urdu) - 12 hours of silence for a service
    that costs nothing. Owner law: the budget must NEVER be the reason the
    pipeline goes silent."""
    # 6 Oct: resolve the EFFECTIVE model, not just the kwarg. The primary lane
    # is yielded as ("primary", {}) - it takes its model from RU_MODEL - so the
    # old check saw an empty dict and called a :free slug "paid". The free lane
    # was then charged against the paid budget, which sat at 220/220, so the
    # batch call was refused for "budget" while a $0 model was idle. Owner law:
    # the budget must never be the reason the pipeline goes silent.
    m = str((kw or {}).get("model") or os.environ.get("RU_MODEL") or "")
    if m.endswith(":free"):
        return True
    # 6 Oct: the owner's GEMINI_API_KEY is the aistudio FREE tier (~1500
    # requests/day, $0). Same owner law as the :free slugs - a lane that costs
    # nothing must never be the reason the pipeline goes silent.
    return (str((kw or {}).get("provider", "")).lower() == "gemini"
            and bool((kw or {}).get("api_key")))


def _ru_spend(n=1, force=False, free=False):
    """Consume n translator API calls from today's budget; False if out.
    Two-tier by design (2 Oct lesson): the SOFT cap (~free-tier quota/day)
    stops the cheap extras - salvage one-liners etc - but the BATCH call
    passes force=True and keeps spending to the HARD ceiling, because one
    batch turns up to 24 lines into cards and it is what the hourly promise
    runs on. Owner law: nothing may stop the pipeline for a whole morning -
    the first version of this gate did exactly that (46/46 by ~02:00 IST ->
    every cycle starved through breakfast) and must never again. Beyond
    HARD everything truly waits for the IST-midnight reset.

    free=True (a :free OpenRouter slug) bypasses the gate entirely: it costs
    nothing, and OpenRouter's own 50/day limit is enforced by them - our
    _ru_lane_failed() parking already stops us hammering a 429'd lane."""
    if free:
        return True
    bp = os.path.join(STATE, "ru_budget.json")
    todayk = datetime.now(IST).date().isoformat()
    try:
        d = json.load(open(bp))
    except Exception:
        d = {}
    if d.get("d") != todayk:
        d = {"d": todayk, "n": 0}
    _n = int(d.get("n", 0))
    if _n + n > _RU_DAILY_HARD:
        return False
    if _n + n > _RU_DAILY_CAP and not force:
        return False
    d["n"] = int(d.get("n", 0)) + n
    try:
        json.dump(d, open(bp, "w"))
    except Exception:
        pass
    return True


# 3 Oct: lanes that answer 429 (OpenRouter's :free tier is hard-capped at
# 50 requests/day on a $0 wallet) or 401/402 (key unfunded) must not be
# retried - the salvage loop walked the WHOLE chain for every bad line, so a
# single cycle could spend the entire daily free quota on dead lanes and
# starve the pipeline for the next 24h. A hard-failed lane is parked for
# _RU_LANE_COOLDOWN and skipped until then.
_RU_LANE_DEAD = {}
_RU_LANE_COOLDOWN = 1800.0
_RU_HARD_FAIL = ("429", "401", "402", "rate limit", "insufficient credit",
                 "quota", "no api key", "unauthorized", "forbidden")


def _ru_lane_failed(label, problems):
    """Park a lane that hard-failed (rate limit / no credits)."""
    txt = " ".join(str(p) for p in (problems or [])).lower()
    if any(h in txt for h in _RU_HARD_FAIL):
        _RU_LANE_DEAD[label] = time.time()
        return True
    return False


def _ru_lane_alive(label):
    t = _RU_LANE_DEAD.get(label)
    return not (t and (time.time() - t) < _RU_LANE_COOLDOWN)


def _ru_attempts():
    """(label, kwargs) salvage attempts: primary first (one-line prompts
    translate where the batch echo'd), then free-tier, direct keys (skipped
    silently until the owner's git secrets exist), then paid slugs.

    3 Oct: every free/salvage attempt carried use_few_shot=True, because the
    owner will not top up OpenRouter ("free tier option only"), so the free
    lane IS the pipeline - and the engine was dropping to its deterministic
    English passthrough, which our echo gate correctly kills. The engine
    ships few-shot examples that teach exactly the output contract (same
    bullet count, names/numbers untouched, idiomatic Roman Urdu); the shim
    had been forcing use_few_shot=False. A weak free model needs them.

    16 Nov ROOT-CAUSE FIX: that flag was applied to EVERY fallback lane,
    including the five direct-key Gemini lanes and the OpenAI lane, which are
    not weak free models - so the 3 Oct rationale never applied to them. It
    also silently defeated roman_urdu.py's own protection: the shim does
    kw.setdefault("use_few_shot", False), which is a no-op the moment the
    caller passes the key explicitly.

    roman_urdu.py's post-mortem of the 27-28 Sep outage describes the result
    exactly: few-shot ON makes the model mirror the shipped code-mixed EN+UR
    examples, "the package's own strict paste validator then rejects those
    lines, every retry fails the same way, and cycles starve at 0-1/5 accepted
    lines." That is a verbatim description of the 20:38-22:15 IST starvation,
    which sat at 1-2 of 5 accepted with every retry killed identically.

    So the flag now follows the model's strength, honouring BOTH decisions:
      * direct-key lanes (Gemini, OpenAI - strong models on the owner's own
        key): use_few_shot=False, the documented-safe setting that validated
        5/5 in the post-mortem.
      * OpenRouter :free slugs (the weak free models the 3 Oct note was
        actually about): use_few_shot=True, unchanged.
    Lanes parked by _ru_lane_failed() are skipped. The primary keeps its
    old setting."""
    yield "primary", {}
    for m in _RU_FALLBACK_MODELS:
        if m == _FREELLM:
            # unified gateway: one key, many upstream providers. Dormant
            # unless the owner has published a reachable address for it.
            _b, _k = _freellm_base(), os.environ.get("FREELLM_API_KEY", "")
            if _b and _k and _ru_lane_alive("freellm"):
                yield "freellm", {"provider": "openai",
                                  "model": os.environ.get("FREELLM_MODEL",
                                                          "auto"),
                                  "api_key": _k, "base_url": _b,
                                  "use_few_shot": False}
            continue
        if isinstance(m, tuple):
            prov, mdl, envi = m
            k = os.environ.get(envi, "")
            if k and _ru_lane_alive(mdl):
                # strong model on the owner's own key -> documented-safe OFF
                yield mdl, {"provider": prov, "model": mdl, "api_key": k,
                            "use_few_shot": False}
        elif _ru_lane_alive(m):
            # weak OpenRouter :free model -> the 3 Oct rationale still applies
            yield m, {"model": m, "use_few_shot": True}


def _line_h(text):
    import hashlib
    return hashlib.sha1((text or "").lower().encode()[:200]).hexdigest()[:12]


def _ru_miss_load():
    try:
        raw = json.load(open(os.path.join(STATE, "ru_misses.json"),
                             encoding="utf-8"))
    except Exception:
        return {}
    now = time.time()
    return {str(k): float(v) for k, v in raw.items()
            if isinstance(v, (int, float)) and now - v < _RU_MISS_TTL}


def _ru_miss_save(bank):
    try:
        json.dump(bank, open(os.path.join(STATE, "ru_misses.json"), "w"))
    except Exception:
        pass


_RU_FUNC = {"ne", "ka", "ke", "ki", "ko", "se", "mein", "me", "par", "pe",
            "hai", "hain", "tha", "thi", "the", "hua", "hui", "hue", "ho",
            "hota", "nahi", "nahin", "kyun", "kyon", "aur", "ya", "karna",
            "kar", "raha", "rahe", "rahay", "rahi", "bola", "boli", "liye",
            "baad", "pehle", "tak", "bhi", "sakte", "sakti", "chahiye"}


# Words that are NEVER written as bare English tokens in genuine Pakistani
# newsroom Roman Urdu (unlike loanwords such as case/meeting/committee/
# launch, deliberately excluded to avoid false kills).
_EN_COMMON = {
    "the", "and", "but", "with", "from", "have", "has", "had", "will",
    "would", "shall", "should", "could", "may", "might", "must", "for",
    "after", "before", "while", "during", "against", "under", "between",
    "about", "again", "than", "into", "over", "near", "said", "says",
    "told", "urges", "warns", "warned", "demands", "seeks", "accuses",
    "blames", "cites", "stages", "smoothly", "gears", "inaugurated",
    "inaugurate", "symbolically", "students", "student", "hostel",
    "hostels", "meal", "meals", "trigger", "triggers", "turnout",
    "results", "locals", "poses", "risks", "crops", "warns", "centre",
    "resignation", "demanding", "conclude", "concludes", "opposes",
}


def _echo_paste(en, ur):
    """Owner 29 Sep 'roman urdu got disturbed': _real_paste is deliberately
    blind to capitalised words, so a VERBATIM English echo
    ('Etihad Rail Dubai Station officially inaugurated.') or an Urdu shell
    with a whole English sentence pasted inside ('...727 Voter Exclusions
    Delhi. What Poll Body Said mein...') slips through. Kill when the line
    mirrors the source too closely AND shows no translating effort: >=55%
    token overlap with NO Urdu function word anywhere, or a verbatim run of
    6+ consecutive source tokens (case-insensitive, punctuation ignored).
    Legit proper-noun-dense newsroom lines ('Hyderabad Metro Pink Line ka
    jaiza meeting hui') carry function words and short name runs -> pass."""
    t_raw = re.findall(r"[A-Za-z][A-Za-z'\-.]*|\d+", en or "")
    t = [w.lower().strip("'.-") for w in t_raw]
    t_cap = [bool(w) and w[0].isupper() for w in t_raw]
    u = [w.lower().strip("'.-") for w in
         re.findall(r"[A-Za-z][A-Za-z'\-.]*|\d+", ur or "")]
    if len(u) < 4:
        return False
    from collections import Counter
    ct = Counter(x for x in t if x)
    shared = sum(min(c, ct[x]) for x, c in
                 Counter(x for x in u if x).items())
    overlap = shared / len(u)
    has_func = any(x in _RU_FUNC for x in u)
    run = best = lcrun = bestlc = 0
    for i in range(len(u)):
        for j in range(len(t)):
            k = 0
            lc = 0
            while (i + k < len(u) and j + k < len(t)
                   and u[i + k] and u[i + k] == t[j + k]):
                k += 1
                if j + k - 1 < len(t) and not t_cap[j + k - 1]:
                    lc = k  # run still all-lowercase (non-proper-noun)
            best = max(best, k)
            bestlc = max(bestlc, lc)
    best = max(best, 0)
    ct2 = Counter(x for x in t if x in _EN_COMMON)
    sc = sum(min(c, ct2[x]) for x, c in
             Counter(x for x in u if x in _EN_COMMON).items())
    return ((overlap >= 0.55 and (not has_func or bestlc >= 3))
            or best >= 6 or (sc >= 3 and overlap >= 0.45))


def _ru_on():
    """Is the owner's roman_urdu translator usable in this run?"""
    try:
        return bool(roman_urdu) and roman_urdu.configured()
    except Exception:
        return False

CYCLE_SECONDS = 90 * 60
POST_SPACING_SECONDS = 75  # pause between posts in one burst (FB rate limits)
MAX_POSTS_PER_CYCLE = 3
STATE = os.path.join(BASE, "state")
STORIES = os.path.join(BASE, "stories")
IST = monitor.IST

HEADLINE_MAP = monitor.HEADLINE_MAP


_LOGTAIL = []           # 28 Sep: in-progress visibility - the tail of this
_DIGEST_NOTE = ""       # last digest verdict of this cycle, lands in state
_RU_STATS = {}          # roman-urdu accept/kill anatomy of the last attempt


def log(*a):            # cycle's stdout rides inside last_cycle.json, which
    msg = (f"[{datetime.now(IST).strftime('%d %b %H:%M:%S')}] "  # the relay
           + " ".join(str(x) for x in a))                         # pushes
    print(msg, flush=True)                                        # EVERY cycle.
    _LOGTAIL.append(msg[-170:])
    del _LOGTAIL[:-130:]


def alert(reason, detail=""):
    os.makedirs(STATE, exist_ok=True)
    with open(os.path.join(STATE, "ALERT.md"), "w", encoding="utf-8") as f:
        f.write(f"# AGENT STOPPED - ACTION NEEDED\n\n"
                f"**When:** {datetime.now(IST).isoformat()}\n\n"
                f"**Reason:** {reason}\n\n{detail}\n")
    log("!!! ALERT:", reason)
    log(detail[:400])


def clear_alert():
    """Remove a stale ALERT.md once publishing works again.

    3 Oct: alert() only ever WROTE the file. Nothing cleared it, so after the
    token recovered the page kept showing "AGENT STOPPED - ACTION NEEDED" from
    hours earlier and the relay's fail-loud step opened a GitHub issue from it.
    A healthy pipeline must not look broken.
    """
    path = os.path.join(STATE, "ALERT.md")
    try:
        if os.path.exists(path):
            os.remove(path)
            log("alert cleared - publishing is healthy again")
            return True
    except Exception as e:
        log("could not clear ALERT.md:", str(e)[:120])
    return False


def slugify(t):
    t = re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")
    return t[:60]


MAX_AGE_HOURS = 26.0  # owner 27 Sep: "take and post" - anything within the
                      # last day across ALL regions/categories is fair game;
                      # hourly silence only when the day is truly exhausted
                      # (old 6h window caused the dead morning hours)
# owner 16 Nov "cover full up election": election and poll coverage leads the
# card. The text test is deliberately wider than the category keywords - it also
# catches a story whose headline carries the election angle without the feed
# tagging it, so nothing that belongs on an election card gets missed.
# "run-off" is deliberately absent: it matched a story about RAIN runoff off a
# dam ("Rising runoff, flood risks"), which is weather, not an election.
# Bare "ec" and "mp" are absent too - "MP" is also Madhya Pradesh and "EC" any
# number of committees. The Election Commission is matched by its full name.
ELECTION_RE = re.compile(
    r"\b(election|elections|electoral|electorate|poll|polls|ballot|ballots|"
    r"voter|voters|voting|vote chori|vote chor|candidate|candidates|"
    r"nomination|nominations|constituency|constituencies|by-?election|"
    r"by-?poll|booth|booths|franchise|suffrage|referendum|plebiscite|"
    r"canvass|canvassing|manifesto|mlc|mla|member of parliament|"
    r"legislative council|legislative assembly|assembly polls?|"
    r"local body polls?|municipal election|panchayat polls?|"
    r"graduate constituency|teachers constituency|election commission|eci|"
    r"chief electoral officer|electoral rolls?|repoll|uncontested|"
    r"run-?off (?:election|poll|vote)|polling|poll turnout|voter turnout|"
    # 16 Nov owner "cover full up election": these were missing, and a story
    # about the ELECTION MACHINERY alone - "Model code of conduct enforced
    # across Uttar Pradesh from tonight", "Electioneering peaks in UP as
    # campaign trail enters last leg" - carried no other election keyword, so
    # it fell through and lost its slot. Proven by /tmp/test_up_election.py.
    r"model code of conduct|election code of conduct|electioneering|"
    r"campaign trail|evm|vvpat|sarpanch|gram panchayat|zila parishad|"
    r"panchayati raj|corporator|councillor|postal ballot|ballot box|"
    r"unopposed|vote share|vote bank|nomination filing|"
    r"municipal corporation election|corporation election|"
    r"urban local bod(?:y|ies)|local body election|panchayat election|"
    r"election results?|assembly election|parliamentary election|"
    r"lok sabha election|reelection|re-election|seat sharing|"
    # a bare "turnout" would false-positive on a protest ("big turnout of
    # protesters"), so only a PERCENTAGE turnout counts as election news.
    r"\d+\s?(?:%|per cent|percent)\s?(?:voter\s?)?turnout)\b", re.I)


def _is_election(text):
    """True when a story is about an election, a poll, or the machinery of one."""
    return bool(ELECTION_RE.search(text or ""))


def _cand_text(c):
    return " ".join([(c.get("title") or ""), (c.get("excerpt") or ""),
                     (c.get("suggested_headline") or "")])


# 16 Nov owner "cover all war news, cyber attacks etc, all big incidents
# whichever section it may be": a war / terror / cyber / major-disaster /
# mass-casualty story is a big incident and must reach the card whatever its
# region or category. classify() is keyword-based and misses some of these
# (e.g. "Israeli strikes in Gaza kill five" was tagged only
# accident_casualty), so the digest detects them from the text directly,
# the same way it detects election stories.
_WAR_CYBER_WORDS = (
    "war|wars|wartime|missile|missiles|airstrike|airstrikes|air strike|"
    "bomb|bombs|bombing|bombed|invasion|invade|invaded|ceasefire|military|"
    "battle|battles|combat|weapon|weapons|airbase|air base|shelling|shelled|"
    "grenade|grenades|fighter jet|fighter jets|warship|warships|nuclear|"
    "army|armies|soldier|soldiers|navy|air force|"
    "terror|terrorist|terrorists|terrorism|hezbollah|houthi|houthis|hamas|"
    "militant|militants|suicide bomb|suicide bomber|extremist|extremists|"
    "isis|taliban|al[- ]?qaeda|jaish|lashkar|hijack|hijacked|"
    "cyber ?attack|cyberattack|cyber strike|cyber warfare|cyber threat|"
    "hacked|hacking|hacker|hackers|ransomware|malware|data breach|phishing|"
    "spyware|trojan|"
    "drone attack|drone strike|rocket attack|rocket strike|missile strike|"
    "strike|strikes|strike on|strikes on|strike in|strikes in|strike at|strikes at|"
    "strike kills|strikes kill|strike hits|strikes hit|"
    "(?<!heart )(?<!panic )attack|attacks|attacked")
_OTHER_BIG_WORDS = (
    "earthquake|tsunami|volcano|volcanic|landslide|hurricane|tornado|"
    "wildfire|avalanche|"
    "blast|explosion|explosions|stampede|building collapse|bridge collapse|"
    "mass casualty|mass casualties|deadly|"
    "massive fire|massive blast|massive explosion|massive crash")
_BI_NUM = (r"(?:ten|eleven|twelve|dozens|scores|many|multiple|\d{2,})")
_BI_CAS = r"(?:killed|dead|injured|wounded|missing)"
WAR_CYBER_RE = re.compile(r"\b(?:" + _WAR_CYBER_WORDS + r")\b", re.I)
BIG_INCIDENT_RE = re.compile(
    r"(\b(?:" + _WAR_CYBER_WORDS + r"|" + _OTHER_BIG_WORDS + r")\b"
    r"|\b" + _BI_NUM + r"\s+" + _BI_CAS + r"\b"
    r"|\b(?:kill|kills|killed|killing|dead|injured|wounded)\s+"
    + _BI_NUM + r"\b)",
    re.I)


# 20 Nov owner: a union walkout was posted as breaking news because the war
# detector matched the bare word "strike". A labour strike is never war.
_LABOUR_STRIKE = re.compile(
    r"\b(union|unions|workers?|employees?|staff|labour|labor|bandh|dharna|"
    r"hunger strike|rail roko|transport strike|bus strike|auto strike|rtc|"
    r"teachers?|doctors?|nurses?|drivers?|conductors?|lorry|truckers?|"
    r"pay revision|wage|salaries|salary|strike call|called off the strike|"
    r"indefinite strike|token strike|flash strike|work to rule|boycott)\b",
    re.I)


def _is_war_cyber(text):
    """True for war, terror or cyber-attack stories (owner 16 Nov, top tier).

    Called on the HEADLINE: genuine war news announces itself there, whereas
    excerpt-only matches ("a Yemeni military source", "attacks on
    candidates") are big incidents but not war headlines."""
    t = text or ""
    if not WAR_CYBER_RE.search(t):
        return False
    # "strike"/"attack" in a labour or ordinary-crime story is not war
    if _LABOUR_STRIKE.search(t):
        return False
    return True


def _is_big_incident(text):
    """True when a story is a war, terror, cyber or other major incident."""
    return bool(BIG_INCIDENT_RE.search(text or ""))


# 16 Nov owner "cricket matches are also going to start, cover that also, give
# updates on that also": cricket (and other sport) stories are covered on the
# card. Detected from the text like election and war stories.
CRICKET_RE = re.compile(
    r"\b(cricket|cricketer|IPL|BCCI|ICC|T20I?|ODIs?|Ranji|Duleep|"
    r"Irani Cup|Ashes|Asia Cup|World Cup|Champions Trophy|"
    r"World Test Championship|county cricket|Big Bash|BBL|PSL|CPL|"
    r"SA20|ILT20|Test match|Test series|one-day international|"
    r"wickets?|batsman|batsmen|batters?|bowlers?|bowling|innings|"
    r"all-rounder|wicket-keeper|spinners?|pacers?|seamers?|fielders?|"
    r"umpires?|LBW|no-ball|free hit|super over|powerplay|maiden over|"
    r"hat-trick|five-wicket|ten-wicket|half-century|"
    r"(?:scored|hits?|smashed|struck) (?:a|his|her) century|"
    r"double century|century stand|net run rate|run-chase|"
    r"playing XI|man of the (?:match|series)|player of the (?:match|series)|"
    r"stumped|Mankad|Duckworth|DRS|DLS|"
    r"football|soccer|FIFA|UEFA|AFC|Ballon d.Or|"
    r"Premier League|Champions League|Europa League|La Liga|Serie A|"
    r"Bundesliga|Ligue 1|Indian Super League|ISL|I-League|Santosh Trophy|"
    r"Messi|Ronaldo|Mbappe|Haaland|Neymar|Man City|Man United|"
    r"Real Madrid|Barcelona|Liverpool|Arsenal|Chelsea|Bayern|Juventus|"
    r"derby|striker|midfielder|goalkeeper|penalty shoot-?out|"
    r"free kick|corner kick|offside|hat ?trick|clean sheet|"
    r"hockey|kabaddi|badminton|Wimbledon|Grand Slam|"
    r"Olympics|Asian Games|Commonwealth Games|"
    r"NBA|NFL|Formula 1|Grand Prix|"
    r"gold medal|silver medal|bronze medal)\b",
    re.I)


# 20 Nov owner "some news are also posted twice". The old guard compared
# WORDS, so the same event written two ways slipped through: "Russian strikes
# damage two bridges in Kyiv" and "Three Kyiv bridges hit during major Russian
# missile attack" rode cards 1.2 hours apart. This compares the ACTORS and the
# EVENT instead, which is what makes two reports the same story.
_EV_GEN_CAP = set("""The A An And But For From Over Under After Before With Without
Into Amid India Indian News More Details Officials Official Police Govt Government
Minister Ministers Chief PM Hyderabad Telangana Reports Report Said Says Say Live
Watch Man Woman Former First Second Third Two Three Four Five Six Seven Eight Nine
Ten New Now Today Polls Poll Election Elections Local Body Bodies Court High Supreme
State Centre Central City District Village Town Road Day Year Years Nov Dec Jan Feb
Mar Apr May Jun Jul Aug Sep Oct Big Top Key Major Full Final Here What Why How When
Where Who Will Can Not No Yes Congress BJP BRS AAP DMK TDP YSRCP Shiv Sena NDA UPA
MLA MLAs MP MPs""".split())
# names that appear in the news every day: sharing one proves nothing
_EV_COMMON = set("""modi rahul gandhi amit shah revanth reddy naidu stalin mamata
kejriwal yogi trump putin biden netanyahu senate republicans democrats house white
police court judge medical college school hospital university student students
minister ministry department cyclone rain weather temple festival market crime
arrest raid probe case russia russian ukraine israel gaza iran china pakistan
america american united states kashmir delhi mumbai chennai kolkata bengaluru
andhra pradesh karnataka kerala""".split())
_EV_STOP = set("""the a an of in on at to for and or is are was were be been being by
with from as it its this that these those after before over under new news more said
says say will would can could has have had not no who whom which what when where why
how amid into out up down off than then them they their there here about against
between during through across first second ahead""".split())
_EV_CAP_RX = re.compile(r"\b[A-Z][A-Za-z'\u2019-]{3,}\b")
_EV_LOW_RX = re.compile(r"\b[a-z][a-z'-]{4,}\b")


def _event_actors(text):
    out = set()
    for w in _EV_CAP_RX.findall(text or ""):
        if w in _EV_GEN_CAP:
            continue
        out.add(w.lower().strip("\u2019's"))
    return out


def _event_words(text):
    return {w for w in _EV_LOW_RX.findall((text or "").lower())
            if w not in _EV_STOP}


def _same_event(t1, t2):
    """True when two headlines report the same event in different words."""
    a, b = _event_actors(t1), _event_actors(t2)
    if not a or not b:
        return False
    shared = a & b
    distinctive = shared - _EV_COMMON
    if len(distinctive) >= 2:
        return True          # two rare actors in common: same story
    if len(shared) >= 3:
        return True          # three actors in common: same story
    if len(shared) >= 2 and distinctive and (_event_words(t1) & _event_words(t2)):
        return True          # a rare actor plus the same event word
    return False


def _is_cricket(text):
    """True for cricket, football and other sport (owner 16 + 20 Nov)."""
    return bool(CRICKET_RE.search(text or ""))


_is_sport = _is_cricket  # the card calls it the sports slot


MAIN_CATS = {"election_poll", "political_major", "govt_announcement", "economic_major",
             "court_judgment", "protest_major", "police_operation",
             "disaster_weather", "international_conflict"}
MIN_GAP_HOURS = 0.33  # owner 26 Sep: breaking solos may run every ~20 min


def eligible(c):
    cls, score, pref = c["class"], c["score"], c["source_preferred"]
    age = c.get("age_hours") or 99
    if age > MAX_AGE_HOURS:
        return False  # stale news is refused outright
    # 16 Nov owner "any major incident post breaking news immediately": a war,
    # cyber or other big incident clears the class/score bar on its own. Before
    # this, a BBC/Al Jazeera war story (class C, score ~62, needs >=72) was
    # refused here and never reached the breaking test at all.
    if _is_war_cyber(c.get("title")) and score >= 50:
        return True
    if cls in ("A", "B"):
        return True
    if set(c.get("categories", [])) & MAIN_CATS and score >= 60:
        return True  # politics / main news: lower bar, still verified+fresh
    if cls == "C" and pref and score >= 72:
        return True
    return False


DAILY_REGULAR_LIMIT = 12    # owner 26 Sep: raised with 24/7 hourly mode
MONTHLY_EMERGENCY_LIMIT = 10  # (casualty cats stay banned - kept as guard)
DAILY_TOTAL_LIMIT = 30      # hard flood guard only
EMERG_CATS = {"fire_explosion", "accident_casualty", "major_crime"}


def load_quota():
    qp = os.path.join(STATE, "quota.json")
    if os.path.exists(qp):
        try:
            return json.load(open(qp, encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_quota(q):
    json.dump(q, open(os.path.join(STATE, "quota.json"), "w", encoding="utf-8"),
              indent=1)


def is_emergency(c):
    return (bool(set(c.get("categories", [])) & EMERG_CATS)
            and c.get("class") == "A" and (c.get("age_hours") or 99) <= 3)


def story_from(c, framed):
    now = datetime.now(IST)
    srcs = [c["source"]] + [x for x in c.get("corroboration", []) if x != c["source"]]
    return {
        "slug": "auto-" + slugify(c["title"]),
        "title": c["title"],
        "headline": HEADLINE_MAP[c["region"]],
        "topic": c["region"] + "-" + slugify(c["title"])[:40],
        "region": c["region"],
        "classification": c["class"],
        "timestamp": now.strftime("%d %b %Y, %I:%M %p IST"),
        "source": " / ".join(srcs),           # internal record only - never shown
        "source_urls": [c["url"]],
        "verified_by": srcs,
        "not_verified": [],
        "key_facts": framed["bullets_en"],
        "frame": framed["frame"],
        "image_rights": "branded graphic only; no third-party photograph used",
        "bullets_en": framed["bullets_en"],
        "bullets_ur": framed["bullets_ur"],
        "auto": True,
    }


def try_publish(story):
    """Returns publish.run exit code."""
    os.makedirs(STORIES, exist_ok=True)
    path = os.path.join(STORIES, story["slug"] + ".json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(story, f, indent=2, ensure_ascii=False)
    return publish.run(path, True), path


def retracted_slugs():
    """Slugs the owner retracted - they must never publish or re-queue."""
    rp = os.path.join(STATE, "retracted.json")
    if not os.path.exists(rp):
        return set()
    try:
        data = json.load(open(rp, encoding="utf-8"))
    except Exception:
        return set()
    out = set()
    for r in data if isinstance(data, list) else [data]:
        if isinstance(r, dict):
            for k in ("slug", "topic"):
                if r.get(k):
                    out.add(r[k])
        elif isinstance(r, str):
            out.add(r)
    return out


def retry_pending():
    """Once credentials exist, pending packages publish themselves."""
    pp = os.path.join(STATE, "pending_publish.json")
    if not os.path.exists(pp):
        return 0
    pend = json.load(open(pp, encoding="utf-8"))
    # 28 Sep: backlog older than 2h references an out-of-date pool - drop
    # it (loudly) instead of replaying stale/duplicate bullets onto the Page.
    _fresh = []
    for it in pend:
        try:
            _age = (datetime.now(IST) - datetime.fromisoformat(it["at"])).total_seconds() / 60
        except Exception:
            _age = 0
        if _age > 120:
            log(f"[pending] dropped stale backlog {it.get('story')} ({_age:.0f} min old)")
        else:
            _fresh.append(it)
    if len(_fresh) != len(pend):
        json.dump(_fresh, open(pp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    pend = _fresh
    if not pend:
        return 0
    cfg = json.load(open(os.path.join(BASE, "config.json"), encoding="utf-8"))
    pub = cfg.get("publish", {})
    pgc = cfg.setdefault("page", {})
    # 28 Sep: CI injects credentials via env (config.json keeps them blank)
    if os.environ.get("FB_PAGE_TOKEN"):
        pgc["page_access_token"] = os.environ["FB_PAGE_TOKEN"]
    if os.environ.get("FB_PAGE_ID", "").strip():
        pgc["facebook_page_id"] = os.environ["FB_PAGE_ID"].strip()
    connected = (pub.get("route") == "zapier" and pub.get("zapier_webhook")
                 and pub.get("zapier_live", False)) or (
        pgc.get("facebook_page_id") and pgc.get("page_access_token"))
    if not connected:
        return 0
    done = 0
    blocked = retracted_slugs()
    try:
        _pub = json.load(open(pub, encoding="utf-8")) if os.path.exists(pub) else []
    except Exception:
        _pub = []
    done_heads = []
    for r in _pub[-40:]:
        done_heads.append(r.get("title") or "")
        done_heads += list(r.get("bullets_en") or [])[:5]
    for item in list(pend):
        if done >= MAX_POSTS_PER_CYCLE:
            break
        sf = item.get("story_file")
        if not sf or not os.path.exists(sf):
            pend.remove(item)
            continue
        ids = {item.get("story")}
        try:
            sd = json.load(open(sf, encoding="utf-8"))
            ids |= {sd.get("slug"), sd.get("topic")}
        except Exception:
            pass
        if ids & blocked:
            log("pending item is owner-retracted, dropped forever:", item["story"])
            pend.remove(item)
            continue
        log("retrying pending story:", item["story"])
        rc = publish.run(sf, True)
        if rc == 0:
            pend.remove(item)
            done += 1
            time.sleep(POST_SPACING_SECONDS)
        elif rc == 3:  # duplicate of an already-published story: drop from queue
            log("pending item now duplicate, dropped:", item["story"])
            pend.remove(item)
        elif rc == 5:
            json.dump(pend, open(pp, "w", encoding="utf-8"), indent=2)
            return 5
    json.dump(pend, open(pp, "w", encoding="utf-8"), indent=2)
    return done



# ---- owner 26 Sep: casualty policy update ------------------------------------
# MAJOR accidents and famous-personality deaths may now be covered - but only
# with correct reporting: a named place, a verified number or a public figure,
# neutral wire tone (no "shocker/tragic" tabloid language), multi-outlet.
TABLOID_RX = re.compile(r"\b(shocker|shocking|bloodbath|horror|horrible|"
                        r"gruesome|gory|nightmare|brutal|barbaric|dread|"
                        r"tragic|tragically|twist|drama|sensational)\b", re.I)
FAMOUS_ROLE = re.compile(
    r"\b(actor|actress|star|hero|heroine|comedian|singer|musician|composer|"
    r"director|filmmaker|producer|writer|author|poet|playback|anchor|journalist|"
    r"cricketer|player|captain|coach|umpire|olympian|athlete|legend|veteran|"
    r"sir|dr|justice|judge|chief justice|minister|cabinet|mp|mla|mnc|"
    r"mayor|governor|president|pm|prime minister|chief minister|dy cm|"
    r"assembly|parliament|bjp|congress|tdp|ysrcp|aipmt?|actor-cum)\b", re.I)
FAMOUS_EVENT = re.compile(
    r"\b(dies|died|dead|killed|passed away|no more|expired|perished|"
    r"murdered|shot|targeted)\b", re.I)
DISASTER_RX = re.compile(
    r"\b(train|plane|aircraft|airliner|chopper|helicopter|ship|vessel|ferry|"
    r"boat|building|bridge|flyover|underbridge|mine|factory|mill|school|"
    r"hospital|theatre|cinema|hall)\b[^.]{0,30}\b"
    r"(crash|crashes|accident|collapse|collapsed|derailment|derailed|fire|"
    r"blast|bomb|stampede|sinking|sink|capsized|leak)\b|"
    r"\b(stampede|blast|bomb blast|building collapse|train derailment|"
    r"plane crash|air crash|gas leak)\b", re.I)
CASUALTY_NUM = re.compile(
    r"\b(\d{1,4})\b(?:\s*(?:people|persons?|of the))?"
    r"\s*(?:killed|dead|died|deaths|clipped)\b|"
    r"\b(?:killed|dead|died)\b[^.]{0,20}?\b(\d{1,4})\b")
CAS_INJURED = re.compile(r"\b(\d{1,4})\b\s*(?:people\s+)?injured\b")


def casualty_allowed(c):
    """Owner 26 Sep: major accidents / famous-personality deaths ride cards
    ONLY with correct reporting. Everything else stays banned, forever."""
    t = c["title"]
    if TABLOID_RX.search(t):
        return False  # tabloid wording = not correct reporting
    if FAMOUS_ROLE.search(t) and FAMOUS_EVENT.search(t):
        return "celeb"  # public figure casualty: no WHERE needed
    for m in CASUALTY_NUM.finditer(t):
        n = int(m.group(1) or m.group(2) or 0)
        if n >= 5:
            return "major"  # 5+ dead: must name a place
    for m in CAS_INJURED.finditer(t):
        if int(m.group(1)) >= 15:
            return "major"
    if DISASTER_RX.search(t) and re.search(
            r"\b(dead|died|killed|injured|missing|feared|rescue|rescuers|"
            r"survivors?|stranded|trapped|hospitalised|burnt|charred)\b",
            t, re.I):
        return "major"  # infrastructure disaster: must name a place
    return False


DIGEST_SLOTS = tuple(range(24))  # owner 26 Sep: hourly, 24/7, never stop
CARDS_PER_DAY = 24          # cap only guards flooding; cadence = gap_min >= 60
DIGEST_SIZE = 5   # owner 16 Sep: every card carries 5 important news items
# 26 Sep: + world.  16 Nov owner "lessen world news, only important world news
# like US, IRAN, ISREAL": "world" is NO LONGER a guaranteed slot. Every card used
# to spend 1 of its 5 bullets on world news whether or not anything important was
# happening abroad, which is exactly what the owner objected to. World stories now
# only ride the card when they outrank local/national news on merit, and the
# WORLD_MAJOR gate in monitor.score() only lets major-power stories (US, Iran,
# Israel, Russia, China, UK, EU, NATO, Pakistan, Japan, France, Germany, Saudi,
# Syria, Iraq, Korea, Afghanistan) reach that bar - plus a genuine mass-casualty
# incident abroad, which stays important by severity.
DIGEST_REGIONS = ("hyderabad", "telangana", "india")
# owner 16 Sep + REFINED 26 Sep: casualty news rides cards ONLY as MAJOR
# accidents (5+ dead / 15+ injured / train-plane-collapse-stampede class, with
# a named place) or famous-personality deaths - always neutral wire tone,
# numbers only from verified headline text. Petty/local incidents and any
# tabloid wording stay banned outright.
NO_CARD_CATS = {"accident_casualty", "fire_explosion", "major_crime"}
NO_CARD_RX = re.compile(r"\b(killed|murder|murdered|died|death|accident|crash|"
                        r"crashes|collision|drowned|suicide|rape|raped)\b", re.I)


TAIL_STOP = {"and", "or", "of", "to", "in", "for", "with", "as", "is", "at",
             "by", "on", "the", "a", "an", "from", "that", "which", "over",
             "after", "before", "against", "between", "into", "during",
             "despite", "unless", "until", "while", "though", "but", "so"}


def _tidy_lead(b):
    b = b.strip()
    if not b.endswith("."):
        b += "."
    words = b.rstrip(".").split(" ")
    while len(words) > 6 and words[-1].lower().strip(",;:") in TAIL_STOP:
        words = words[:-1]  # never end a headline on a dangling connector
    return " ".join(words) + "."


# 26 Sep: converter can only render structured headline shapes. For verified
# multi-word headlines with no safe translation frame, the APPROVED 25 Sep
# theme (card-2251 bullet 3) pairs the exact EN headline with a NEUTRAL
# generic Urdu line that asserts nothing beyond the category - it can never
# be wrong. Real converter output always wins; this is the last fallback.
GEN_UR = (("political_major", ("Siyasat se muttaliq ahem khabar hai.",
                               "Siyasi harkat se judi khabar hai.",
                               "Siyasi harkat ki taza khabar hai.",
                               "Siyasat ki is khabar par sab ki nazar hai.")),
          ("govt_announcement", ("Sarkar ki taraf se ahem eilan hai.",
                                 "Sarkari faislay se muttaliq khabar hai.")),
          ("economic_major", ("Maashiayat se muttaliq ahem khabar hai.",
                               "Karobari mahol se judi khabar hai.")),
          ("court_judgment", ("Adalat ke faislay se muttaliq khabar hai.",
                               "Qanooni karrawai se muttaliq khabar hai.")),
          ("protest_major", ("Ihtijaj se muttaliq khabar hai.",)),
          ("security_terror", ("Suraksha ka ahem mamla hai.",)),
          ("police_operation", ("Police ki karrawai se muttaliq khabar hai.",)),
          ("international_conflict", ("Duniya ki siyast se muttaliq khabar hai.",
                                      "Videshi mamlaat se judi khabar hai.")))
GEN_UR_REGION = {"hyderabad": ("Shehar ki ahem khabar hai.",
                               "Shehar ke liye ahem khabar hai."),
                 "telangana": ("Sooba ki ahem khabar hai.",
                               "Sooba ke liye ahem khabar hai."),
                 "india": ("Mulk bhar ki ahem khabar hai.",
                           "Mulk ke liye ahem khabar hai."),
                 "world": ("Duniya ki ahem khabar hai.",
                           "Duniya bhar se judi khabar hai.")}


_TAG_LINES = ({t for _, _ts in GEN_UR for t in _ts}
              | {t for _ts in GEN_UR_REGION.values() for t in _ts}
              | {"Ahem khabar hai."})


def _gentag(c):
    import hashlib
    cats = set(c.get("categories", []))
    opts = None
    for key, tags in GEN_UR:
        if key in cats:
            opts = tags
            break
    if opts is None:
        opts = GEN_UR_REGION.get(c.get("region"), ("Ahem khabar hai.",))
    h = int(hashlib.md5((c.get("url") or c["title"]).encode()).hexdigest(), 16)
    return opts[h % len(opts)]


def _lead_ok(b):
    if len(b) <= 95:
        return _tidy_lead(b)
    if "," in b:  # compress at the last comma, keep it a full sentence
        cut = b[:b.rindex(",")].rstrip()
        if len(cut) >= 40:
            return _tidy_lead(cut)
    return None



def _norm_bullet(text):
    """Punctuation-free lowercase key for a published bullet (exact-dup test)."""
    return " ".join(re.findall(r"[a-z0-9]+", (text or "").lower()))


def _same_story(t1, t2):
    """Cross-feed duplicates: same story, different headline wording."""
    a = {w for w in re.findall(r"[a-z0-9]{5,}", t1.lower())}
    b = {w for w in re.findall(r"[a-z0-9]{5,}", t2.lower())}
    if not a or not b:
        return False
    j = len(a & b) / len(a | b)
    long_shared = any(w in b for w in a if len(w) >= 9)
    if re.search(r"\b(red|orange|yellow|weather)\s+alert\b", t1, re.I) and \
       re.search(r"\b(red|orange|yellow|weather)\s+alert\b", t2, re.I) and \
       long_shared:
        return True  # same weather-alert event, different wording
    # owner 23/24 Sep: same story must never appear as two bullets - two
    # headlines sharing a distinctive name AND the same event verb are one story
    # (fixes "Xi arrives in US" + "Xi arrives in Washington" riding one card).
    C1 = set(re.findall(r"[A-Z][A-Za-z'\u2019\-]{1,}", t1))
    C2 = set(re.findall(r"[A-Z][A-Za-z'\u2019\-]{1,}", t2))
    GENERIC_CAP = {"India", "China", "US", "UK", "Pakistan", "Russia", "Ukraine",
                   "Israel", "Gaza", "Telangana", "Hyderabad", "Delhi", "Mumbai",
                   "Congress", "BJP", "TDP", "YSRCP", "BRS", "SP", "AAP", "Police",
                   "Court", "Supreme", "High", "Minister", "Government", "Centre",
                   "Assembly", "Parliament", "State", "City", "District", "Red",
                   "Orange", "Heavy", "Two", "Three", "Four", "Five", "One", "New",
                   "Top", "Big", "After", "Amid", "Odisha", "Bihar", "Kerala",
                   "Maharashtra", "Karnataka", "Andhra", "Pradesh"}
    ACTION = {"arrives", "arrived", "reaches", "reached", "wins", "won", "seizes",
              "seized", "arrests", "arrested", "resigns", "resigned", "signs",
              "signed", "launches", "launched", "visits", "visited", "meets",
              "met", "dies", "died", "announces", "announced", "appoints",
              "appointed", "sacks", "sacked", "protests", "protested", "marches",
              "marched", "closes", "closed", "opens", "opened", "suspends",
              "suspended", "kills", "killed", "holds", "held"}
    A1 = set(re.findall(r"[a-z]{5,}", t1.lower()))
    A2 = set(re.findall(r"[a-z]{5,}", t2.lower()))
    if (C1 & C2) - GENERIC_CAP and (A1 & A2 & ACTION):
        return True
    # owner 23 Sep "same news 2 bullets": two headlines about the SAME person
    # (shared 2-word full name) inside the same legal saga are one story even
    # when the verbs differ ("SC upholds X's disqualification" +
    # "BRS welcomes SC order on X, dares him...").
    LEGAL = ("court", "supreme", "judgment", "verdict", "order", "bench",
             "judge", "plea", "pleas", "disqualification", "hearing", "ruling")
    bg1 = set(map(str.lower, re.findall(
        r"\b[A-Z][a-z]{2,} [A-Z][a-z]{2,}\b", t1)))
    bg2 = set(map(str.lower, re.findall(
        r"\b[A-Z][a-z]{2,} [A-Z][a-z]{2,}\b", t2)))
    _GEN_BIG = {"high court", "supreme court", "new delhi", "central gov",
                "state gov", "andhra pradesh", "madhya pradesh", "chief min",
                "prime min", "press conf", "social med"}
    _distinct_bg = {b for b in (bg1 & bg2)
                    if b not in _GEN_BIG and
                    not all(w in {g.lower() for g in GENERIC_CAP}
                            for w in b.split())}
    if _distinct_bg and (A1 & set(LEGAL)) and (A2 & set(LEGAL)):
        return True
    g1 = t1.lower().split()[0] if t1.split() else ""
    g2 = t2.lower().split()[0] if t2.split() else ""
    GENERIC = {"govt", "government", "police", "centre", "center", "court", "sc",
               "supreme", "hc", "high", "imd", "eci", "ec", "union", "india", "pm",
               "cm", "minister", "officials", "reports", "watch", "rupee", "sensex"}
    if g1 != g2 and g1 not in GENERIC and g2 not in GENERIC:
        return j >= 0.60  # different leading actors: same story only if near-identical
    return j >= 0.40 or (long_shared and j >= 0.25)


def build_digest(cands, catchup=False):
    """One card, three full headlines: Hyderabad + Telangana + India.
    catchup (owner 25 Sep): after silence, older-but-clean stories may still
    ride a backlog card; quality gates are identical."""
    pub = os.path.join(STATE, "published.json")
    done = set()
    done_urls = set()
    done_bullets = set()
    if os.path.exists(pub):
        try:
            for r in json.load(open(pub, encoding="utf-8")):
                done.add(r.get("topic"))
                done |= set(r.get("story_topics", []))  # no story repeats across cards
                # 6 Oct (owner: "stop posting the same card again and again"):
                # the card record only ever stored ONE url - the FIRST story's -
                # and never stored story_topics at all, so a digest's other 3-4
                # stories had no persisted identity whatsoever and rode later
                # cards again. Proven on 26 Oct: "Supreme Court issued notice
                # against Andhra Pradesh." rode four different cards on 24 Oct,
                # each time as bullet #3/#4 behind a different lead story.
                for _u in [r.get("url")] + list(r.get("source_urls") or []):
                    if _u:
                        done_urls.add(_u.split("?")[0])
                # bullets_en IS stored for every card ever published, so this
                # also covers the 562 cards published before the identity
                # fields existed.
                for _b in (r.get("bullets_en") or []):
                    if _b:
                        done_bullets.add(_norm_bullet(_b))
        except Exception:
            pass
    blocked = retracted_slugs()
    try:
        _pub = json.load(open(pub, encoding="utf-8")) if os.path.exists(pub) else []
    except Exception:
        _pub = []
    # 6 Oct: the old "already rode a card" guard looked at only the last 40
    # cards. A story can be re-picked from a different feed with different
    # wording well outside that window, so index EVERY published bullet by its
    # long words and compare only against bullets that share one - cheap enough
    # to run over all history, thorough enough to catch a reworded re-run.
    done_heads = []
    for r in _pub:
        done_heads.append(r.get("title") or "")
        done_heads += list(r.get("bullets_en") or [])
    _LONG_WORD = re.compile(r"[a-z0-9]{9,}")
    # 6 Oct: a bullet with no 9+ letter word ("Cyclone Arnab closing in on
    # Andhra coast...") indexed nothing at all, so a REWORDED re-run of it
    # slipped past the fuzzy test. Every bullet is therefore also indexed by
    # its 7-8 letter words, and that second index is consulted only when the
    # first finds nothing - cheap in the common case, no blind spot left.
    _MID_WORD = re.compile(r"[a-z0-9]{7,8}")
    _bullets_by_word = {}
    _bullets_by_mid = {}
    for _b in done_bullets:
        for _w in set(_LONG_WORD.findall(_b)):
            _bullets_by_word.setdefault(_w, []).append(_b)
        for _w in set(_MID_WORD.findall(_b)):
            _bullets_by_mid.setdefault(_w, []).append(_b)

    # 20 Nov owner "some news are also posted twice": word-overlap misses the
    # same event told differently ("Russian strikes damage two bridges in
    # Kyiv" / "Three Kyiv bridges hit during major Russian missile attack",
    # 1.2 hours apart). Index the last 36 hours of cards and compare by actors
    # and event instead. The window keeps it cheap and stops a long-running
    # story from being banned for ever.
    _recent_heads = []
    try:
        _cut = datetime.now(IST) - timedelta(hours=36)
        for r in _pub[-120:]:
            try:
                _ts = datetime.fromisoformat(r.get("published_at") or "")
            except Exception:
                continue
            if _ts < _cut:
                continue
            if r.get("title"):
                _recent_heads.append(r["title"])
            _recent_heads += [b for b in (r.get("bullets_en") or []) if b]
    except Exception:
        _recent_heads = []

    def _rode_recently(title):
        """True when a card in the last 36h already carried this event."""
        return any(_same_event(title, h) for h in _recent_heads)

    def _already_published(lead):
        """True when this exact story already rode a card, however worded."""
        if _norm_bullet(lead) in done_bullets:
            return True
        _cand = set()
        for _w in set(_LONG_WORD.findall((lead or "").lower())):
            _cand.update(_bullets_by_word.get(_w, ()))
        if not _cand:
            for _w in set(_MID_WORD.findall((lead or "").lower())):
                _cand.update(_bullets_by_mid.get(_w, ()))
        return any(_same_story(lead, b) for b in _cand)
    today = datetime.now(IST).strftime("%Y-%m-%d")
    TAG = {"hyderabad": "Hyderabad ki khabar",
           "telangana": "Telangana ki khabar",
           "india": "India ki khabar",
           "world": "Duniya ki khabar"}
    pool = []
    _skip = {"stale": 0, "casualty": 0, "class": 0, "conv": 0,
             "qual": 0, "dup": 0, "fr": 0, "tag": 0, "mt": 0, "bank": 0}

    def _collect(minscore, maxage, minC):
            for c in cands:
                age = c.get("age_hours") or 99
                if age > maxage or (c.get("score") or 0) < minscore:
                    _skip["stale"] += 1
                    continue
                if any(_same_story(c["title"], dt) for dt in done_heads if dt):
                    _skip["dup"] += 1
                    continue  # 28 Sep: stories that rode a recent card must
                              # never re-enter the pool - they only came here
                              # to be net-dropped, starving picks to <3
                if _rode_recently(c["title"]):
                    _skip["dup"] += 1
                    continue  # 20 Nov: same event, different words
                # 16 Nov owner "cover all war news, cyber attacks etc, all big
                # incidents whichever section": a war / cyber / major-incident
                # story is never casualty-banned, whatever its category.
                # 16 Nov owner wants war news through this ban. 20 Nov owner
                # ("26 year old man found dead ???? news"): ONLY war, never
                # ordinary local deaths. Exempting every "big incident" here
                # re-opened the casualty ban the owner closed on 26 Sep.
                _ban = (bool(set(c.get("categories", [])) & NO_CARD_CATS)
                        or bool(NO_CARD_RX.search(c["title"]))) \
                       and not _is_war_cyber(c.get("title"))
                _cv = casualty_allowed(c) if _ban else False
                if _ban and not (_cv == "celeb"
                                 or (_cv == "major"
                                     and writer._has_loc(c["title"]))):
                    _skip["casualty"] += 1
                    continue  # 26 Sep: only MAJOR(+WHERE)/celebrity may pass
                _sc = c.get("score") or 0
                if c.get("class") not in ("A", "B") and \
                   not set(c.get("categories", [])) & MAIN_CATS and \
                   not (c.get("source_preferred") and _sc >= 65) and \
                   not _sc >= minC:
                    _skip["class"] += 1
                    continue  # 18 Sep: class C with score >=60 now rides cards
                fr = writer.build(c)
                if fr and len(fr["bullets_en"]) >= 3:
                    lead = _lead_ok(fr["bullets_en"][0])
                    lead_ur = _lead_ok(fr["bullets_ur"][0])
                    alt = writer.urdu_headline(c["title"])
                    if alt and len(alt) <= 118 and lead and \
                       not writer.bullet_quality(lead, alt):
                        lead_ur = alt          # converter-first: cleaner Urdu
                    elif lead_ur and len(lead_ur) > 118:
                        lead_ur = alt or lead_ur
                else:
                    _skip["fr"] += 1   # no frame: title + converter pair is enough
                    lead = _lead_ok(c["title"].strip())
                    lead_ur = writer.urdu_headline(c["title"]) if lead else None
                if not lead or not lead_ur or len(lead_ur) > 118:
                    t2 = re.sub(r"\s+[A-Z][a-z]+-wide\b", "", c["title"].strip())
                    t2 = re.sub(r",?\s+on\s+[A-Z][a-z]+\s+\d{1,2}"
                                r"(?:st|nd|th|rd)?(?:\s+to\s+\d{1,2}"
                                r"(?:st|nd|th|rd)?)?", "", t2).strip()
                    if t2 and t2 != c["title"].strip():
                        a2 = writer.urdu_headline(t2)
                        l2 = _lead_ok(t2)
                        if (a2 and len(a2) <= 95 and l2
                                and not writer.bullet_quality(l2, a2)):
                            lead, lead_ur = l2, a2  # date/modifier was the only overhang
                if not lead or not lead_ur or len(lead_ur) > 118:
                    if lead and len(lead) <= 95 and _ru_on():
                        lead_ur = ""    # roman_urdu step fills this after picks
                    else:
                        _skip["conv"] += 1
                        continue
                tw = {w.lower() for w in re.findall(r"[A-Za-z]{7,}", c["title"])}
                if not tw & {w.lower() for w in re.findall(r"[A-Za-z]{7,}", lead)}:
                    lead = _lead_ok(c["title"].strip())  # frame lost the substance
                    lead_ur = writer.urdu_headline(c["title"]) if lead else None
                    if (not lead_ur or len(lead_ur) > 118) and lead and fr:
                        # tier 3: keep the frame pair if its lead still shares substance
                        tw7 = {w.lower() for w in re.findall(r"[A-Za-z]{6,}", c["title"])}
                        fr7 = {w.lower() for w in re.findall(r"[A-Za-z]{6,}", fr["bullets_en"][0])}
                        if len(fr["bullets_en"][0]) >= 50 and len(tw7 & fr7) >= 2:
                            lead, lead_ur = fr["bullets_en"][0], fr["bullets_ur"][0]
                            lead, lead_ur = _lead_ok(lead), _lead_ok(lead_ur)
                    if not lead or not lead_ur or len(lead_ur) > 118:
                        if lead and len(lead) <= 95 and _ru_on():
                            lead_ur = ""    # roman_urdu fills after picks
                        else:
                            _skip["conv"] += 1
                            continue
                # 6 Oct: exact url match first (cheap, covers every story the
                # card recorded), then the lead-bullet match over all history.
                _u = (c.get("url") or "").split("?")[0]
                if _u and _u in done_urls:
                    _skip["dup"] += 1
                    continue
                if _already_published(lead):
                    _skip["dup"] += 1
                    continue
                topic = c["region"] + "-" + slugify(c["title"])[:40]
                if topic in done or topic in blocked:
                    continue
                if any(topic == x[3] for x in pool):
                    _skip["dup"] += 1
                    continue  # same story from another feed - one bullet only
                _q = [] if not lead_ur else writer.bullet_quality(lead, lead_ur)
                if _q:
                    alt = writer.urdu_headline(c["title"])  # converter repair before skip
                    if alt and len(alt) <= 118 and not writer.bullet_quality(lead, alt):
                        lead_ur = alt
                        _q = []
                if _q and _ru_on() and all(q.startswith("ur-") for q in _q):
                    lead_ur = ""    # 27 Sep owner: rules line failed quality
                    _q = []         # -> the translator gets its one chance
                if _q and set(_q) <= {"en-titlecase-garbage", "en-no-specifics"}:
                    # 25 Sep: feed writes Title Case; synthesize the SAME fact as
                    # a sentence-case lead, then re-run ALL gates on it
                    e2 = writer.en_headline(c["title"])
                    l2 = _lead_ok(e2) if e2 else None
                    if l2 and not writer.bullet_quality(l2, lead_ur):
                        lead = l2
                        _q = []
                if _q:
                    _skip["qual"] += 1
                    continue  # 17 Sep gate: no vague EN / English-fragment Urdu bullets
                pool.append((c, lead, lead_ur, topic))

    # never-silent stages (owner 23 Sep): the FULL pipeline runs per stage -
    # pool -> picks -> quality/cross-card nets - and a card ships the moment a
    # stage yields >= 3 clean bullets. Previously the nets could drop picks
    # below 3 AFTER the stage loop and the run went silent with pool=3.
    def _build_picks():
        def rank(x):  # ELECTION first (owner 16 Nov), then politics, then Class A,
                      # then FRESHEST, then score
            c = x[0]
            # 16 Nov owner "cover full up election": election and poll stories
            # lead the card. They used to lose every slot to a class-A accident
            # or disaster because classify() only ever awards class A to the
            # "hard" incident categories, so a pure election story could never
            # be class A however high it scored. Ranking them first fixes that
            # without touching the score or the gates.
            #
            # 26 Oct owner "set the cycle to hourly and breaking news": inside a
            # tier the freshest story now leads, so an hour-old item beats a
            # twenty-hour-old one and the card reads as breaking news rather
            # than a round-up of yesterday.
            try:
                age_bucket = round(min(float(c.get("age_hours") or 99), 48) / 6)
            except Exception:
                age_bucket = 8
            # 16 Nov owner "cover all war news, cyber attacks etc, all big
            # incidents": war / cyber first, then other big incidents, then
            # election, then politics, then Class A, freshest, score.
            return (0 if _is_war_cyber(c.get("title")) else 1,
                    0 if _is_big_incident(_cand_text(c)) else 1,
                    0 if _is_election(_cand_text(c)) else 1,
                    0 if _is_cricket(_cand_text(c)) else 1,
                    0 if "political_major" in c.get("categories", []) else 1,
                    0 if c["class"] == "A" else 1,
                    age_bucket, -(c.get("score") or 0))
        picks = []

        def _taken(x, cur):
            """A pick is refused if the card already carries this story -
            same url, same wording, or (20 Nov owner "some news are also
            posted twice") the same event written a different way."""
            if x in cur:
                return True
            for p in cur:
                if _same_story(x[0]["title"], p[0]["title"]):
                    return True
                if _same_event(x[0]["title"], p[0]["title"]):
                    return True
            return False

        def _usable(x):
            if not x[2] and _line_h(x[1]) in _miss_bank:
                _skip["bank"] += 1
                return False   # banked (recently failed RU) - next story tries
            return True

        def _election_full(cur):
            return (sum(1 for p in cur if _is_election(_cand_text(p[0])))
                    >= _ELECTION_PICK_CAP)

        def _take(pred, cur, limit=1):
            """Fill up to `limit` slots with the best stories matching pred."""
            n = 0
            for x in sorted(pool, key=rank):
                if len(cur) >= DIGEST_SIZE or n >= limit:
                    break
                if not pred(x) or not _usable(x):
                    continue
                if _is_election(_cand_text(x[0])) and _election_full(cur):
                    continue
                if not _taken(x, cur):
                    cur.append(x)
                    n += 1
            return n

        # 20 Nov owner: "KEEP LOCAL NEWS HYDERABAD, TELANGANA, FIRST AND THEN
        # INDIA AND SPORTS AND WORLD ONLY IMPORTANT NEWS". The card is built
        # in exactly that order, so the bullets read in that order too.
        #   1 Hyderabad   2 Telangana   3 India   4 Sports   5 World (important)
        # 21 Nov: a Hyderabad paper's wire copy of a Test match is tagged
        # region="hyderabad", so the local slots were swallowing cricket and
        # pushing real local news off the card. Sport rides the sports slot.
        def _sportish(x):
            return ("sports" in (x[0].get("categories") or [])
                    or _is_sport(_cand_text(x[0])))

        _take(lambda x: x[0]["region"] == "hyderabad" and not _sportish(x),
              picks)
        _take(lambda x: x[0]["region"] == "telangana" and not _sportish(x),
              picks)
        _take(lambda x: x[0]["region"] == "india" and not _sportish(x), picks)
        # sports slot: cricket or football, whichever is the better story.
        # Trust monitor's "sports" category as well as the word list here, so
        # a match report the word list words differently ("Jaiswal's 85,
        # Rahul's unbeaten fifty") still reaches the slot the owner asked for.
        # prefer a match report that brings a photo with it - the owner asked
        # for pictures on every card, and sport is the slot most likely to
        # have one going spare.
        def _has_img(x):
            return isinstance(x[0].get("image"), str) \
                and bool(x[0].get("image", "").strip())

        if not _take(lambda x: _sportish(x) and _has_img(x), picks):
            _take(_sportish, picks)
        # world slot: ONLY important world news - war, cyber or a big incident.
        # Minor world news still never rides the card.
        _take(lambda x: (x[0]["region"] == "world"
                         and (_is_war_cyber(x[0].get("title"))
                              or _is_big_incident(_cand_text(x[0])))), picks)
        # any war story that is not tagged world (Manipur, Kashmir) still
        # belongs on the card - it is the news the owner asked for on 16 Nov
        _take(lambda x: _is_war_cyber(x[0].get("title")), picks)
        # fill the rest local-first so the card never runs short
        for _reg in ("hyderabad", "telangana", "india"):
            _take(lambda x, r=_reg: x[0]["region"] == r and not _sportish(x),
                  picks, limit=DIGEST_SIZE)
        _take(lambda x: True, picks, limit=DIGEST_SIZE)
        if len(picks) < DIGEST_SIZE:  # fallback: full headline bullets with Urdu tags
            def rank2(c):
                return (0 if "political_major" in c.get("categories", []) else 1,
                        0 if c["class"] == "A" else 1, -(c.get("score") or 0))
            for c in sorted(cands, key=rank2):
                if len(picks) >= DIGEST_SIZE:
                    break
                age = c.get("age_hours") or 99
                if age > 12 or (c.get("score") or 0) < 55:
                    continue
                if set(c.get("categories", [])) & NO_CARD_CATS:
                    continue
                if NO_CARD_RX.search(c["title"]):
                    continue
                if re.search(r"\b(must|should)\s+[a-z]", c["title"]) and not \
                   re.search(r"\b(says?|said|urges?|urgest?|demands?|calls?|warns?)\b",
                             c["title"], re.I):
                    continue
                if any(_same_story(c["title"], dt) for dt in done_heads if dt):
                    continue  # already rode an earlier card
                _sc2 = c.get("score") or 0
                if c.get("class") not in ("A", "B") and \
                   not set(c.get("categories", [])) & MAIN_CATS and \
                   not (c.get("source_preferred") and _sc2 >= 65) and \
                   not _sc2 >= 60:
                    continue
                topic = c["region"] + "-" + slugify(c["title"])[:40]
                if topic in done or topic in blocked:
                    continue
                lead = _lead_ok(c["title"].strip())
                if not lead:
                    continue
                if any(lead == x[1] for x in picks) or \
                   any(topic == x[3] for x in picks) or \
                   any(_same_story(c["title"], x[0]["title"]) for x in picks):
                    continue
                ur_line = writer.urdu_headline(c["title"])
                if not ur_line or len(ur_line) > 118:
                    if lead and len(lead) <= 95 and _ru_on():
                        if _line_h(lead) in _miss_bank:
                            _skip["bank"] += 1
                            continue  # 1 Oct: banked at selection time too
                        ur_line = ""    # roman_urdu fills after picks
                    else:
                        continue  # never publish a confusing Urdu mirror
                if ur_line and writer.bullet_quality(lead, ur_line):
                    _qb = writer.bullet_quality(lead, ur_line)
                    if _ru_on() and all(q.startswith("ur-") for q in _qb):
                        ur_line = ""  # 27 Sep: failed rules line -> LLM turn
                    else:
                        continue  # 17 Sep gate: rules-quality Urdu only
                picks.append((c, lead, ur_line, topic))
        # (owner 26 Sep verdict: rules-only Urdu - no MT, no filler)
        # final safety nets: unsafe pairs AND cross-card repeats (23 Sep)
        _pre = list(picks)
        def _pair_ok(p):
            return (not p[2]) or not writer.bullet_quality(p[1], p[2])
        picks = [p for p in picks if _pair_ok(p)]
        for p in _pre:
            if p not in picks:
                log(f"[digest] net-dropped(qual): {p[1][:55]} :: {p[2][:45]}")
        _pre = list(picks)
        picks = [p for p in picks
                 if not any(_same_story(p[0]["title"], dt) for dt in done_heads if dt)]
        for p in _pre:
            if p not in picks:
                log(f"[digest] net-dropped(repeat): {p[1][:55]}")
        return picks

    picks = []
    # 1 Oct fix: the miss bank is now consulted DURING selection (below),
    # not after. Previously the same top-ranked repeat-echo lines were
    # picked every cycle and only pruned AFTER _build_picks, so the run
    # starved at <3 while dozens of clean stories sat one rank lower.
    _miss_bank = _ru_miss_load()
    # 1 Oct (2nd outage same day): when the PRIMARY translator model has a
    # multi-hour echo fit, every cycle died 0/N accepted and only salvage
    # (4 attempts/line) fought it. Now a zero-accept cycle ROTATES the whole
    # batch call to the next model in the chain - one model's bad hour can
    # never freeze the page. Gates unchanged; only the voice asking changes.
    _ROT_FILE = os.path.join(STATE, "ru_rot.json")
    try:
        _rot = int(json.load(open(_ROT_FILE)).get("i", 0) or 0)
    except Exception:
        _rot = 0
    _stages = [(55, 12, 60), (50, 16, 55), (45, 20, 50),
               (45, 26, 50)]  # owner 27 Sep: every run finishes at the wide
                              # 26h window - nothing eligible left = wait, not
                              # a narrower filter
    for _stage, (_ms, _ma, _mc) in enumerate(_stages):
        _collect(_ms, _ma, _mc)
        log(f"[digest] stage-{_stage}: pool={len(pool)} skips={_skip}")
        picks = _build_picks()
        if len(picks) >= 3:
            if _stage:
                log(f"[digest] stage-{_stage} relaxation used (pool={len(pool)})")
            break
    # ---- owner's Roman Urdu translation step (26 Sep) -------------------
    # ONE roman_urdu call fills Urdu for every bullet the rules could not
    # render (rule Urdu is never re-translated). A line failing the module's
    # validators - or our pair gates - DEMOTES its story; if fewer than 3
    # publishable bullets remain the run WAITS silently ("no important news
    # => don't post"; filler is permanently banned).
    # 28 Sep (outage round 3, the REAL starvation cause): the engine
    # returned ok=True with zero problems, yet 3/5 lines died on MY gates:
    #   * digits - Roman Urdu legitimately SPELLED OUT English numerals
    #     ("Rs 5 Lakh Crore" -> "paanch lakh crore"). Requiring an exact
    #     digit-set match is wrong; only digits that APPEARED in Urdu but
    #     never in English (fabricated facts) may veto.
    #   * en-quality - bullet_quality() is the news-safety gate (casualty
    #     policy etc.) and is applied to every pick up front; re-running it
    #     on the Urdu side of a full, engine-validated translation only ever
    #     re-flags the story itself. Stories that passed the pre-gate are
    #     safe; the Urdu mirror is judged on its own form (paste/script/len).
    _NW = {"zero":"0","ek":"1","do":"2","teen":"3","char":"4","paanch":"5",
           "panch":"5","cheh":"6","che":"6","saat":"7","aath":"8","nau":"9",
           "das":"10","gyarah":"11","barah":"12","pandrah":"15","solah":"16",
           "bees":"20","teis":"23","chaalis":"40","chalis":"40","saat sau":"700",
           "assi":"80","navbe":"90","sau":"100","dedh":"150","hazaar":"1000",
           "ek lakh":"100000","dhai lakh":"250000","paanch lakh":"500000",
           "ek crore":"10000000","dhai crore":"25000000","panch arab":"5000000000"}
    def _en_digits(t):
        out = set(re.findall(r"\d+(?:[.,]\d+)*", t))
        tl = " " + t.lower() + " "
        for w, v in _NW.items():
            if f" {w} " in tl:
                out.add(v)
        return out
    if _miss_bank:
        log(f"[digest] ru-miss bank active: {len(_miss_bank)} line(s) "
            f"held back at selection (next stories took their slots)")
    _pend = [i for i, p in enumerate(picks) if not p[2]]
    _RU_STATS.clear()
    _RU_STATS.update({"pre_ru_picks": len(picks), "to_translate": len(_pend)})
    _kill = {}
    def _killed(i, why):
        _kill.setdefault(why, []).append(i + 1)
    if _pend and _ru_on():
        _am = [x for x in _ru_attempts() if x[0] != "primary"]
        if _am:
            # 6 Oct: prefer the first LIVE lane. The rotation index only
            # exists to stop us hammering a lane that just failed - it must
            # never outrank a lane that is answering. Gemini leads the chain,
            # so a normal cycle lands on it; the index still rotates when the
            # head of the chain is parked by _ru_lane_failed().
            _head = _am[0]
            if 0 < _rot and not _ru_lane_alive(_head[0]):
                _lbl2, _kw2 = _am[(_rot - 1) % len(_am)]
                if _ru_lane_alive(_lbl2):
                    _head = (_lbl2, _kw2)
            # chosen lane first, every other live lane behind it as a retry
            _order = [_head] + [x for x in _am if x[0] != _head[0]]
        else:
            _order = []
        _bad = set(_pend)
        _RU_STATS["kills"] = {}
        # 6 Oct: retry the BATCH on a fresh live lane instead of starving.
        # Observed 26 Oct: the head lane (gemini-3.8-flash) echoed all five
        # lines; the per-line salvage pass then rescued only 2 of the remaining
        # 3 through gemini-3.5-flash, so the card missed the 3-line gate by one
        # and the hour was lost. One fresh batch beats four private re-asks.
        # _res is read after this loop; if no lane is live the loop never
        # runs, and an unbound _res would take the WHOLE digest down - no
        # card at all, which is worse than a short one.
        _res = {}
        for _try, (_lbl, _kw) in enumerate(_order[:_RU_BATCH_TRIES]):
            _need = [i for i in _pend if i in _bad]
            if not _need:
                break
            if not _ru_spend(force=True, free=_ru_is_free(_kw)):
                if _try == 0:
                    _RU_STATS["engine"] = {
                        "ok": False, "lines_in": 0, "lines_want": len(_pend),
                        "method": "budget",
                        "problems": "daily translator HARD budget reached"}
                break
            _res = roman_urdu.translate_lines(
                [picks[i][1] for i in _need], **_kw)
            # 3 Oct: if this lane answered 429/401/402, park it so the salvage
            # loop below does not spend the rest of the daily quota on it.
            if _ru_lane_failed(_lbl, _res.get("problems")):
                log(f"[digest] lane '{_lbl}' hard-failed "
                    f"(rate limit/no credits) - parked for 30 min")
            _cand = _res.get("lines") or []
            if not _ru_translated(_res):
                # dictionary passthrough, not a translation - take nothing
                # from this lane and let the next one answer
                if _try == 0:
                    _RU_STATS["engine"] = {
                        "ok": bool(_res.get("ok")),
                        "lines_in": len(_cand), "lines_want": len(_pend),
                        "method": str(_res.get("method") or "?")[:16],
                        "problems": str(_res.get("problems") or [])[:160]}
                    _RU_STATS["kills"] = {"passthrough-refused": "all"}
                log(f"[digest] lane '{_lbl}' returned "
                    f"method={_res.get('method')!r} (no model answered) - "
                    f"refused, not published")
                continue
            if _try == 0:
                _RU_STATS["engine"] = {
                    "ok": bool(_res.get("ok")),
                    "lines_in": len(_cand), "lines_want": len(_pend),
                    "method": str(_res.get("method") or "?")[:16],
                    "problems": str(_res.get("problems") or [])[:160]}
            if len(_cand) != len(_need):
                if _try == 0:
                    _RU_STATS["kills"] = {"count-mismatch": "all"}
                continue
            _got = 0
            for _k, i in enumerate(_need):
                _l = (_cand[_k] or "").strip()
                _en = picks[i][1]
                if not _l:
                    _killed(i, "empty"); continue
                if len(_l) > 118:
                    _killed(i, "len>118"); continue
                if _NONROMAN_RE.search(_l):
                    _killed(i, "urdu-script"); continue
                _ed = set(re.findall(r"\d+(?:[.,]\d+)*", _en))
                _ud = set(re.findall(r"\d+(?:[.,]\d+)*", _l))
                if _ud - _ed - _en_digits(_en):
                    _killed(i, "digits"); continue  # fabricated numbers only
                # en-quality is intentionally NOT re-applied here (see note above)
                if _real_paste(_en, _l):
                    _killed(i, "paste"); continue  # untranslated lowercase run
                if _echo_paste(_en, _l):
                    _killed(i, "english-echo"); continue  # verbatim/wholesale
                picks[i] = picks[i][:2] + (_l,) + picks[i][3:]
                _bad.discard(i)
                _got += 1
            if _got:
                log(f"[digest] lane '{_lbl}' batch: {_got}/{len(_need)} "
                    f"accepted ({len(_pend) - len(_bad)}/{len(_pend)} total)")
            if len(_pend) - len(_bad) >= _RU_CARD_MIN:
                break
        _RU_STATS["kills"] = _RU_STATS.get("kills") or _kill
        _RU_STATS["accepted"] = len(_pend) - len(_bad)
        # 28 Sep salvage pass, WIDENED 29 Sep: every killed line (not just a
        # starving card) gets ONE private re-ask - a single-line prompt
        # translates where the batch echo'd. Capped at 4 lines/cycle: the API
        # is still spent only on lines that failed quality (owner 27 Sep).
        if _bad:
            _sal_t0 = time.time()
            for i in sorted(_bad)[:4]:
                if time.time() - _sal_t0 > 360:
                    log("[digest] salvage budget (6 min) reached - "
                        "remaining killed lines wait for the next cycle")
                    break
                _en2 = picks[i][1]
                if not _en2:
                    continue
                for _lbl2, _kw2 in _ru_attempts():
                    if not _ru_spend(free=_ru_is_free(_kw2)):
                        log("[digest] translator daily call budget reached - "
                            "remaining salvage waits for the IST midnight reset")
                        break
                    try:
                        _r2 = roman_urdu.translate_lines([_en2], **_kw2)
                    except Exception:
                        continue
                    # 3 Oct: a 429/401/402 here means this lane is dead, not
                    # that the line is bad - park it and stop burning quota.
                    if _ru_lane_failed(_lbl2, _r2.get("problems")):
                        log(f"[digest] salvage lane '{_lbl2}' hard-failed "
                            f"- parked for 30 min")
                        continue
                    if not _ru_translated(_r2):
                        continue        # dictionary passthrough - refuse
                    _l2 = ((_r2.get("lines") or [""])[0] or "").strip()
                    if (not _l2 or len(_l2) > 118 or _NONROMAN_RE.search(_l2)
                            or _real_paste(_en2, _l2)
                            or _echo_paste(_en2, _l2)):
                        continue
                    _ed2 = set(re.findall(r"\d+(?:[.,]\d+)*", _en2))
                    _ud2 = set(re.findall(r"\d+(?:[.,]\d+)*", _l2))
                    if _ud2 - _ed2 - _en_digits(_en2):
                        continue
                    picks[i] = picks[i][:2] + (_l2,) + picks[i][3:]
                    _bad.discard(i)
                    _RU_STATS.setdefault("salvaged", []).append(i + 1)
                    _RU_STATS.setdefault("via", {})[str(i + 1)] = _lbl2
                    break
            _RU_STATS["accepted"] = len(_pend) - len(_bad)
        log(f"[digest] roman-urdu step: {len(_pend) - len(_bad)}/"
            f"{len(_pend)} lines accepted"
            + ("" if not _res.get("problems") else
               " (watch: " + str(_res["problems"][0])[:60] + ")"))
        if _bad:
            _wipe = len(_bad) == len(_pend)  # nothing accepted at all
            _ts = (time.time() - max(0, _RU_MISS_TTL - 1500)) if _wipe else time.time()
            for i in _bad:
                _miss_bank[_line_h(picks[i][1])] = _ts
            if _wipe:
                log(f"[digest] wipeout cycle: {len(_bad)} lines banked with "
                    f"25-min fuse (API/echo storm) instead of 4h")
            _ru_miss_save(_miss_bank)
            picks = [p for j, p in enumerate(picks) if j not in _bad]
        try:
            _acc = _RU_STATS.get("accepted", 0)
            _new = 0 if _acc > 0 else (_rot + 1)
            if _new != _rot:
                json.dump({"i": _new, "at": time.time()}, open(_ROT_FILE, "w"))
                if _new:
                    log("[digest] RU engine rotation -> model #%d "
                        "(zero-accept cycle; next try: %s)"
                        % (_new, _RU_FALLBACK_MODELS[(_new - 1) % len(_RU_FALLBACK_MODELS)]))
        except Exception:
            pass
    picks = [p for p in picks if p[2]]   # never ship an empty Urdu mirror
    if len(picks) < 3:
        log("[digest] wait: <3 stories with publishable Urdu this cycle")
        return None
    now = datetime.now(IST)
    return {
        "slug": f"auto-digest-{today}-s{now.strftime('%H%M')}",
        "topic": f"digest-{today}-{now.strftime('%H%M')}",
        "title": "Daily digest: " + " | ".join(p[0]["title"][:40] for p in picks),
        "headline": "📰 LATEST NEWS",
        "region": "india", "classification": "B",
        "timestamp": now.strftime("%d %b %Y, %I:%M %p IST"),
        "source": " / ".join(dict.fromkeys(p[0]["source"] for p in picks)),
        "source_urls": [p[0]["url"] for p in picks],
        "verified_by": "automated pipeline (multi-feed cross-check)",
        "key_facts": [],
        "story_topics": [p[3] for p in picks],
        "bullets_en": [p[1] for p in picks],
        "bullets_ur": [p[2] for p in picks],
        # 6 Oct (owner: "add photos to the card"): the card now carries a news
        # photo. The digest picks 4-5 stories; take the artwork from the first
        # pick that actually has some, so the picture is always a story on the
        # card. Selection itself is untouched - this only reads what the feed
        # already published.
        "image_url": next((p[0].get("image") for p in picks
                           if isinstance(p[0].get("image"), str)
                           and p[0].get("image", "").strip()), ""),
        # 20 Nov owner "now you are not adding photos": only ONE url was ever
        # offered to the renderer, so if that one download failed (CDN hotlink
        # block, 403, timeout) the card went out with no picture at all. Every
        # story on the card now offers its artwork and the first that actually
        # downloads is used.
        "image_urls": [p[0].get("image") for p in picks
                       if isinstance(p[0].get("image"), str)
                       and p[0].get("image", "").strip()],
    }


def sync_published():
    """Pull every card any other run has committed, before this cycle decides.

    WHY (owner 26 Oct "stop posting the same card again and again"): a relay run
    checks the repo out ONCE and then runs six cycles over an hour. If a cycle
    posts nothing, no commit happens, so the working copy keeps the
    published.json it was born with - and a card posted by the owner's manual
    push in the meantime is invisible to it. Proven on 26 Oct: the 19:56 IST
    card was committed at 15:00Z, yet the relay's 15:12Z cycle (checked out at
    14:34Z) re-published its lead story.

    So every cycle re-reads published.json from origin and unions it with the
    local copy. Never loses a card, never blocks on a failure - a fetch that
    fails just leaves the local copy alone.
    """
    path = os.path.join(STATE, "published.json")
    if not os.path.exists(path):
        return
    try:
        import subprocess
        subprocess.run(["git", "fetch", "--quiet", "origin", "HEAD"],
                       capture_output=True, timeout=90)
        r = subprocess.run(["git", "show", "origin/HEAD:news_agent/state/published.json"],
                           capture_output=True, text=True, timeout=90)
        if r.returncode != 0 or not r.stdout.strip():
            return
        remote = json.loads(r.stdout)
        if not isinstance(remote, list):
            return
        local = json.load(open(path, encoding="utf-8"))
        if not isinstance(local, list):
            return

        def _key(x):
            return x.get("fb_post_id") or (x.get("published_at"), x.get("title"))

        seen, out = set(), []
        for rec in list(local) + list(remote):
            k = _key(rec)
            if k in seen:
                continue
            seen.add(k)
            out.append(rec)
        try:
            out.sort(key=lambda x: x.get("published_at") or "")
        except Exception:
            pass
        if len(out) > len(local):
            json.dump(out, open(path, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
            log(f"[sync] published.json {len(local)} -> {len(out)} "
                f"(+{len(out) - len(local)} from other runs)")
    except Exception as _e:
        log(f"[sync] skipped: {type(_e).__name__} {_e}")


def _origin_json(rel):
    """A state file exactly as it exists on origin, or None."""
    import subprocess
    try:
        subprocess.run(["git", "fetch", "--quiet", "origin", "HEAD"],
                       capture_output=True, timeout=90)
        r = subprocess.run(["git", "show", "origin/HEAD:" + rel],
                           capture_output=True, text=True, timeout=90)
        if r.returncode != 0 or not r.stdout.strip():
            return None
        return json.loads(r.stdout)
    except Exception:
        return None


def _drop_repeats(dg):
    """Return dg with any bullet that already rode a card removed, or None.

    Owner 26 Oct: "in the cards you are repeating 1-3 news ans 2 new news".
    Every bullet - not just the lead - has to be a story no card has carried.
    This re-reads the history from origin (so another run's just-committed card
    counts) and prunes until at least three clean bullets remain.
    """
    if not isinstance(dg, dict):
        return None
    remote = _origin_json("news_agent/state/published.json")
    if not isinstance(remote, list):
        return dg
    try:
        local = json.load(open(os.path.join(STATE, "published.json"),
                               encoding="utf-8"))
        if isinstance(local, list):
            have = {json.dumps(r, sort_keys=True) for r in remote}
            remote = remote + [r for r in local
                               if json.dumps(r, sort_keys=True) not in have]
    except Exception:
        pass

    seen_b, seen_u = set(), set()
    for rec in remote:
        for b in (rec.get("bullets_en") or []):
            if b:
                seen_b.add(_norm_bullet(b))
        for u in [rec.get("url")] + list(rec.get("source_urls") or []):
            if u:
                seen_u.add(u.split("?")[0])

    keep_en, keep_ur, keep_src = [], [], []
    for en, ur, src in zip(dg.get("bullets_en") or [],
                           dg.get("bullets_ur") or [],
                           dg.get("source_urls") or []):
        u = (src or "").split("?")[0]
        if u and u in seen_u:
            log(f"[drop] url already published: {u[:70]}")
            continue
        if _norm_bullet(en) in seen_b:
            log(f"[drop] bullet already published: {en[:70]}")
            continue
        keep_en.append(en)
        keep_ur.append(ur)
        keep_src.append(src)

    if len(keep_en) < 3:
        return None
    out = dict(dg)
    out["bullets_en"] = keep_en
    out["bullets_ur"] = keep_ur[:len(keep_en)]
    out["source_urls"] = keep_src
    out["story_topics"] = list(keep_src)
    log(f"[drop] kept {len(keep_en)}/{len(dg.get('bullets_en') or [])} "
        f"bullets that no card has carried")
    return out


def _cadence_from_published(today):
    """(cards published today, minutes since the last one) from published.json.

    26 Oct owner "set the cycle to hourly" + "stop posting you posted so many
    cards": quota.json is written by whichever run finishes last, so two runs in
    flight silently clobber each other. At 22:30 it claimed 17 cards and a last
    card at 19:32 while 31 cards had actually shipped - so the gate read a
    three-hour gap and stayed wide open. published.json is the record of what
    really went on the Page and is committed reliably, so it is the only honest
    source for the cadence.
    """
    try:
        rows = json.load(open(os.path.join(STATE, "published.json"),
                              encoding="utf-8"))
    except Exception:
        return 0, 9999.0
    if not isinstance(rows, list):
        return 0, 9999.0
    stamps = [r.get("published_at") or "" for r in rows]
    stamps = [a for a in stamps if a[:10] == today]
    gap = 9999.0
    if stamps:
        try:
            gap = (datetime.now(IST) -
                   datetime.fromisoformat(max(stamps))).total_seconds() / 60
        except Exception:
            gap = 9999.0
    return len(stamps), gap


def _another_run_just_posted(minutes=12):
    """True when ANOTHER run committed a card in the last `minutes`.

    WHY (owner 26 Oct "stop posting you posted so many cards"): the hourly relay
    and the owner's manual push use different concurrency groups, so both were
    in flight at once. Each read the same quota, each picked the same fresh
    stories, and each published them - the 22:20 and 22:23 cards shared three
    stories, all under the same source url. Re-reading the quota from origin
    immediately before publishing closes that window: if the other run already
    landed a card, this one stands down.
    """
    q = _origin_json("news_agent/state/quota.json")
    if not isinstance(q, dict):
        return False
    at = q.get("last_card_at")
    if not at:
        return False
    try:
        gap = (datetime.now(IST) -
               datetime.fromisoformat(at)).total_seconds() / 60
    except Exception:
        return False
    return 0 <= gap < minutes


def one_cycle():
    sync_published()
    cfg = json.load(open(os.path.join(BASE, "config.json"), encoding="utf-8"))
    log("=== cycle start ===")
    _cu = os.environ.get("DIGEST_CATCHUP") == "1"
    # owner 27 Sep: full breadth every run. 20 Nov: raised 120 -> 170 because
    # adding sport to the candidate pool was pushing publishable stories out
    # of a fixed 120 and the digest came up short. The cap only limits how
    # many candidates are considered; selection and all quality gates are
    # unchanged.
    cyc = monitor.run_cycle(26 if _cu else cfg["rules"]["max_post_age_hours"],
                            170)

    def _prio(c):  # owner 16 Nov "cover full up election": election & poll
                   # coverage first, then politics & main news, then by score
        elec = _is_election(_cand_text(c))
        main = bool(set(c.get("categories", [])) & MAIN_CATS) or c.get("class") == "A"
        return (0 if elec else 1, 0 if main else 1, -(c.get("score") or 0))
    cyc["candidates"].sort(key=_prio)
    if cyc["errors"] and cyc["feeds_ok"] == 0:
        alert("All monitored feeds failed", "\n".join(cyc["errors"]))
        raise SystemExit(9)

    posted = retry_pending()
    q0 = load_quota()
    today0 = datetime.now(IST).strftime("%Y-%m-%d")
    if q0.get("day") != today0:
        q0 = {"day": today0, "regular": 0, "day_total": 0,
              "emerg_month": q0.get("emerg_month", {})}
    # 26 Oct: the cadence comes from published.json, not from quota.json -
    # see _cadence_from_published for why the quota was lying.
    ncards, gap_min = _cadence_from_published(today0)
    # owner 25 Sep catch-up: a manual dispatch with catchup=1 (or ANY silence
    # past 3h inside the window) drains the backlog at 30-min spacing until the
    # pool is exhausted; the normal 2h slot rhythm resumes by itself.
    catchup = (os.environ.get("DIGEST_CATCHUP") == "1"
               or gap_min >= 150)  # owner 26 Sep: no quiet hours any more
    if catchup:
        log(f"[catchup] armed: {gap_min:.0f} min since last card")
    cap = CARDS_PER_DAY + (6 if catchup else 0)
    _slot = DIGEST_SLOTS[min(ncards, len(DIGEST_SLOTS) - 1)]
    # 28 Sep ~15:45 owner: "pls now post hourly" - catch-up draining is
    # over; steady hourly card rhythm from here, any hour of the day.
    # (55 so the 10-min cycle lands each hour without skipping a slot.)
    # 26 Oct owner: "set the cycle to hourly" and "stop posting you posted so
    # many cards". 31 cards went out on 26 Oct because DIGEST_FORCE=1 let a
    # manual push publish on top of the relay's own card. The 55-minute floor is
    # now the cadence and NOTHING overrides it - FORCE only means "this slot is
    # allowed to fire", never "fire sooner than hourly".
    _due = gap_min >= 55
    global _DIGEST_NOTE
    _collide = _another_run_just_posted(12)
    if not (ncards < cap and _due and posted != 5) or _collide:
        _DIGEST_NOTE = (f"gate closed: ncards {ncards}/{cap}, gap {gap_min:.0f} min, "
                        f"retry_pending={posted}"
                        + (", another run posted <12 min ago" if _collide else ""))
    if (ncards < cap and _due and posted != 5 and not _collide):
        try:
            dg = build_digest(cyc["candidates"], catchup=catchup)
        except Exception as ex:
            import traceback
            log("DIGEST BUILD CRASH:", ex, traceback.format_exc()[-400:])
            dg = None
        _DIGEST_NOTE = "starved: <3 publishable Urdu lines"
        if dg:
            _DIGEST_NOTE = "ready"
            log("DIGEST ready: 3 fresh stories, one card")
            # 26 Oct owner "dont repest the news in the cards": the pool loop
            # checks each story as it is picked, but by the time the card is
            # ready another run may have published one of them. Re-pull the
            # history and drop any bullet that already rode a card, so a card
            # only ever ships stories nobody has seen.
            dg = _drop_repeats(dg)
            if dg is None:
                _DIGEST_NOTE = ("starved: every pick had already ridden a card "
                                "on another run")
                log("DIGEST dropped: all picks already published elsewhere")
            else:
                rc, _ = try_publish(dg)
            if rc == 0:
                q0["digests"] = {today0: ncards + 1}
                q0["last_card_at"] = datetime.now(IST).isoformat()
                q0["day_total"] = q0.get("day_total", 0) + 1
                save_quota(q0)
                _DIGEST_NOTE = f"PUBLISHED {dg['slug']}"
                q0["refuse_streak"] = 0
                log("published daily card:", dg["slug"])
                time.sleep(POST_SPACING_SECONDS)
            elif rc == 3:
                _DIGEST_NOTE = "duplicate, slot skipped"
                log("digest duplicate, skipping this slot")
                q0["digests"] = {today0: ncards + 1}
                save_quota(q0)
            else:
                _DIGEST_NOTE = f"REFUSED rc={rc} - retrying next cycle"
                log(f"digest refused (rc={rc}), will retry next cycle")
                # 28 Sep: a Meta-side wall (#283 etc.) lets cycles build
                # cards forever while nothing publishes. Three refusals in
                # a row = not transient: write ALERT.md so the job-end step
                # escalates it to a GitHub issue (fail loud, never silent).
                q0["refuse_streak"] = int(q0.get("refuse_streak") or 0) + 1
                if q0["refuse_streak"] >= 3:
                    alert(f"Publishing refused by Facebook {q0['refuse_streak']} cycles in a row",
                          f"Latest rc={rc}. Cards are building fine - the wall is on the "
                          "Meta side (permissions/review). Pipeline will resume on its own "
                          "the moment Graph accepts posts again.")
                save_quota(q0)
    if posted == 5:
        alert("Facebook publish failed while retrying pending posts",
              "Check Page token permissions (pages_manage_posts).")
        raise SystemExit(9)
    n = 0 if not isinstance(posted, int) else posted

    for c in cyc["candidates"]:
        if n >= MAX_POSTS_PER_CYCLE:
            break
        if not eligible(c):
            log("stale or low-score, skipped:", c["title"][:60])
            continue
        framed = writer.build(c)
        if not framed:
            log("queued for editorial pass (no safe auto frame):", c["title"][:70])
            continue
        story = story_from(c, framed)
        if {story.get("slug"), story.get("topic")} & retracted_slugs():
            log("BLOCKED owner-retracted story:", story["slug"])
            continue
        # owner 26 Sep: BREAKING posts as it happens; everything else rides
        # the hourly card. Breaking = Class A, score >= 88, politics/main-news.
        # 16 Nov owner "any major incident post breaking news immediately":
        # war / cyber and other big-incident stories also break at once, so a
        # major incident never waits for the hourly card.
        _breaking = ((c.get("class") == "A" and (c.get("score") or 0) >= 88
                      and bool(set(c.get("categories", [])) & MAIN_CATS))
                     or (_is_war_cyber(c.get("title"))
                         and (c.get("score") or 0) >= 50))
        # 20 Nov owner ("6 people died in a building incident at the area ...
        # what type of news is this"): a casualty / accident / crime story is
        # never posted as a breaking solo. War is the only exception, and it
        # is the only thing the clause above lets through.
        if _breaking and not _is_war_cyber(c.get("title")):
            _cat_ban = (bool(set(c.get("categories", [])) & NO_CARD_CATS)
                        or bool(NO_CARD_RX.search(c["title"])))
            if _cat_ban and casualty_allowed(c) != "celeb":
                log("casualty story never breaks:", c["title"][:60])
                _breaking = False
        # never post as breaking a story that already rode this cycle's card
        if _breaking and dg and story.get("topic") in (dg.get("story_topics") or []):
            _breaking = False
        if not _breaking:
            log("held for the next card:", story["slug"])
            continue
        emerg = False  # casualty-type cats are owner-banned, never emergency-post
        log("BREAKING accepted (score %s): %s" % (c.get("score"), story["slug"][:60]))
        q = load_quota()
        today = datetime.now(IST).strftime("%Y-%m-%d")
        month = today[:7]
        if q.get("day") != today:
            q = {"day": today, "regular": 0, "day_total": 0,
                 "emerg_month": q.get("emerg_month", {})}
        if q.get("day_total", 0) >= DAILY_TOTAL_LIMIT:
            log("daily total limit reached, holding:", story["slug"])
            continue
        if emerg:
            if q["emerg_month"].get(month, 0) >= MONTHLY_EMERGENCY_LIMIT:
                log("monthly emergency limit (10) reached, holding:", story["slug"])
                continue
        elif q.get("regular", 0) >= DAILY_REGULAR_LIMIT:
            log("daily regular limit (3) reached, holding:", story["slug"])
            continue
        elif q.get("last_at"):
            gap = (datetime.now(IST) -
                   datetime.fromisoformat(q["last_at"])).total_seconds() / 3600
            if gap < MIN_GAP_HOURS:
                log("2h spacing rule, holding till next slot:", story["slug"])
                continue
        log(f"AUTO POST candidate [{c['class']}|{c['score']}|{c['region']}"
            f"{'|EMERGENCY' if emerg else ''}]:", c["title"][:70])
        rc, path = try_publish(story)
        if rc == 0:
            n += 1
            q["day_total"] = q.get("day_total", 0) + 1
            if emerg:
                q["emerg_month"][month] = q["emerg_month"].get(month, 0) + 1
            else:
                q["regular"] = q.get("regular", 0) + 1
                q["last_at"] = datetime.now(IST).isoformat()
            save_quota(q)
            log("published:", story["slug"])
            clear_alert()
            time.sleep(POST_SPACING_SECONDS)  # avoid FB Page rate-limit errors
        elif rc == 3:
            log("duplicate suppressed:", story["slug"])
        elif rc == 4:
            log("packaged, awaiting Facebook connection:", story["slug"])
        elif rc == 5:
            alert("Facebook API error during publish",
                  f"story={story['slug']}\nSee logs above for the Graph API response.")
            raise SystemExit(9)
        else:
            log(f"refused by pre-publish checks (rc={rc}):", story["slug"])

    # ---- editorial inbox: owner-pasted story files are published here ----
    sdir = os.path.join(BASE, "stories")
    inbox = sorted(os.path.join(sdir, f) for f in os.listdir(sdir)
                   if f.startswith("inbox-") and f.endswith(".json"))         if os.path.isdir(sdir) else []
    for fp in inbox:
        if n >= MAX_POSTS_PER_CYCLE:
            log("inbox held for next cycle (post cap):", os.path.basename(fp))
            continue
        try:
            story = json.load(open(fp, encoding="utf-8"))
        except Exception as e:
            log("inbox file unreadable, removed:", os.path.basename(fp), e)
            os.remove(fp)
            continue
        log("INBOX story:", story.get("slug", os.path.basename(fp)))
        rc, path = try_publish(story)
        if rc == 0:
            n += 1
            log("published from inbox:", story.get("slug"))
            os.remove(fp)
            time.sleep(POST_SPACING_SECONDS)
        elif rc == 3:
            log("inbox duplicate suppressed:", story.get("slug"))
            os.remove(fp)
        elif rc == 5:
            log("inbox publish failed (FB), keeping for retry:", story.get("slug"))
        else:
            log(f"inbox refused by pre-publish checks (rc={rc}), removed:",
                story.get("slug"))
            os.remove(fp)

    summary = {"cycle": cyc["cycle_id"], "at": datetime.now(IST).isoformat(),
               "candidates": len(cyc["candidates"]), "published_this_cycle": n}
    summary["digest"] = _DIGEST_NOTE
    summary["ru"] = dict(_RU_STATS)
    summary["tail"] = list(_LOGTAIL)[-30:]   # live diagnostics (refusal reasons,
                                              # roman-urdu acceptance, stage skips)
    os.makedirs(STATE, exist_ok=True)
    json.dump(summary, open(os.path.join(STATE, "last_cycle.json"), "w"), indent=2)
    log(f"=== cycle end: {n} new post(s), "
        f"{len(cyc['candidates'])} candidates scanned ===")


def _pin_guard():
    """2 Oct night (owner: "national reporter is also getting posted"): the
    first line of defence. publish._lock_page already refuses a foreign page
    at post time; this refuses BEFORE any work happens, so a wrong FB_PAGE_ID
    can never even build a card. An empty pin is left to the publisher, which
    reports 'not connected' instead of guessing."""
    _pid = os.environ.get("FB_PAGE_ID", "").strip()
    if _pid and _pid != publish.AZAD_PAGE_ID:
        log("REFUSED: FB_PAGE_ID=" + _pid + " is not Azad Daily ("
            + publish.AZAD_PAGE_ID + "); this agent never posts elsewhere")
        raise SystemExit(9)


def main():
    _pin_guard()
    if "--once" in sys.argv:
        log("single-cycle mode (--once)")
        one_cycle()
        return
    # Owner 28 Sep "no interruptions": self-reliant relay loop. Bounded by
    # --deadline=MIN so the workflow hands off to a fresh runner before any
    # hard kill; tolerates single-cycle errors; pushes memory after EVERY
    # cycle so even a crash can never re-post a story or lose the cadence.
    _dl = 0.0
    for _a in sys.argv:
        if _a.startswith("--deadline="):
            try:
                _dl = float(_a.split("=", 1)[1])
            except ValueError:
                pass
    _iv = float(os.environ.get("DAEMON_INTERVAL_MIN", "10"))
    _t0 = time.time()
    log(f"autonomous relay up - cycle every {_iv:.0f} min"
        + (f", deadline {_dl:.0f} min" if _dl else ", no deadline"))
    _fails = 0
    # 28 Sep: hard per-cycle wall budget - a frozen runner or wedged cycle
    # cannot hold the slot; the alarm raises into the existing 3-strike
    # handler which commits memory and moves on.
    _cmax = int(os.environ.get("DAEMON_CYCLE_MAX_SEC", "780"))
    class _CycleTimeout(Exception):
        pass
    def _fire(_sig, _frm):
        raise _CycleTimeout(f"cycle exceeded {_cmax}s wall budget")
    signal.signal(signal.SIGALRM, _fire)
    while True:
        try:
            signal.setitimer(signal.ITIMER_REAL, _cmax)
            one_cycle()
            signal.setitimer(signal.ITIMER_REAL, 0)
            _fails = 0
        except SystemExit:
            _commit_memory()
            raise
        except Exception:
            signal.setitimer(signal.ITIMER_REAL, 0)
            _fails += 1
            log("cycle error (%d/3): %s" % (_fails, traceback.format_exc()[-350:]))
            if _fails >= 3:
                alert("relay: 3 consecutive cycle failures - exiting; a fresh "
                      "runner takes over within minutes (cron+watchdog)", "")
                _commit_memory()
                raise SystemExit(9)
        _commit_memory()
        if _dl and (time.time() - _t0) / 60.0 >= _dl - 8:
            log("relay deadline reached - handing off to the next queued run")
            return
        _slp = _iv * 60
        try:
            # 1 Oct (credit outage): engine method 'fallback' + zero accepts
            # == OpenRouter rejecting every call (402, empty wallet). Keep
            # scanning (rule-Urdu cards still publish) but stop spending API
            # calls every 10 min, and alert ONCE per 6 h so the owner sees
            # the one manual action there is: add credits.
            _eng = _RU_STATS.get("engine") or {}
            if (_eng.get("method") == "fallback" and not _RU_STATS.get("accepted")
                    and _RU_STATS.get("to_translate")):
                _slp = max(_slp, 1200)
                _cfile = os.path.join(STATE, "credit_alarm.json")
                _last = 0.0
                try:
                    _last = float(json.load(open(_cfile)).get("at", 0))
                except Exception:
                    pass
                if time.time() - _last > 6 * 3600:
                    json.dump({"at": time.time()}, open(_cfile, "w"))
                    # 3 Oct: the old text told the owner to "add ~$5 at
                    # openrouter.ai/credits" - he has explicitly refused any
                    # top-up ("free tier option only"), so the alert must name
                    # the VERIFIED cause and the FREE ways out, not a purchase.
                    # Verified by raw HTTP probe (news_agent/state/
                    # free_lane_check.json, refreshed daily at 00:05 UTC):
                    #   * OpenRouter :free tier = 50 requests/day on a $0
                    #     wallet -> HTTP 429 until 00:00 UTC ("Add 10 credits
                    #     to unlock 1000/day")
                    #   * the owner's OpenAI key is VALID but has no credits
                    #     -> HTTP 429 insufficient_quota
                    #   * Gemini / Groq lanes have no key set
                    alert("Roman-Urdu translator has no working free lane",
                          "The hourly digest cannot translate, so it publishes 0. "
                          "Verified causes (see news_agent/state/free_lane_check.json, "
                          "refreshed daily): OpenRouter's free tier is capped at 50 "
                          "requests/day and today's are used (HTTP 429 until 00:00 UTC); "
                          "the OpenAI key is valid but has $0 credits. "
                          "FREE fix, ~1 minute, no card: create a key at "
                          "aistudio.google.com/apikey (or console.groq.com/keys) and "
                          "store it as the repo secret GEMINI_API_KEY (or GROQ_API_KEY) "
                          "- the code already tries that lane first. "
                          "The free OpenRouter quota also returns at 00:00 UTC "
                          "(05:30 IST) with 50 calls/day. "
                          "Editorial inbox cards still publish immediately - they "
                          "bypass the translator entirely.")
        except Exception:
            pass
        time.sleep(_slp)


def _commit_memory():
    """Best-effort: push state after each cycle (relay mode). Never raises."""
    import subprocess
    root = os.path.dirname(BASE)
    def _git(*a, check=False):
        try:
            r = subprocess.run(("git", "-C", root) + a, capture_output=True,
                               timeout=60)
            if check and r.returncode != 0:
                raise RuntimeError(r.stderr.decode()[:200])
            return r
        except Exception:
            if check:
                raise
            return None
    br = os.environ.get("GITHUB_REF_NAME", "main")
    try:
        _git("add", "news_agent/state", "news_agent/stories", check=True)
        d = _git("diff", "--cached", "--quiet")
        if d is None or d.returncode == 0:
            return  # nothing changed
        _git("-c", "user.name=azad-news-agent", "-c", "user.email=agent@localhost",
             "commit", "-m", f"relay cycle {datetime.now(IST).isoformat(timespec='seconds')}",
             check=True)
        _git("fetch", "origin", f"refs/heads/{br}")
        _git("push", "origin", f"HEAD:refs/heads/{br}", check=True)
        log("[relay] memory pushed")
    except Exception as e:
        # 1 Oct: on rejection, rebase our commit on the remote tip ONCE and
        # retry. No blind reset: that would silently drop this runner's
        # cycle state (published.json/ru_misses dedupe).
        try:
            r = _git("rebase", f"origin/{br}")
            if r is not None and r.returncode != 0:
                # 1 Oct: a conflict here used to ABORT the rebase - the
                # runner's HEAD stayed diverged and every later push was
                # rejected too (all of #297's cycles were invisible).
                # State files are owned by the runner mid-cycle: take OURS,
                # finish the rebase, let the push through.
                _git("checkout", "--theirs", "--", "news_agent/state")
                _git("add", "news_agent/state")
                c = _git("-c", "core.editor=true", "rebase", "--continue")
                if c is not None and c.returncode != 0:
                    _git("rebase", "--skip")
            _git("push", "origin", f"HEAD:refs/heads/{br}", check=True)
            log("[relay] memory pushed (after rebase)")
        except Exception as e2:
            log("[relay] memory push skipped:", str(e2)[:120])


if __name__ == "__main__":
    main()

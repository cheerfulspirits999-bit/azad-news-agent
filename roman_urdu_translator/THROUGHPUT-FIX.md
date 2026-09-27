# THROUGHPUT FIX — "1-4 cards/day phir ruk jata hai" ki asal wajah

**TL;DR — yeh code ka masla nahi hai. Aapki repo mein `ROMAN_URDU_API_KEY` secret
set hi nahi hai, isliye translator kabhi chalta hi nahi. Ek secret add karne se
24 cards/day ka raasta khul jata hai — koi code change nahi.**

Aapka code pehle se 24 cards/hourly ke liye design kiya gaya hai:

```python
# news_agent/autod.py
CARDS_PER_DAY = 24                 # line 288
DIGEST_SLOTS  = tuple(range(24))   # line 287  "owner 26 Sep: hourly, 24/7, never stop"
_due = (gap_min >= 30 if catchup else gap_min >= 60)   # line 754 - hourly
MIN_GAP_HOURS = 0.33               # line 79  - 20 min, blocker nahi
```

GitHub Actions cron bhi har 15 minute hai (`5,20,35,50 * * * *` = 96 slots/day).
Yaani cadence theek hai. **Problem yeh hai ke har cycle mein card banne ke liye
kam az kam 3 "publishable Roman Urdu" bullets chahiye, aur woh mil nahi rahe.**

---

## 1. Saboot (production run se, 27 Sep 2026 08:00 UTC, conclusion=success)

`news-cycle` workflow run `36304865128` ke logs:

```
RU_API_KEY:                                                          <-- KHAALI
[digest] stage-0: pool=3 skips={'stale': 0, 'casualty': 23, 'class': 0,
                                'conv': 20, 'qual': 14, 'dup': 0,
                                'fr': 28, 'tag': 0, 'mt': 0}
DIGEST ready: 3 fresh stories, one card
published daily card: auto-digest-2026-09-27-s1331
```

Aur repo settings se:

| Check | Result |
|---|---|
| `secrets.ROMAN_URDU_API_KEY` | **MOJOOD NAHI** (sirf 6 secrets: AGENT_PAT, FB_APP_ID, FB_APP_SECRET, FB_PAGE_ID, FB_PAGE_TOKEN, FB_REFRESH_TOKEN) |
| `vars.RU_PROVIDER` / `RU_MODEL` / `RU_BASE_URL` | **total vars: 0** — koi bhi nahi |
| `state/last_cycle.json` | `candidates: 60, published_this_cycle: 0` |
| `state/quota.json` | `day_total: 1, digests: {"2026-09-27": 1}` |

**Skip counters ka matlab** (autod.py line 470):

| Counter | Is run mein | Matlab |
|---|---|---|
| `conv` | **20** | `writer.urdu_headline()` rules-converter Urdu bana nahi saka → candidate **drop** |
| `qual` | **14** | `writer.bullet_quality()` ne Urdu reject ki → candidate **drop** |
| `fr` | 28 | koi safe auto-frame nahi mila |
| `casualty` | 23 | casualty category banned hai (owner policy) |
| `mt` | 0 | machine-translation path **kabhi chala hi nahi** (kyunke `_ru_on()` False hai) |

Yaani **34 candidates sirf Roman Urdu ki wajah se mare** (conv 20 + qual 14).
85 candidates mein se pool sirf **3** bacha — jo digest ka bare-minimum hai.
Jab pool 3 se neeche girta hai, card banta hi nahi:

```python
# news_agent/autod.py line ~696
picks = [p for p in picks if p[2]]      # never ship an empty Urdu mirror
if len(picks) < 3:
    log("[digest] wait: <3 stories with publishable Urdu this cycle")
    return None                          # <-- IS CYCLE MEIN KOI CARD NAHI
```

Isi liye din mein 1-4 cards aate hain aur phir "ruk" jata hai — woh rukta nahi,
har cycle mein `<3 publishable Urdu` ki wajah se silently wait karta rehta hai.

---

## 2. Kyun `_ru_on()` False hai (exact chain)

```python
# .github/workflows/news-cycle.yml
RU_API_KEY:  ${{ secrets.ROMAN_URDU_API_KEY }}   # secret mojood nahi -> khaali string
RU_PROVIDER: ${{ vars.RU_PROVIDER }}             # var mojood nahi -> khaali
RU_MODEL:    ${{ vars.RU_MODEL }}                # khaali
RU_BASE_URL: ${{ vars.RU_BASE_URL }}             # khaali

# news_agent/roman_urdu.py
def _key():
    return (_env("RU_API_KEY") or _env("GROQ_API_KEY") or _env("OPENAI_API_KEY")
            or _env("GOOGLE_API_KEY") or _env("ANTHROPIC_API_KEY"))   # sab khaali

def configured():
    p = _env("RU_PROVIDER", "openai")
    return True if p == "mock" else bool(_key())      # -> False

# news_agent/autod.py line 39
def _ru_on():
    return bool(roman_urdu) and roman_urdu.configured()   # -> False
```

Aur `_ru_on()` False hone ka seedha asar yahan padta hai:

```python
# news_agent/autod.py line ~627 (_build_picks ke andar)
ur_line = writer.urdu_headline(c["title"])
if not ur_line or len(ur_line) > 118:
    if lead and len(lead) <= 95 and _ru_on():   # <-- False, isliye neeche wala branch
        ur_line = ""                            #    LLM baad mein bharta
    else:
        continue                                # <-- CANDIDATE TURANT DROP, koi rescue nahi
```

Matam: rules-converter fail = candidate khatam. LLM ka koi chance hi nahi milta.
Woh 20 `conv` skips isi line par mare hain.

---

## 3. FIX — 3 minute, koi code change nahi

### Step 1: ek LLM API key lein (koi ek)

| Provider | Key | Cost (24 cards/day, ~5 bullets) | Note |
|---|---|---|---|
| **Groq** (recommended) | `gsk_...` | **Free tier** | Sub-second, `llama-3.3-70b-versatile` |
| OpenAI | `sk-...` | ~$0.22/month with `gpt-4o-mini` | Sab se balanced |
| Google AI Studio | `AIza...` | Free tier | `gemini-2.0-flash` |

### Step 2: GitHub repo mein secret add karein

`Settings → Secrets and variables → Actions → Secrets tab → New repository secret`

```
Name:  ROMAN_URDU_API_KEY
Value: gsk_xxxxxxxxxxxxxxxx        (ya sk-xxxx / AIzaxxxx)
```

### Step 3: variables add karein (provider ke hisaab se)

`Settings → Secrets and variables → Actions → Variables tab → New repository variable`

**Groq ke liye:**
```
RU_PROVIDER = openai
RU_MODEL    = llama-3.3-70b-versatile
RU_BASE_URL = https://api.groq.com/openai/v1
```

**OpenAI ke liye:**
```
RU_PROVIDER = openai
RU_MODEL    = gpt-4o-mini
RU_BASE_URL = https://api.openai.com/v1
```

**Gemini ke liye:**
```
RU_PROVIDER = gemini
RU_MODEL    = gemini-2.0-flash
RU_BASE_URL = (khaali chhod dein)
```

### Step 4: test karein

Repo → **Actions → news-cycle → Run workflow** (button). Log mein ab yeh dikhna chahiye:

```
RU_API_KEY: gsk_...                       <-- bhara hua
[digest] stage-0: pool=14 skips={... 'conv': 6, 'qual': 4 ...}    <-- pool barh gaya
[digest] roman-urdu step: 9/11 lines accepted                     <-- yeh line NAHI aati thi
DIGEST ready: 3 fresh stories, one card
```

`[digest] roman-urdu step: X/Y lines accepted` ka log aana hi is baat ka saboot
hai ke translator chal para.

### Umeed-war natija

`conv` skips (20) ka bara hissa khatam, kyunke ab rules-converter fail hone par
candidate drop nahi hoga — `ur_line=""` set hoga aur LLM usse bharega.
`pool=3` se barh kar 10-20 par jana chahiye, yaani **har ghante card banega**
(`gap_min >= 60` aur `CARDS_PER_DAY = 24` pehle se set hain).

---

## 4. Doosre masail jo raaste mein mile (fix karne se throughput aur barhega)

### 4.1 ⚠️ `max_tokens` truncation — yeh key add karne ke BAAD bhi kaat sakta hai

```python
# news_agent/roman_urdu.py line ~128
body = {"model": model, "temperature": temp,
        "max_tokens": 40 + 45 * user.count("\n"), "messages": msgs}
```

5 bullets (title + 4) = 4 newlines → **max_tokens = 220**. Roman Urdu aksar
English se zyada words leta hai, aur 5 news bullets aaram se 250-350 tokens le
jaate hain. Output **beech mein cut** ho jata hai → line count mismatch →
`_validate` fail → `ok=False, lines=[]` → **saare bullets drop** → card nahi.

**Fix:** `max_tokens` ko `40 + 120 * user.count("\n")` kar dein, ya simply `2048`.
(Naye folder ka module `max_tokens=4096` use karta hai — yeh failure mode usmein nahi.)

### 4.2 All-or-nothing failure

```python
# purana translate_lines() - validation fail par:
return {"ok": False, "lines": [], "method": "llm-invalid", ...}
```

**Ek** kharab line ki wajah se **saari** lines chali jati hain (`lines=[]`).
Naya module line-specific error report ke sath retry karta hai aur fail hone par
structure-preserving fallback deta hai — yaani `_bad` set mein sirf asal kharab
line jati hai, poori batch nahi.

### 4.3 `paste_check(n=4)` bohot sakht hai

```python
en = "Telangana targets 5 GW data centre capacity by 2030"
ur = "Telangana ne 5 GW data centre ki salahiyat barhanay ka faisla kiya"   # achhi Urdu!
paste_check(en, ur, 4)   # -> True  = FATAL, bullet drop
```

`5 GW data centre` ek jayaz technical phrase hai, lekin 4 consecutive words match
hone par bullet `en-words-pasted` par mar jata hai. Tech/business news mein yeh
baar bar hoga.

**Fix:** `paste_check(en, ur, 5)` (autod.py line ~682 par call hoti hai), ya
technical terms ki whitelist. Naya module `RU_PASTE_N` env var se yeh configurable
rakhta hai (default 4 = purane jaisa, barhane ke liye `RU_PASTE_N=5`).

### 4.4 `DAILY_REGULAR_LIMIT = 12` (solo/breaking path)

Digest path `CARDS_PER_DAY = 24` use karta hai (theek hai), lekin **solo/breaking**
posts `DAILY_REGULAR_LIMIT = 12` par cap hain. Agar aap 24 solo cards bhi chahte
hain to isse 24 karein. Note: log message abhi bhi purana text bolta hai —
`log("daily regular limit (3) reached")` jabke limit 12 hai (autod.py line 820).

### 4.5 `_skip["mt"]` dead counter hai

`_skip` dict mein `"mt": 0` define hai (line 470) lekin kahin increment nahi hota.
Logs mein hamesha 0 dikhega — ignore karein.

---

## 5. Naya folder ismein kya madad karta hai

`roman_urdu_translator/` ko maine aapke card pipeline ke constraints ke mutabiq
banaya hai, taaki adopt karna pade to friction na ho:

| Aapka constraint | Naya module |
|---|---|
| `MAXLEN = 118` per line | `validate(max_line_len=118)` → over-long line par **retry**, phir `trim_to()` word-boundary par |
| `paste_check(en, ur, 4)` | Bilkul wahi algorithm, `RU_PASTE_N` se configurable |
| `translate_lines(lines) -> {ok, lines, method, problems, warning}` | **Exact same shape** — drop-in |
| `translate_news(title_en, bullets_en) -> {ok, title_ur, bullets_ur, ...}` | **Exact same shape** — drop-in |
| `configured()` / `_ru_on()` gate | Same semantics (mock = True, warna key check) |
| `writer.bullet_quality()` ke `ur-*` reasons | Module output ke liye non-fatal (autod.py jaisa hi) |
| Cycle kabhi crash na ho | `translate_lines()` **kabhi raise nahi karta** |
| Env var names | `RU_API_KEY`, `RU_PROVIDER`, `RU_MODEL`, `RU_BASE_URL`, `RU_MAX_RETRIES`, `RU_TIMEOUT`, `RU_TEMPERATURE` — sab same |

Extra jo naye module mein hai aur purane mein nahi:
- `prompts/few_shot_examples.md` — 5 worked examples (purane `_SYSTEM` mein koi example nahi)
- `max_tokens = 4096` (4.1 wala truncation bug nahi)
- Line-specific retry with error report
- Structure-preserving fallback (`lines=[]` nahi)
- HTTP API + demo page (`http_server.py`)
- 66 offline tests

**Lekin yaad rakhein:** naya folder add karne se cards apne aap 24/day nahi ho
jayenge — `autod.py` abhi bhi `news_agent/roman_urdu.py` import karta hai.
**Section 3 wala secret add karna hi asal fix hai.** Uske baad agar quality aur
behtar karni ho to `INTEGRATION.md` ka section 3 (drop-in shim) follow karein.

---

## 6. Emergency option (sirf agar aaj hi cards chahiye)

Agar API key ka intezam nahi ho raha aur aap chahte hain ke pipeline abhi chale:

```
RU_PROVIDER = mock      (variable, secret ki zaroorat nahi)
```

`configured()` mock par `True` deta hai → `_ru_on()` True → cards banne lagenge.
**Lekin Roman Urdu quality bohot kharab hogi** (word-swap, grammar nahi) — yeh
sirf plumbing test ke liye hai, production ke liye nahi. Real key lagte hi
`RU_PROVIDER` hata dein ya `openai` kar dein.

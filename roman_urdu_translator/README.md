# Roman Urdu Translation Agent
### Aapke hourly news-posting automation ke liye drop-in English → Roman Urdu step

Yeh module sirf ek kaam karta hai, aur usse achhi tarah karta hai:
**English news text (title + 3–5 bullets) ko natural Roman Urdu mein badalna — same line order, same bullet count, same numbers/names/emojis ke sath.**

Taaki aapka card generator English aur Roman Urdu text ko aasani se merge kar sake
aur Facebook post automatic chale.

---

## 1. Aapka Roman Urdu kharab kyun aa raha tha? (root causes)

| Problem | Wajah | Is module ka fix |
|---|---|---|
| Urdu/Arabic script aa jata hai | Model ko "Roman" ki sakht pabandi nahi thi | Prompt mein hard ban + output validator (`has_urdu_script`) + auto-retry |
| Hinglish / Hindi words | Generic prompt "Urdu" kehta hai, "Pakistani Urdu" nahi | Style guide + lexicon (hukumat, elaan, izafa, afraad, mutabiq) |
| Word-for-word, ajeeb jumlay | Literal translation | Few-shot examples + "translate meaning, re-express idiomatically" rule |
| Bullets kam/zyada ya reorder | Model summarize kar deta hai | Line-count + bullet-marker validation, warna retry |
| Numbers/dates badal jate hain | Model digits ko words mein likh deta hai | Number-preservation check ("6.2" → "chhe point do" reject) |
| Naam/brand/hashtag translate ho jata hai | Koi rule nahi tha | "Keep in English" list + hashtag/URL/emoji validator |
| "Sure, here is the translation:" jaisi extra baatein | Chat-style prompt | Strip-wrapper cleanup + output-only contract |
| Pipeline crash jab API fail ho | Koi fallback nahi | Dictionary fallback + warning, card kabhi block nahi hota |

---

## 2. Files

```
roman-urdu-translator/
├── roman_urdu.py                 # main engine (CLI + Python API + validator + fallback)
├── http_server.py                # tiny HTTP server for n8n / Make / Zapier
├── n8n_workflow_snippet.json     # ready-to-import n8n nodes
├── prompts/
│   ├── system_prompt.txt         # THE agent prompt (is copy karein kisi bhi AI node mein)
│   └── few_shot_examples.md      # 5 worked examples (quality booster)
├── tests/
│   └── test_offline.py           # 66 offline tests, API key ke baghair
├── THROUGHPUT-FIX.md             # ★ "1-4 cards/day then stops" ka diagnosis + fix
└── INTEGRATION.md                # news_agent/ ke sath compatibility + drop-in shim
```

**Zero dependencies** — sirf Python standard library. `pip install` ki zaroorat nahi.

### Card-pipeline compatibility (`news_agent/` ke sath drop-in)

`roman_urdu.py` mein yeh functions aapke existing module ki **exact same shape**
dete hain, taaki swap karna ho to caller code na badalna pade:

| Function | Kaam |
|---|---|
| `translate_lines(lines)` | `{ok, lines, method, problems, warning}` — `autod.py` isi ko call karta hai |
| `translate_news(title_en=..., bullets_en=[...])` | `{ok, title_ur, bullets_ur, problems, warning, method}` |
| `configured(provider)` | `_ru_on()` gate ke liye same semantics |
| `paste_check(en, ur, n)` | `writer.bullet_quality()` wala English-paste test |
| `trim_to(line, 118)` | card ke `MAXLEN` cap mein word-boundary par fit karna |
| `MAXLEN` / `PASTE_N` | 118 / 4 — `RU_MAXLEN`, `RU_PASTE_N` env se override |

Over-long lines aur English-paste runs ab **validator ke andar hi** pakde jate
hain (retry ke sath), na ke baad mein `bullet_quality()` par bullet marne se.

---

## 3. Quick start (5 minute)

```bash
cd roman-urdu-translator

# a) Offline test — sab kuch kaam kar raha hai ya nahi
python3 tests/test_offline.py

# b) Simple translation (real model)
export RU_PROVIDER=openai            # openai | gemini | anthropic
export RU_MODEL=gpt-4o-mini          # ya gpt-4o / gemini-2.0-flash / claude-sonnet-4-5
export RU_API_KEY=sk-...
echo "Petrol price may increase by Rs 8 per litre this week" | python3 roman_urdu.py

# c) Aapke news agent ki tarah structured input
python3 roman_urdu.py --json-in '{
  "title_en": "Earthquake of magnitude 6.2 hits eastern Afghanistan",
  "bullets_en": [
    "At least 18 people were killed and 40 injured",
    "Rescue teams reached the affected area after six hours",
    "The UN has promised emergency aid",
    "Roads and communication networks remain badly damaged"
  ]
}'
```

Output JSON mein aapko yeh milta hai:

```json
{
  "title_ur": "Mashriqi Afghanistan mein 6.2 intensity ka zalzala",
  "bullets_ur": ["Kam az kam 18 afraad jaan-ba-haq aur 40 zakhmi", "..."],
  "summary_ur": "...",
  "roman_urdu": "Mashriqi Afghanistan mein 6.2 intensity ka zalzala\n- Kam az kam 18 afraad...\n- ...",
  "_bullets_translation": { "ok": true, "method": "llm", "problems": [], "warning": null }
}
```

> `roman_urdu` field seedha card par print karne ke liye ready hai.
> `bullets_ur` list `bullets_en` ke sath index-by-index aligned hai.

---

## 4. Aapke AI Arena agent mein kaise jodein

Aapka flow abhi aisa hai:

```
[Cron hourly] → [News fetch/scrape] → [Recreate: logo + 3-5 bullets EN + Roman Urdu]
             → [Card image] → [Facebook Page post]
```

Isko aisa kar dein — translation ko alag step banayein:

```
[Cron hourly] → [News fetch] → [Recreate: logo + 3-5 bullets (ENGLISH ONLY)]
             → [★ ROMAN URDU TRANSLATE STEP ★]      <-- yahan yeh module
             → [Card image: EN bullets + UR bullets ek hi card par]
             → [Validation: translation_ok?] → [Facebook Page post]
```

### Option A — HTTP node (sab se aasan, recommended)

Server chalayein (apne host/VPS/Render/Railway/Docker par):

```bash
RU_API_KEY=sk-... python3 http_server.py --port 8080 --provider openai --model gpt-4o-mini
```

Server ke endpoints:

| Endpoint | Method | Kaam |
|---|---|---|
| `/` | GET | **Live demo page** — browser mein card preview (EN + UR), button se test |
| `/health` | GET | `{"status":"ok","provider":...,"model":...,"key_present":true/false}` |
| `/translate` | POST | `{"text":"..."}` → `{"ok":true,"roman_urdu":"...","lines_ur":[...]}` |
| `/translate-card` | POST | `{"title_en":"...","bullets_en":[...]}` → card-ready fields |

Demo page par banner batata hai ke server LIVE hai ya MOCK (bina API key).
Mock mode sirf plumbing test karne ke liye hai — uska Roman Urdu achha nahi hota,
kyunke usmein koi LLM nahi chalta.

Phir aapke automation mein ek HTTP Request step add karein:

```
POST http://<your-host>:8080/translate-card
Content-Type: application/json

{ "title_en": "...", "bullets_en": ["...", "...", "..."] }
```

Response:

```json
{
  "ok": true,
  "title_en": "...", "title_ur": "...",
  "bullets_en": ["..."], "bullets_ur": ["..."],
  "card_text_en": "TITLE\n- b1\n- b2",
  "card_text_ur": "TITLE\n- b1\n- b2",
  "bullet_count": 3
}
```

`card_text_en` aur `card_text_ur` dono seedha aapke image/card step ko dein —
same card par English upar, Roman Urdu neeche.

### Option B — Python call (agar aapka agent script-based hai)

```python
from roman_urdu import translate_news

english = "\n".join(["- " + b for b in bullets_en])   # 3-5 bullets
res = translate_news(english)

if res["ok"]:
    bullets_ur = [l.lstrip("-*• ").strip() for l in res["lines_ur"]]
else:
    log.warning("translation fallback used: %s", res["problems"])
    bullets_ur = [l.lstrip("-*• ").strip() for l in res["lines_ur"]]  # phir bhi safe

draw_card(title_en, bullets_en, res_title_ur, bullets_ur)   # aapka existing renderer
```

### Option C — Sirf prompt copy karein (koi bhi AI node: n8n, Make, Zapier, Arena)

Agar aap code host nahi karna chahte, to `prompts/system_prompt.txt` ka poora text
apne AI node ke **System** field mein paste kar dein, aur **User** field mein sirf English
news dein. Bas itna karein aur quality foran behtar ho jayegi.

`n8n_workflow_snippet.json` mein ready nodes hain (HTTP node, OpenAI node, Code node
jo output ko normalize karta hai, aur IF node jo kharab translation ko rokta hai).

### Important: card par dono languages ek sath

Aapke card renderer mein ab 2 text blocks honge. Behtar layout:

```
┌────────────────────────────────────┐
│  [LOGO]            26 Sep 2026     │
│                                    │
│  Earthquake of magnitude 6.2       │   ← English title (bold, bara font)
│  hits eastern Afghanistan          │
│  • At least 18 people killed       │
│  • Rescue teams reached after 6h   │
│                                    │
│  ─────────────────────────────     │
│                                    │
│  Mashriqi Afghanistan mein 6.2     │   ← Roman Urdu title (regular font)
│  intensity ka zalzala              │
│  • Kam az kam 18 afraad            │
│    jaan-ba-haq                     │
│  • Rescue teams 6 ghantay baad     │
│    mutasira ilaqay mein pohnchien  │
│                                    │
│  #PakistanNews  #BreakingNews      │
└────────────────────────────────────┘
```

Tip: Roman Urdu ko chhote font/secondary color mein rakhein, aur bullets ko
**same order** mein rakhein (index se zip karein, text search se nahi).

Facebook caption ke liye bhi dono versions bhejein:

```python
caption = f"{title_en}\n\n{title_ur}\n" + "\n".join(
    f"• {en}\n  {ur}" for en, ur in zip(bullets_en, bullets_ur)
) + f"\n\n{hashtags}"
```

---

## 5. Environment variables

| Variable | Default | Kaam |
|---|---|---|
| `RU_PROVIDER` | `openai` | `openai`, `gemini`, `anthropic`, `mock` |
| `RU_MODEL` | `gpt-4o-mini` | Model naam (sasta + tez; quality chahiye to `gpt-4o`) |
| `RU_API_KEY` | – | Kisi bhi provider ki key (ya `OPENAI_API_KEY` etc.) |
| `RU_BASE_URL` | OpenAI | Groq / OpenRouter / Ollama / vLLM ke liye badal dein |
| `RU_TEMPERATURE` | `0.2` | Kam = consistent. 0.3 se upar na karein |
| `RU_MAX_RETRIES` | `2` | Validation fail par auto-retry |
| `RU_TIMEOUT` | `60` | Seconds |
| `RU_SERVER_TOKEN` | – | Set karein to HTTP server par `Authorization: Bearer <token>` lazmi |
| `PORT` | `8080` | HTTP server port |

**Groq (free + fast) example:**
```bash
export RU_PROVIDER=openai
export RU_BASE_URL=https://api.groq.com/openai/v1
export RU_MODEL=llama-3.3-70b-versatile
export RU_API_KEY=gsk_...
```

**Local Ollama (bilkul free, no internet):**
```bash
export RU_PROVIDER=openai
export RU_BASE_URL=http://localhost:11434/v1
export RU_MODEL=qwen2.5:7b
export RU_API_KEY=ollama
```

---

## 6. Automation-safety features (yeh sab automatic hai)

1. **Script guard** — output mein Urdu/Arabic/Devanagari character mila to reject + retry.
   Fallback par bhi mila to character strip kar diya jata hai. Card par kabhi
   Urdu script nahi jayega.
2. **Line/bullet alignment** — 5 bullets gaye to 5 hi wapas aayenge, same markers ke sath.
   Warna retry, warna fallback.
3. **Number/date/fact lock** — digits, %, scores, URLs, hashtags, @mentions, emojis
   check hote hain.
4. **Untranslated detector** — agar 75% se zyada words English jaise hi rahein
   to model ko dobara kaam karne bheja jata hai error report ke sath.
5. **Never-crash** — API down / key expire / rate limit? Pipeline rukta nahi.
   Deterministic fallback chalta hai aur `warning` field set hota hai
   (aap usse Slack/Telegram/Email alert bhej sakte hain).
6. **Idempotent** — same input par same structure. Batch/cron ke liye safe.
7. **Exit codes** — CLI: `0` = ok, `1` = fallback used / validation issue, `2` = empty input.
   Automation mein isse branch bana sakte hain.

---

## 7. Tests

```bash
python3 tests/test_offline.py
# RESULT: 66 passed, 0 failed
```

Tests yeh cover karte hain: structure preservation, Urdu-script rejection,
line-count mismatch, digits→words rejection, untranslated detection,
hashtag/emoji loss, wrapper stripping, fallback path, JSON payload mode,
aur HTTP endpoints (`/health`, `/translate`, `/translate-card`).

Real model ke sath spot-check:

```bash
python3 roman_urdu.py --json --text "Google launches new AI model for smartphones
- The update is free for all Android users
- Battery usage will be reduced by up to 20%"
```

---

## 8. Quality ko aur behtar karne ke tips

- **Model**: `gpt-4o-mini` acha balance hai. Budget ho to `gpt-4o` ya `claude-sonnet-4-5`
  Roman Urdu mein noticeably behtar hain.
- **Temperature 0.2** rakhein — consistency important hai, creativity nahi.
- **Few-shot ON** rakhein (default ON). `--no-few-shot` sirf token bachane ke liye.
- Apni pasand ki spelling (`hukumat` vs `sarkar`, `kyunke` vs `kyun ke`) fix karni ho to
  `prompts/system_prompt.txt` ke section 5 mein word list edit kar dein —
  module foran usse follow karega.
- Apne niche ke 10–15 examples `prompts/few_shot_examples.md` mein add kar dein
  (cricket, politics, tech, business — jo bhi aapki news zyada hoti hai).
- Har 100 posts mein se 2–3 manually check karein; jo galti mile usse prompt mein rule bana dein.

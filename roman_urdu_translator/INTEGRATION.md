# INTEGRATION — yeh folder aapke existing `news_agent/` ke sath kaise fit hota hai

**Yeh folder purane code ko replace NAHI karta.** Repo ka existing
`news_agent/roman_urdu.py` (331 lines, `autod.py` mein wired) jaisa hai waisa
rahega. Yeh ek **alag, parallel implementation** hai jo aap chaaho to baad mein
adopt kar sakte ho, ya sirf iska prompt/validator ideas udhaar le sakte ho.

Push safety: `.github/workflows/*.yml` sirf `schedule` aur `workflow_dispatch`
par trigger hote hain, `push` par nahi — isliye yeh folder add karne se koi
news cycle ya Facebook post trigger nahi hoti.

---

## 1. Dono modules ka comparison

| | `news_agent/roman_urdu.py` (purana) | `roman_urdu_translator/` (yeh naya) |
|---|---|---|
| Env vars | `RU_API_KEY`, `RU_PROVIDER`, `RU_MODEL`, `RU_BASE_URL`, `RU_MAX_RETRIES` | **Bilkul wahi naam** — GitHub secrets/vars dobara banane ki zaroorat nahi |
| System prompt | Compact, inline `_SYSTEM` (~40 lines) | Alag file `prompts/system_prompt.txt` (93 lines, 7 sections) + `prompts/few_shot_examples.md` (5 worked examples) |
| Few-shot examples | Nahi | Haan — quality ka sab se bara farq yahi hai |
| Validation fail par | `ok=False`, **`lines=[]`** (khaali) → caller rules-based Urdu par gir jata hai | Structure-preserving **dictionary fallback** deta hai + retry with error report |
| Card line cap | `MAXLEN = 118` enforce | Enforce nahi — neeche snippet dekho |
| HTTP API | Nahi (module/CLI only) | `http_server.py`: `/translate`, `/translate-card`, `/health` + demo page |
| Dependencies | stdlib only | stdlib only |
| Providers | OpenAI-compatible + mock | OpenAI-compatible + Gemini + Anthropic + mock |

**Aapki quality problem ka sab se baro waja:** purane module mein few-shot
examples nahi hain aur validation fail hone par `lines=[]` wapas jata hai — jis
se `autod.py` rules-based (word-swap) Urdu use karta hai. Wahi kachhi Roman Urdu
card par chhap jati hai. Naya module us case mein bhi structure-preserving
fallback deta hai, aur few-shot ki wajah se fail hone ki noobat hi kam aati hai.

---

## 2. Card ke 118-char cap ke sath compatible banane ka tareeqa

Purana renderer har line par **118 characters** ka hard cap lagata hai
(`news_agent/roman_urdu.py` → `MAXLEN = 118`). Naya module line count preserve
karta hai lekin lambai ka khyaal model par chhodta hai. Isliye card par bhejne
se pehle yeh wrap helper laga lein:

```python
MAXLEN = 118  # same cap as news_agent/render.py

def wrap_line(text: str, limit: int = MAXLEN) -> str:
    """Line ko limit mein fit karein - pehle compress, phir word-boundary trim."""
    if len(text) <= limit:
        return text
    # 1) redundant words nikalein
    for junk in (" ke mutabiq,", " yeh baat yaad rahe ke", " wazeh rahe ke"):
        text = text.replace(junk, "")
    if len(text) <= limit:
        return text
    # 2) word boundary par kaatein
    cut = text[:limit]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(",;:") + "…"
```

Ya prompt level par hi rokna ho to `prompts/system_prompt.txt` ke Section 1 mein
yeh line add kar dein:

```
- Every output line must be at most 118 characters (the card renderer's hard cap).
  Shorten phrasing, never cut a fact or a number.
```

---

## 3. Agar baad mein switch karna ho (2 minute, optional)

`news_agent/autod.py` line ~673 yeh call karta hai:

```python
_res = roman_urdu.translate_lines([picks[i][1] for i in _pend])
```

Naya module same shape ka result de sakta hai — ek chhota sa shim kaafi hai
(`news_agent/` mein **kuch change nahi karna**, sirf import badalna hai):

```python
# news_agent/autod.py ke top par, purani import ki jagah:
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "roman_urdu_translator"))
import roman_urdu as _new_ru

class roman_urdu:                      # same interface, behtar internals
    configured = staticmethod(lambda: bool(os.environ.get("RU_API_KEY")))
    paste_check = __import__("importlib").import_module(
        "roman_urdu_legacy_paste_check")  # ya purana paste_check copy kar lein

    @staticmethod
    def translate_lines(lines):
        r = _new_ru.translate_news("\n".join(lines))
        return {"ok": r["ok"], "lines": r["lines_ur"], "method": r["method"],
                "problems": r["problems"], "warning": r["warning"]}
```

Lekin yeh **abhi zaroori nahi**. Behtar tareeqa: pehle naye module ko
`prompts/system_prompt.txt` + `prompts/few_shot_examples.md` ka content
purane `_SYSTEM` variable mein daal kar dekhein — 90% quality gain wahin se
aayega, bina koi wiring badle.

---

## 4. Is folder ko akela test karna

```bash
cd roman_urdu_translator
python3 tests/test_offline.py          # 31 passed, 0 failed (no API key needed)
python3 roman_urdu.py --mock --text "Petrol price may increase by Rs 8 per litre"

# Real model ke sath (wahi secrets jo news_agent use karta hai):
export RU_PROVIDER=openai RU_MODEL=gpt-4o-mini RU_API_KEY=<ROMAN_URDU_API_KEY>
python3 roman_urdu.py --json --text "Google launches new AI model
- The update is free for all Android users
- Battery usage will be reduced by up to 20%"
```

GitHub Actions mein test chalana ho to yeh step kisi bhi workflow mein add ho
sakta hai (read-only, koi post nahi karta):

```yaml
- run: python3 roman_urdu_translator/tests/test_offline.py
```

---

## 5. Files is folder mein

| File | Kaam |
|---|---|
| `roman_urdu.py` | engine: `translate_news()`, `translate_payload()`, validator, retry, fallback, CLI |
| `http_server.py` | HTTP API + browser demo page (n8n/Make/Zapier ke liye) |
| `prompts/system_prompt.txt` | **the agent prompt** — purane `_SYSTEM` se kahin zyada detailed |
| `prompts/few_shot_examples.md` | 5 worked news examples + anti-patterns |
| `tests/test_offline.py` | 31 offline tests |
| `run.sh`, `Dockerfile`, `docker-compose.yml`, `deploy/*.service`, `.env.example` | hosting options |
| `n8n_workflow_snippet.json` | ready nodes (HTTP + OpenAI + normalize + quality gate) |
| `README.md`, `QUICKSTART.md` | documentation |

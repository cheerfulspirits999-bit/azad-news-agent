"""roman_urdu - the owner's "Roman Urdu Translation Agent", wired in 26 Sep.

One translation step for the hourly card: English news lines (title and/or
bullets) go in, natural Pakistani Roman Urdu comes back - SAME line count,
same order, same digits, names kept, Roman script only.

Design (from the owner-supplied spec):
  * hard output contract in the system prompt (no Urdu/Arabic/Devanagari
    script, one plain line per input line, no chatter, <=118 chars per line),
  * an output validator: script guard, line-count alignment, digit lock,
    untranslated detector, wrapper stripping - each failure re-prompts the
    model once or twice with the error report,
  * NEVER crashes: missing key / dead API / persistently invalid output
    returns ok=False so the caller can fall back (our pipeline then keeps
    only rule-renderable Urdu - "if no important news, don't post").

Providers are env-driven and OpenAI-compatible chat endpoints cover OpenAI,
Groq (free tier), OpenRouter, Ollama, vLLM:
  RU_PROVIDER   openai (default) | gemini | anthropic | mock
  RU_MODEL      gpt-4o-mini (default); gemini-2.0-flash / llama-3.3-70b fine
  RU_BASE_URL   https://api.openai.com/v1 (default)
  RU_API_KEY    key (GROQ_API_KEY / OPENAI_API_KEY also read, in that order)
  RU_TEMPERATURE 0.2   RU_MAX_RETRIES 2   RU_TIMEOUT 60
  RU_MOCK       good|script|short|same|digits|wrapper  (offline tests only)

Public API: configured(), translate_lines(list[str]) -> dict,
translate_news(title_en, bullets_en) -> dict, CLI: see __main__.
"""
import json
import os
import re
import urllib.error
import urllib.request

MAXLEN = 118                      # our card renderer's hard per-line cap
_SCRIPT_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF"
                        r"\uFB50-\uFDFF\uFE70-\uFEFF\u0900-\u097F]")
_WORD_RE = re.compile(r"[A-Za-z']{4,}")
_DIGIT_RE = re.compile(r"\d+(?:[.,]\d+)*")
_NUMBER_RE = re.compile(r"\b(one|two|three|four|five|six|seven|eight|nine|"
                        r"ten|eleven|twelve|crore|crores|lakh|lakhs|trillion)\b")

_SYSTEM = """You are a newsroom translator. Convert each English news line into
NATURAL PAKISTANI ROMAN URDU the way English-language Pakistani dailies render
headlines. Contract, no exceptions:
1. Roman/Latin script ONLY. Never Urdu, Arabic or Devanagari characters. No
   Hindi-style words where the Urdu word is everyday (use: hukumat, sarkar,
   elaan, afraad, zakhmi, mutasir, mutabiq, girftar, kaarrawaai, baithak,
   faisla, maamla, qeemat, barhofti, iltizam, chhanbheen not pradarshan).
2. Reply with EXACTLY one output line per input line, in the same order. No
   numbering, no bullet markers, no title, no explanations, no "Sure" chatter.
3. Every number, digit, amount, date and percentage in an input line must
   appear UNCHANGED in the matching output line. Never turn digits into words
   and never invent or drop figures.
4. Keep proper names, cities, parties, organisations, laws, brands and
   acronyms in their original English spelling (Hyderabad, Telangana, PD Act,
   TDP, ISRO, Rs). Translate ordinary English words instead of leaving whole
   English runs.
5. Translate the MEANING idiomatically, verb-final, like an Urdu reporter -
   never word-for-word. Example:
   IN: Earthquake of magnitude 6.2 hits eastern Afghanistan
   OUT: Mashriqi Afghanistan mein 6.2 ki shiddat ka zalzala, mutasir ilaqon
   mein kahram.
6. Each output line must be at most 118 characters, end with '.' and carry
   exactly the facts of its input line - no additions, no editorialising.
7. If a line genuinely cannot be rendered as clean Roman Urdu, reply with the
   single word IMPOSSIBLE on that line instead of forcing bad output."""


def _env(name, default=""):
    v = (os.environ.get(name) or "").strip()
    return v or default


def _num(name, default, cast=int):
    """Numeric env that can NEVER crash a cycle - bad values fall back."""
    try:
        return cast(_env(name, str(default)))
    except (ValueError, TypeError):
        sys.stderr.write(f"[roman_urdu] ignoring bad {name} value; using {default}\n")
        return default


def _key():
    return (_env("RU_API_KEY") or _env("GROQ_API_KEY")
            or _env("OPENAI_API_KEY") or _env("GOOGLE_API_KEY")
            or _env("ANTHROPIC_API_KEY"))


def configured():
    p = _env("RU_PROVIDER", "openai")
    return True if p == "mock" else bool(_key())


# ---------------------------------------------------------------- providers

def _call_llm(system, user, attempt_report=""):
    """One chat turn; returns raw text. Raises on any transport failure."""
    provider = _env("RU_PROVIDER", "openai")
    if provider == "mock":
        return _mock(user)
    if attempt_report:
        user += "\n\nYour previous reply FAILED validation:\n" + attempt_report \
                + "\nFix it and reply with only the corrected lines."
    key = _key()
    timeout = _num("RU_TIMEOUT", 60)
    model = _env("RU_MODEL", "gpt-4o-mini")
    temp = float(_env("RU_TEMPERATURE", "0.2"))
    msgs = [{"role": "system", "content": system},
            {"role": "user", "content": user}]
    if provider == "gemini":
        url = ("https://generativelanguage.googleapis.com/v1beta/models/"
               f"{model}:generateContent?key={key}")
        body = {"system_instruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"temperature": temp}}
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            j = json.loads(r.read().decode())
        return "".join(p.get("text", "")
                       for p in j["candidates"][0]["content"]["parts"])
    if provider == "anthropic":
        body = {"model": model, "max_tokens": 160 * len(msgs),
                "temperature": temp, "system": system,
                "messages": [m for m in msgs if m["role"] == "user"]}
        req = urllib.request.Request(
            _env("RU_BASE_URL", "https://api.anthropic.com") + "/v1/messages",
            data=json.dumps(body).encode(),
            headers={"content-type": "application/json",
                     "x-api-key": key, "anthropic-version": "2023-06-01"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            j = json.loads(r.read().decode())
        return "".join(b.get("text", "") for b in j["content"])
    # default: OpenAI-compatible /chat/completions (OpenAI, Groq, OpenRouter,
    # Ollama, vLLM - point RU_BASE_URL at the host)
    url = _env("RU_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    body = {"model": model, "temperature": temp,
            "max_tokens": 40 + 45 * user.count("\n"), "messages": msgs}
    req = urllib.request.Request(
        url + "/chat/completions", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + key})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        j = json.loads(r.read().decode())
    return j["choices"][0]["message"]["content"]


def _mock(user):
    """Deterministic stand-in for offline tests. RU_MOCK picks the failure
    mode; 'good' applies a small glossary so validators pass."""
    mode = _env("RU_MOCK", "good")
    gloss = {"earthquake": "zalzala", "hits": "ilaqay ko jhakejhoor gaya",
             "eastern": "mashriqi", "at": "kam", "least": "az",
             "people": "afraad", "were": "", "killed": "jaan se gaye",
             "injured": "zakhmi ho gaye", "rescue": "rahushi", "teams":
             "karkun", "reached": "pohnch gaye", "the": "", "affected":
             "mutasir", "area": "ilaqay", "after": "baad", "six": "",
             "hours": "ghanton", "has": "ne", "promised": "ka iltizam kiya",
             "emergency": "fori", "aid": "madad", "roads": "sarken",
             "and": "aur", "communication": "rwabtat", "networks":
             "zarie", "remain": "ab", "badly": "shadid", "damaged":
             "nuqsan zayed hain", "price": "qeemat", "may": "ho sakti",
             "increase": "izafa", "by": "", "per": "prat", "litre": "liter",
             "this": "is", "week": "hafte", "petrol": "pyardhar",
             "google": "googal", "launches": "lounch kiya", "new": "naya",
             "model": "model", "for": "ke liye", "smartphones":
             "smartphonon", "update": "apdet", "free": "muft", "all":
             "tamam", "android": "aindrroid", "users": "istemaal",
             "battery": "batteri", "usage": "khp", "will": "hoga",
             "reduced": "kami", "up": "tak", "to": "", "percent": "pratishat"}
    out = []
    for ln in user.splitlines():
        ln = re.sub(r"^\s*\d+[).:]\s*", "", ln.strip())
        if not ln:
            continue
        if mode == "script":
            out.append("\u0632\u0627\u0644\u0632\u0644\u0627 " + ln[:20])
            continue
        if mode == "same":
            out.append(ln)
            continue
        toks = []
        for w in re.findall(r"\S+", ln):
            pure = re.sub(r"[^A-Za-z]", "", w).lower()
            g = gloss.get(pure)
            toks.append(re.sub(r"[A-Za-z']+", lambda m: gloss.get(
                m.group(0).lower(), m.group(0)), w) if g is not None else w)
        s = " ".join(t for t in toks if t)
        s = re.sub(r"\s+", " ", s)
        if mode == "wrapper":
            s = ("Sure! Here is the translation:\n- " + s) if not out \
                else s
        if mode == "digits":
            s = re.sub(r"\d+(?:[.,]\d+)*", "several", s)
        out.append((s[:MAXLEN - 2].rstrip(" .,;:") if len(s) > MAXLEN
                    else s).rstrip(".") + ".")
    if mode == "short":
        out = out[:max(1, len(out) - 1)]
    return "\n".join(out)


def paste_check(en, ur, n=4):
    """True if `ur` still carries an unbroken run of >=n words copied from
    `en` (case-insensitive). Vocabulary-free, so it flags real English
    pastes without punishing legitimate proper nouns (Hyderabad, PD Act)."""
    t = re.findall(r"[a-z0-9][a-z0-9'.\-]*", (en or "").lower())
    u = re.findall(r"[a-z0-9][a-z0-9'.\-]*", (ur or "").lower())
    if len(t) < n or len(u) < n:
        return False
    tg = {tuple(t[j:j + n]) for j in range(len(t) - n + 1)}
    return any(tuple(u[i:i + n]) in tg for i in range(len(u) - n + 1))


# ---------------------------------------------------------------- validate

def _strip_junk(raw):
    """Wrapper chatter / markdown fences / numbering / bullets off the top."""
    t = re.sub(r"```[a-z]*", "", raw)
    t = t.replace("**", "").replace("__", "")
    lines = []
    for ln in t.splitlines():
        ln = ln.strip()
        ln = re.sub(r"^[-*\u2022\u25cf\ufeff]+\s*", "", ln)
        ln = re.sub(r"^\d+\s*[).:]\s*", "", ln)
        if not ln:
            continue
        # chat-style preambles live only ABOVE the content; drop them there
        if not lines and re.match(
                r"^(sure|okey|ok|okay|here|output|translation|translated|"
                r"note|result|corrected|following|below)\b", ln, re.I):
            continue
        lines.append(ln)
    return lines


def _validate(src, out):
    """Return list of per-line problems; [] means publishable."""
    probs = []
    if len(out) != len(src):
        probs.append(f"line-count {len(out)} != {len(src)}")
        return probs
    for i, (a, b) in enumerate(zip(src, out), 1):
        if not b.strip():
            probs.append(f"line {i}: empty")
            continue
        if b.strip().upper().startswith("IMPOSSIBLE"):
            probs.append(f"line {i}: translator declined")
            continue
        if _SCRIPT_RE.search(b):
            probs.append(f"line {i}: non-Roman script leaked")
        if len(b) > MAXLEN:
            probs.append(f"line {i}: {len(b)} chars > {MAXLEN}")
        d_in = _DIGIT_RE.findall(a)
        d_out = _DIGIT_RE.findall(b)
        for d in d_in:
            if d not in d_out:
                probs.append(f"line {i}: number {d} lost or rewritten")
        for d in d_out:
            if d not in d_in:
                probs.append(f"line {i}: invented number {d}")
        if _NUMBER_RE.search(b) and not _NUMBER_RE.search(a):
            probs.append(f"line {i}: digits turned into words")
        wa = {w.lower() for w in _WORD_RE.findall(a)}
        if wa:
            shared = sum(1 for w in _WORD_RE.findall(b)
                         if w.lower() in wa)
            if shared / len(wa) > 0.85:
                probs.append(f"line {i}: still English (untranslated)")
        if paste_check(a, b):
            probs.append(f"line {i}: verbatim English word-run kept")
        if len(b) > 60 and not re.search(r"[.!?]$", b):
            probs.append(f"line {i}: missing full stop")
    return probs


# ---------------------------------------------------------------- entry

def translate_lines(lines):
    """lines: plain English strings (news bullets). Returns
    {ok, lines, method, problems, warning}. ok=False => caller falls back;
    this function itself never raises."""
    src = [l.strip() for l in (lines or []) if l and l.strip()]
    if not src:
        return {"ok": False, "lines": [], "method": "none",
                "problems": ["empty input"], "warning": None}
    if not configured():
        return {"ok": False, "lines": [], "method": "none",
                "problems": ["no RU_API_KEY configured"], "warning": None}
    user = "\n".join(src)
    report = ""
    last = []
    tries = max(1, int(_env("RU_MAX_RETRIES", "2")) + 1)
    warning = None
    for n in range(tries):
        try:
            raw = _call_llm(_SYSTEM, user, report)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError,
                KeyError, ValueError, IndexError) as e:
            warning = f"provider unreachable: {e}"
            return {"ok": False, "lines": [], "method": "none",
                    "problems": [warning], "warning": warning}
        last = _strip_junk(raw or "")
        probs = _validate(src, last)
        if not probs:
            return {"ok": True, "lines": last,
                    "method": "mock" if _env("RU_PROVIDER") == "mock"
                    else "llm", "problems": [], "warning": None}
        report = "\n".join(probs)
    return {"ok": False, "lines": [], "method": "llm-invalid",
            "problems": report.split("\n"), "warning": warning}


def translate_news(title_en="", bullets_en=()):
    """Card-shaped API from the owner's spec: title + bullets in, aligned
    Urdu out. ok=False when anything failed validation."""
    bullets = [b.strip() for b in (bullets_en or []) if b and b.strip()]
    src = ([title_en.strip()] if title_en and title_en.strip() else []) + bullets
    res = translate_lines(src)
    off = 1 if title_en and title_en.strip() else 0
    return {"ok": res["ok"], "title_ur": res["lines"][0] if off and res["ok"]
            else "", "bullets_ur": res["lines"][off:] if res["ok"] else [],
            "problems": res["problems"], "warning": res["warning"],
            "method": res["method"]}


if __name__ == "__main__":
    import sys
    if "--json-in" in sys.argv:
        payload = json.loads(sys.argv[sys.argv.index("--json-in") + 1])
        print(json.dumps(translate_news(payload.get("title_en", ""),
                                         payload.get("bullets_en", [])),
                         ensure_ascii=False, indent=2))
        sys.exit(0)
    txt = sys.stdin.read() if len(sys.argv) < 2 else " ".join(sys.argv[1:])
    r = translate_lines([l for l in txt.splitlines() if l.strip()])
    for l in r["lines"]:
        print("- " + l)
    sys.exit(0 if r["ok"] else 1)

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
roman_urdu.py  --  English -> Roman Urdu translation step for news automation.

Zero dependencies (standard library only). Works with:
  * OpenAI-compatible APIs (OpenAI, Groq, DeepInfra, OpenRouter, Ollama, LM Studio, vLLM)
  * Google Gemini
  * Anthropic Claude

Design goals for automation:
  1. Structure-preserving: N lines in -> N lines out, same bullet markers.
     (So your card generator can zip english_lines and urdu_lines safely.)
  2. Self-healing: validates the model output and auto-retries with an
     explicit error report. Falls back to a deterministic dictionary-based
     translation so the pipeline never breaks and never posts Urdu-script text.
  3. Scriptable: usable as a Python module, a CLI (stdin/stdout), or behind a
     tiny HTTP server for n8n / Make / Zapier HTTP-Request nodes.

CLI examples
------------
    cat bullets_en.txt | python3 roman_urdu.py
    python3 roman_urdu.py --text "Petrol price may increase by Rs 8 per litre"
    python3 roman_urdu.py --json-in '{"bullets_en": ["a", "b"]}' --json
    python3 roman_urdu.py --mock        # offline test, no API key needed
    python3 roman_urdu.py --serve --port 8080

Python example
--------------
    from roman_urdu import translate_news
    result = translate_news(
        "PM announces new tax relief package\n- Finance Minister confirms it\n- Starts July 2026"
    )
    print(result["roman_urdu"])          # ready for the card
    print(result["lines_ur"])            # ["...", "...", "..."]
    print(result["ok"], result["method"])
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------
# Configuration (all overridable by environment variables / CLI flags)
# --------------------------------------------------------------------------

DEFAULT_PROVIDER = os.environ.get("RU_PROVIDER", "openai")          # openai | gemini | anthropic | mock
DEFAULT_MODEL = os.environ.get("RU_MODEL", "gpt-4o-mini")
DEFAULT_TEMPERATURE = float(os.environ.get("RU_TEMPERATURE", "0.2"))
DEFAULT_MAX_RETRIES = int(os.environ.get("RU_MAX_RETRIES", "2"))     # retries AFTER the first attempt
DEFAULT_TIMEOUT = int(os.environ.get("RU_TIMEOUT", "60"))

ENDPOINTS = {
    "openai": os.environ.get(
        "RU_BASE_URL",
        os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
    ).rstrip("/")
    + "/chat/completions",
    "gemini": os.environ.get(
        "RU_GEMINI_URL", "https://generativelanguage.googleapis.com/v1beta/models"
    ).rstrip("/"),
    "anthropic": os.environ.get(
        "RU_ANTHROPIC_URL", "https://api.anthropic.com/v1/messages"
    ),
}

API_KEYS = {
    "openai": os.environ.get("RU_API_KEY") or os.environ.get("OPENAI_API_KEY", ""),
    "gemini": os.environ.get("RU_API_KEY") or os.environ.get("GEMINI_API_KEY")
    or os.environ.get("GOOGLE_API_KEY", ""),
    "anthropic": os.environ.get("RU_API_KEY") or os.environ.get("ANTHROPIC_API_KEY", ""),
}

PROMPT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")

# --------------------------------------------------------------------------
# Prompts
# --------------------------------------------------------------------------

_SYSTEM_PROMPT_CACHE: Optional[str] = None


def load_system_prompt(path: Optional[str] = None) -> str:
    """Load the system prompt. Falls back to a compact built-in version."""
    global _SYSTEM_PROMPT_CACHE
    if _SYSTEM_PROMPT_CACHE and path is None:
        return _SYSTEM_PROMPT_CACHE

    candidates = [path] if path else [
        os.path.join(PROMPT_DIR, "system_prompt.txt"),
        os.path.join(os.getcwd(), "prompts", "system_prompt.txt"),
    ]
    for cand in candidates:
        if cand and os.path.isfile(cand):
            try:
                with open(cand, "r", encoding="utf-8") as fh:
                    text = fh.read().strip()
                if text:
                    if path is None:
                        _SYSTEM_PROMPT_CACHE = text
                    return text
            except OSError:
                pass

    compact = (
        "You are a professional English to Roman Urdu translation engine for Pakistani news media. "
        "Output ONLY the Roman Urdu translation: no explanation, no notes, no markdown fences. "
        "Roman script only - never Urdu/Arabic characters. "
        "Keep the exact same number of lines, the same blank lines and the same bullet markers as the input. "
        "Translate every line; never summarize, skip, merge or reorder. "
        "Keep names, brands, places, hashtags, mentions, URLs, emojis, currency codes, units, digits and dates exactly as they are. "
        "Do not convert digits into words. "
        "Use natural Pakistani news-style Roman Urdu (hukumat, elaan, izafa, tehqeeq, mutabiq, afraad, shehriyon), "
        "not Hindi-heavy wording and not literal word-for-word translation. "
        "Stay factual and neutral. Never add information that is not in the source."
    )
    if path is None:
        _SYSTEM_PROMPT_CACHE = compact
    return compact


def load_few_shot(path: Optional[str] = None) -> List[Dict[str, str]]:
    """
    Parse prompts/few_shot_examples.md into OpenAI-style messages.
    Returns [] if the file is missing or unparseable (optional feature).
    """
    candidates = [path] if path else [os.path.join(PROMPT_DIR, "few_shot_examples.md")]
    raw = ""
    for cand in candidates:
        if cand and os.path.isfile(cand):
            try:
                with open(cand, "r", encoding="utf-8") as fh:
                    raw = fh.read()
                break
            except OSError:
                raw = ""
    if not raw:
        return []

    messages: List[Dict[str, str]] = []
    blocks = re.split(r"\n-{20,}\n", raw)
    for block in blocks:
        if "USER:" not in block or "ASSISTANT:" not in block:
            continue
        user_part = block.split("USER:", 1)[1].split("ASSISTANT:", 1)[0].strip()
        asst_part = block.split("ASSISTANT:", 1)[1].strip()
        if user_part and asst_part and "IGNORE THIS LINE" not in asst_part:
            messages.append({"role": "user", "content": user_part})
            messages.append({"role": "assistant", "content": asst_part})
    return messages


# --------------------------------------------------------------------------
# HTTP helpers
# --------------------------------------------------------------------------


def _post(url: str, payload: Dict[str, Any], headers: Dict[str, str], timeout: int) -> Dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # noqa: PERF203
        body = exc.read().decode("utf-8", "replace")[:600]
        raise RuntimeError(f"HTTP {exc.code} from {url}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Network error calling {url}: {exc.reason}") from exc


def call_llm(
    text: str,
    *,
    provider: str = DEFAULT_PROVIDER,
    model: str = DEFAULT_MODEL,
    api_key: str = "",
    temperature: float = DEFAULT_TEMPERATURE,
    timeout: int = DEFAULT_TIMEOUT,
    system_prompt: Optional[str] = None,
    use_few_shot: bool = True,
) -> str:
    """Single LLM call. Returns the raw model text."""
    system_prompt = system_prompt or load_system_prompt()
    messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]
    if use_few_shot:
        messages.extend(load_few_shot())
    messages.append({"role": "user", "content": text})

    key = api_key or API_KEYS.get(provider, "")

    if provider == "openai":
        if not key:
            raise RuntimeError("Missing OpenAI API key (set RU_API_KEY or OPENAI_API_KEY).")
        resp = _post(
            ENDPOINTS["openai"],
            {
                "model": model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": 4096,
            },
            {"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
            timeout,
        )
        return resp["choices"][0]["message"]["content"]

    if provider == "anthropic":
        if not key:
            raise RuntimeError("Missing Anthropic API key (set RU_API_KEY or ANTHROPIC_API_KEY).")
        resp = _post(
            ENDPOINTS["anthropic"],
            {
                "model": model or "claude-sonnet-4-5",
                "system": system_prompt,
                "messages": messages[1:],
                "max_tokens": 4096,
                "temperature": temperature,
            },
            {
                "Content-Type": "application/json",
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
            },
            timeout,
        )
        return "".join(part.get("text", "") for part in resp.get("content", []))

    if provider == "gemini":
        if not key:
            raise RuntimeError("Missing Gemini API key (set RU_API_KEY or GEMINI_API_KEY).")
        model_name = model or "gemini-2.0-flash"
        url = f"{ENDPOINTS['gemini']}/{model_name}:generateContent?key={key}"
        gemini_msgs = []
        for msg in messages[1:]:
            role = "model" if msg["role"] == "assistant" else "user"
            gemini_msgs.append({"role": role, "parts": [{"text": msg["content"]}]})
        resp = _post(
            url,
            {
                "system_instruction": {"parts": [{"text": system_prompt}]},
                "contents": gemini_msgs,
                "generationConfig": {
                    "temperature": temperature,
                    "maxOutputTokens": 4096,
                },
            },
            {"Content-Type": "application/json"},
            timeout,
        )
        parts = resp.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        return "".join(p.get("text", "") for p in parts)

    if provider == "mock":
        return mock_translate(text)

    raise RuntimeError(f"Unknown provider: {provider}")


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

# Urdu / Arabic script blocks that must NEVER appear in Roman Urdu output.
_SCRIPT_RANGES = [
    (0x0600, 0x06FF),   # Arabic
    (0x0750, 0x077F),   # Arabic Supplement
    (0xFB50, 0xFDFF),   # Arabic Presentation Forms-A
    (0xFE70, 0xFEFF),   # Arabic Presentation Forms-B
    (0x08A0, 0x08FF),   # Arabic Extended-A
    (0x0900, 0x097F),   # Devanagari (Hindi script)
]

URDU_SCRIPT_RE = re.compile(
    "[" + "".join(f"\\u{lo:04X}-\\u{hi:04X}" for lo, hi in _SCRIPT_RANGES) + "]"
)

BULLET_RE = re.compile(r"^\s*(?:[-*+•·‣⁃◦]|\d+[.)]|#{1,6})\s+")
NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")


def has_urdu_script(text: str) -> bool:
    return bool(URDU_SCRIPT_RE.search(text))


def strip_wrappers(text: str) -> str:
    """Remove common LLM noise so downstream nodes get clean text."""
    out = text.strip()
    # markdown fences
    out = re.sub(r"^```[a-zA-Z0-9_-]*\s*\n?", "", out)
    out = re.sub(r"\n?```\s*$", "", out)
    # "Translation:" / "Roman Urdu:" style prefixes
    out = re.sub(
        r"^(?:roman urdu|translation|translated text|urdu|output|jawab|answer)\s*[:\-–]\s*",
        "",
        out,
        flags=re.IGNORECASE,
    )
    # leading/trailing blank lines
    return out.strip("\n")


def _markers(lines: List[str]) -> List[str]:
    """Extract the bullet marker of each line ('' when the line has none)."""
    markers = []
    for line in lines:
        m = BULLET_RE.match(line)
        if m:
            marker = m.group(0).strip()
            markers.append(re.sub(r"\s+$", "", marker))
        else:
            markers.append("")
    return markers


def validate(source: str, translated: str) -> Tuple[bool, List[str]]:
    """
    Return (is_ok, list_of_problems). Used to decide whether to retry.
    """
    problems: List[str] = []
    src_lines = source.strip("\n").split("\n")
    out_lines = translated.strip("\n").split("\n")

    if has_urdu_script(translated):
        bad = sorted({c for c in translated if URDU_SCRIPT_RE.match(c)})
        problems.append(
            "Output contains Urdu/Arabic/Devanagari script characters: "
            + " ".join(bad[:12])
            + ". Roman script only."
        )

    if len(out_lines) != len(src_lines):
        problems.append(
            f"Line count mismatch: input has {len(src_lines)} lines, output has {len(out_lines)}. "
            "Return exactly one translated line per input line, keeping blank lines in place."
        )
    else:
        src_markers = _markers(src_lines)
        out_markers = _markers(out_lines)
        for i, (a, b) in enumerate(zip(src_markers, out_markers), start=1):
            if a != b:
                problems.append(
                    f"Line {i}: bullet marker changed from {a!r} to {b!r}. Keep markers identical."
                )
                break
        for i, (s, o) in enumerate(zip(src_lines, out_lines), start=1):
            if s.strip() == "" and o.strip() != "":
                problems.append(f"Line {i}: input is blank but output is not. Keep blank lines blank.")
                break

    src_numbers = NUMBER_RE.findall(source)
    out_numbers = NUMBER_RE.findall(translated)
    if src_numbers and not all(n in out_numbers for n in src_numbers):
        missing = [n for n in src_numbers if n not in out_numbers]
        problems.append(
            "Numbers changed or missing: "
            + ", ".join(missing[:10])
            + ". Keep every digit, percentage, score and date exactly as in the source."
        )

    urls = re.findall(r"https?://\S+|www\.\S+", source)
    for u in urls:
        if u not in translated:
            problems.append(f"URL missing from output: {u}. Keep URLs unchanged.")

    tags = re.findall(r"(?:#\w+|@[A-Za-z0-9_]+)", source)
    for t in tags:
        if t not in translated:
            problems.append(f"Hashtag/mention missing from output: {t}. Keep it unchanged.")

    emojis_src = {c for c in source if ord(c) > 0x1F000}
    emojis_out = {c for c in translated if ord(c) > 0x1F000}
    if emojis_src and not emojis_src.issubset(emojis_out):
        problems.append("Some emojis were dropped: " + " ".join(sorted(emojis_src - emojis_out)))

    # Untranslated-detection: if >70% of the words are identical to English, warn.
    def words(s: str) -> List[str]:
        return re.findall(r"[A-Za-z]{4,}", s.lower())

    src_words, out_words = words(source), words(translated)
    if src_words and out_words:
        overlap = sum(1 for w in out_words if w in set(src_words)) / len(out_words)
        if overlap > 0.75:
            problems.append(
                f"Output looks mostly untranslated ({overlap:.0%} of words identical to English). "
                "Translate the sentences into natural Roman Urdu, keeping only names/brands/terms in English."
            )

    if not translated.strip():
        problems.append("Output is empty.")

    return (not problems), problems


# --------------------------------------------------------------------------
# Deterministic fallback (keeps automation alive, never emits Urdu script)
# --------------------------------------------------------------------------

FALLBACK_DICT = [
    (r"\bgovernment\b", "hukumat"), (r"\badministration\b", "intezamia"),
    (r"\bannouncement\b", "elaan"), (r"\bannounced\b", "elaan kar diya"),
    (r"\bdecision\b", "faisla"), (r"\bstatement\b", "bayan"),
    (r"\binvestigation\b", "tehqeeq"), (r"\bprotest\b", "ehtijaj"),
    (r"\bmeeting\b", "mulaqat"), (r"\bagreement\b", "muahida"),
    (r"\belection\b", "intikhabat"), (r"\beconomy\b", "maeshat"),
    (r"\bprices?\b", "qeematein"), (r"\bincrease[d]?\b", "izafa"),
    (r"\bdecrease[d]?\b", "kami"), (r"\bpossible\b", "mumkin"),
    (r"\baccording to\b", "ke mutabiq"), (r"\bpeople\b", "afraad"),
    (r"\bcitizens\b", "shehriyon"), (r"\bcountry\b", "mulk"),
    (r"\bworld\b", "duniya"), (r"\bminister\b", "wazir"),
    (r"\bpresident\b", "sadar"), (r"\bprime minister\b", "wazir-e-azam"),
    (r"\bnew\b", "naya"), (r"\btoday\b", "aaj"), (r"\btomorrow\b", "kal"),
    (r"\bweek\b", "hafta"), (r"\bmonth\b", "mahina"), (r"\byear\b", "saal"),
    (r"\bkilled\b", "jaan-ba-haq"), (r"\binjured\b", "zakhmi"),
    (r"\bearthquake\b", "zalzala"), (r"\bflood\b", "siylab"),
    (r"\bcricket\b", "cricket"), (r"\bmatch\b", "match"),
    (r"\bseries\b", "series"), (r"\bwickets?\b", "wickets"),
    (r"\bruns?\b", "runs"), (r"\bteam\b", "team"),
    (r"\breports?\b", "raptar"), (r"\bconfirmed\b", "tasdeeq"),
]


def fallback_translate(text: str) -> str:
    """
    Last-resort translation: regex word substitution, line structure preserved.
    Quality is low but it guarantees (a) Roman script, (b) correct line/bullet
    structure, (c) untouched numbers/names. Log a warning when this is used.
    """
    out_lines = []
    for line in text.split("\n"):
        m = BULLET_RE.match(line)
        marker, body = (m.group(0), line[m.end():]) if m else ("", line)
        new_body = body
        for pattern, repl in FALLBACK_DICT:
            new_body = re.sub(pattern, repl, new_body, flags=re.IGNORECASE)
        out_lines.append(marker + new_body)
    return "\n".join(out_lines)


def mock_translate(text: str) -> str:
    """
    Offline stand-in used by --mock and by the test suite. It mimics a *good*
    model: preserves lines/markers/numbers and swaps a few common words.
    Replace with nothing in production (just don't pass provider='mock').
    """
    # Ordered: longer phrases first. Function words are included so the output
    # stops looking like English (the validator checks for that).
    lexicon = [
        ("prime minister", "wazir-e-azam"),
        ("finance minister", "finance minister"),
        ("according to", "ke mutabiq"),
        ("opposition parties", "mukhalif jamaatein"),
        ("rescue teams", "rescue teams"),
        ("is now", "ab"),
        ("will be", "hogi"),
        ("could be", "ho sakta hai"),
        ("may be", "ho sakta hai"),
        ("at least", "kam az kam"),
        ("has been", "ho chuka hai"),
        ("have been", "ho chukay hain"),
        ("the government", "hukumat"),
        ("government", "hukumat"),
        ("announced", "elaan kar diya"),
        ("announces", "elaan karta hai"),
        ("announcement", "elaan"),
        ("confirmed", "tasdeeq kar di"),
        ("confirms", "tasdeeq karta hai"),
        ("expected", "tawqoa"),
        ("expects", "tawqoa rakhti hai"),
        ("per month", "mahana"),
        ("per litre", "prati litre"),
        ("this week", "is haftay"),
        ("today", "aaj"),
        ("tomorrow", "kal"),
        ("week", "haftay"),
        ("month", "mahinay"),
        ("year", "saal"),
        ("prices", "qeematein"),
        ("price", "qeemat"),
        ("increase", "izafa"),
        ("decrease", "kami"),
        ("people", "afraad"),
        ("citizens", "shehriyon"),
        ("minister", "wazir"),
        ("new", "naya"),
        ("killed", "jaan-ba-haq"),
        ("injured", "zakhmi"),
        ("earthquake", "zalzala"),
        ("flood", "siylab"),
        ("match", "match"),
        ("series", "series"),
        ("report", "raptar"),
        ("reports", "raptaron"),
        ("decision", "faisla"),
        ("statement", "bayan"),
        ("investigation", "tehqeeq"),
        ("protest", "ehtijaj"),
        ("meeting", "mulaqat"),
        ("agreement", "muahida"),
        ("election", "intikhabat"),
        ("economy", "maeshat"),
        ("country", "mulk"),
        ("world", "duniya"),
        ("president", "sadar"),
        ("salary", "tankhwa"),
        ("revenue", "revenue"),
        ("consumers", "aam shehri"),
        ("worried", "pareshan"),
        ("inflation", "mehangai"),
        ("relief", "relief"),
        ("package", "package"),
        ("tax", "tax"),
        ("fully", "mukammal tor par"),
        ("exempt", "exempt"),
        ("apply", "laagu"),
        ("from", "se"),
        ("to", "tak"),
        ("and", "aur"),
        ("but", "lekin"),
        ("after", "baad"),
        ("before", "pehle"),
        ("more", "zyada"),
        ("less", "kam"),
        ("up to", "tak"),
        ("lose", "nuqsan uthana"),
        ("call", "qarar deti hain"),
        ("are", "hain"),
        ("is", "hai"),
        ("was", "tha"),
        ("were", "thay"),
        ("will", "ga"),
        ("can", "sakta hai"),
        ("could", "sakta tha"),
        ("has", "ne"),
        ("have", "ne"),
        ("had", "ke paas tha"),
        ("the", ""),
        ("a", ""),
        ("an", ""),
        ("of", "ka"),
        ("in", "mein"),
        ("on", "par"),
        ("for", "ke liye"),
        ("with", "ke sath"),
        ("by", "ne"),
        ("at", "par"),
        ("become", "ho jana"),
        ("becomes", "ho jata hai"),
        ("hit", "se mutasir hua"),
        ("hits", "ne apni lapet mein liya"),
        ("reached", "pohnch gayeen"),
        ("remain", "barqarar hain"),
        ("badly", "shadeed"),
        ("damaged", "nuqsan pohncha"),
        ("promised", "wada kiya"),
        ("emergency", "hungami"),
        ("aid", "emdad"),
    ]
    out = []
    for line in text.split("\n"):
        m = BULLET_RE.match(line)
        marker, body = (m.group(0), line[m.end():]) if m else ("", line)
        for en, ur in lexicon:
            body = re.sub(rf"\b{re.escape(en)}\b", ur, body, flags=re.IGNORECASE)
        body = re.sub(r"\s{2,}", " ", body).strip()
        out.append(marker + body)
    return "\n".join(out)


# --------------------------------------------------------------------------
# Main API
# --------------------------------------------------------------------------


def translate_news(
    text: str,
    *,
    provider: str = DEFAULT_PROVIDER,
    model: str = DEFAULT_MODEL,
    api_key: str = "",
    temperature: float = DEFAULT_TEMPERATURE,
    max_retries: int = DEFAULT_MAX_RETRIES,
    timeout: int = DEFAULT_TIMEOUT,
    system_prompt: Optional[str] = None,
    use_few_shot: bool = True,
    strict: bool = True,
) -> Dict[str, Any]:
    """
    Translate English news text into Roman Urdu.

    Returns a dict:
      {
        "ok": bool,                 # passed validation
        "roman_urdu": str,          # <- use this on the card
        "lines_ur": [str, ...],     # per-line, aligned with lines_en
        "lines_en": [str, ...],
        "method": "llm"|"retry"|"fallback",
        "attempts": int,
        "problems": [str, ...],     # last validation issues (empty when ok)
        "warning": str|None,
      }
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    result: Dict[str, Any] = {
        "ok": False,
        "roman_urdu": "",
        "lines_ur": [],
        "lines_en": text.strip("\n").split("\n"),
        "method": "fallback",
        "attempts": 0,
        "problems": [],
        "warning": None,
    }

    if not text.strip():
        result["ok"] = True
        result["roman_urdu"] = text
        result["lines_ur"] = result["lines_en"]
        result["method"] = "noop"
        return result

    base_prompt = system_prompt or load_system_prompt()
    attempt_text = text
    problems: List[str] = []

    for attempt in range(1, max_retries + 2):
        result["attempts"] = attempt
        try:
            raw = call_llm(
                attempt_text,
                provider=provider,
                model=model,
                api_key=api_key,
                temperature=temperature,
                timeout=timeout,
                system_prompt=base_prompt,
                use_few_shot=use_few_shot,
            )
        except Exception as exc:  # noqa: BLE001
            problems = [f"LLM call failed: {exc}"]
            time.sleep(min(2 ** attempt, 8))
            continue

        cleaned = strip_wrappers(raw)
        ok, problems = validate(text, cleaned)
        if ok:
            result.update(
                ok=True,
                roman_urdu=cleaned,
                lines_ur=cleaned.split("\n"),
                method="llm" if attempt == 1 else "retry",
                problems=[],
            )
            return result

        # Build a self-correcting retry prompt
        attempt_text = (
            text
            + "\n\n=== SYSTEM NOTE ===\n"
            + "Your previous output was rejected for these reasons:\n- "
            + "\n- ".join(problems)
            + "\nReturn the corrected Roman Urdu translation only, "
            + f"with exactly {len(result['lines_en'])} lines and identical bullet markers."
        )

    # All attempts failed -> deterministic fallback so the pipeline never dies
    fb = fallback_translate(text)
    fb = strip_wrappers(fb)
    ok_fb, problems_fb = validate(text, fb)
    result.update(
        ok=ok_fb,
        roman_urdu=fb,
        lines_ur=fb.split("\n"),
        method="fallback",
        problems=[] if ok_fb else problems_fb,
        warning=(
            "LLM translation failed validation after "
            f"{result['attempts']} attempts; dictionary fallback used. "
            "Check your API key/model/network."
        ),
    )
    if strict and not ok_fb and has_urdu_script(fb):
        # Absolute guarantee: never emit Urdu script.
        result["roman_urdu"] = URDU_SCRIPT_RE.sub("", result["roman_urdu"])
        result["lines_ur"] = result["roman_urdu"].split("\n")
    return result


# --------------------------------------------------------------------------
# JSON mode (for n8n / Make / Zapier)
# --------------------------------------------------------------------------


def translate_payload(payload: Dict[str, Any], **kwargs) -> Dict[str, Any]:
    """
    Accepts a flexible news payload and returns the same payload enriched with
    Roman Urdu fields. Recognised input keys (any of):
        title / headline / title_en / headline_en
        bullets / bullets_en / points / bullets_english  (list[str] or str)
        summary / description / caption / body / text
    Output adds:
        title_ur, bullets_ur, summary_ur, roman_urdu (single card-ready string)
    """
    out = dict(payload)
    kw = kwargs

    def pick(*keys: str) -> Optional[str]:
        for k in keys:
            if k in payload and payload[k]:
                return payload[k]
        return None

    def as_lines(value: Any) -> List[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        return [ln.strip() for ln in str(value).split("\n") if ln.strip()]

    title = pick("title", "headline", "title_en", "headline_en", "news_title")
    bullets = as_lines(pick("bullets", "bullets_en", "points", "bullets_english", "items"))
    summary = pick("summary", "description", "caption", "body", "text", "content")

    if title:
        out["title_ur"] = translate_news(title, **kw)["roman_urdu"]
    if bullets:
        # One call for the whole list keeps context (better pronouns/consistency)
        joined = "\n".join(bullets)
        res = translate_news(joined, **kw)
        lines = res["lines_ur"]
        if len(lines) != len(bullets):  # defensive: align by index
            lines = (lines + bullets[len(lines):])[: len(bullets)]
        out["bullets_ur"] = lines
        out["_bullets_translation"] = {
            "ok": res["ok"], "method": res["method"],
            "problems": res["problems"], "warning": res["warning"],
        }
    if summary:
        out["summary_ur"] = translate_news(summary, **kw)["roman_urdu"]

    # Card-ready combined block
    parts = []
    if out.get("title_ur"):
        parts.append(out["title_ur"])
    if out.get("bullets_ur"):
        parts.extend("- " + b if not b.startswith(("- ", "* ", "•")) else b for b in out["bullets_ur"])
    if out.get("summary_ur"):
        parts.append(out["summary_ur"])
    out["roman_urdu"] = "\n".join(parts)
    return out


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="English -> Roman Urdu translator for news automation.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--text", help="Text to translate (else read stdin)")
    p.add_argument("--file", help="Read input text from a file")
    p.add_argument("--json-in", help='JSON payload string, e.g. {"bullets_en": [...]}')
    p.add_argument("--json", action="store_true", help="Print full JSON result")
    p.add_argument("--provider", default=DEFAULT_PROVIDER,
                   help="openai | gemini | anthropic | mock (default: %(default)s)")
    p.add_argument("--model", default=DEFAULT_MODEL, help="Model name (default: %(default)s)")
    p.add_argument("--api-key", default="", help="Override API key")
    p.add_argument("--temperature", type=float, default=DEFAULT_TEMPERATURE)
    p.add_argument("--max-retries", type=int, default=DEFAULT_MAX_RETRIES)
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    p.add_argument("--no-few-shot", action="store_true", help="Disable few-shot examples")
    p.add_argument("--mock", action="store_true", help="Offline deterministic mode (testing)")
    p.add_argument("--serve", action="store_true", help="Run a tiny HTTP server for n8n/Make")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))
    p.add_argument("--prompt", help="Path to a custom system prompt file")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.serve:
        from http_server import serve  # noqa: WPS433  (local file)
        return serve(host=args.host, port=args.port, defaults=vars(args))

    provider = "mock" if args.mock else args.provider
    kwargs = dict(
        provider=provider,
        model=args.model,
        api_key=args.api_key,
        temperature=args.temperature,
        max_retries=args.max_retries,
        timeout=args.timeout,
        use_few_shot=not args.no_few_shot,
    )
    if args.prompt:
        kwargs["system_prompt"] = load_system_prompt(args.prompt)

    if args.json_in:
        payload = json.loads(args.json_in)
        out = translate_payload(payload, **kwargs)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    if args.text is not None:
        text = args.text
    elif args.file:
        with open(args.file, "r", encoding="utf-8") as fh:
            text = fh.read()
    else:
        text = sys.stdin.read()

    if not text.strip():
        sys.stderr.write("error: empty input\n")
        return 2

    res = translate_news(text, **kwargs)

    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(res["roman_urdu"])

    if res.get("warning"):
        sys.stderr.write(f"warning: {res['warning']}\n")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

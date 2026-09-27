#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Offline test suite for the Roman Urdu translation step.
No API key needed -- runs the validator, fallback, mock provider and HTTP layer.

    python3 tests/test_offline.py
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import http_server  # noqa: E402
import roman_urdu as R  # noqa: E402

PASS, FAIL = 0, 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


SAMPLE = (
    "PM announces new tax relief package\n"
    "- Finance Minister confirms relief will start from July 2026\n"
    "- Salary up to Rs 100,000 per month will be fully exempt\n"
    "- The government expects to lose Rs 45 billion in revenue\n"
    "- Opposition parties call the announcement \"an election gimmick\""
)


def test_structure() -> None:
    print("\n[1] Structure preservation (mock provider)")
    res = R.translate_news(SAMPLE, provider="mock")
    check("ok flag is True", res["ok"] is True, str(res["problems"]))
    check("5 lines in -> 5 lines out", len(res["lines_ur"]) == 5, str(len(res["lines_ur"])))
    check("all bullets kept", all(l.strip().startswith("-") for l in res["lines_ur"][1:]),
          json.dumps(res["lines_ur"], ensure_ascii=False))
    check("no Urdu script", not R.has_urdu_script(res["roman_urdu"]))
    check("numbers preserved", all(n in res["roman_urdu"] for n in ["100,000", "45", "2026"]),
          res["roman_urdu"])
    check("word substituted (government->hukumat)", "hukumat" in res["roman_urdu"].lower())


def test_validator() -> None:
    print("\n[2] Validator catches bad outputs")
    bad_script = SAMPLE.replace("government", "حکومت")
    ok, probs = R.validate(SAMPLE, bad_script)
    check("rejects Urdu script", ok is False and any("script" in p.lower() for p in probs), str(probs))

    ok, probs = R.validate(SAMPLE, "\n".join(SAMPLE.split("\n")[:3]))
    check("rejects wrong line count", ok is False and any("Line count" in p for p in probs), str(probs))

    ok, probs = R.validate(SAMPLE, SAMPLE.replace("100,000", "one hundred thousand"))
    check("rejects digits->words", ok is False and any("Numbers" in p for p in probs), str(probs))

    ok, probs = R.validate(SAMPLE, SAMPLE)
    check("flags untranslated English", ok is False and any("untranslated" in p.lower() for p in probs),
          str(probs))

    tagged = "Breaking #PakistanNews by @reporter\n- 3 people killed in Quetta"
    ok, probs = R.validate(tagged, "Breaking news mein 3 afraad Quetta mein jaan-ba-haq")
    check("flags dropped hashtag/mention", ok is False
          and any("Hashtag" in p for p in probs) and any("mention" in p.lower() for p in probs), str(probs))

    emojied = "\U0001F3CF Pakistan won the match\n- 3 wickets left"
    good = "\U0001F3CF Pakistan ne match jeet liya\n- 3 wickets baqi thay"
    ok, probs = R.validate(emojied, good)
    check("accepts a good translation", ok is True, str(probs))


def test_strip_wrappers() -> None:
    print("\n[3] LLM noise stripping")
    raw = "```text\nTranslation: Hukumat ne elaan kar diya\n```"
    check("removes fences + label", R.strip_wrappers(raw) == "Hukumat ne elaan kar diya",
          repr(R.strip_wrappers(raw)))


def test_fallback() -> None:
    print("\n[4] Fallback keeps the pipeline alive")
    res = R.translate_news(SAMPLE, provider="openai", api_key="", max_retries=0, timeout=2)
    check("returns fallback method", res["method"] == "fallback", res["method"])
    check("fallback has 5 lines", len(res["lines_ur"]) == 5, str(len(res["lines_ur"])))
    check("fallback has no Urdu script", not R.has_urdu_script(res["roman_urdu"]))
    check("warning present", bool(res["warning"]), str(res["warning"]))


def test_payload() -> None:
    print("\n[5] Structured payload (n8n style)")
    payload = {
        "title_en": "Petrol price may increase by Rs 8 per litre this week",
        "bullets_en": [
            "The government is reviewing oil prices today",
            "Petrol could become Rs 8 per litre more expensive",
            "New prices will apply from midnight",
        ],
        "summary_en": "Consumers are worried about inflation.",
    }
    out = R.translate_payload(payload, provider="mock")
    check("title_ur produced", bool(out.get("title_ur")), json.dumps(out, ensure_ascii=False)[:200])
    check("3 bullets -> 3 bullets_ur", len(out.get("bullets_ur", [])) == 3, str(out.get("bullets_ur")))
    check("roman_urdu card string built", "\n" in out.get("roman_urdu", ""), out.get("roman_urdu", "")[:120])
    check("no Urdu script anywhere", not R.has_urdu_script(json.dumps(out, ensure_ascii=False)))


def test_http() -> None:
    print("\n[6] HTTP server (mock mode)")
    http_server.DEFAULTS.update(provider="mock")
    port = 8899
    from http.server import ThreadingHTTPServer
    srv = ThreadingHTTPServer(("127.0.0.1", port), http_server.Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    time.sleep(0.6)

    def call(path: str, body: dict) -> dict:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=10) as resp:
            health = json.loads(resp.read().decode("utf-8"))
        check("/health responds", health.get("status") == "ok", str(health))

        r1 = call("/translate", {"text": SAMPLE})
        check("POST /translate ok", r1.get("ok") is True and len(r1["lines_ur"]) == 5,
              json.dumps(r1, ensure_ascii=False)[:200])

        r2 = call("/translate-card", {
            "title_en": "Earthquake hits Quetta",
            "bullets_en": ["18 people killed", "40 injured", "Rescue teams reached after 6 hours"],
        })
        check("POST /translate-card ok", r2.get("ok") is True and r2["bullet_count"] == 3,
              json.dumps(r2, ensure_ascii=False)[:200])
        check("card_text_ur has title + 3 bullets",
              len(r2["card_text_ur"].split("\n")) == 4, r2["card_text_ur"])
        check("bullets_en and bullets_ur aligned", len(r2["bullets_en"]) == len(r2["bullets_ur"]))
    finally:
        srv.shutdown()
        srv.server_close()


def test_card_constraints() -> None:
    print("\n[8] Card constraints: MAXLEN=118 + paste_check (news_agent compatibility)")
    check("MAXLEN defaults to 118", R.MAXLEN == 118, str(R.MAXLEN))
    check("PASTE_N defaults to 4", R.PASTE_N == 4, str(R.PASTE_N))

    src = "Headline here\n- short bullet"
    long_line = "- " + "word " * 40            # way over 118
    ok, probs = R.validate(src, "Headline\n" + long_line.strip(), max_line_len=118)
    check("rejects line over 118 chars", ok is False and any("card cap" in p for p in probs), str(probs))

    ok, probs = R.validate(src, "Headline\n- chhota bullet", max_line_len=118)
    check("accepts lines within cap", ok is True, str(probs))

    # paste_check: 4+ consecutive English words copied verbatim
    en = "Telangana targets 5 GW data centre capacity by 2030"
    pasted = "Telangana targets 5 GW data centre capacity 2030 tak"
    check("paste_check flags verbatim run", R.paste_check(en, pasted, 4) is True)
    urdu_ish = "Telangana ne 2030 tak 5 GW ke data centres ki salahiyat barhanay ka hadaf rakha"
    check("paste_check passes real Urdu", R.paste_check(en, urdu_ish, 4) is False)
    ok, probs = R.validate(en, urdu_ish, paste_n=4)
    check("validate() clean for good Urdu", not any("verbatim" in p for p in probs), str(probs))
    ok, probs = R.validate(en, pasted, paste_n=4)
    check("validate() flags English paste", ok is False and any("verbatim" in p for p in probs), str(probs))

    # DOCUMENTED GOTCHA: paste_check(n=4) is aggressive - a legitimate technical
    # noun phrase of 4+ words ("5 GW data centre") trips it even in good Urdu.
    # This is exactly what silently kills bullets in news_agent/autod.py.
    tricky = "Telangana ne 5 GW data centre ki salahiyat barhanay ka faisla kiya"
    check("4-word technical phrase trips paste_check (known gate behaviour)",
          R.paste_check(en, tricky, 4) is True)
    check("...but passes with paste_n=5", R.paste_check(en, tricky, 5) is False)

    check("trim_to respects word boundary", len(R.trim_to("a" * 50 + " bbb ccc", 30)) <= 30)
    check("trim_to leaves short lines alone", R.trim_to("chhota", 118) == "chhota")


def test_drop_in_api() -> None:
    print("\n[9] Drop-in API for news_agent/autod.py")
    # translate_lines() must return the EXACT shape the old module returned
    res = R.translate_lines(["Petrol price may increase", "The government is reviewing prices"],
                            provider="mock")
    for key in ("ok", "lines", "method", "problems", "warning"):
        check(f"translate_lines has '{key}'", key in res, str(list(res.keys())))
    check("translate_lines returns 2 lines", len(res["lines"]) == 2, str(res["lines"]))
    check("every line <= MAXLEN", all(len(l) <= R.MAXLEN for l in res["lines"]), str(res["lines"]))
    check("translate_lines never raises on empty", R.translate_lines([], provider="mock")["ok"] is False)
    check("translate_lines never raises on None", R.translate_lines(None, provider="mock")["ok"] is False)

    # unconfigured provider must report ok=False (so autod.py's _ru_on() gate works)
    un = R.translate_lines(["Petrol price may increase"], provider="openai", api_key="")
    check("unconfigured -> ok=False + 'not configured'",
          un["ok"] is False and any("configured" in p for p in un["problems"]), str(un["problems"]))
    check("configured('mock') is True", R.configured("mock") is True)

    # card mode: title_en=/bullets_en= (old module's translate_news signature)
    card = R.translate_news(title_en="Earthquake hits Quetta",
                            bullets_en=["18 people killed", "40 injured", "Rescue teams reached"],
                            provider="mock")
    for key in ("ok", "title_ur", "bullets_ur", "problems", "warning", "method"):
        check(f"card mode has '{key}'", key in card, str(list(card.keys())))
    check("card mode: 3 bullets out", len(card["bullets_ur"]) == 3, str(card["bullets_ur"]))
    check("card mode: title_ur non-empty", bool(card["title_ur"]), str(card["title_ur"]))
    check("card mode: all lines <= MAXLEN",
          all(len(l) <= R.MAXLEN for l in [card["title_ur"]] + card["bullets_ur"]))

    # text mode must still work exactly as before
    txt = R.translate_news("Petrol price may increase", provider="mock")
    check("text mode still returns roman_urdu", "roman_urdu" in txt and bool(txt["roman_urdu"]))


def test_prompt_files() -> None:
    print("\n[7] Prompt assets")
    sp = R.load_system_prompt()
    check("system prompt loaded (>1000 chars)", len(sp) > 1000, str(len(sp)))
    check("prompt forbids Urdu script", "Roman script ONLY" in sp)
    check("prompt enforces the 118-char card cap", "118 characters" in sp)
    check("prompt forbids English paste runs", "NO ENGLISH PASTE" in sp)
    fs = R.load_few_shot()
    check("few-shot examples parsed", len(fs) >= 4, str(len(fs)))
    check("few-shot alternates user/assistant",
          all(m["role"] == ("user" if i % 2 == 0 else "assistant") for i, m in enumerate(fs)))
    check("few-shot contains no Urdu script",
          not any(R.has_urdu_script(m["content"]) for m in fs))


if __name__ == "__main__":
    print("=" * 62)
    print("Roman Urdu translation step -- offline tests")
    print("=" * 62)
    test_prompt_files()
    test_structure()
    test_validator()
    test_strip_wrappers()
    test_fallback()
    test_payload()
    test_http()
    test_card_constraints()
    test_drop_in_api()
    print("\n" + "=" * 62)
    print(f"RESULT: {PASS} passed, {FAIL} failed")
    print("=" * 62)
    sys.exit(1 if FAIL else 0)

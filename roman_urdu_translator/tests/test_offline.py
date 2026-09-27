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


def test_prompt_files() -> None:
    print("\n[7] Prompt assets")
    sp = R.load_system_prompt()
    check("system prompt loaded (>1000 chars)", len(sp) > 1000, str(len(sp)))
    check("prompt forbids Urdu script", "Roman script ONLY" in sp)
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
    print("\n" + "=" * 62)
    print(f"RESULT: {PASS} passed, {FAIL} failed")
    print("=" * 62)
    sys.exit(1 if FAIL else 0)

"""Offline tests for roman_urdu (no network, no API key). Run:
    python3 tests/test_roman_urdu.py     from the news_agent directory.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["RU_PROVIDER"] = "mock"
os.environ.pop("RU_API_KEY", None)
import roman_urdu as R  # noqa: E402

N = F = 0


def ck(name, cond):
    global N, F
    N += 1
    if not cond:
        F += 1
        print("FAIL:", name)


def mock(mode):
    os.environ["RU_MOCK"] = mode


SRC = ["Earthquake of magnitude 6.2 hits eastern Afghanistan",
       "At least 18 people were killed and 40 injured",
       "Rescue teams reached the affected area after six hours",
       "The UN has promised emergency aid",
       "Roads and communication networks remain badly damaged"]

mock("good")
r = R.translate_lines(SRC)
ck("happy path ok", r["ok"] and len(r["lines"]) == len(SRC))
ck("digits locked", "6.2" in r["lines"][0] and "18" in r["lines"][1])
ck("all roman", not any(R._SCRIPT_RE.search(l) for l in r["lines"]))
ck("lines end with period", all(l.endswith(".") for l in r["lines"]))
ck("length cap", all(len(l) <= R.MAXLEN for l in r["lines"]))

mock("script")
r = R.translate_lines(SRC)
ck("urdu script rejected", not r["ok"] and any("script" in p for p in r["problems"]))

mock("short")
r = R.translate_lines(SRC)
ck("line-count enforced", not r["ok"] and any("line-count" in p for p in r["problems"]))

mock("same")
r = R.translate_lines(SRC)
ck("untranslated detected", not r["ok"] and any("untranslated" in p for p in r["problems"]))

mock("digits")
r = R.translate_lines(["Petrol price may increase by Rs 8 per litre this week"])
ck("digit loss caught", not r["ok"] and any("number" in p for p in r["problems"]))

mock("wrapper")
r = R.translate_lines(["Google launches new model for smartphones"])
ck("wrapper chatter stripped & ok", r["ok"] and "sure" not in r["lines"][0].lower())
ck("no bullet marker leaks", not r["lines"][0].startswith("-"))

ck("unconfigured -> not ok", not R.configured() or os.environ.get("RU_PROVIDER") == "mock")
os.environ["RU_PROVIDER"] = "openai"
ck("no key => not configured", not R.configured())
r = R.translate_lines(["Government announced new aid"])
ck("never crashes without key", r["ok"] is False and r["method"] == "none")
os.environ["RU_PROVIDER"] = "mock"

r = R.translate_news(title_en=SRC[0], bullets_en=SRC[1:])
mock("good")
r = R.translate_news(title_en=SRC[0], bullets_en=SRC[1:])
ck("card API alignment", r["ok"] and len(r["bullets_ur"]) == 4
   and "6.2" in r["title_ur"])

ck("empty input handled", not R.translate_lines([])["ok"])
ck("junk input handled", not R.translate_lines(["   ", ""])["ok"])

os.environ["RU_MAX_RETRIES"] = "0"
mock("short")
ck("retries bounded", not R.translate_lines(SRC)["ok"])
del os.environ["RU_MAX_RETRIES"]

print(f"RESULT: {N - F} passed, {F} failed")
sys.exit(1 if F else 0)

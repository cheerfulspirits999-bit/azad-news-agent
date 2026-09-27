"""Contract tests for the roman_urdu SHIM (news_agent/roman_urdu.py).

The engine itself is covered by the owner's own suite:
    python3 ../roman_urdu_translator/tests/test_offline.py   (66 tests)
This file only checks what news_agent/autod.py relies on: import works,
public surface exists, no-key mode never raises and never says ok.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.pop("RU_API_KEY", None)
os.environ.pop("RU_PROVIDER", None)
import roman_urdu as R  # noqa: E402

N = F = 0


def ck(name, cond):
    global N, F
    N += 1
    if not cond:
        F += 1
        print("FAIL:", name)


ck("engine is owner package", R.ENGINE == "owner-package")
for fn in ("configured", "translate_lines", "translate_news", "paste_check"):
    ck(f"surface: {fn} callable", callable(getattr(R, fn, None)))
ck("MAXLEN = 118 (card cap)", R.MAXLEN == 118)
ck("no key => not configured", R.configured() is False)
r = R.translate_lines(["Government announced a new tax on petrol."])
ck("no-key call returns clean failure", r["ok"] is False and r["lines"] == []
   and "problems" in r and "warning" in r)
ck("empty input handled", R.translate_lines([])["ok"] is False)

# paste semantics: verbatim 4+ word runs from the EN line are caught;
# translated prose that legitimately keeps proper nouns is not.
en = "Woman belt shop owner in Hyderabad booked under PD Act."
ur_ok = "Hyderabad mein khatoon belt shop ke maalik PD Act ke tahat book hue."
ur_bad = "Woman belt shop owner in Hyderabad ko PD Act ke tahat book kiya gaya."
ck("names-only Urdu passes", R.paste_check(en, ur_ok) is False)
ck("verbatim 4-word run caught", R.paste_check(en, ur_bad) is True)

mock_on = os.environ.get("RU_PROVIDER")
os.environ["RU_PROVIDER"] = "mock"
import importlib  # noqa: E402
importlib.reload(R)
ck("mock provider => configured", R.configured() is True)
r = R.translate_lines(["Government announced a new tax on petrol."])
ck("mock call never raises", isinstance(r, dict) and "ok" in r)
if mock_on is None:
    del os.environ["RU_PROVIDER"]
else:
    os.environ["RU_PROVIDER"] = mock_on
importlib.reload(R)

print(f"RESULT: {N - F} passed, {F} failed")
sys.exit(1 if F else 0)

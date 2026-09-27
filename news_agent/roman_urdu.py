"""roman_urdu - public shim wired into the hourly digest (autod.py).

27 Sep: the owner's own translator package (repo-root ``roman_urdu_translator/``,
pushed with run.sh and tests) is now the ENGINE. This module just loads it and
re-exports the exact call surface autod.py uses, so integration never breaks
even if the package folder is moved or missing - we fall back to the previous
built-in engine (roman_urdu_legacy.py, kept in-tree as the safety net).

autod.py calls:  configured(), translate_lines(list[str]) -> {ok, lines,
problems, warning}, paste_check(en, ur), MAXLEN.
"""
import importlib.util
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_OWNER_ENGINE = os.path.normpath(
    os.path.join(_HERE, "..", "roman_urdu_translator", "roman_urdu.py"))
_LEGACY = os.path.join(_HERE, "roman_urdu_legacy.py")


def _load_from(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


if os.path.isfile(_OWNER_ENGINE):
    try:
        _mod = _load_from("roman_urdu_translator_engine", _OWNER_ENGINE)
        ENGINE = "owner-package"
    except Exception as _exc:                      # never break the cycle
        sys.stderr.write(f"[roman_urdu] owner package failed to load ({_exc}); "
                         "using legacy fallback engine\n")
        _mod = _load_from("roman_urdu_legacy", _LEGACY)
        ENGINE = "legacy"
else:
    _mod = _load_from("roman_urdu_legacy", _LEGACY)
    ENGINE = "legacy"

# --- re-export the surface autod.py depends on ------------------------------
MAXLEN = int(getattr(_mod, "MAXLEN", 118))
configured = _mod.configured
translate_lines = _mod.translate_lines
translate_news = _mod.translate_news
paste_check = getattr(_mod, "paste_check", None)
if paste_check is None:                            # owner pkg removed it? legacy has it
    _lm = sys.modules.get("roman_urdu_legacy") or _load_from("roman_urdu_legacy", _LEGACY)
    paste_check = _lm.paste_check

if __name__ == "__main__":
    import json
    txt = sys.stdin.read().strip()
    print(json.dumps(translate_lines(txt.splitlines()), ensure_ascii=False,
                     indent=2))

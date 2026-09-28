"""Roman-Urdu quality gate - canonical engine + adapter.

THE quality engine is the OWNER'S package (roman_urdu_translator/ at the
repo root). This module loads it under the name
`roman_urdu_translator_engine` so nothing else needs to change, and
exposes translate_lines()/translate_news(). The legacy pipeline lives on
in roman_urdu_legacy.py, used ONLY if the owner package cannot load at
all.

Two enforced operating points (post-mortem of the 27-28 Sep posting
outage, documented in OPERATIONS.md):
  * RU_TEMPERATURE defaults to 0 - at the package default (0.2) the
    model intermittently ECHOES the English input instead of translating
    it; temperature 0 makes the clean-translation behaviour stable.
  * use_few_shot is forced OFF - the few-shot examples shipped with the
    package teach a code-mixed EN+UR style which the model mirrors; the
    package's own strict paste validator then rejects those lines, every
    retry fails the same way, and cycles starve at 0-1/5 accepted lines.
    With few-shot off, the exact same leads translate and validate 5/5.
Both are defaults an operator can still override via the workflow env -
the shim only supplies values when none were given.
"""
import importlib.util
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_PKG_DIR = os.path.join(_ROOT, "roman_urdu_translator")
_ENGINE_PATH = os.path.join(_PKG_DIR, "roman_urdu.py")
_LEGACY_PATH = os.path.join(_HERE, "roman_urdu_legacy.py")

# deterministic translation default (see docstring); setdefault keeps an
# explicit ops override (workflow env) authoritative
os.environ.setdefault("RU_TEMPERATURE", "0")

def _load_from(modname, path):
    spec = importlib.util.spec_from_file_location(modname, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[modname] = m
    spec.loader.exec_module(m)
    return m


_mod = None
_ENGINE_NAME = "legacy"
try:
    _mod = _load_from("roman_urdu_translator_engine", _ENGINE_PATH)
    if not callable(getattr(_mod, "translate_lines", None)):
        _mod = None
    else:
        _ENGINE_NAME = "owner-package"
except Exception as e:  # broken engine -> legacy; must never crash the cycle
    print(f"[roman_urdu] owner package failed to load ({e}); using legacy",
          file=sys.stderr)
    _mod = None

if _mod is None:
    try:
        _mod = _load_from("roman_urdu_legacy", _LEGACY_PATH)
    except Exception as e2:
        raise RuntimeError(f"neither engine loads: {e2}") from e2
else:
    # romanize.py (legacy) owns paste_check; the owner package defines it
    # inline. The shim must export it either way for card validation.
    try:
        from romanize import paste_check  # noqa: F401  (same function)
    except ImportError:
        from roman_urdu_legacy import paste_check  # noqa: F401

# --- owner-package operating-point enforcement (see docstring) -----------
if _ENGINE_NAME == "owner-package":
    _orig_translate_lines = _mod.translate_lines
    _orig_translate_news = _mod.translate_news

    def translate_lines(lines, **kw):
        kw.setdefault("use_few_shot", False)
        return _orig_translate_lines(lines, **kw)

    def translate_news(text, **kw):
        kw.setdefault("use_few_shot", False)
        return _orig_translate_news(text, **kw)

    _mod.translate_lines = translate_lines
    _mod.translate_news = translate_news
else:
    translate_lines = _mod.translate_lines
    translate_news = getattr(_mod, "translate_news", None) or (
        lambda text, **kw: _mod.translate_lines([line.strip() for line in
                                                  text.splitlines() if line.strip()]))

# bind the (possibly wrapped) engine entry points as module globals
_translate_lines = _mod.translate_lines
_translate_news = _mod.translate_news

ENGINE = _ENGINE_NAME  # "owner-package" when canonical engine is live
MAXLEN = getattr(_mod, "MAXLEN", 118)
_CLI = os.path.join(_PKG_DIR, "cli.py")
CLI_PATH = os.path.join(_PKG_DIR, "cli.py")
translate_text = translate_news
TRANSLATE_TIMEOUT_SEC = int(os.environ.get("RU_TIMEOUT", "40"))

# CLI-compatible shims for older callers
romanize = translate_lines
sanitize_roman_urdu = None
_EN_NUM_WORDS = None


def configured():
    """True when the engine can answer (key set). Never crashes callers."""
    try:
        f = getattr(_mod, "configured", None)
        return bool(f()) if callable(f) else True
    except Exception:
        return False


def __getattr__(name):
    global sanitize_roman_urdu, _EN_NUM_WORDS
    if name == "sanitize_roman_urdu":
        sanitize_roman_urdu = getattr(_mod, "sanitize_roman_urdu", None)
        return sanitize_roman_urdu
    if name == "_EN_NUM_WORDS":
        _EN_NUM_WORDS = getattr(_mod, "_EN_NUM_WORDS", {})
        return _EN_NUM_WORDS
    raise AttributeError(name)


if __name__ == "__main__":
    import json
    txt = sys.stdin.read().strip()
    print(json.dumps(translate_lines(txt.splitlines()), ensure_ascii=False,
                     indent=2))

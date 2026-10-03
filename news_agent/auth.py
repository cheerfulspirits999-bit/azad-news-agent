"""Token self-renewal for the Azad Daily publisher (owner 26 Sep: permanent).

Runs before every publish cycle. Needs four GitHub Secrets once set by the
owner: FB_PAGE_TOKEN, FB_APP_ID, FB_APP_SECRET, FB_REFRESH_TOKEN.

Logic:
  1. debug the live FB_PAGE_TOKEN; if it is valid and does not expire within
     48h (or never expires) -> do nothing and exit 0.
  2. otherwise: FB_REFRESH_TOKEN (long-lived user token, ~60d) -> re-exchange
     for a fresh long-lived user token -> derive a NON-EXPIRING Page token.
  3. push both fresh tokens back into the GitHub secrets so the chain
     renews itself forever, and export FB_PAGE_TOKEN for this very run.

Any failure exits non-zero WITHOUT touching the pipeline: the normal publish
then fails loudly (rc 5 + pending retry + watchdog alert) rather than posting
through any other route.
"""
import base64
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from nacl.public import PublicKey, SealedBox

API = "https://graph.facebook.com/v21.0"
GRACE = 48 * 3600.0  # renew when < 48h of life remains
# Owner mandate (2 Oct, verbatim): page 1538679366414544 / app 1605100577825791.
# This agent posts to Azad Daily and NOTHING else - the owner runs a separate
# agent for National Reporter on a separate account, and a credential derived
# for any other page is a bug, not a fallback.
AZAD_PAGE_ID = "1538679366414544"


def _norm_pid(raw):
    """A page id must always be an exact digit string.

    3 Oct: an unquoted numeric env literal reaches the Actions runner as a
    FLOAT ("1.53867936641454E+15"), and any comparison against the real id
    then silently fails. Normalise float/exponent forms back to digits.
    """
    s = str(raw or "").strip()
    if not s:
        return ""
    try:
        f = float(s)
        if f == int(f) and len(str(int(f))) >= 10:
            return str(int(f))
    except (TypeError, ValueError):
        pass
    return s


def _jget(url):
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)


def _debug(token):
    try:
        d = _jget(f"{API}/debug_token?input_token={token}"
                  f"&access_token={token}")["data"]
    except Exception:
        return None
    return d if d.get("is_valid") else None


# Graph codes that mean "this token will never work again" even though
# debug_token still reports a future expiry for it.
#   460 - session invalidated (user changed password / Facebook rotated it)
#   463 - session expired
#   102 - session expired (older wording)
_DEAD_SUBCODES = {460, 463, 102}


def _token_alive(tok, pid=""):
    """Is this token ACTUALLY usable, as opposed to merely unexpired?

    3 Oct: debug_token reported is_valid=True, expires_at=2026-12-02 for a
    token Facebook had already invalidated (code 190 subcode 460). Because
    _need_renew trusted that, auth.py printed "Page token healthy, nothing to
    do" on every cycle while every single post failed rc=5 for nine cycles
    straight and the only clue was ALERT.md. Only a REAL call proves liveness,
    so make one and read the error instead of guessing.

    Returns True when the token works, or when the answer is unknown (never
    churn the vault on a transient network error).
    """
    target = pid or _norm_pid(os.environ.get("FB_PAGE_ID", "")) or AZAD_PAGE_ID
    url = f"{API}/{target}?fields=id&access_token={urllib.parse.quote(tok)}"
    try:
        d = _jget(url)
        return bool(d.get("id"))
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode() or "{}")
        except Exception:
            return True
        err = body.get("error") or {}
        code = err.get("code")
        sub = err.get("error_subcode")
        if code == 190 and sub in _DEAD_SUBCODES:
            print(f"auth: token is DEAD - Graph code {code} subcode {sub}: "
                  f"{(err.get('message') or '')[:160]}", file=sys.stderr)
            return False
        return True
    except Exception:
        return True


def _need_renew(tok):
    if not tok:
        return True
    d = _debug(tok)
    if not d:
        return True
    exp = d.get("expires_at") or 0
    if exp != 0 and exp - time.time() < GRACE:
        return True
    # 3 Oct: an invalidated token can still report a far-future expiry, so the
    # expiry check above alone let a dead credential sit in the vault while
    # every post failed. Probe for real before calling it healthy.
    return not _token_alive(tok)


def _exchange(refresh, app_id, app_secret):
    q = urllib.parse.urlencode({"grant_type": "fb_exchange_token",
                                "client_id": app_id,
                                "client_secret": app_secret,
                                "fb_exchange_token": refresh})
    return _jget(f"{API}/oauth/access_token?{q}")["access_token"]


def _page_tokens(user_tok):
    d = _jget(f"{API}/me/accounts?fields=id,access_token&access_token={user_tok}")
    # 3 Oct: Graph returns the page `id` as an INTEGER. Keying the dict with
    # the raw value made every later `pid not in pages` lookup (pid is the
    # string AZAD_PAGE_ID) fail - so a perfectly valid token was rejected with
    # "no Page access from user token". Normalise to str.
    return {str(p["id"]): p["access_token"] for p in d.get("data", [])}


def _set_secret(name, value):
    """Encrypt + PUT a repo secret. Requires GITHUB_TOKEN with secrets:write."""
    owner = os.environ.get("GH_OWNER", "")
    repo = os.environ.get("GH_REPO", "")
    gh_tok = os.environ.get("AGENT_PAT", "") or os.environ.get("GITHUB_TOKEN", "")
    if not (owner and repo and gh_tok):
        print(f"auth: cannot push secret {name} (missing GH env)", file=sys.stderr)
        return False
    api = f"https://api.github.com/repos/{owner}/{repo}/actions/secrets/{name}"
    hdr = {"Authorization": f"Bearer {gh_tok}",
           "Accept": "application/vnd.github+json", "User-Agent": "azad-auth"}

    def req(url):
        r = urllib.request.Request(url, headers=hdr)
        with urllib.request.urlopen(r, timeout=30) as resp:
            return json.load(resp)

    base = f"https://api.github.com/repos/{owner}/{repo}/actions/secrets"
    pk = req(f"{base}/public-key")
    sealed = SealedBox(PublicKey(base64.b64decode(pk["key"]))).encrypt(
        value.encode())
    body = {"encrypted_value": base64.b64encode(sealed).decode(),
            "key_id": pk.get("key_id")}
    r = urllib.request.Request(api, data=json.dumps(body).encode(),
                               headers={**hdr, "Content-Type": "application/json"},
                               method="PUT")
    urllib.request.urlopen(r, timeout=30)
    print(f"auth: updated secret {name}")
    return True


def _export_current():
    """Never let a failed refresh blind the publisher: always export the
    token/pid from the vault env for THIS run; publish step stays loud if
    the token itself is dead."""
    gh_env = os.environ.get("GITHUB_ENV", "")
    tok = os.environ.get("FB_PAGE_TOKEN", "").strip()
    pid = _norm_pid(os.environ.get("FB_PAGE_ID", ""))
    if gh_env:
        with open(gh_env, "a", encoding="utf-8") as f:
            f.write(f"FB_PAGE_TOKEN={tok}\nFB_PAGE_ID={pid}\n")
    return tok, pid

def _su_pages():
    """System-user path (owner 2 Oct): the Business-portfolio system user
    token (secret FB_SU_TOKEN) is never tied to a browser session, so its
    assigned page tokens are the most durable credential we can hold.
    Returns {page_id: page_access_token} - empty when unset/unassigned."""
    su = os.environ.get("FB_SU_TOKEN", "").strip()
    if not su:
        return {}
    try:
        d = _jget(f"{API}/me/assigned_pages?fields=id,access_token&limit=20"
                  f"&access_token={su}")
        # same int/str normalisation as _page_tokens (3 Oct)
        return {str(p["id"]): p["access_token"] for p in d.get("data", [])
                if p.get("access_token")}
    except Exception as e:
        print(f"auth: system-user page list failed ({str(e)[:120]})",
              file=sys.stderr)
        return {}


def _ig_state(tok, pid):
    """Owner 2 Oct: the page's linked Instagram gets the same card. This
    resolves the link + the token's granted scopes ONCE PER RUN and drops
    them in state/ig_link.json, which the relay commits (observable from
    anywhere) and publish.py reads (no extra Graph round-trip at publish
    time). Never fatal: no link or no scope just records the reason."""
    out = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "ig_id": None,
           "username": None, "scopes": [], "reason": ""}
    try:
        d = _jget(f"{API}/{pid}?fields="
                  "instagram_business_account.id,instagram_business_account.username"
                  f"&access_token={tok}")
        ig = d.get("instagram_business_account") or {}
        out["ig_id"] = ig.get("id")
        out["username"] = ig.get("username")
        if not ig:
            out["reason"] = "no linked IG business account visible to the page token"
        pr = _jget(f"{API}/me/permissions?access_token={tok}")
        out["scopes"] = sorted(x.get("permission", "") for x in pr.get("data", []))
    except Exception as e:
        out["reason"] = str(e)[:200]
    if not out["scopes"]:
        # Page tokens (incl. system-user derived) cannot list /me/permissions;
        # debug_token on themselves works and carries the same scope array.
        d2 = _debug(tok) or {}
        out["scopes"] = sorted(d2.get("scopes", []))
    try:
        sd = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state")
        with open(os.path.join(sd, "ig_link.json"), "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
    except Exception:
        pass
    line = (f"IG link: ig_id={out['ig_id']} user={out['username']} "
            f"scopes={','.join(out['scopes']) or '-'} {out['reason']}".rstrip())
    print("auth:", line)
    smry = os.environ.get("GITHUB_STEP_SUMMARY", "")
    if smry:
        try:
            with open(smry, "a", encoding="utf-8") as f:
                f.write(f"**auth** - {line}\n")
        except Exception:
            pass
    return out


def _visibility_check(tok, pid_hint=""):
    """Owner 28 Sep: posts must reach the PUBLIC, every cycle, verifiably.
    Meta renders posts published through a Page's own auto-app (app_id ==
    page_id) only for accounts with a role on that app - the post itself
    looks public (is_hidden false, no feed_targeting), so the gate can only
    be detected structurally. Check it at every relay start, print the
    verdict and commit it in state/visibility_mode.json. Never fails the
    pipeline."""
    try:
        d = _debug(tok)
        if not d:
            return
        app = str(d.get("app_id", ""))
        obj = str(d.get("id", "") or pid_hint or
                _norm_pid(os.environ.get("FB_PAGE_ID", "")))
        gated = bool(app) and app == obj
        mode = ("ROLE-GATED: publishing app is the Page auto-app (" + app +
                "). Meta shows its posts only to app-role accounts - owner "
                "must provide a fresh token from a LIVE app with "
                "pages_manage_posts (developers.facebook.com > My Apps).") \
            if gated else ("public-at-API: signing app " + (app or "?") +
                           " is not the Page auto-app; if reach is still "
                           "limited, switch THIS app to Live in the Meta "
                           "dashboard - no review needed for your own Page")
        print("auth: VISIBILITY MODE - " + mode)
        state = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "state")
        os.makedirs(state, exist_ok=True)
        json.dump({"mode": "role-gated" if gated else "public",
                   "app_id": app, "page_id": obj,
                   "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                               time.gmtime()),
                   "note": mode},
                  open(os.path.join(state, "visibility_mode.json"), "w"),
                  indent=1)
    except Exception as e:
        print(f"auth: visibility check skipped ({e})")


def main():
    _export_current()  # safety net first; renewed values overwrite below
    tok = os.environ.get("FB_PAGE_TOKEN", "").strip()
    # IG link must be resolved FIRST, before anything downstream can die:
    # publish.py's piggyback reads state/ig_link.json, so a skipped or
    # failed visibility/feed probe must never cost us the IG answer.
    _ig_state(tok, _norm_pid(os.environ.get("FB_PAGE_ID", "")))
    upgrade = False
    if not _need_renew(tok):
        d0 = _debug(tok) or {}
        if (os.environ.get("FB_SU_TOKEN")
                and "instagram_basic" not in (d0.get("scopes") or [])):
            upgrade = True
            print("auth: page token valid but IG-blind - upgrading via "
                  "system user")
    if upgrade or _need_renew(tok):
        pages = _su_pages()
        if pages:
            print(f"auth: system-user grants {len(pages)} page token(s)")
    if not upgrade and not _need_renew(tok):
        print("auth: Page token healthy, nothing to do")
        try:
            _visibility_check(tok)
        except Exception as _ve:
            print(f"auth: visibility probe failed ({_ve}) - non-fatal")
        return 0
    user = None
    if not pages:
        app_id = (os.environ.get("FB_APP_ID", "")
                  or os.environ.get("FACEBOOK_APP_ID", ""))
        app_secret = os.environ.get("FB_APP_SECRET", "")
        refresh = os.environ.get("FB_REFRESH_TOKEN", "").strip()
        if not (app_id and app_secret and refresh):
            print("auth: renewal IMPOSSIBLE - set FB_APP_ID (or the alias "
                  "this repo actually uses, FACEBOOK_APP_ID) + FB_APP_SECRET "
                  "+ FB_REFRESH_TOKEN (or FB_SU_TOKEN); confirm they reach "
                  "this step's env", file=sys.stderr)
            return 2
        try:
            user = _exchange(refresh, app_id, app_secret)  # fresh 60d
            pages = _page_tokens(user)
        except Exception as _ue:
            print(f"auth: user-token renewal failed ({str(_ue)[:140]})",
                  file=sys.stderr)
            pages = {}
        if not pages:
            print("auth: no Page access from user token either "
                  "(system user unassigned too?)", file=sys.stderr)
            return 3
    # 2 Oct night (owner: "national reporter is also getting posted"): the
    # ONLY page this agent may ever hold a credential for is Azad Daily. The
    # old `else: next(iter(pages.items()))` branch minted a token for WHATEVER
    # page the owner's user token happened to list first - and that user token
    # administers National Reporter too, so one empty FB_PAGE_ID run could
    # silently swap the vault credential to the wrong page. Deleted: the page
    # is pinned in code, and "not in the list" means STOP, never guess.
    pid = AZAD_PAGE_ID
    if pid not in pages:
        print("auth: REFUSED - " + pid + " (Azad Daily) is not among the "
              "token's pages " + str(sorted(pages)) + "; keeping the last "
              "good vault token and deriving nothing", file=sys.stderr)
        return 2
    ptok = pages[pid]
    if "pages_manage_posts" not in (_debug(ptok) or {}).get("scopes", []):
        print("auth: derived Page token lacks pages_manage_posts", file=sys.stderr)
        return 4
    gh_env = os.environ.get("GITHUB_ENV", "")
    if gh_env:
        with open(gh_env, "a", encoding="utf-8") as f:
            f.write(f"FB_PAGE_TOKEN={ptok}\nFB_PAGE_ID={pid}\n")
    # Vault writes are persistence for FUTURE jobs - a flaky secrets PUT
    # (rate limit, secondary login, 403) must never abort the renewal that
    # THIS job's publisher already relies on (2 Oct incident: old order let
    # a failed PUT strand a stale token in the vault while GITHUB_ENV never
    # got the new one; the step still looked green via continue-on-error).
    _pairs = [("FB_PAGE_TOKEN", ptok)]
    if user:
        _pairs.append(("FB_REFRESH_TOKEN", user))
    for _n, _v in _pairs:
        try:
            _set_secret(_n, _v)
        except Exception as _se:
            print(f"auth: secret {_n} not persisted this run ({_se}) - "
                  "relying on the next run's renewal to retry", file=sys.stderr)
    print(f"auth: renewed page token for {pid}; written to $GITHUB_ENV")
    _visibility_check(ptok, pid)
    _ig_state(ptok, pid)
    return 0


if __name__ == "__main__":
    sys.exit(main())

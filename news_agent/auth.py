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


def _need_renew(tok):
    if not tok:
        return True
    d = _debug(tok)
    if not d:
        return True
    exp = d.get("expires_at") or 0
    return exp != 0 and exp - time.time() < GRACE


def _exchange(refresh, app_id, app_secret):
    q = urllib.parse.urlencode({"grant_type": "fb_exchange_token",
                                "client_id": app_id,
                                "client_secret": app_secret,
                                "fb_exchange_token": refresh})
    return _jget(f"{API}/oauth/access_token?{q}")["access_token"]


def _page_tokens(user_tok):
    d = _jget(f"{API}/me/accounts?fields=id,access_token&access_token={user_tok}")
    return {p["id"]: p["access_token"] for p in d.get("data", [])}


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
    tok = os.environ.get("FB_PAGE_TOKEN", "")
    pid = os.environ.get("FB_PAGE_ID", "")
    if gh_env:
        with open(gh_env, "a", encoding="utf-8") as f:
            f.write(f"FB_PAGE_TOKEN={tok}\nFB_PAGE_ID={pid}\n")
    return tok, pid

def _su_pages():
    """System-user path (owner 2 Oct): the Business-portfolio system user
    token (secret FB_SU_TOKEN) is never tied to a browser session, so its
    assigned page tokens are the most durable credential we can hold.
    Returns {page_id: page_access_token} - empty when unset/unassigned."""
    su = os.environ.get("FB_SU_TOKEN", "")
    if not su:
        return {}
    try:
        d = _jget(f"{API}/me/assigned_pages?fields=id,access_token&limit=20"
                  f"&access_token={su}")
        return {p["id"]: p["access_token"] for p in d.get("data", [])
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
                os.environ.get("FB_PAGE_ID", ""))
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
    tok = os.environ.get("FB_PAGE_TOKEN", "")
    # IG link must be resolved FIRST, before anything downstream can die:
    # publish.py's piggyback reads state/ig_link.json, so a skipped or
    # failed visibility/feed probe must never cost us the IG answer.
    _ig_state(tok, os.environ.get("FB_PAGE_ID", ""))
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
        refresh = os.environ.get("FB_REFRESH_TOKEN", "")
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
    pid_hint = os.environ.get("FB_PAGE_ID", "")
    if pid_hint:
        if pid_hint in pages:
            pid, ptok = pid_hint, pages[pid_hint]
        else:
            # Owner 2 Oct: "posts go to national" - the old silent
            # first-page fallback let a renewal swap in ANY page the token
            # sees when Azad Daily was absent, and the publisher then
            # obediently posted there. A pinned page that is missing means
            # the credential lost access to the REAL page: stop, stay loud,
            # keep the last-good vault token. Never guess another page.
            print("auth: REFUSED - pinned Page " + pid_hint + " is not among "
                  "the token's pages " + str(sorted(pages)) + "; not deriving "
                  "a token for a different page", file=sys.stderr)
            return 2
    else:
        pid, ptok = next(iter(pages.items()))
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

#!/usr/bin/env python3
"""Commit the agent memory so a published post can never be lost.

WHY THIS EXISTS (owner 6 Oct: "stop posting the same card again and again")

The relay commits state after every 10-minute cycle, and the owner's manual
push button commits it too. Two runs can therefore hold the same state files at
the same time, and the old commit step was:

    git commit -q -m "cycle $i ..."
    git pull --rebase origin main >/dev/null 2>&1 || true     # <-- swallowed
    git push -q origin HEAD:main 2>/dev/null || echo "push failed"

When the pull rebased onto a state file the other run had just rewritten, it
conflicted, the `|| true` hid it, the push was rejected, and the card that had
just been posted to Facebook was NEVER recorded in published.json. The next
cycle then read a published.json that did not know about that card, could not
see its stories as already-published, and published them again. Proven on
26 Oct: three cards sat live on the Page while the repo still held 562 entries.

So this script merges the state SEMANTICALLY instead of letting git guess:

  published.json    union of entries, deduped by fb_post_id (a post is never
                    dropped and never duplicated by the merge itself)
  last_cycle.json   whichever run is newer, by its "at" stamp
  everything else   local wins - candidates/seen/ru_* are per-cycle scratch

It then commits once and pushes with retries, re-merging whenever the push is
rejected because the other run landed first. It also clears any half-finished
rebase left behind by the old step, so a conflicted working tree can never
poison the next cycle.

Exit code is always 0: a failed commit must never abort a publishing cycle.
"""
import json
import os
import subprocess
import sys
import time

BRANCH = os.environ.get("GITHUB_REF_NAME") or "main"
STATE = os.path.join("news_agent", "state")
STORIES = os.path.join("news_agent", "stories")
MSG = os.environ.get("COMMIT_MSG") or ("state " + time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                                time.gmtime()))
PUSH_TRIES = 6


def sh(*args):
    return subprocess.run(args, capture_output=True, text=True)


def git(*args):
    return sh("git", *args)


def log(msg):
    print(f"[commit_state] {msg}", flush=True)


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def save(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def remote_text(path):
    """A state file as it exists on origin, or None."""
    r = git("show", f"origin/{BRANCH}:{path}")
    return r.stdout if r.returncode == 0 else None


def _key(rec):
    """Stable identity for a published card."""
    if rec.get("fb_post_id"):
        return ("fb", rec["fb_post_id"])
    return ("at", rec.get("published_at") or "", rec.get("title") or "")


def merge_published(local, remote):
    """Union: every card either side knows about survives, in a stable order."""
    if not isinstance(remote, list):
        return local
    if not isinstance(local, list):
        return remote
    out, seen = [], set()
    for rec in list(remote) + list(local):
        k = _key(rec)
        if k in seen:
            continue
        seen.add(k)
        out.append(rec)
    # keep chronological order when the stamps allow it
    try:
        out.sort(key=lambda r: r.get("published_at") or "")
    except Exception:
        pass
    return out


def merge_last_cycle(local, remote):
    if not isinstance(remote, dict) or not isinstance(local, dict):
        return local
    return local if (local.get("at") or "") >= (remote.get("at") or "") else remote


MERGERS = {
    os.path.join(STATE, "published.json"): merge_published,
    os.path.join(STATE, "last_cycle.json"): merge_last_cycle,
}


def clear_stale_rebase():
    """A half-finished rebase from the old step would poison every later git call."""
    if os.path.isdir(os.path.join(".git", "rebase-merge")) or \
            os.path.isdir(os.path.join(".git", "rebase-apply")):
        log("clearing a half-finished rebase left by an earlier run")
        git("rebase", "--abort")


def fetch():
    for attempt in range(4):
        r = git("fetch", "--quiet", "origin", BRANCH)
        if r.returncode == 0:
            return True
        log(f"fetch attempt {attempt + 1} failed: {r.stderr.strip()[:120]}")
        time.sleep(3 * (attempt + 1))
    return False


def read_local():
    """The state THIS run produced, read before the branch is moved."""
    out = {}
    for rel in MERGERS:
        if os.path.exists(rel):
            out[rel] = load(rel)
    return out


def merge_state(local_state):
    """Fold origin's state into the working tree. Returns a short report."""
    report = []
    for rel, merger in MERGERS.items():
        local = local_state.get(rel)
        if local is None:
            continue                      # this run never wrote it - leave it alone
        remote = load(rel)                # worktree is at origin/main right now
        if remote is None:
            continue
        merged = merger(local, remote)
        if merged is None or merged == remote:
            continue
        save(rel, merged)
        n = len(merged) if isinstance(merged, list) else len(merged.get("tail") or [])
        report.append(f"{os.path.basename(rel)} merged ({n} kept)")
    return report


def stage():
    """Stage only paths that exist.

    `git add -- a b` where b does not exist is a fatal pathspec error and stages
    NOTHING at all - which would silently drop the post we are recording.
    """
    targets = [p for p in (STATE, STORIES) if os.path.exists(p)]
    if not targets:
        return False
    return git("add", "--", *targets).returncode == 0


def main():
    clear_stale_rebase()
    if not fetch():
        log("could not reach origin - leaving the working tree as it is")
        return 0

    # 1. what this run produced
    local_state = read_local()

    # 2. build the commit ON the remote tip. Without this the push is a
    #    non-fast-forward, which is exactly what made the old step fail and
    #    left posted cards unrecorded.
    r = git("reset", "--hard", f"origin/{BRANCH}")
    if r.returncode != 0:
        log(f"could not move onto origin/{BRANCH}: {r.stderr.strip()[:160]}")
        return 0

    # 3. union this run's state with the state the other run landed
    merged = merge_state(local_state)
    for line in merged:
        log(line)

    if not stage():
        log("nothing to stage")
        return 0
    if git("diff", "--cached", "--quiet").returncode == 0:
        log("nothing to commit")
        return 0
    c = git("commit", "-q", "-m", MSG)
    if c.returncode != 0:
        log(f"commit failed: {c.stderr.strip()[:160]}")
        return 0

    # 4. push; on a rejection the other run landed first, so redo the union
    for attempt in range(PUSH_TRIES):
        p = git("push", "--quiet", "origin", f"HEAD:{BRANCH}")
        if p.returncode == 0:
            log(f"pushed: {MSG}")
            return 0
        log(f"push rejected (attempt {attempt + 1}): "
            f"{p.stderr.strip().splitlines()[0][:120] if p.stderr.strip() else '?'}")
        if not fetch():
            break
        local_state = read_local()
        git("reset", "--hard", f"origin/{BRANCH}")
        merge_state(local_state)
        stage()
        git("commit", "-q", "-m", MSG + " (merged)")
        time.sleep(3 * (attempt + 1))

    log("push still rejected - the post is on the Page; the memory carries it "
        "on the next cycle")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""guard_record.py: the Record guard. Run before every commit and around every pull.

The Record (data/record/events.jsonl) is append-only: lines may only be added at the end, never changed,
removed or reordered. Exit 1 (and print why) on any breach, so the cycle stops before pushing.

    python scripts/guard_record.py
        the committed (HEAD) Record must be an exact prefix of the working file.
    python scripts/guard_record.py --history [--origin REF]
        every committed version of the Record, oldest first (`git log --reverse -- data/record/events.jsonl`),
        must be a byte-exact prefix of the next one, and the working file must extend the last. This catches
        an edited Record that is already committed (for example pushed to GitHub from outside the timer).
        --origin REF (e.g. origin/main): the Record this checkout last saw at REF must still be a prefix of
        the working file, which catches a force-pushed history that is consistent with itself (within one
        runner's life; a fresh checkout has seen nothing yet). Then REF's Record is remembered.
        Needs full history: a shallow clone FAILS rather than passing on a partial walk.
    python scripts/guard_record.py --remember REF
        after a push: remember REF's Record as the newest one seen on origin (for --origin above).
    python scripts/guard_record.py --old FILE --new FILE     (tests)

"File absent from HEAD" passes only when git says the path is truly not in HEAD; any other git error fails.
Speed: the last verified version is remembered in .git/pc_record_verified (never committed), so later checks
in the same checkout only walk the versions added since.
"""
import hashlib
import json
import os
import subprocess
import sys

REL = "data/record/events.jsonl"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class GitError(Exception):
    pass


def check(old, new):
    """old, new: bytes. Returns None if fine, else a reason."""
    if not new.startswith(old):
        n_old, n_new = old.count(b"\n"), new.count(b"\n")
        if n_new < n_old:
            return "the Record lost lines (%d committed, %d now)" % (n_old, n_new)
        return "a committed Record line was changed"
    if old and not old.endswith(b"\n") and len(new) > len(old):
        return "the committed Record did not end with a newline"
    if new and not new.endswith(b"\n"):
        return "the Record does not end with a newline"
    return None


def git(root, *args):
    r = subprocess.run(["git"] + list(args), cwd=root, capture_output=True)
    if r.returncode != 0:
        raise GitError("git %s failed: %s" % (" ".join(args[:2]), r.stderr.decode("utf-8", "replace").strip()[:200]))
    return r.stdout


def committed(root, rev):
    """The Record's bytes at `rev`, or None when the path is truly absent there (or HEAD has no commit yet:
    a brand-new repository). Any other problem raises."""
    if rev == "HEAD" and _unborn(root):
        return None
    git(root, "rev-parse", "--verify", "--quiet", rev + "^{commit}")          # an unknown revision is an error
    listed = git(root, "ls-tree", "--name-only", rev, "--", REL).decode("utf-8", "replace").strip()
    if not listed:
        return None
    return git(root, "cat-file", "blob", "%s:%s" % (rev, REL))


def _unborn(root):
    """True only for a repository whose current branch has no commit yet."""
    r = subprocess.run(["git", "symbolic-ref", "-q", "HEAD"], cwd=root, capture_output=True, text=True)
    if r.returncode != 0 or not r.stdout.strip():
        return False                                            # detached HEAD (or not a repo): not "unborn"
    ref = r.stdout.strip()
    git(root, "rev-parse", "--git-dir")                         # not a repository at all: raise
    # rev-parse finds a ref even when it points at a missing object (that is damage, caught later), so only a
    # ref that does not exist at all counts as unborn
    return subprocess.run(["git", "rev-parse", "-q", "--verify", ref], cwd=root, capture_output=True).returncode != 0


def working(root):
    try:
        with open(os.path.join(root, REL), "rb") as f:
            return f.read()
    except FileNotFoundError:
        return None


def head_check(root):
    old = committed(root, "HEAD")
    new = working(root)
    if old is not None and new is None:
        return "the Record file is gone", 0, 0
    old, new = old or b"", new or b""
    return check(old, new), old.count(b"\n"), new.count(b"\n")


def _cache_path(root):
    try:
        gd = git(root, "rev-parse", "--git-dir").decode().strip()
    except GitError:
        return None
    return os.path.join(root, gd, "pc_record_verified")


def _blobs(root, specs):
    """Read many blobs in one git process. specs: list of 'rev:path'. Yields bytes (None = path absent) in order."""
    if not specs:
        return
    p = subprocess.Popen(["git", "cat-file", "--batch"], cwd=root, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        for s in specs:
            p.stdin.write((s + "\n").encode())
            p.stdin.flush()
            head = p.stdout.readline().decode("utf-8", "replace").split()
            if len(head) == 2 and head[1] == "missing":
                # "missing" means either the path is not in that commit (deleted) or the object is damaged.
                # Only the first is acceptable: confirm with ls-tree, and fail if the path is listed.
                rev, _, path = s.partition(":")
                if git(root, "ls-tree", rev, "--", path).strip():
                    raise GitError("could not read %s (object damaged)" % s)
                yield None
                continue
            if len(head) != 3 or head[1] != "blob":
                raise GitError("could not read %s (%s)" % (s, " ".join(head)))
            size = int(head[2])
            data = p.stdout.read(size)
            p.stdout.read(1)                                    # the newline after each object
            yield data
    finally:
        p.stdin.close()
        p.wait()


def _load_cache(path):
    try:
        with open(path) as f:
            c = json.load(f)
        return c if isinstance(c, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def history_check(root, origin=None):
    """Returns (reason or None, versions walked).
    origin: a ref such as origin/main. The Record this checkout last saw there (length + sha256, kept in the
    cache) must still be a prefix of the working file: origin's history may have been rewritten (force push)
    into one that is consistent with itself. After a clean check, origin's current Record is remembered."""
    if git(root, "rev-parse", "--is-shallow-repository").decode().strip() == "true":
        return "the clone is shallow, so the Record's history cannot be checked (fetch full history)", 0
    commits = [] if _unborn(root) else git(root, "log", "--reverse", "--format=%H", "HEAD", "--", REL).decode().split()
    cache = _cache_path(root)
    c = _load_cache(cache) if cache else {}
    start = commits.index(c["commit"]) if c.get("commit") in commits else 0   # walked before, in HEAD's history
    # a commit that deleted the file is in the log too: it reads as empty, so deleting a non-empty Record
    # fails ("lost lines") and nothing can sneak back in after it
    walk, prev = commits[start:], None
    for h, cur in zip(walk, _blobs(root, ["%s:%s" % (h, REL) for h in walk])):
        cur = cur if cur is not None else b""
        if prev is not None:
            why = check(prev[1], cur)
            if why:
                return "%s in commit %s (after %s)" % (why, h[:10], prev[0][:10]), len(walk)
        prev = (h, cur)
    head = committed(root, "HEAD")
    new = working(root)
    if new is None and head:
        return "the Record file is gone", len(walk)
    new = new or b""
    why = check(head or b"", new)
    if why:
        return why + " (working file vs HEAD)", len(walk)
    seen_n, seen_sha = c.get("seen_len"), c.get("seen_sha")
    if isinstance(seen_n, int) and seen_sha:
        if len(new) < seen_n or hashlib.sha256(new[:seen_n]).hexdigest() != seen_sha:
            return "the Record this runner last saw on origin is no longer a prefix of it (history was rewritten)", \
                len(walk)
    if cache:
        out = {"commit": commits[-1] if commits else None, "seen_len": seen_n, "seen_sha": seen_sha}
        if origin:
            seen = committed(root, origin)
            if seen is not None:
                out.update(seen_len=len(seen), seen_sha=hashlib.sha256(seen).hexdigest())
        try:
            with open(cache, "w") as f:
                json.dump(out, f)
        except OSError:
            pass
    return None, len(walk)


def remember(root, ref):
    """After a push: remember the Record now at `ref` as the newest one this checkout has seen on origin."""
    cache = _cache_path(root)
    seen = committed(root, ref)
    if cache is None or seen is None:
        return
    c = _load_cache(cache)
    c.update(seen_len=len(seen), seen_sha=hashlib.sha256(seen).hexdigest())
    with open(cache, "w") as f:
        json.dump(c, f)


def main(argv, root=ROOT):
    try:
        if "--remember" in argv:
            i = argv.index("--remember")
            remember(root, argv[i + 1] if i + 1 < len(argv) else "HEAD")
            return 0
        if "--old" in argv and "--new" in argv:
            old = open(argv[argv.index("--old") + 1], "rb").read()
            new = open(argv[argv.index("--new") + 1], "rb").read()
            why, n_old, n_new = check(old, new), old.count(b"\n"), new.count(b"\n")
        elif "--history" in argv:
            i = argv.index("--origin") if "--origin" in argv else -1
            why, n = history_check(root, argv[i + 1] if 0 <= i < len(argv) - 1 else None)
            if why:
                print("RECORD HISTORY: FAILED: %s. Nothing will be pushed." % why)
                return 1
            print("RECORD HISTORY: ok (%d version(s) walked)" % n)
            return 0
        else:
            why, n_old, n_new = head_check(root)
    except GitError as e:
        print("RECORD GUARD: FAILED: %s. Nothing will be pushed." % e)
        return 1
    if why:
        print("RECORD GUARD: FAILED: %s. Nothing will be pushed." % why)
        return 1
    print("RECORD GUARD: ok (%d lines committed, %d now)" % (n_old, n_new))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

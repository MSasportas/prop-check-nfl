"""names.py: match a player name from the odds feed or ESPN to an nflverse gsis id.

Rules, in order, and never a guess:
  1. exact full name (accents, punctuation, case and Jr/Sr/II/III ignored)
  2. same last name AND same first initial, and only one such player in the game
Anything else is reported as unmatched and left out.
"""
import re
import unicodedata

_SUFFIX = {"jr", "sr", "ii", "iii", "iv", "v"}


def norm(name):
    n = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z]", "", n)


def split_name(name):
    """-> (first, last) lowercase ascii, suffix dropped. 'Zachary L'Heureux Jr.' -> ('zachary', 'lheureux')."""
    n = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    parts = [re.sub(r"[^a-z]", "", p) for p in n.replace("-", " ").split()]
    parts = [p for p in parts if p]
    while len(parts) > 1 and parts[-1] in _SUFFIX:
        parts.pop()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return "", parts[0]
    return parts[0], "".join(parts[1:])


def full_key(name):
    f, l = split_name(name)
    return f + l


class Matcher:
    """Built from the two rosters of one game. people: list of dict(id, first, last, team, pos)."""

    def __init__(self, people):
        self.by_full = {}
        self.by_last = {}
        for p in people:
            nm = "%s %s" % (p["first"], p["last"])
            self.by_full.setdefault(full_key(nm), []).append(p)
            f, l = split_name(nm)
            self.by_last.setdefault(l, []).append((f, p))
            # a two-word last name can also be read as first + middle + last by the feed
            tail = split_name(p["last"])[1] if " " in p["last"].strip() else None
            if tail and tail != l:
                self.by_last.setdefault(tail, []).append((f, p))

    def find(self, name):
        hits = self.by_full.get(full_key(name), [])
        if len({h["id"] for h in hits}) == 1:
            return hits[0]
        if hits:
            return None                                   # two players with the same full name: do not guess
        f, l = split_name(name)
        if not l or not f:
            return None
        cands = {p["id"]: p for (pf, p) in self.by_last.get(l, []) if pf[:1] == f[:1]}
        if len(cands) == 1:
            return next(iter(cands.values()))
        return None


def display(first, last):
    """'Nathan', 'MacKinnon' -> 'N. MacKinnon'"""
    first = (first or "").strip()
    return ("%s. %s" % (first[0], last)) if first else last


# nicknames the feed and the roster disagree on (first names only); both sides are tried
NICK = {"aj": "aj", "cj": "cj", "dj": "dj", "jk": "jk", "kj": "kj", "tj": "tj", "pj": "pj", "rj": "rj",
        "hollywood": "marquise", "chig": "chigoziem", "bam": "kendall"}


def variants(name):
    """Extra spellings: 'A.J. Brown' and 'AJ Brown' already normalise the same; this adds the roster's
    football name vs full name when they differ (handled by the Matcher being given both)."""
    return [name]

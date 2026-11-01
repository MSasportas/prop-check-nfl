"""notify.py: Discord messages to Matt, only when he has something to act on. Never fails or slows a run.

Same behaviour as Prop Check NHL and NBA (copied from them, reshaped for the NFL slate). Sends (all plain text, one
webhook post each):
  NEW PICK   one message per run for every open pick not told about before (max 8 blocks, then "+N more"): bold bet line,
             game, kickoff, how settled it is, "+X% vs N books, we say Y%, price needs Z%", link, and "Bet only at this
             price or better". No parlays exist in NFL, so there is never a parlay block.
  PULLED     one short message for picks he was told about that were pulled before kickoff, each with what it means
             if he already bet it at the shown price (a Hard Rock bet can't be taken back)
  FINAL      one short message when the last price check (the C read, with inactives in) has passed for picks he was
             told about: from then on only a late injury or inactive can pull them. A pick first shown at that read says
             "Price final" in its NEW block instead. Sent only while fewer than FINAL_ROOM messages went out that ET day,
             so it never crowds out a pick or pull
  ACTION     odds keys missing / refused / pool low or empty (the page's own sentence), once per ET day each
  hello      once, the first time the webhook works
Never: no-pick days, heartbeats, injury checks, grading, errors, vetoes. Quiet 12:30 AM to 8:00 AM ET (items are held:
they are still open at 8:00 and go out then). At most 12 messages per ET day (FINAL pings stop at 9). A new price or a
settled-level change never re-notifies (a pick's price is frozen anyway).

The webhook URL comes ONLY from the env DISCORD_WEBHOOK_URL (a GitHub secret named DISCORD_WEBHOOK). It is registered as a
secret (scrubbed from stdout/stderr and every file write), never logged, never put in state, and exception text from the
post is never kept. Unset or empty: this module does nothing.

State: data/state/notified.json = ids and timestamps only.
Test hooks: in PC_NOW / PC_DATA_DIR mode nothing is ever posted to Discord unless PC_DISCORD_FIXTURE_URL (a local
test server) is set; then posts go there (DISCORD_WEBHOOK_URL must still be non-empty).
"""
import json
import os
import urllib.error
import urllib.request

import common

PAGE = "https://msasportas.github.io/prop-check-nfl/"
MAX_PER_DAY = 12
FINAL_ROOM = 9
MAX_LINES = 8
QUIET_FROM = (0, 30)        # inclusive
QUIET_TO = (8, 0)           # exclusive
TIMEOUT = 5
BUDGET_SEC = 14             # all posts of one run together
HELLO = ("Prop Check NFL notifications are on. You'll get a message only when there is a bet to place, "
         "when one is pulled, or when odds keys need attention.")
CHECK = "Bet only at this price or better. If Hard Rock shows worse, skip it."
KEEP_DAYS = 2              # sent / action counters
KEEP_PICK_DAYS = 8         # told-ids: a pick can be told Friday and still be open for Monday night; slates run Tue to Tue
KEYS_REFUSED_TEXT = "Hard Rock prices are off: the odds keys were refused. New keys are needed."


def state_path():
    return common.dpath("state", "notified.json")


def _test_mode():
    return bool(os.environ.get("PC_NOW") or os.environ.get("PC_DATA_DIR"))


def endpoint():
    """The URL to post to, or None (do nothing). Registers the secret so it can never be printed or written."""
    hook = (os.environ.get("DISCORD_WEBHOOK_URL") or "").strip()
    if not hook:
        return None
    common.register_secret(hook)
    tail = hook.rstrip("/").rsplit("/", 1)[-1]
    common.register_secret(tail)                              # the token alone, too
    fixture = (os.environ.get("PC_DISCORD_FIXTURE_URL") or "").strip()
    if fixture:
        return fixture
    return None if _test_mode() else hook


def quiet(now):
    hm = (now.hour, now.minute)
    return QUIET_FROM <= hm < QUIET_TO


class Sender:
    """Posts one message. send() is True only after a 2xx. 429 or anything else: False, nothing kept."""

    def __init__(self, url, clock):
        self.url, self.clock = url, clock
        self.t0 = clock()
        self.stop = False                                     # a 429 ends the run's sending

    def send(self, text):
        if self.stop:
            return False
        body = json.dumps({"content": text[:1990], "allowed_mentions": {"parse": []}}).encode("utf-8")
        for attempt in range(2):
            if self.clock() - self.t0 > BUDGET_SEC:
                return False
            try:
                req = urllib.request.Request(self.url, data=body, method="POST", headers={
                    "Content-Type": "application/json", "User-Agent": "prop-check-nfl (notify)"})
                with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                    return 200 <= r.status < 300
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    self.stop = True                          # give up for this cycle (Retry-After not waited on)
                    return False
                if e.code < 500:
                    return False
            except Exception:
                pass
        return False


# ---------------------------------------------------------------- state
def load_state():
    st = common.read_json(state_path())
    if not isinstance(st, dict):
        st = {}
    st.setdefault("picks", {})              # id -> ISO time told
    st.setdefault("pulled", {})             # id -> ISO time the pull was told
    st.setdefault("final", {})              # id -> ISO time "price final" was told
    st.setdefault("sent", {})               # ET date -> messages sent that day
    st.setdefault("action", {})             # ET date -> ["refused", "pool"] already told
    st.setdefault("hello", None)
    return st


def _prune(st, now):
    """Told-ids are kept 8 days (a Friday pick must not be told again on Monday night); the daily counters 2."""
    from datetime import timedelta
    cutoff = (now - timedelta(days=KEEP_PICK_DAYS)).date().isoformat()
    for k in ("picks", "pulled", "final"):
        st[k] = {i: t for i, t in st[k].items() if isinstance(t, str) and t[:10] >= cutoff}
    keep = {(now.date() - timedelta(days=i)).isoformat() for i in range(KEEP_DAYS)}
    for k in ("sent", "action"):
        st[k] = {d: v for d, v in st[k].items() if d in keep}


# ---------------------------------------------------------------- wording
def _t(iso, now):
    dt = common.parse_iso(iso)
    day = "" if dt.date() == now.date() else dt.strftime("%a ")
    return day + common.fmt_time(dt) + " ET"


SETTLED_ICON = {"injury_report": "📋", "inactives": "✅"}
SETTLED_LABEL = {"injury_report": "Injury report in", "inactives": "Inactives confirmed"}


def _state_label(p):
    """How settled the pick is: Injury report in / Inactives confirmed (from the slate's own level)."""
    lvl = p.get("settled")
    label = p.get("settled_text") or SETTLED_LABEL.get(lvl, "Injury report in")
    return "%s %s" % (SETTLED_ICON.get(lvl, "📋"), label)


SHORT = {"pass_yds": "pass yds", "pass_td": "pass TDs", "rush_yds": "rush yds", "rush_att": "carries",
         "rec": "receptions", "rec_yds": "rec yds"}


def _bet(p):
    """'OVER 54.5 rec yds' (falls back to the full bet text)."""
    if p.get("side") and p.get("line") is not None and p.get("market") in SHORT:
        return "%s %s %s" % (p["side"].upper(), ("%g" % float(p["line"])), SHORT[p["market"]])
    return p["bet"]


def _game(p):
    return p.get("game") or ""


def _decimal(price):
    return 1 + (price / 100.0 if price > 0 else 100.0 / -price)


def breakeven_pct(price):
    """The chance a bet at this American price needs to break even, in percent (one decimal)."""
    return round(100.0 / _decimal(price), 1)


def we_say_pct(p):
    """Our final chance, backed out of the final EV at the frozen price (EV = chance x decimal - 1). None if unknown."""
    ev = p.get("final_ev_pct")
    if not isinstance(ev, (int, float)):
        return None
    return round(100.0 * (1 + ev / 100.0) / _decimal(p["price"]), 1)


def pick_line(p, now):
    """One pick as a short Discord block: the bet on top, then when and how settled, then the numbers."""
    when = " · ".join(x for x in (_game(p), _t(p["kickoff"], now), _state_label(p),
                                  "✅ Price final" if (p.get("flags") or {}).get("price_final") else "") if x)
    need = "price needs %s%%" % ("%g" % breakeven_pct(p["price"]))
    if p.get("method") == "pin_solo" and isinstance(p.get("pin_solo"), dict):
        ps = p["pin_solo"]
        nums = "Pinnacle moved %+.1f pts, other books %+.1f; Hard Rock %s is %+.1f%% vs Pinnacle's fair price" % (
            ps.get("pin_move_pts") or 0.0, ps.get("others_move_pts") or 0.0, common.fmt_price(p["price"]),
            ps.get("ev_vs_fair_pct") or 0.0)
    else:
        say = we_say_pct(p)
        nums = "%s, %s%s" % (p.get("vs") or "Gap vs market", ("we say %s%%, " % ("%g" % say)) if say is not None else "", need)
    return "\n".join(["**%s %s  %s**" % (p["player"], _bet(p), common.fmt_price(p["price"])), when, nums])


def _open(x):
    return x.get("state") == "open" and not x.get("pulled") and not x.get("graded")


def _ahead(p, now):
    return common.parse_iso(p["kickoff"]) > now


def build_new(slate, st, now):
    """-> (text, picks) for open picks not yet told about, or None."""
    new_p = [p for p in slate.get("picks", []) if _open(p) and p.get("rank") is not None
             and p["id"] not in st["picks"] and _ahead(p, now)]
    new_p.sort(key=lambda p: (p["day"], p["rank"], p["id"]))
    if not new_p:
        return None
    blocks = [pick_line(p, now) for p in new_p]
    n = len(blocks)
    head = "🏈 **NEW PICK%s · %s**" % ("" if n == 1 else "S (%d)" % n, now.strftime("%a %b %-d"))
    tail = "%s\n%s" % (CHECK, PAGE)
    shown = []
    for b in blocks[:MAX_LINES]:
        trial = "\n\n".join([head] + shown + [b, "+%d more on the page" % n, tail])
        if len(trial) > 1900:
            break
        shown.append(b)
    parts = [head] + shown
    if n > len(shown):
        parts.append("+%d more on the page" % (n - len(shown)))
    text = "\n\n".join(parts + [tail])
    return text, new_p


def build_pulled(slate, st, now):
    items = []
    for p in slate.get("picks", []):
        if p.get("state") == "pulled" and p.get("pulled") and p["id"] in st["picks"] and p["id"] not in st["pulled"] \
                and _ahead(p, now):
            line = "%s %s %s (%s)" % (p["player"], _bet(p), common.fmt_price(p["price"]), p["pulled"]["reason"])
            if p["pulled"].get("if_bet"):
                line += "\n   " + p["pulled"]["if_bet"]
            items.append(("picks", p["id"], line))
    if not items:
        return None
    text = "❌ **PULLED. Don't bet these now:**\n" + "\n".join("• " + s for _, _, s in items[:MAX_LINES])
    if len(items) > MAX_LINES:
        text += "\n+%d more pulled on the page" % (len(items) - MAX_LINES)
    return text + "\n" + PAGE, items


def build_final(slate, st, now):
    """Open picks already told about whose last price check is done (and not told yet): (text, ids) or None."""
    items = [p for p in slate.get("picks", []) if (p.get("flags") or {}).get("price_final") and _open(p)
             and p["id"] in st["picks"] and p["id"] not in st["final"] and _ahead(p, now)]
    if not items:
        return None
    items.sort(key=lambda p: (p["day"], p.get("rank") or 999, p["id"]))
    lines = ["• %s %s %s" % (p["player"], _bet(p), common.fmt_price(p["price"])) for p in items[:MAX_LINES]]
    text = "✅ **PRICE FINAL.** Last price check done. Only a late injury or inactive can pull these now:\n" + "\n".join(lines)
    if len(items) > MAX_LINES:
        text += "\n+%d more on the page" % (len(items) - MAX_LINES)
    return text + "\n" + CHECK + "\n" + PAGE, [p["id"] for p in items]


def build_actions(slate, st, today):
    """Things only Matt can do: [(code, text)] not yet told today (the page's own sentences)."""
    told = set(st["action"].get(today, []))
    s = slate.get("status") or {}
    out = []
    keys = [b["text"] for b in slate.get("banners", [])
            if isinstance(b, dict) and b.get("level") in ("warn", "stop")
            and any(w in str(b.get("text", "")).lower() for w in ("odds keys", "odds credits"))]
    if (s.get("odds") or {}).get("state") == "keys_refused":
        out.append(("refused", KEYS_REFUSED_TEXT))
    elif keys:
        out.append(("pool", keys[0]))
    return [(c, t) for c, t in out if c not in told]


# ---------------------------------------------------------------- the entry point
def notify(slate=None, sender=None):
    """Called at the end of a run. Returns the number of messages sent. Raises nothing."""
    try:
        return _notify(slate, sender)
    except BaseException as e:                                 # never fail or stop the run; keep nothing from e
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
        return 0


def _notify(slate, sender):
    url = endpoint()
    if not url:
        return 0
    now = common.now()
    if quiet(now):
        return 0
    if slate is None:
        slate = common.read_json(common.dpath("slate.json"))
    valid = common.parse_iso(slate.get("valid_until")) if isinstance(slate, dict) else None
    if not valid or valid <= now:
        return 0                                               # old slate: say nothing about it
    sender = sender or Sender(url, __import__("time").monotonic)
    st = load_state()
    before = json.dumps(st, sort_keys=True)
    today = now.date().isoformat()
    _prune(st, now)
    sent = [0]
    stamp = common.iso(now)

    def post(text, mark):
        if st["sent"].get(today, 0) >= MAX_PER_DAY:
            return False                                       # over the cap: drop silently
        if not sender.send(text):
            return False                                       # not a 2xx: nothing marked, retried next cycle
        st["sent"][today] = st["sent"].get(today, 0) + 1
        sent[0] += 1
        mark()
        _save(st, before)                                      # written at once after each 2xx: no double send
        return True

    def set_hello():
        st["hello"] = stamp
    if not st["hello"]:
        post(HELLO, set_hello)
    pulled = build_pulled(slate, st, now)
    if pulled:
        def mark_pulled():
            for kind, i, _ in pulled[1]:
                st["pulled"][i] = stamp
        post(pulled[0], mark_pulled)
    new = build_new(slate, st, now)
    if new:
        def mark_new():
            for p in new[1]:
                st["picks"][p["id"]] = stamp
                if (p.get("flags") or {}).get("price_final"):
                    st["final"][p["id"]] = stamp        # its NEW block already said "Price final"
        post(new[0], mark_new)
    fin = build_final(slate, st, now)
    if fin and st["sent"].get(today, 0) < FINAL_ROOM:
        def mark_final():
            for i in fin[1]:
                st["final"][i] = stamp
        post(fin[0], mark_final)
    for code, text in build_actions(slate, st, today):
        post("⚠️ **Needs you:** " + text, lambda c=code: st["action"].setdefault(today, []).append(c))
    _save(st, before)
    return sent[0]


def _save(st, before):
    if json.dumps(st, sort_keys=True) != before:
        common.write_json(state_path(), st)

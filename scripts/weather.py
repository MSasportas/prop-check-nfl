"""weather.py: wind and rain at kickoff for outdoor games (open-meteo, free, no key) -> data/state/weather.json.

Read for games within ops.weather_hours of kickoff, at most every ops.weather_every_min. Domes are skipped
(and say "indoors"). Retractable roofs with no word from games.csv are read but marked "roof may be closed".
The engine does not use weather yet (DS: no weather term in the model); Research shows it.
"""
from datetime import timedelta

import common
import teams

URL = ("https://api.open-meteo.com/v1/forecast?latitude=%.4f&longitude=%.4f"
       "&hourly=temperature_2m,precipitation_probability,precipitation,wind_speed_10m,wind_gusts_10m"
       "&wind_speed_unit=mph&temperature_unit=fahrenheit&precipitation_unit=inch&timezone=UTC&forecast_days=7")


def path():
    return common.dpath("state", "weather.json")


def load():
    return common.read_json(path(), {}) or {}


def at_kickoff(js, kick):
    """The forecast hour nearest kickoff -> dict(temp_f, wind_mph, gust_mph, rain_pct, rain_in) or None."""
    try:
        h = js["hourly"]
        times = h["time"]
    except (KeyError, TypeError):
        return None
    want = kick.astimezone(common.ET).astimezone(__import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:00")
    if want not in times:
        return None
    i = times.index(want)
    g = lambda k: (h.get(k) or [None] * len(times))[i]
    return {"temp_f": g("temperature_2m"), "wind_mph": g("wind_speed_10m"), "gust_mph": g("wind_gusts_10m"),
            "rain_pct": g("precipitation_probability"), "rain_in": g("precipitation")}


def text(w):
    if not w:
        return None
    if w.get("roof") == "dome":
        return "Indoors"
    parts = []
    if w.get("temp_f") is not None:
        parts.append("%d°F" % round(w["temp_f"]))
    if w.get("wind_mph") is not None:
        parts.append("wind %d mph" % round(w["wind_mph"]))
    if w.get("rain_pct") is not None and w["rain_pct"] >= 30:
        parts.append("%d%% chance of rain" % round(w["rain_pct"]))
    s = ", ".join(parts)
    if w.get("roof") == "retractable":
        s += " (roof may be closed)"
    return s or None


def check(now, games, cfg):
    ops = cfg["ops"]
    st = load()
    st.setdefault("games", {})
    hours = ops.get("weather_hours", 72)
    every = timedelta(minutes=ops.get("weather_every_min", 180))
    keep = {g["id"] for g in games}
    st["games"] = {k: v for k, v in st["games"].items() if k in keep}
    n_ok = n_fail = n_dome = 0
    for g in games:
        k = common.parse_iso(g["start"])
        if not (now <= k <= now + timedelta(hours=hours)):
            continue
        v = teams.venue(g["home"], g.get("stadium_id"), g.get("roof"))
        if v is None:
            continue
        old = st["games"].get(g["id"]) or {}
        if v["roof"] == "dome":
            if old.get("roof") != "dome":
                st["games"][g["id"]] = {"roof": "dome", "text": "Indoors"}
            n_dome += 1
            continue
        last = common.parse_iso(old.get("at"))
        if last is not None and now - last < every and now >= last:
            continue
        js = common.feed_json("open_meteo", URL % (v["lat"], v["lon"]), "open_meteo_%s.json" % g["home"], timeout=20,
                              retries=1)
        w = at_kickoff(js, k) if js else None
        if w is None:
            n_fail += 1
            continue
        w.update(roof=v["roof"], at=common.iso(now))
        w["text"] = text(w)
        st["games"][g["id"]] = w
        n_ok += 1
    common.write_json(path(), st)
    if not (n_ok or n_fail):
        return {"status": "skipped", "note": "nothing due (%d indoors)" % n_dome}
    # weather is display-only plus one over-blocking rule: a failed read never marks the run failed
    return {"status": "partly" if n_fail else "ok", "note": "%d read, %d failed, %d indoors" % (n_ok, n_fail, n_dome)}

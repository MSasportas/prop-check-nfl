"""teams.py: the 32 NFL teams and the venues they play at.

Internal team codes are nflverse's (games.csv, play-by-play, rosters): LA = Rams, WAS = Commanders, LV, LAC, JAX.
ESPN uses LAR and WSH; the odds feed uses full names. Everything is converted here and nowhere else.

TEAMS[code] = (odds feed full name, ESPN abbreviation, ESPN team id, home stadium id, lat, lon, roof)
roof: outdoors | dome | retractable (a retractable roof's state on game day comes from games.csv when it says)
"""
import re
import unicodedata

TEAMS = {
    "ARI": ("Arizona Cardinals", "ARI", 22, "PHO00", 33.5276, -112.2626, "retractable"),
    "ATL": ("Atlanta Falcons", "ATL", 1, "ATL97", 33.7554, -84.4008, "retractable"),
    "BAL": ("Baltimore Ravens", "BAL", 33, "BAL00", 39.2780, -76.6227, "outdoors"),
    "BUF": ("Buffalo Bills", "BUF", 2, "BUF00", 42.7738, -78.7870, "outdoors"),
    "CAR": ("Carolina Panthers", "CAR", 29, "CAR00", 35.2258, -80.8528, "outdoors"),
    "CHI": ("Chicago Bears", "CHI", 3, "CHI98", 41.8623, -87.6167, "outdoors"),
    "CIN": ("Cincinnati Bengals", "CIN", 4, "CIN00", 39.0955, -84.5161, "outdoors"),
    "CLE": ("Cleveland Browns", "CLE", 5, "CLE00", 41.5061, -81.6995, "outdoors"),
    "DAL": ("Dallas Cowboys", "DAL", 6, "DAL00", 32.7473, -97.0945, "retractable"),
    "DEN": ("Denver Broncos", "DEN", 7, "DEN00", 39.7439, -105.0201, "outdoors"),
    "DET": ("Detroit Lions", "DET", 8, "DET00", 42.3400, -83.0456, "dome"),
    "GB": ("Green Bay Packers", "GB", 9, "GNB00", 44.5013, -88.0622, "outdoors"),
    "HOU": ("Houston Texans", "HOU", 34, "HOU00", 29.6847, -95.4107, "retractable"),
    "IND": ("Indianapolis Colts", "IND", 11, "IND00", 39.7601, -86.1639, "retractable"),
    "JAX": ("Jacksonville Jaguars", "JAX", 30, "JAX00", 30.3239, -81.6373, "outdoors"),
    "KC": ("Kansas City Chiefs", "KC", 12, "KAN00", 39.0489, -94.4839, "outdoors"),
    "LV": ("Las Vegas Raiders", "LV", 13, "VEG00", 36.0909, -115.1833, "dome"),
    "LAC": ("Los Angeles Chargers", "LAC", 24, "LAX01", 33.9535, -118.3392, "dome"),
    "LA": ("Los Angeles Rams", "LAR", 14, "LAX01", 33.9535, -118.3392, "dome"),
    "MIA": ("Miami Dolphins", "MIA", 15, "MIA00", 25.9580, -80.2389, "outdoors"),
    "MIN": ("Minnesota Vikings", "MIN", 16, "MIN01", 44.9737, -93.2581, "dome"),
    "NE": ("New England Patriots", "NE", 17, "BOS00", 42.0909, -71.2643, "outdoors"),
    "NO": ("New Orleans Saints", "NO", 18, "NOR00", 29.9511, -90.0812, "dome"),
    "NYG": ("New York Giants", "NYG", 19, "NYC01", 40.8135, -74.0745, "outdoors"),
    "NYJ": ("New York Jets", "NYJ", 20, "NYC01", 40.8135, -74.0745, "outdoors"),
    "PHI": ("Philadelphia Eagles", "PHI", 21, "PHI00", 39.9008, -75.1675, "outdoors"),
    "PIT": ("Pittsburgh Steelers", "PIT", 23, "PIT00", 40.4468, -80.0158, "outdoors"),
    "SF": ("San Francisco 49ers", "SF", 25, "SFO01", 37.4030, -121.9700, "outdoors"),
    "SEA": ("Seattle Seahawks", "SEA", 26, "SEA00", 47.5952, -122.3316, "outdoors"),
    "TB": ("Tampa Bay Buccaneers", "TB", 27, "TAM00", 27.9759, -82.5033, "outdoors"),
    "TEN": ("Tennessee Titans", "TEN", 10, "NAS00", 36.1665, -86.7713, "outdoors"),
    "WAS": ("Washington Commanders", "WSH", 28, "WAS00", 38.9078, -76.8645, "outdoors"),
}

# venues that are not a team's home (international and neutral games): stadium id -> (name, lat, lon, roof)
VENUES = {
    "LON00": ("Wembley Stadium", 51.5560, -0.2796, "outdoors"),
    "LON02": ("Tottenham Hotspur Stadium", 51.6043, -0.0664, "outdoors"),
    "MAD01": ("Bernabeu", 40.4531, -3.6883, "retractable"),
    "MUN01": ("Allianz Arena", 48.2188, 11.6247, "outdoors"),
    "PAR00": ("Stade de France", 48.9244, 2.3601, "outdoors"),
    "MEX00": ("Estadio Banorte", 19.3029, -99.1505, "outdoors"),
    "MEL00": ("Melbourne Cricket Ground", -37.8200, 144.9834, "outdoors"),
    "RIO00": ("Maracana Stadium", -22.9122, -43.2302, "outdoors"),
}

EXTRA_NAMES = {"Washington Football Team": "WAS", "Oakland Raiders": "LV", "San Diego Chargers": "LAC",
               "St Louis Rams": "LA", "St. Louis Rams": "LA"}


def _key(name):
    n = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", n)


NAME_TO_CODE = {_key(t[0]): code for code, t in TEAMS.items()}
NAME_TO_CODE.update({_key(n): c for n, c in EXTRA_NAMES.items()})
ESPN_TO_CODE = {t[1]: code for code, t in TEAMS.items()}
ESPN_TO_CODE.update({"LA": "LA", "WAS": "WAS", "LAR": "LA", "WSH": "WAS", "JAC": "JAX"})
ESPN_ID_TO_CODE = {str(t[2]): code for code, t in TEAMS.items()}
# nflverse uses a few older codes in older files
ALIASES = {"LAR": "LA", "WSH": "WAS", "JAC": "JAX", "OAK": "LV", "SD": "LAC", "STL": "LA"}


def code_for_feed(feed_name):
    """'Los Angeles Rams' -> 'LA'. None when unknown (never guessed)."""
    return NAME_TO_CODE.get(_key(feed_name))


def code_for_espn(abbr=None, team_id=None):
    if team_id is not None and str(team_id) in ESPN_ID_TO_CODE:
        return ESPN_ID_TO_CODE[str(team_id)]
    return ESPN_TO_CODE.get((abbr or "").upper())


def norm_code(code):
    c = (code or "").upper()
    return ALIASES.get(c, c) if c not in TEAMS else c


def feed_name(code):
    return TEAMS[code][0]


def espn_id(code):
    return TEAMS[code][2]


def short_name(code):
    """'Seattle Seahawks' -> 'Seahawks'"""
    return TEAMS[code][0].split()[-1]


def venue(home_code, stadium_id=None, roof=None):
    """Where a game is played: dict(stadium_id, lat, lon, roof). roof from games.csv wins when it is set:
    dome / closed -> indoor, outdoors / open -> weather matters. Retractable with no word -> 'retractable'."""
    t = TEAMS.get(home_code)
    if stadium_id and stadium_id in VENUES:
        name, lat, lon, r = VENUES[stadium_id]
    elif t:
        lat, lon, r = t[4], t[5], t[6]
    else:
        return None
    r2 = (roof or "").strip().lower()
    if r2 in ("dome", "closed"):
        r = "dome"
    elif r2 in ("outdoors", "open"):
        r = "outdoors"
    return {"stadium_id": stadium_id or (t[3] if t else None), "lat": lat, "lon": lon, "roof": r}

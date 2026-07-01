#!/usr/bin/env python3
"""Team stats tool — FIFA World Cup 2026, multi-source with cross-validation.

Sources:
  - football-data.org  (primary; needs free FOOTBALL_DATA_TOKEN)  -> full results + standings
  - TheSportsDB (key '3', keyless)                                -> cross-validation

Reads tokens from environment or a local .env file (KEY=VALUE lines).

Usage:
  python3 stats.py team "Mexico"
  python3 stats.py matchup "Mexico" "Ecuador"

Prints each team's WC2026 results (played, W-D-L, goals for/against) and,
where two sources overlap, whether their scorelines AGREE (cross-validation).
"""
import argparse, json, os, sys, urllib.request, urllib.parse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import teamf, Spinner

R = "\033[0m"; BOLD = "\033[1m"; DIM = "\033[90m"; WHT = "\033[97m"
GRN = "\033[32m"; YLW = "\033[33m"; RED = "\033[31m"
RESC = {"W": GRN, "D": YLW, "L": RED, "LIVE": BOLD + RED}
RESZH = {"W": "勝", "D": "和", "L": "敗", "LIVE": "LIVE"}

FD_BASE = "https://api.football-data.org/v4"
TSDB_BASE = "https://www.thesportsdb.com/api/v1/json/3"
WC_CODE = "WC"          # football-data.org competition code
WC_SEASON = 2026
TSDB_WC_LEAGUE = "4429"  # FIFA World Cup (verified via API)


# ---------- config ----------
def load_env():
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, ".env")
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def http(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "stats-readonly"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.getcode(), json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode("utf-8", "replace")[:200]}
    except Exception as e:
        return "ERR", {"error": str(e)[:200]}


# ---------- source: football-data.org ----------
def fd_matches():
    """All WC2026 finished matches. Returns list of dicts: home, away, hg, ag, stage."""
    tok = os.environ.get("FOOTBALL_DATA_TOKEN")
    if not tok:
        return None, "no FOOTBALL_DATA_TOKEN (register free at football-data.org)"
    code, data = http(f"{FD_BASE}/competitions/{WC_CODE}/matches?season={WC_SEASON}",
                      headers={"X-Auth-Token": tok, "User-Agent": "stats"})
    if code != 200:
        return None, f"HTTP {code}: {data.get('error', data)}"
    out = []
    for m in data.get("matches", []):
        ft = (m.get("score") or {}).get("fullTime") or {}
        if ft.get("home") is None:
            continue
        out.append({"home": m["homeTeam"]["name"], "away": m["awayTeam"]["name"],
                    "hg": ft["home"], "ag": ft["away"],
                    "stage": m.get("stage", ""), "date": (m.get("utcDate") or "")[:10],
                    "status": m.get("status", "")})
    return out, None


# ---------- source: TheSportsDB ----------
def tsdb_team_id(name):
    code, data = http(f"{TSDB_BASE}/searchteams.php?t={urllib.parse.quote(name)}")
    if code != 200 or not data.get("teams"):
        return None
    for t in data["teams"]:
        if t.get("strSport") == "Soccer" and t.get("strTeam", "").lower() == name.lower():
            return t["idTeam"]
    return data["teams"][0].get("idTeam")


def tsdb_matches(name):
    """WC2026 matches TheSportsDB has for this team (free tier = partial)."""
    tid = tsdb_team_id(name)
    seen, out = set(), []
    urls = [f"{TSDB_BASE}/eventslast.php?id={tid}"] if tid else []
    urls.append(f"{TSDB_BASE}/eventsseason.php?id={TSDB_WC_LEAGUE}&s={WC_SEASON}")
    for u in urls:
        code, data = http(u)
        if code != 200:
            continue
        for e in (data.get("results") or data.get("events") or []):
            if e.get("intHomeScore") is None:
                continue
            if name not in (e.get("strHomeTeam", "") + e.get("strAwayTeam", "")):
                continue
            key = e.get("idEvent")
            if key in seen:
                continue
            seen.add(key)
            out.append({"home": e["strHomeTeam"], "away": e["strAwayTeam"],
                        "hg": int(e["intHomeScore"]), "ag": int(e["intAwayScore"]),
                        "stage": e.get("strRound", ""), "date": e.get("dateEvent", "")})
    return out


# ---------- analysis ----------
def team_view(matches, team):
    """From a match list, keep this team's games and compute W-D-L, GF, GA."""
    rows, w = [], {"W": 0, "D": 0, "L": 0, "gf": 0, "ga": 0}
    for m in matches:
        if team not in (m["home"], m["away"]):
            # loose match on substring for name variants
            if team.lower() not in (m["home"] + m["away"]).lower():
                continue
        home = team.lower() in m["home"].lower()
        gf, ga = (m["hg"], m["ag"]) if home else (m["ag"], m["hg"])
        res = "W" if gf > ga else "D" if gf == ga else "L"
        finished = m.get("status", "FINISHED") == "FINISHED"
        if finished:  # only completed games count toward form/record
            w[res] += 1; w["gf"] += gf; w["ga"] += ga
        tag = res if finished else "LIVE"
        rows.append((m["date"], m["home"], m["hg"], m["ag"], m["away"], m["stage"], tag))
    rows.sort()
    return rows, w


def cross_check(fd_rows, tsdb_rows):
    """Match by (date, teams) and report agreement on scoreline."""
    def key(r):
        return (r[0][:10], frozenset([r[1].lower()[:6], r[4].lower()[:6]]))
    tmap = {key(r): (r[2], r[3]) for r in tsdb_rows}
    agree = disagree = only_fd = 0
    notes = []
    for r in fd_rows:
        k = key(r)
        if k in tmap:
            fd_score = {frozenset([(r[1], r[2]), (r[4], r[3])])}
            same = (r[2], r[3]) == tmap[k] or (r[3], r[2]) == tmap[k]
            if same:
                agree += 1
            else:
                disagree += 1
                notes.append(f"    ⚠ MISMATCH {r[1]} {r[2]}-{r[3]} {r[4]} | TSDB says {tmap[k]}")
        else:
            only_fd += 1
    return agree, disagree, only_fd, notes


def _match_lines(rows):
    for d, h, hg, ag, a, st, res in rows:
        rc = RESC.get(res, "")
        print(f"    {DIM}{d}{R}  {teamf(h)} {BOLD}{hg}-{ag}{R} {teamf(a)}  {rc}{RESZH.get(res, res)}{R}")


def _record(w, tail=""):
    print(f"  {GRN}{w['W']} 勝{R} {YLW}{w['D']} 和{R} {RED}{w['L']} 敗{R}   "
          f"{DIM}進 {w['gf']} · 失 {w['ga']}{R}{tail}")


def show_team(team):
    with Spinner(f"抓取 {team} 戰績"):
        fd, fd_err = fd_matches()
        tsdb = tsdb_matches(team)
    print(f"\n  {BOLD}{WHT}{teamf(team)}{R}  {DIM}— FIFA World Cup 2026{R}")
    if fd is not None:
        rows, w = team_view(fd, team)
        _record(w)
        _match_lines(rows)
        trows, _ = team_view(tsdb, team)
        ag, dis, only, notes = cross_check(rows, trows)
        if dis > 0:   # 一致就不顯示;只有比分不符才示警
            print(f"  {RED}⚠ 交叉驗證(TheSportsDB)發現 {dis} 場比分不符:{R}")
            for n in notes:
                print(n)
    else:
        print(f"  {DIM}football-data.org 不可用:{fd_err}{R}")
        rows, w = team_view(tsdb, team)
        _record(w, tail=f"  {DIM}(TheSportsDB free,覆蓋不完整){R}")
        _match_lines(rows)


def main():
    load_env()
    ap = argparse.ArgumentParser(description="WC2026 team stats, multi-source + cross-validation")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("team"); t.add_argument("name")
    m = sub.add_parser("matchup"); m.add_argument("teamA"); m.add_argument("teamB")
    args = ap.parse_args()
    if args.cmd == "team":
        show_team(args.name)
    else:
        show_team(args.teamA); show_team(args.teamB)
    print()


if __name__ == "__main__":
    main()

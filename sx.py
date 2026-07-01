#!/usr/bin/env python3
"""SX Bet read-only odds tool — FIFA World Cup 2026.

Reads are keyless (no API key, no wallet). All odds are TAKER decimal odds
(what you actually get if you place the bet), verified against the sx.bet UI.

Usage:
  python3 sx.py games [--days N] [--date YYYY-MM-DD]
  python3 sx.py odds [EVENTID ...] [--date YYYY-MM-DD] [--days N] [--all]

Times are shown in Taipei (UTC+8). --date filters by Taipei calendar day.
"""
import argparse, json, os, sys, urllib.request, urllib.parse, time
from datetime import datetime, timezone, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import countdown, teamf, human, bar, wljust, Spinner, odds_color

API = "https://api.sx.bet"
USDC = "0x6629Ce1Cf35Cc1329ebB4F63202F3f197b3F050B"
WC_LEAGUE = 1715
SC = 10**20
TZ = timezone(timedelta(hours=8))  # Asia/Taipei

R = "\033[0m"; BOLD = "\033[1m"; DIM = "\033[90m"; WHT = "\033[97m"
CYN = "\033[36m"; GRN = "\033[32m"; YLW = "\033[33m"; RED = "\033[31m"

TYPE_LABEL = {52: "Match Winner", 1: "Yes/No", 3: "Handicap", 2: "Total", 88: "To Qualify"}


def get(path, **params):
    q = "?" + urllib.parse.urlencode(params) if params else ""
    url = API + path + q
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "sx-readonly"})
            return json.load(urllib.request.urlopen(req, timeout=30))
        except Exception as e:
            if attempt == 2:
                raise RuntimeError(f"GET {url} failed: {e}")
            time.sleep(1.0)


def tpe(iso):
    """ISO UTC string -> Taipei datetime."""
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return dt.astimezone(TZ)


# ---------- fetchers ----------
def fetch_fixtures(league_id=WC_LEAGUE):
    return get("/fixture/active", leagueId=league_id)["data"]


def fetch_sports():
    return get("/sports")["data"]


def fetch_active_leagues(sport_id):
    return get("/leagues/active", sportId=sport_id)["data"]


def in_window(f, args):
    t = tpe(f["startDate"])
    if args.date:
        return t.strftime("%Y-%m-%d") == args.date
    now = datetime.now(TZ)
    return now - timedelta(hours=3) <= t <= now + timedelta(days=args.days)


def fetch_markets(event_id):
    out, key = [], None
    while True:
        p = {"eventId": event_id, "pageSize": 100}
        if key:
            p["paginationKey"] = key
        d = get("/markets/active", **p)["data"]
        out += d["markets"]
        key = d.get("nextKey")
        if not key:
            return out


def fetch_orders(event_id):
    out, page = [], 0
    while True:
        d = get("/orders", sportXeventId=event_id, page=page, perPage=1000)["data"]
        rows = d if isinstance(d, list) else d.get("orders", [])
        out += rows
        if len(rows) < 1000:
            return out
        page += 1
        if page > 20:
            return out


def fetch_volume(event_id, max_pages=15):
    total, n, key, pages = 0.0, 0, None, 0
    while True:
        p = {"sportXeventId": event_id, "pageSize": 300}
        if key:
            p["paginationKey"] = key
        d = get("/trades", **p)["data"]
        rows = d["trades"]
        n += len(rows)
        total += sum(float(t.get("normalizedStake", 0))
                     for t in rows if t.get("tradeStatus") == "SUCCESS" and t.get("valid", True))
        key = d.get("nextKey")
        pages += 1
        if not key or pages >= max_pages:
            return total, n, (key is not None)


# ---------- odds math (verified vs sx.bet UI) ----------
def remaining_usdc(o):
    rem = int(o["totalBetSize"]) - int(o["fillAmount"])
    if rem <= 0:
        return 0.0
    return (rem * SC // int(o["percentageOdds"]) - rem) / 1e6


def best_side(maker_orders):
    """Given makers on the OPPOSITE outcome, return taker (decimal, implied, $@best, $total)."""
    active = [o for o in maker_orders if o.get("orderStatus", "ACTIVE") == "ACTIVE"
              and remaining_usdc(o) > 0]
    if not active:
        return None
    best_p = max(int(o["percentageOdds"]) for o in active)
    implied = 1 - best_p / SC
    if implied <= 0:
        return None
    at_best = sum(remaining_usdc(o) for o in active if int(o["percentageOdds"]) == best_p)
    total = sum(remaining_usdc(o) for o in active)
    return (1 / implied, implied, at_best, total)


def market_quote(market, orders):
    mh = market["marketHash"].lower()
    mo = [o for o in orders if o["marketHash"].lower() == mh]
    o1_makers = [o for o in mo if o["isMakerBettingOutcomeOne"]]
    o2_makers = [o for o in mo if not o["isMakerBettingOutcomeOne"]]
    # taker betting outcomeOne matches makers on outcomeTwo, and vice versa
    return {"one": best_side(o2_makers), "two": best_side(o1_makers)}


def orow(name, q, hi=False):
    """一列 outcome:名稱 + taker 賠率 + 隱含機率長條 + 可下注量。"""
    if not q:
        print(f"     {wljust(name, 22)} {DIM}—{R}")
        return
    dec, imp, at_best, total = q
    nm = (BOLD if hi else "") + WHT + wljust(name, 22) + R
    liqc = DIM if total < 100 else ""
    oc = odds_color(dec)
    print(f"     {nm} {BOLD}{oc}{dec:>6.2f}{R} {DIM}{imp*100:>5.1f}%{R} {oc}{bar(imp)}{R} {liqc}${human(total)}{R}")


def market_block(title, rows):
    """rows: [(name, q), ...];最低賠率(最熱)那列以綠色標記。"""
    print(f"\n  {CYN}── {title}{R}")
    decs = [q[0] for _, q in rows if q]
    best = min(decs) if decs else None
    for name, q in rows:
        orow(name, q, hi=(q is not None and best is not None and q[0] == best))


# ---------- commands ----------
def cmd_games(args):
    fx = [f for f in fetch_fixtures(args.league) if in_window(f, args)]
    fx.sort(key=lambda f: f["startDate"])
    label = args.date or f"next {args.days}d"
    print(f"\nFIFA World Cup 2026 fixtures ({label}, Taipei time) — {len(fx)} games\n")
    st = {1: "scheduled", 2: "LIVE", 9: "starting"}
    for f in fx:
        t = tpe(f["startDate"])
        cd = countdown(f["startDate"])
        p1 = teamf(f["participantOneName"])
        p2 = teamf(f["participantTwoName"])
        extra = st.get(f["status"], f["status"])
        if cd and f["status"] == 1:
            extra += f" ⏱ {cd}"
        print(f"  {f['eventId']:<11} {t:%m/%d %H:%M}  {p1:>20} vs {p2:<20} [{extra}]")
    print()


def find_events(args):
    if args.events:
        fx = {f["eventId"]: f for f in fetch_fixtures(args.league)}
        return [fx[e] for e in args.events if e in fx] or \
               [{"eventId": e, "participantOneName": "?", "participantTwoName": "?",
                 "startDate": "1970-01-01T00:00:00Z", "status": "?"} for e in args.events]
    fx = [f for f in fetch_fixtures(args.league) if in_window(f, args)]
    fx.sort(key=lambda f: f["startDate"])
    return fx


def cmd_odds(args):
    events = find_events(args)
    if not events:
        print("No matching fixtures."); return
    for f in events:
        ev = f["eventId"]
        a, b = f["participantOneName"], f["participantTwoName"]
        with Spinner(f"抓取 {a} vs {b} 盤口"):
            markets = fetch_markets(ev)
            orders = fetch_orders(ev)
            vol, ntr, capped = fetch_volume(ev)
        t = tpe(f["startDate"]) if f["startDate"] != "1970-01-01T00:00:00Z" else None

        rule = f"{DIM}  " + "─" * 60 + R
        sub = ""
        if t:
            sub = f"   {DIM}{t:%m/%d %H:%M} Taipei{R}"
            cd = countdown(f["startDate"])
            if cd:
                sub += f"  {DIM}⏱ {cd}{R}"
        print("\n" + rule)
        print(f"  {BOLD}{WHT}{teamf(a)} vs {teamf(b)}{R}{sub}")
        print(f"  {DIM}{ev} · {len(markets)} markets · 成交 ${human(vol)}{'+' if capped else ''} ({ntr} 筆){R}")
        print(rule)

        by_type = {}
        for m in markets:
            by_type.setdefault(m["type"], []).append(m)

        # 獨贏(兩面,和局退款)
        for m in by_type.get(52, []):
            q = market_quote(m, orders)
            title = "獨贏 [和局退款]"
            if q["one"] and q["two"]:
                vig = (q["one"][1] + q["two"][1] - 1) * 100
                title += f"   {DIM}vig {vig:.2f}%{R}{CYN}"
            market_block(title, [(teamf(m["outcomeOneName"]), q["one"]),
                                 (teamf(m["outcomeTwoName"]), q["two"])])

        # 1X2 + 不輸(double chance)
        yesno = by_type.get(1, [])
        if yesno:
            rows, dc = [], {}
            for m in yesno:
                q = market_quote(m, orders)
                name = m["outcomeOneName"]
                disp = "平手" if name in ("Tie", "Draw") else f"{teamf(name)} 勝"
                rows.append((disp, q["one"]))
                if name not in ("Tie", "Draw") and q["two"]:
                    dc[name] = q["two"]
            if a in dc:
                rows.append((f"{teamf(b)} 不輸", dc[a]))
            if b in dc:
                rows.append((f"{teamf(a)} 不輸", dc[b]))
            market_block("1X2 / 不輸(勝或和)", rows)

        # 晉級
        for m in by_type.get(88, []):
            n1 = m["outcomeOneName"].replace(" (To Qualify)", "")
            n2 = m["outcomeTwoName"].replace(" (To Qualify)", "")
            q = market_quote(m, orders)
            market_block("晉級", [(teamf(n1), q["one"]), (teamf(n2), q["two"])])

        # 主讓分 / 主大小
        for typ, title in [(3, "讓分"), (2, "大小")]:
            mains = [m for m in by_type.get(typ, []) if m.get("mainLine")] or \
                    ([m for m in by_type.get(typ, [])][:1] if not args.all else [])
            show = by_type.get(typ, []) if args.all else mains
            show.sort(key=lambda m: (m.get("line") or 0))
            for m in show:
                q = market_quote(m, orders)
                market_block(f"{title}  {m['outcomeOneName']} / {m['outcomeTwoName']}",
                             [(m["outcomeOneName"], q["one"]), (m["outcomeTwoName"], q["two"])])
    print()


def main():
    ap = argparse.ArgumentParser(description="SX Bet read-only FIFA WC 2026 odds tool")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("games", help="list fixtures")
    g.add_argument("--days", type=int, default=3)
    g.add_argument("--date", help="Taipei calendar day YYYY-MM-DD")
    g.add_argument("--league", type=int, default=WC_LEAGUE, help="leagueId (default World Cup 1715)")
    o = sub.add_parser("odds", help="odds board for events")
    o.add_argument("events", nargs="*", help="event IDs (default: use --date/--days)")
    o.add_argument("--days", type=int, default=2)
    o.add_argument("--date", help="Taipei calendar day YYYY-MM-DD")
    o.add_argument("--league", type=int, default=WC_LEAGUE, help="leagueId (default World Cup 1715)")
    o.add_argument("--all", action="store_true", help="show all handicap/total lines")
    args = ap.parse_args()
    if args.cmd == "games":
        cmd_games(args)
    elif args.cmd == "odds":
        cmd_odds(args)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Live in-play tracker for a single SX Bet match (polling, keyless).

Polls REST every few seconds and redraws a terminal dashboard:
  - live score + match clock
  - tracked markets (Match Winner / main Total / To Qualify) taker odds
    with movement arrows (↑ drifting, ↓ shortening) vs last tick and vs open
  - trade activity (count growth) + recent trades feed

This is NOT websocket — for a human watcher, 3-4s polling looks the same and
needs no API key. Upgrade path to Centrifugo websocket noted in CLAUDE.md.

Usage:  python3 live.py L19315552 [--interval 4] [--iterations 0=infinite]
Run it yourself for a continuous view:  ! python3 live.py L19315552
"""
import argparse, os, sys, time, json, urllib.request, urllib.parse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sx
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=8))
CLEAR = "\033[2J\033[H"


def http(path, **params):
    q = "?" + urllib.parse.urlencode(params) if params else ""
    req = urllib.request.Request(sx.API + path + q, headers={"User-Agent": "live"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)
    except Exception:
        return None


def live_score(ev):
    d = http("/live-scores", sportXEventIds=ev)
    try:
        s = d["data"][0] if isinstance(d["data"], list) else list(d["data"].values())[0]
        return s
    except Exception:
        return None


def recent_trades(ev, window_sec=600):
    """Trades in the last window_sec, newest first (default sort is oldest-first,
    so we filter by startDate then sort client-side)."""
    start = int(time.time()) - window_sec
    d = http("/trades", sportXeventId=ev, startDate=start, pageSize=50)
    try:
        trades = d["data"]["trades"]
        trades.sort(key=lambda t: int(t.get("betTime", 0)), reverse=True)
        return trades, len(trades)
    except Exception:
        return [], None


def arrow(cur, prev):
    if prev is None or cur is None:
        return "  "
    if cur > prev + 1e-9:
        return "↑ "   # drifting (less likely / better for backers)
    if cur < prev - 1e-9:
        return "↓ "   # shortening (more likely)
    return "= "


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("event")
    ap.add_argument("--interval", type=float, default=4)
    ap.add_argument("--iterations", type=int, default=0)
    args = ap.parse_args()
    ev = args.event

    markets = sx.fetch_markets(ev)
    by_type = {}
    for m in markets:
        by_type.setdefault(m["type"], []).append(m)
    tracked = []
    tracked += [("独赢", m) for m in by_type.get(52, [])]
    tracked += [("大小", m) for m in by_type.get(2, []) if m.get("mainLine")]
    tracked += [("让分", m) for m in by_type.get(3, []) if m.get("mainLine")]
    tracked += [("晋级", m) for m in by_type.get(88, [])]
    teams = (markets[0].get("teamOneName"), markets[0].get("teamTwoName")) if markets else ("?", "?")

    opens, prev = {}, {}
    i = 0
    while True:
        i += 1
        orders = sx.fetch_orders(ev)
        score = live_score(ev)
        trades, count = recent_trades(ev)
        now = datetime.now(TZ).strftime("%H:%M:%S")

        lines = [CLEAR]
        lines.append(f"  ⚽ {teams[0]} vs {teams[1]}   (tick {i} @ {now} Taipei, 每{args.interval:g}s輪詢)")
        if score:
            sc = f"{score.get('teamOneScore','?')} - {score.get('teamTwoScore','?')}"
            per = score.get("currentPeriod", ""); pt = score.get("periodTime", "")
            lines.append(f"  比分 {sc}   {per} {pt}")
        else:
            lines.append("  比分: (live-scores 尚無資料 / 賽前)")
        dc = f"  近10分鐘成交: {count} 筆" if count is not None else ""
        lines.append(dc)
        lines.append("")
        lines.append(f"  {'市場':<6}{'選項':<22}{'賠率':>7} {'vs前':>4} {'vs開盤':>8}  可下注$")
        lines.append("  " + "-" * 62)
        for label, m in tracked:
            q = sx.market_quote(m, orders)
            for side, name in [("one", m["outcomeOneName"]), ("two", m["outcomeTwoName"])]:
                r = q[side]
                key = m["marketHash"] + side
                if not r:
                    lines.append(f"  {label:<6}{name:<22}{'—':>7}")
                    continue
                dec = r[0]; tot = r[3]
                opens.setdefault(key, dec)
                op = opens[key]
                a = arrow(dec, prev.get(key))
                d_open = dec - op
                sign = "+" if d_open >= 0 else ""
                lines.append(f"  {label:<6}{name:<22}{dec:6.2f} {a:>4} {sign}{d_open:6.2f}  ${tot:>8,.0f}")
                prev[key] = dec
            label = ""
        lines.append("")
        lines.append("  最新成交(近10分鐘):")
        for t in trades[:6]:
            ts = datetime.fromtimestamp(int(t.get("betTime", 0)), TZ).strftime("%H:%M:%S") if t.get("betTime") else ""
            side = "O1" if t.get("bettingOutcomeOne") else "O2"
            odds = 1 / (1 - int(t.get("odds", 0)) / 10**20) if t.get("odds") else 0
            lines.append(f"    {ts}  ${float(t.get('normalizedStake',0)):>7,.0f} @ {odds:4.2f}  ({side}) {t.get('tradeStatus','')}")
        sys.stdout.write("\r\n".join(lines) + "\r\n"); sys.stdout.flush()

        if args.iterations and i >= args.iterations:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n  已停止追蹤")

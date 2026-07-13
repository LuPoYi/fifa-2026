#!/usr/bin/env python3
"""盤中事件監看(betting alert monitor):比分 + 大小盤 + (選填)API-Football 數據。
每 CYCLE 秒印一次 STATUS,或進球/終場即時印出並退出(讓上層收到通知後重啟接續)。
免金鑰(比分/賠率走 sx.py);API-Football 數據為選填,且**只在要印出時呼叫一次**(省額度,
絕不放在每 10 秒的迴圈裡打——踩過雷:每迴圈打會很快燒光每日 100 次)。

用法: python3 watch_ev.py <SX_eventId> <lastH> <lastA> [API-Football_fixtureId]
  例: python3 watch_ev.py L19427178 1 0 1582681
背景跑: 由上層以 run_in_background 啟動,進球/終場/到 CYCLE 就退出→上層讀輸出→重啟接續。
"""
import os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sx

EV = sys.argv[1]
CYCLE = 180  # 幾秒印一次 STATUS(進球/終場會提前退出)
last_h, last_a = (int(sys.argv[2]), int(sys.argv[3])) if len(sys.argv) > 3 else (0, 0)
API_FIX = sys.argv[4] if len(sys.argv) > 4 else None  # API-Football fixture id(≠ SX eventId)

def _keys():
    """讀 .env 內的 API-Football key(可放 APISPORTS_KEY / APISPORTS_KEY2 供輪替備援)。"""
    ks = []
    env = os.path.join(HERE, ".env")
    if os.path.exists(env):
        for l in open(env):
            if l.startswith("APISPORTS_KEY=") or l.startswith("APISPORTS_KEY2="):
                ks.append(l.strip().split("=", 1)[1])
    return ks

def api_stats():
    """回傳 [homeDict, awayDict](含 xG/射門/控球等)或 None。只在要印出時呼叫。"""
    if not API_FIX:
        return None
    import json, urllib.request
    for key in _keys():
        try:
            req = urllib.request.Request(
                "https://v3.football.api-sports.io/fixtures/statistics?fixture=" + API_FIX,
                headers={"x-apisports-key": key})
            d = json.load(urllib.request.urlopen(req, timeout=15))["response"]
            if len(d) < 2:
                continue  # 空(該 key 額度滿/停權)→ 換下一把
            out = []
            for team in d[:2]:
                s = {x["type"]: x["value"] for x in team["statistics"]}
                out.append({"poss": s.get("Ball Possession"), "xg": s.get("expected_goals"),
                            "sog": s.get("Shots on Goal"), "tot": s.get("Total Shots"),
                            "box": s.get("Shots insidebox"), "cor": s.get("Corner Kicks")})
            return out
        except Exception:
            continue
    return None

def score():
    try:
        d = sx.get("/live-scores", sportXEventIds=EV)["data"]
        s = d[0] if isinstance(d, list) else list(d.values())[0]
        return (int(s.get("teamOneScore", 0)), int(s.get("teamTwoScore", 0)),
                s.get("currentPeriod", ""), s.get("periodTime", ""))
    except Exception:
        return None

def quotes():
    """抓 Over/Under 1.5、2.5 的 taker 賠率(免費、走 sx.py)。"""
    out = {}
    try:
        ms = sx.fetch_markets(EV); orders = sx.fetch_orders(EV)
        for m in ms:
            t = m.get("type"); line = m.get("line")
            if t == 2 and line == 2.5:
                q = sx.market_quote(m, orders)
                if q.get("one"): out["o25"] = q["one"][0]
                if q.get("two"): out["u25"] = q["two"][0]
            if t == 2 and line == 1.5:
                q = sx.market_quote(m, orders)
                if q.get("one"): out["o15"] = q["one"][0]
    except Exception:
        pass
    return out

def build_line(sc):
    """只在要印出時呼叫 → 這裡才打 quotes()+api_stats(),一個 report 一次。"""
    q = quotes()
    scs = f"{sc[0]}-{sc[1]} {sc[2]} {sc[3]}" if sc else "?"
    parts = []
    if "o15" in q: parts.append(f"O1.5 {q['o15']:.2f}")
    if "o25" in q: parts.append(f"O2.5 {q['o25']:.2f}")
    if "u25" in q: parts.append(f"U2.5 {q['u25']:.2f}")
    line = f"{scs} | " + " ".join(parts)
    per_now = sc[2] if sc else ""
    if per_now not in ("Break Time", "Break time", "Half Time", "Halftime"):  # 中場不呼叫 API
        stt = api_stats()
        if stt:
            h, a = stt
            line += (f" | xG {h['xg']}-{a['xg']} 射正 {h['sog']}-{a['sog']} 總射 {h['tot']}-{a['tot']}"
                     f" 禁區 {h['box']}-{a['box']} 角球 {h['cor']}-{a['cor']} 控球 {h['poss']}/{a['poss']}")
    return line

start = time.time()
while True:
    sc = score()  # 每 10 秒只讀免費比分(偵測進球),不打 API-Football
    if sc:
        h, a, per, pt = sc
        if per in ("Finished", "Full time"):
            print(f"FT: 終場 {h}-{a}。{build_line(sc)}", flush=True); break
        if (h, a) != (last_h, last_a):
            time.sleep(4); sc2 = score()  # 4 秒 debounce,防 feed 抖動/VAR 誤報
            if not sc2 or (sc2[0], sc2[1]) != (h, a):
                continue
            tot = h + a
            tag = " ★Over2.5已中★" if tot >= 3 else ""
            print(f"GOAL(確認): {last_h}-{last_a}→{h}-{a}!(總分{tot}){tag} {build_line(sc)}", flush=True); break
    if time.time() - start >= CYCLE:
        print(f"STATUS: {build_line(sc)}", flush=True); break
    time.sleep(10)

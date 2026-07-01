#!/usr/bin/env python3
"""Live in-play tracker via Centrifugo WebSocket (real push, low latency).

Needs SX_API_KEY (in .env). Flow:
  1. exchange API key -> realtime JWT token   (GET /user/realtime-token/api-key)
  2. websocket connect to wss://realtime.sx.bet/connection/websocket
  3. connect command (token) -> subscribe order_book:event_<ev> + fixtures:live_scores
  4. seed full book via REST snapshot, then apply incremental order pushes
  5. redraw a coloured dashboard on updates (throttled)

Usage:  python3 live_ws.py L19315552 [--seconds 0=infinite]
Watch continuously yourself:  ! python3 live_ws.py L19315552
"""
import argparse, json, os, shutil, sys, time, unicodedata, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sx
import websocket
import re
from common import sparkline, teamf, wlen, odds_color
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=8))
WS_URL = "wss://realtime.sx.bet/connection/websocket"
CLEAR = "\033[2J\033[H"

# ---- colours ----
R = "\033[0m"; BOLD = "\033[1m"; DIM = "\033[2m"
RED = "\033[31m"; GRN = "\033[32m"; YLW = "\033[33m"; CYN = "\033[36m"
GREY = "\033[90m"; WHT = "\033[97m"

PERIOD = {"1st Half": "上半場", "2nd Half": "下半場", "Half Time": "中場", "Halftime": "中場",
          "Extra Time": "延長賽", "Penalties": "PK", "Not Started": "未開賽",
          "Finished": "已結束", "Awaiting ET": "等待延長"}


vlen = wlen   # 用 common 的寬度計算(正確處理國旗雙寬)
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def vis(s):
    return wlen(_ANSI.sub("", s))


def pad(s, width, right=False):
    g = max(0, width - vlen(s))
    return (" " * g + s) if right else (s + " " * g)


def fmt_clock(pt):
    try:
        n = int(pt)
        return f"{n//60}:{n%60:02d}" if n >= 0 else ""
    except Exception:
        return str(pt or "")


def arrow(cur, prev):
    if prev is None or cur is None or abs(cur - prev) < 1e-9:
        return f"{GREY}▬{R}"
    return f"{GRN}▲{R}" if cur > prev else f"{RED}▼{R}"


def get_token():
    key = os.environ.get("SX_API_KEY")
    if not key:
        sys.exit("no SX_API_KEY in .env")
    req = urllib.request.Request(sx.API + "/user/realtime-token/api-key",
                                 headers={"x-api-key": key, "User-Agent": "sx"})
    return json.load(urllib.request.urlopen(req, timeout=20))["token"]


def load_env():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(p):
        for line in open(p):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


class Tracker:
    def __init__(self, ev):
        self.ev = ev
        self.markets = sx.fetch_markets(ev)
        by = {}
        for m in self.markets:
            by.setdefault(m["type"], []).append(m)
        a, b = (self.markets[0].get("teamOneName"), self.markets[0].get("teamTwoName")) if self.markets else ("?", "?")
        self.teams = (a, b)
        t1 = {m["outcomeOneName"]: m for m in by.get(1, [])}

        # row spec: (group_label, display_name, market, side)  — quarter handicap intentionally excluded
        rows = []
        for mw in by.get(52, []):
            rows.append(("獨贏", mw["outcomeOneName"], mw, "one"))
            rows.append(("獨贏", mw["outcomeTwoName"], mw, "two"))
        if b in t1:
            rows.append(("不輸", f"{a} 勝或和", t1[b], "two"))   # Not B = A win/draw
        if a in t1:
            rows.append(("不輸", f"{b} 勝或和", t1[a], "two"))   # Not A = B win/draw
        for ln in (1.5, 2.5, 3.5):
            tm = [m for m in by.get(2, []) if m.get("line") == ln]
            if tm:
                rows.append(("大小", f"Over {ln}", tm[0], "one"))
                rows.append(("大小", f"Under {ln}", tm[0], "two"))
        for tq in by.get(88, []):
            rows.append(("晉級", tq["outcomeOneName"].replace(" (To Qualify)", ""), tq, "one"))
            rows.append(("晉級", tq["outcomeTwoName"].replace(" (To Qualify)", ""), tq, "two"))
        self.rows = rows

        self.book = {o["orderHash"]: o for o in sx.fetch_orders(ev)}
        self.score = {}
        try:  # seed score via REST so it shows before the first push
            req = urllib.request.Request(sx.API + f"/live-scores?sportXEventIds={ev}", headers={"User-Agent": "sx"})
            d = json.load(urllib.request.urlopen(req, timeout=10))["data"]
            self.score = (d[0] if isinstance(d, list) else list(d.values())[0]) or {}
        except Exception:
            pass
        self.prev = {}
        self.opens = {}
        self.hist = {}
        self.updates = 0
        self.last_msg = 0.0

    def apply_orders(self, data):
        rows = data if isinstance(data, list) else (data.get("orders", [data]) if isinstance(data, dict) else [])
        for o in rows:
            oh = o.get("orderHash")
            if not oh:
                continue
            st = o.get("status") or o.get("orderStatus") or "ACTIVE"
            o["orderStatus"] = st
            if st != "ACTIVE" or int(o.get("fillAmount", 0)) >= int(o.get("totalBetSize", 1)):
                self.book.pop(oh, None)
            else:
                self.book[oh] = o
        self.updates += 1
        self.last_msg = time.time()

    def apply_score(self, data):
        if str(data.get("sportXEventId", data.get("sportXeventId", ""))) == self.ev:
            self.score = data
            self.last_msg = time.time()

    def draw(self, connected):
        orders = list(self.book.values())
        W = min(84, max(60, shutil.get_terminal_size((120, 40)).columns - 4))
        top = f"{DIM}{CYN}╔{'═'*W}╗{R}"
        mid = f"{DIM}{CYN}╟{'─'*W}╢{R}"
        bot = f"{DIM}{CYN}╚{'═'*W}╝{R}"

        def wrap(inner):  # 左右都收邊框;用 vis() 算可見寬度(忽略 ANSI)
            padn = max(0, W - 1 - vis(inner))
            return f"{DIM}{CYN}║{R} {inner}{' ' * padn}{DIM}{CYN}║{R}"

        out = [CLEAR, top]
        # title
        dot = f"{GRN}●{R}" if connected else f"{YLW}◌{R}"
        state = f"{GRN}已連線{R}" if connected else f"{YLW}連線中{R}"
        ago = f"{time.time()-self.last_msg:.0f}s 前" if self.last_msg else "—"
        title = f"⚽ {BOLD}{WHT}{self.teams[0]} vs {self.teams[1]}{R}"
        meta = f"{dot} {state} {GREY}· push {self.updates} · {ago}{R}"
        out.append(wrap(pad(title, 34) + "  " + meta))
        # score
        s1 = self.score.get("teamOneScore", "–"); s2 = self.score.get("teamTwoScore", "–")
        sig = [str(s1), str(s2)]   # 內容簽章(不含時鐘/計時,用來判斷是否需要重繪)
        per = PERIOD.get(self.score.get("currentPeriod", ""), self.score.get("currentPeriod", ""))
        clk = fmt_clock(self.score.get("periodTime", ""))
        scoreline = f"{BOLD}{YLW}{s1} – {s2}{R}   {CYN}{per} {clk}{R}" if self.score else f"{GREY}(等待 live-scores push){R}"
        out.append(wrap("比分 " + scoreline))
        out.append(mid)
        # header
        hdr = (f"{BOLD}{pad('市場',6)}{pad('選項',18)}{pad('賠率',7,1)} "
               f"{pad('',3)}{pad('vs開盤',8,1)}{pad('可下注$',12,1)}  {pad('走勢線',12)}{R}")
        out.append(wrap(hdr))
        out.append(mid)

        last_label = None
        for label, name, m, side in self.rows:
            q = sx.market_quote(m, orders)
            r = q[side]
            lab = label if label != last_label else ""
            last_label = label
            disp = teamf(name)
            cellL = f"{CYN}{pad(lab,6)}{R}"
            if not r:
                line = cellL + pad(disp, 18) + f"{GREY}{pad('—',7,1)}{R}"
                out.append(wrap(line))
                continue
            dec, tot = r[0], r[3]
            key = m["marketHash"] + side
            self.opens.setdefault(key, dec)
            h = self.hist.setdefault(key, [])
            if not h or abs(h[-1] - dec) > 1e-9:   # 只在賠率真的變動時記點
                h.append(dec)
                if len(h) > 120:
                    del h[0]
            ar = arrow(dec, self.prev.get(key))
            d = dec - self.opens[key]
            dcol = GRN if d > 0 else RED if d < 0 else GREY
            liq = f"{GREY}" if tot < 100 else ""
            spark = sparkline(self.hist[key], 18)
            line = (cellL + f"{WHT}{pad(disp,18)}{R}"
                    + f"{BOLD}{odds_color(dec)}{pad(f'{dec:.2f}',7,1)}{R} "
                    + f"{pad(ar,3)}{dcol}{pad(('+' if d>=0 else '')+f'{d:.2f}',8,1)}{R}"
                    + f"{liq}{pad(f'${tot:,.0f}',12,1)}{R}  {CYN}{spark}{R}")
            out.append(wrap(line))
            self.prev[key] = dec
            sig.append(f"{key}:{dec:.3f}:{tot:.0f}")
        out.append(bot)
        out.append(f"{GREY}  {GRN}▲{GREY} 賠率升   {RED}▼{GREY} 賠率降   ▬ 持平 · 空白鍵暫停 · Ctrl+C 離開{R}")
        return "\r\n".join(out), "|".join(sig)   # \r\n:即使終端機殘留 raw 模式也不跑版


def _poll_space():
    """非阻塞讀 stdin:偵測到空白鍵回 True(需先進入 cbreak 模式)。"""
    if not sys.stdin.isatty():
        return False
    import select
    hit = False
    while select.select([sys.stdin], [], [], 0)[0]:
        if sys.stdin.read(1) == " ":
            hit = True
    return hit


def run(ev, seconds):
    load_env()
    token = get_token()
    tr = Tracker(ev)
    ws = websocket.create_connection(WS_URL, timeout=10)
    ws.send(json.dumps({"connect": {"token": token, "name": "python"}, "id": 1}))
    ws.settimeout(1.0)
    connected = False
    start = time.time()
    last_draw = 0.0
    last_sig = None
    paused = False

    old_tty = None
    if sys.stdin.isatty():
        import termios, tty
        fd = sys.stdin.fileno()
        old_tty = termios.tcgetattr(fd)
        tty.setcbreak(fd)   # 讀單鍵不需 Enter;ISIG 仍在 → Ctrl+C 有效
    try:
        while True:
            try:
                data_in = ws.recv()
                for line in data_in.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    msg = json.loads(line)
                    if msg == {}:
                        ws.send("{}")
                        continue
                    if "connect" in msg and "id" in msg:
                        ws.send(json.dumps({"subscribe": {"channel": f"order_book:event_{ev}"}, "id": 2}))
                        ws.send(json.dumps({"subscribe": {"channel": "fixtures:live_scores"}, "id": 3}))
                        connected = True
                        continue
                    push = msg.get("push")
                    if push:
                        ch, d = push.get("channel", ""), (push.get("pub") or {}).get("data")
                        if d is None:
                            continue
                        if ch.startswith("order_book"):
                            tr.apply_orders(d)
                        elif ch.startswith("fixtures:live_scores"):
                            tr.apply_score(d)
            except websocket.WebSocketTimeoutException:
                pass
            except (websocket.WebSocketConnectionClosedException, ConnectionError):
                print("  ⚠ 連線中斷,重連中…", flush=True)
                time.sleep(2)
                ws = websocket.create_connection(WS_URL, timeout=10)
                ws.send(json.dumps({"connect": {"token": token, "name": "python"}, "id": 1}))
                ws.settimeout(1.0)
                connected = False
                last_sig = None
                continue

            if _poll_space():                      # 空白鍵切換暫停
                paused = not paused
                if paused:
                    sys.stdout.write(f"\r\n  {YLW}⏸ 已暫停(可用滑鼠複製,空白鍵恢復){R}\r\n")
                    sys.stdout.flush()
                else:
                    last_sig = None                 # 恢復後強制重繪

            now = time.time()
            if not paused and now - last_draw >= 1.0:
                frame, sig = tr.draw(connected)
                if sig != last_sig:                 # 只有內容變了才重繪(靜止時不重畫,方便選取)
                    sys.stdout.write(frame + "\r\n")
                    sys.stdout.flush()
                    last_sig = sig
                last_draw = now
            if seconds and now - start >= seconds:
                ws.close()
                break
    finally:
        if old_tty is not None:
            import termios
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_tty)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("event")
    ap.add_argument("--seconds", type=float, default=0)
    a = ap.parse_args()
    try:
        run(a.event, a.seconds)
    except KeyboardInterrupt:
        print("\n  已停止追蹤")

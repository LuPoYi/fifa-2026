#!/usr/bin/env python3
"""終端機版 FIFA World Cup 2026 對戰表(bracket)。

資料來自 football-data.org(需 FOOTBALL_DATA_TOKEN in .env)。
32 強 → 16 強 → 8 強 → 4 強 → 決賽,含比分 / 時間 / 狀態。勝方以**顏色**標記並自動晉級。
對戰樹結構採官方 2026 bracket;各輪結果由 API 依隊名即時填入,勝方向上傳遞。

用法:  python3 bracket.py
"""
import json, os, shutil, sys, time, urllib.request
from datetime import datetime, timezone, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import flag, countdown

TZ = timezone(timedelta(hours=8))
FD = "https://api.football-data.org/v4/competitions/WC/matches?season=2026"

LEAVES = [
    ("Germany", "Paraguay"), ("France", "Sweden"),
    ("South Africa", "Canada"), ("Netherlands", "Morocco"),
    ("Portugal", "Croatia"), ("Spain", "Austria"),
    ("United States", "Bosnia-Herzegovina"), ("Belgium", "Senegal"),
    ("Brazil", "Japan"), ("Ivory Coast", "Norway"),
    ("Mexico", "Ecuador"), ("England", "Congo DR"),
    ("Argentina", "Cape Verde Islands"), ("Australia", "Egypt"),
    ("Switzerland", "Algeria"), ("Colombia", "Ghana"),
]
SHORT = {"Bosnia-Herzegovina": "Bosnia", "Cape Verde Islands": "C.Verde",
         "United States": "USA", "South Africa": "S.Africa", "Ivory Coast": "Ivory Coast",
         "Netherlands": "Netherlands", "Switzerland": "Swiss", "Congo DR": "Congo DR"}

R = "\033[0m"; WIN = "\033[1;32m"; LOSE = "\033[90m"; LIVE = "\033[1;31m"
PROJ = "\033[36m"; TIME = "\033[90m"; HDR = "\033[1;36m"; GOLD = "\033[1;33m"; DIMC = "\033[90m"

GAP = 5
FLAGS_ON = True
COLW = [37, 25, 19, 15, 13]
COLX, WIDTH = [], 0
ROUND_NAMES = ["32 強", "16 強", "8 強", "4 強", "決賽"]


def layout(colw):
    global COLW, COLX, WIDTH
    COLW = colw
    COLX = [0]
    for w in COLW[:-1]:
        COLX.append(COLX[-1] + w + GAP)
    WIDTH = COLX[-1] + COLW[-1] + 2


layout([37, 25, 19, 15, 13])


def token():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.environ.get("FOOTBALL_DATA_TOKEN"):
        return os.environ["FOOTBALL_DATA_TOKEN"]
    if os.path.exists(p):
        for line in open(p):
            if line.startswith("FOOTBALL_DATA_TOKEN=") and line.strip() != "FOOTBALL_DATA_TOKEN=":
                return line.strip().split("=", 1)[1]
    sys.exit("需要 FOOTBALL_DATA_TOKEN(見 .env.example)")


def short(name):
    if not name:
        return "?"
    return SHORT.get(name, name if len(name) <= 12 else name[:12])


def named(t):
    """國旗 + 空格 + 隊名(短);窄視窗(FLAGS_ON=False)時省略國旗。"""
    f = flag(t) if FLAGS_ON else ""
    return f"{f} {short(t)}" if f else short(t)


def fetch():
    tok = token()
    last = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(FD, headers={"X-Auth-Token": tok, "User-Agent": "bracket"})
            return json.load(urllib.request.urlopen(req, timeout=30))["matches"]
        except urllib.error.HTTPError as e:
            last = e
            # 400/429 多為 football-data 免費版限速(10 次/分),退避重試
            if e.code in (400, 429, 403, 500, 502, 503) and attempt < 3:
                time.sleep(2 * (attempt + 1))
                continue
            raise
        except Exception as e:
            last = e
            if attempt < 3:
                time.sleep(1)
                continue
            raise
    raise last


def build():
    ms = [m for m in fetch() if m["stage"] in
          ("LAST_32", "LAST_16", "QUARTER_FINALS", "SEMI_FINALS", "FINAL")]
    by_pair = {}
    for m in ms:
        h, a = m["homeTeam"].get("name"), m["awayTeam"].get("name")
        if h and a:
            by_pair[frozenset((h, a))] = m

    def adv(m):
        if not m:
            return None
        w = m["score"].get("winner")
        return m["homeTeam"]["name"] if w == "HOME_TEAM" else m["awayTeam"]["name"] if w == "AWAY_TEAM" else None

    rounds = [[{"a": p[0], "b": p[1], "m": by_pair.get(frozenset(p))} for p in LEAVES]]
    for r in range(4):
        nxt = []
        for i in range(len(rounds[r]) // 2):
            ta, tb = adv(rounds[r][2 * i]["m"]), adv(rounds[r][2 * i + 1]["m"])
            m = by_pair.get(frozenset((ta, tb))) if ta and tb else None
            nxt.append({"a": ta, "b": tb, "m": m})
        rounds.append(nxt)
    champion = adv(rounds[4][0]["m"])

    order = [("LAST_32", "32 強"), ("LAST_16", "16 強"), ("QUARTER_FINALS", "8 強"),
             ("SEMI_FINALS", "4 強"), ("FINAL", "決賽")]
    stage = "決賽"
    for st, lab in order:
        if any(m["stage"] == st and m["status"] != "FINISHED" for m in ms):
            stage = lab; break
    return rounds, {"champion": champion, "stage": stage}


def label(node, is_leaf):
    """回傳 (text, [(start,end,color),...])。勝方用顏色標記,不用符號。"""
    A, B, m = node["a"], node["b"], node["m"]
    a, b = named(A), named(B)   # 國旗 + 空格 + 隊名
    if not (A or B):
        return "?", [(0, 1, DIMC)]
    if m and m["status"] == "FINISHED":
        ft = m["score"]["fullTime"]; w = m["score"].get("winner")
        sc = f"{ft['home']}-{ft['away']}"
        text = f"{a} {sc} {b}"
        b0 = len(a) + 1 + len(sc) + 1
        if w == "HOME_TEAM":
            spans = [(0, len(a), WIN), (b0, len(text), LOSE)]
        elif w == "AWAY_TEAM":
            spans = [(0, len(a), LOSE), (b0, len(text), WIN)]
        else:
            spans = []
        return text, spans
    if m and m["status"] in ("IN_PLAY", "PAUSED"):
        ft = m["score"]["fullTime"]
        text = f"{a} {ft['home']}-{ft['away']} {b} ●"
        return text, [(0, len(text), LIVE)]
    # upcoming
    if A and B:
        if is_leaf and m and countdown(m["utcDate"]):
            ts = f"⏱ {countdown(m['utcDate'])}"
            text = f"{a} vs {b} {ts}"
            return text, [(0, len(a), PROJ), (len(a) + 4, len(a) + 4 + len(b), PROJ),
                          (len(text) - len(ts), len(text), TIME)]
        text = f"{a} vs {b}"
        return text, [(0, len(a), PROJ), (len(text) - len(b), len(text), PROJ)]
    # only one side known
    text = f"{a} vs {b}"
    known = a if A else b
    s = 0 if A else len(text) - len(b)
    return text, [(s, s + len(known), PROJ)]


def render():
    global FLAGS_ON
    cols = shutil.get_terminal_size((160, 50)).columns
    if cols < WIDTH + 4:           # 視窗太窄 → 收合國旗 + 縮欄
        FLAGS_ON = False
        layout([23, 18, 13, 11, 9])
    try:
        rounds, meta = build()
    except Exception as e:
        code = getattr(e, "code", "")
        print(f"\r\n  {RED}⚠ 對戰表資料抓取失敗{(' (HTTP ' + str(code) + ')') if code else ''}{R}")
        print(f"  {DIMC}football-data.org 免費版限每分鐘 10 次,稍等幾秒再試。{R}\r\n")
        return
    centers = [[i * 2 for i in range(16)]]
    for r in range(1, 5):
        centers.append([(centers[r - 1][2 * i] + centers[r - 1][2 * i + 1]) // 2
                        for i in range(len(rounds[r]))])
    height = centers[0][-1] + 1
    grid = [[" "] * WIDTH for _ in range(height)]
    row_spans = {}

    def put(row, x, s):
        for k, ch in enumerate(s):
            if 0 <= row < height and 0 <= x + k < WIDTH:
                grid[row][x + k] = ch

    # 連接線(圓角、灰色)
    for r in range(1, 5):
        cr = COLX[r - 1] + COLW[r - 1]
        mid = COLX[r] - 2
        for i in range(len(rounds[r])):
            a, b, p = centers[r - 1][2 * i], centers[r - 1][2 * i + 1], centers[r][i]
            for x in range(cr, mid):
                put(a, x, "─"); put(b, x, "─")
            put(a, mid, "╮"); put(b, mid, "╯")
            for row in range(a + 1, b):
                put(row, mid, "│")
            put(p, mid, "├")
            for x in range(mid + 1, COLX[r]):
                put(p, x, "─")
    fx = COLX[4] + COLW[4]
    for x in range(fx, fx + 3):
        put(centers[4][0], x, "─")

    # 放標籤 + 記錄顏色 span
    for r in range(5):
        for i, n in enumerate(rounds[r]):
            text, spans = label(n, r == 0)
            text = text[:COLW[r]]
            row, x = centers[r][i], COLX[r]
            put(row, x, text)
            row_spans[row] = [(x + s, min(x + e, x + len(text)), c) for (s, e, c) in spans if s < len(text)]

    # 連接線上色(灰),再套用 label 顏色 span
    def colorize(row_idx):
        chars = grid[row_idx]
        spans = sorted(row_spans.get(row_idx, []))
        out, i = [], 0
        for s, e, c in spans:
            s = max(s, i)
            if s > i:
                out.append(_dim_connectors("".join(chars[i:s])))
            out.append(c + "".join(chars[s:e]) + R)
            i = e
        out.append(_dim_connectors("".join(chars[i:])))
        return "".join(out).rstrip()

    def _dim_connectors(seg):
        if seg.strip() and any(ch in seg for ch in "─│╮╯├"):
            return DIMC + seg + R
        return seg

    lines = [colorize(r) for r in range(height)]
    champ = named(meta["champion"]) if meta["champion"] else "?"
    cc = GOLD if meta["champion"] else DIMC
    lines[centers[4][0]] += f"  🏆 {cc}{champ}{R}"

    # 標題 + 輪次表頭
    now = datetime.now(TZ)
    hdr_line = [" "] * WIDTH
    for r in range(5):
        for k, ch in enumerate(ROUND_NAMES[r]):
            if COLX[r] + k < WIDTH:
                hdr_line[COLX[r] + k] = ch
    header = HDR + "".join(hdr_line).rstrip() + R

    print()
    print(f"{HDR}  ⚽ FIFA WORLD CUP 2026 · 對戰表{R}"
          f"   {DIMC}目前:{R}{GOLD}{meta['stage']}{R}   {DIMC}{now:%m/%d %H:%M} Taipei{R}")
    print(f"  {WIN}綠=勝者/晉級{R}  {LIVE}紅=進行中{R}  {PROJ}藍=已出線待戰{R}  {DIMC}灰=未定{R}")
    print()
    print("  " + header)
    for ln in lines:
        print("  " + ln)
    print()


if __name__ == "__main__":
    try:
        render()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"\r\n  {RED}⚠ 對戰表無法顯示:{e}{R}\r\n")

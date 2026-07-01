#!/usr/bin/env python3
"""互動式主入口 — SX Bet 賠率分析工具(預設世界盃,可換賭盤)。

直接跑:  python3 main.py   → 用 ↑/↓ 方向鍵移動,Enter 選擇(也可按數字快速選)。
需要選比賽時,會從 API 撈賽程清單讓你挑,不用手動輸入 eventId。可「換賭盤」切到其他運動/聯賽。

底層沿用現有工具(也可各自單獨呼叫):
  sx.py  賽程/賠率板 · live_ws.py 即時(WS) · live.py 即時(輪詢) · stats.py 球隊數據 · bracket.py 對戰表
"""
import os, sys, subprocess, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sx
from common import countdown, teamf, Spinner

PY = sys.executable
WC_LEAGUE = 1715
STATUS = {1: "賽前", 2: "LIVE", 3: "結束", 4: "取消", 5: "延期", 6: "中斷", 7: "放棄", 8: "斷訊", 9: "即將開始"}
R = "\033[0m"; SEL = "\033[1;36m"; DIM = "\033[90m"; TITLE = "\033[1;36m"; LIVE = "\033[1;32m"
CLEAR = "\033[2J\033[H"

board = {"leagueId": WC_LEAGUE, "label": "FIFA World Cup", "sportId": 5}


# ---------- 鍵盤 / 選單 ----------
def read_key():
    import termios, tty
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\x1b":
            nxt = sys.stdin.read(1)
            if nxt in ("[", "O"):
                return {"A": "up", "B": "down", "C": "right", "D": "left"}.get(sys.stdin.read(1), "other")
            return "esc"
        if ch in ("\r", "\n"):
            return "enter"
        if ch == "\x03":
            raise KeyboardInterrupt
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def choose(title, labels, back_hint="← 返回"):
    """↑/↓ 移動 · → / Enter 進入 · ← / Esc 返回 · q 離開。
    回傳 index(進入)、'BACK'(←/Esc)或 'QUIT'(q)。非 TTY 時退回數字輸入。"""
    if not sys.stdin.isatty():
        print(title)
        for i, l in enumerate(labels, 1):
            print(f"  {i}) {l}")
        print("  0) 返回")
        try:
            s = input("  選擇 > ").strip()
        except EOFError:
            return "QUIT"
        if s == "0":
            return "BACK"
        return int(s) - 1 if s.isdigit() and 1 <= int(s) <= len(labels) else "BACK"

    idx = 0
    while True:
        buf = [CLEAR, *title.split("\n"), ""]
        for i, l in enumerate(labels):
            buf.append(f"  {SEL}▶ {l}{R}" if i == idx else f"    {DIM}{l}{R}")
        buf.append("")
        buf.append(f"{DIM}  ↑/↓ 移動 · → 進入 · {back_hint}{R}")
        # 用 \r\n 明確回車:即使終端機殘留 raw 模式(OPOST 關)也不會階梯狀跑版
        sys.stdout.write("\r\n".join(buf) + "\r\n")
        sys.stdout.flush()
        k = read_key()
        if k == "up":
            idx = (idx - 1) % len(labels)
        elif k == "down":
            idx = (idx + 1) % len(labels)
        elif k in ("enter", "right"):
            return idx
        elif k in ("left", "esc"):
            return "BACK"
        elif k in ("q", "Q"):
            return "QUIT"


def wait_back():
    """動作結束後等使用者按 ← 返回;用讀鍵模式,不回顯方向鍵亂碼。"""
    if not sys.stdin.isatty():
        return
    sys.stdout.write(f"\r\n{DIM}  ← 返回{R}\r\n")
    sys.stdout.flush()
    while True:
        k = read_key()
        if k in ("left", "esc", "enter", "q", "Q"):
            return
        # ↑/↓/→ 等其他鍵靜默忽略(cbreak 讀取,不會出現 ^[[A 亂碼)


# ---------- 資料 / 動作 ----------
def has_sx_key():
    if os.environ.get("SX_API_KEY"):
        return True
    p = os.path.join(HERE, ".env")
    if os.path.exists(p):
        for line in open(p):
            if line.strip().startswith("SX_API_KEY=") and line.strip() != "SX_API_KEY=":
                return True
    return False


def run_script(script, *args):
    subprocess.run([PY, os.path.join(HERE, script), *args])


_FX_CACHE = {}   # leagueId -> (timestamp, fixtures)


def get_fixtures(force=False):
    lid = board["leagueId"]
    now = time.time()
    if not force and lid in _FX_CACHE and now - _FX_CACHE[lid][0] < 20:
        return _FX_CACHE[lid][1]
    try:
        with Spinner("抓取賽程"):
            fx = sx.fetch_fixtures(lid)
    except Exception as e:
        print(f"  取得賽程失敗:{e}")
        return _FX_CACHE.get(lid, (0, []))[1]
    fx.sort(key=lambda f: f["startDate"])
    _FX_CACHE[lid] = (now, fx)
    return fx


def fixture_label(f):
    t = sx.tpe(f["startDate"])
    st = STATUS.get(f["status"], str(f["status"]))
    cd = countdown(f["startDate"])
    p1, p2 = teamf(f["participantOneName"]), teamf(f["participantTwoName"])
    if f["status"] in (2, 9):
        tag = f"{LIVE}[{st}]{R}"
    elif cd:
        tag = f"[{st}] {DIM}⏱ {cd}{R}"
    else:
        tag = f"[{st}]"
    return f"{t:%m/%d %H:%M}  {p1} vs {p2}  {tag}"


def match_menu(m):
    """第 2 層:選定某場比賽後,在裡面連續做各種分析。"""
    a, b = m["participantOneName"], m["participantTwoName"]
    while True:
        wc = board["leagueId"] == WC_LEAGUE
        acts = [("賠率板 · 完整盤口", "odds"), ("即時盤口追蹤(live)", "live")]
        if wc:
            acts.append(("球隊數據 · 雙方戰績", "stats"))
        acts.append(("完整賽前分析(賠率 + 數據)", "full"))
        t = sx.tpe(m["startDate"])
        st = STATUS.get(m["status"], str(m["status"]))
        cd = countdown(m["startDate"])
        cds = f"  {DIM}⏱ {cd}{R}" if cd and m["status"] not in (2, 9) else ""
        title = (f"{TITLE}  {teamf(a)} vs {teamf(b)}{R}   "
                 f"{DIM}{t:%m/%d %H:%M} Taipei · {st}{R}{cds}")
        i = choose(title, [x[0] for x in acts], back_hint="← 返回賽程")
        if i == "QUIT":
            return "QUIT"
        if not isinstance(i, int):
            return None
        act = acts[i][1]
        if act == "odds":
            run_script("sx.py", "odds", m["eventId"]); wait_back()
        elif act == "live":
            script = "live_ws.py" if has_sx_key() else "live.py"
            mode = "WebSocket" if script == "live_ws.py" else "輪詢(未設 SX_API_KEY)"
            print(f"\n  啟動即時追蹤【{mode}】… 按 Ctrl+C 返回\n")
            try:
                run_script(script, m["eventId"])
            except KeyboardInterrupt:
                print("\n  已停止追蹤")
            wait_back()
        elif act == "stats":
            run_script("stats.py", "matchup", a, b); wait_back()
        elif act == "full":
            print(f"\n{TITLE}── 盤口 ──{R}")
            run_script("sx.py", "odds", m["eventId"])
            if wc:
                print(f"\n{TITLE}── 雙方數據 ──{R}")
                run_script("stats.py", "matchup", a, b)
            else:
                print(f"\n{DIM}(非世界盃,略過球隊數據){R}")
            wait_back()


def change_board():
    try:
        with Spinner("抓取運動清單"):
            sports = sorted(sx.fetch_sports(), key=lambda s: s["label"])
    except Exception as e:
        print(f"  取得運動清單失敗:{e}"); wait_back(); return
    i = choose("  選擇運動:", [s["label"] for s in sports])
    if not isinstance(i, int):
        return
    sport = sports[i]
    try:
        with Spinner("抓取聯賽清單"):
            leagues = sorted(sx.fetch_active_leagues(sport["sportId"]), key=lambda l: -l.get("popularity", 0))
    except Exception as e:
        print(f"  取得聯賽清單失敗:{e}"); wait_back(); return
    if not leagues:
        print(f"\n  「{sport['label']}」目前沒有進行中的聯賽,換一個試試。"); wait_back(); return
    j = choose(f"  選擇聯賽({sport['label']}):", [l["label"].strip() for l in leagues])
    if not isinstance(j, int):
        return
    lg = leagues[j]
    board.update(leagueId=lg["leagueId"], label=lg["label"].strip(), sportId=sport["sportId"])


def board_menu():
    """第 1 層:目前賭盤的賽程清單(→進入某場)+ 換賭盤 / 對戰表。"""
    while True:
        wc = board["leagueId"] == WC_LEAGUE
        fx = get_fixtures()   # 全部進行中場次(倒數以 ⏱ ~Nd/~Nh 顯示)
        specials = [(f"🔄 換賭盤(目前:{board['label']})", "board")]
        if wc:
            specials.append(("🏆 對戰表 bracket", "bracket"))
        labels = [s[0] for s in specials] + [fixture_label(f) for f in fx]

        title = f"{TITLE}  ⚽  SX Bet 賠率分析工具{R}   {DIM}賭盤:{R}{TITLE}{board['label']}{R}"
        if not fx:
            title += f"\n{DIM}  ⚠ 這個賭盤目前沒有進行中的賽事,選「換賭盤」切換。{R}"

        i = choose(title, labels, back_hint="q 離開")
        if i == "QUIT":
            print("  掰掰"); return
        if not isinstance(i, int):     # ← 在最上層不動作
            continue
        if i < len(specials):
            key = specials[i][1]
            if key == "board":
                change_board()
            elif key == "bracket":
                run_script("bracket.py"); wait_back()
        else:
            if match_menu(fx[i - len(specials)]) == "QUIT":
                print("  掰掰"); return


if __name__ == "__main__":
    try:
        board_menu()
    except KeyboardInterrupt:
        print("\n  掰掰")

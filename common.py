#!/usr/bin/env python3
"""共用小工具:國旗 emoji、開賽倒數、顯示寬度、sparkline、載入動畫。"""
import itertools, sys, threading, time, unicodedata
from datetime import datetime, timezone, timedelta

TZ = timezone(timedelta(hours=8))

# 隊名 → ISO 3166-1 alpha-2(用 football-data / SX 的常見名稱)
NAME2ISO = {
    "Germany": "DE", "Paraguay": "PY", "France": "FR", "Sweden": "SE",
    "South Africa": "ZA", "Canada": "CA", "Netherlands": "NL", "Morocco": "MA",
    "Portugal": "PT", "Croatia": "HR", "Spain": "ES", "Austria": "AT",
    "United States": "US", "USA": "US", "Bosnia-Herzegovina": "BA", "Bosnia-Herz": "BA",
    "Belgium": "BE", "Senegal": "SN", "Brazil": "BR", "Japan": "JP",
    "Ivory Coast": "CI", "Norway": "NO", "Mexico": "MX", "Ecuador": "EC",
    "Congo DR": "CD", "Argentina": "AR", "Cape Verde Islands": "CV", "Cape Verde": "CV",
    "Australia": "AU", "Egypt": "EG", "Switzerland": "CH", "Algeria": "DZ",
    "Colombia": "CO", "Ghana": "GH", "South Korea": "KR", "Korea Republic": "KR",
    "Czechia": "CZ", "Czech Republic": "CZ", "Curacao": "CW", "Curaçao": "CW",
    "Uzbekistan": "UZ", "Panama": "PA", "Uruguay": "UY", "Poland": "PL",
    "Denmark": "DK", "Serbia": "RS", "Qatar": "QA", "Iran": "IR", "IR Iran": "IR",
    "Saudi Arabia": "SA", "Nigeria": "NG", "Cameroon": "CM", "Tunisia": "TN",
    "Costa Rica": "CR", "Peru": "PE", "Chile": "CL", "Italy": "IT", "Turkey": "TR",
    "Türkiye": "TR", "Jordan": "JO", "New Zealand": "NZ", "Haiti": "HT",
    "Panama": "PA", "Honduras": "HN", "Jamaica": "JM", "Venezuela": "VE",
    "Scotland": "GB", "Wales": "GB", "England": "GB", "Northern Ireland": "GB",
    "Republic of Ireland": "IE", "Ireland": "IE", "Greece": "GR", "Ukraine": "UA",
}


def flag(name):
    """回傳國旗 emoji(兩個 regional indicator,終端機顯示為 2 欄);未知回 ''。"""
    iso = NAME2ISO.get(name)
    if not iso and name:
        iso = NAME2ISO.get(name.strip())
    if not iso:
        return ""
    return chr(0x1F1E6 + ord(iso[0]) - 65) + chr(0x1F1E6 + ord(iso[1]) - 65)


def teamf(name):
    """'🇧🇷 Brazil'(國旗與隊名間空一格);無國旗時回原名。"""
    f = flag(name)
    return f"{f} {name}" if f else name


def cwidth(ch):
    o = ord(ch)
    if 0x1F1E6 <= o <= 0x1F1FF:   # regional indicator:兩個 = 2 欄,各算 1
        return 1
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return 2
    if o >= 0x1F000:              # 其他 emoji
        return 2
    return 1


def wlen(s):
    return sum(cwidth(c) for c in s)


def wljust(s, n):
    return s + " " * max(0, n - wlen(s))


def wrjust(s, n):
    return " " * max(0, n - wlen(s)) + s


def hours_until(iso_utc, now=None):
    """距開賽還有幾小時(float);已開賽為負,無法解析回 None。"""
    if not iso_utc:
        return None
    try:
        t = datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
    except Exception:
        return None
    now = now or datetime.now(timezone.utc)
    return (t - now).total_seconds() / 3600


def countdown(iso_utc, now=None):
    """概略倒數(不精確到分):'<1h'、'~7h'、'~2d';已開賽回 ''。"""
    h = hours_until(iso_utc, now)
    if h is None or h <= 0:
        return ""
    if h < 1:
        return "<1h"
    if h < 24:
        return f"~{round(h)}h"
    return f"~{round(h / 24)}d"


def human(n):
    """金額人性化縮寫:88034 -> '88k'、1234567 -> '1.2M'。"""
    n = float(n)
    if n >= 1e6:
        return f"{n/1e6:.1f}M"
    if n >= 1e3:
        return f"{n/1e3:.0f}k"
    return f"{n:.0f}"


def odds_color(dec):
    """賠率數字的五段配色(256 色):<1.5 亮綠 / 1.5-2 綠 / 2-3 黃 / 3-5 橘 / >=5 灰。"""
    if dec is None:
        return ""
    if dec < 1.5:
        return "\033[38;5;46m"    # 亮綠(極保守)
    if dec < 2.0:
        return "\033[38;5;34m"    # 綠(保守)
    if dec < 3.0:
        return "\033[38;5;226m"   # 黃(不錯)
    if dec < 5.0:
        return "\033[38;5;208m"   # 橘(偏冷)
    return "\033[38;5;244m"       # 灰(冷門・差不多)


def bar(frac, width=10):
    """機率長條:0~1 -> '██████░░░░'。"""
    frac = max(0.0, min(1.0, frac))
    full = int(round(frac * width))
    return "█" * full + "░" * (width - full)


class Spinner:
    """with Spinner('抓取盤口'): ...  抓資料時顯示旋轉動畫(非 TTY 時靜默)。"""
    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, msg="載入中"):
        self.msg = msg
        self._stop = threading.Event()
        self._t = None

    def __enter__(self):
        if sys.stdout.isatty():
            self._t = threading.Thread(target=self._run, daemon=True)
            self._t.start()
        return self

    def _run(self):
        for fr in itertools.cycle(self.FRAMES):
            if self._stop.is_set():
                break
            sys.stdout.write(f"\r  \033[36m{fr}\033[0m {self.msg}…")
            sys.stdout.flush()
            time.sleep(0.09)

    def __exit__(self, *a):
        self._stop.set()
        if self._t:
            self._t.join(timeout=0.3)
        if sys.stdout.isatty():
            sys.stdout.write("\r\033[K")
            sys.stdout.flush()


SPARK = "▁▂▃▄▅▆▇█"


def sparkline(vals, width=12):
    """把數列畫成迷你走勢線(取最後 width 個)。"""
    vals = [v for v in vals if v is not None][-width:]
    if len(vals) < 2:
        return " " * width
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-9:
        return SPARK[3] * len(vals)
    return "".join(SPARK[min(7, int((v - lo) / (hi - lo) * 7 + 0.5))] for v in vals)

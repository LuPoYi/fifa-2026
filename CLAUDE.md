# fifa2026 — SX Bet World Cup 2026 賠率分析

針對 SX Bet(鏈上 betting exchange)的 FIFA World Cup 2026 賽事做即時賠率分析。
**方向:即時分析 + 警報,人工下單**(不把錢包私鑰交給程式,不做自動下單)。

**定位**:這是給作者自己的參考型 repo —— 下一場想玩時回來即時查盤、當作日後開發類似工具的範本、
換其他比賽或賭盤時沿用同一套模式調整。所以工具求「唯讀、可重用、參數化」,不追求一次到位的完整產品。
各別的深入 research 不放進本 repo(見 `.gitignore`)。

## 主入口:`main.py`(互動選單)

`./start.sh`(啟動腳本,免記 python3;等同 `python3 main.py`)→ **階層式選單**,導覽:**↑/↓ 移動、→ 進入、← 返回上一層**(termios raw key;無數字快速選;非 TTY 如管線退回數字輸入方便測試)。
兩層設計(消除舊版三個選項的重複):**第 1 層 `board_menu`** = 目前賭盤的賽程清單 + 換賭盤 + 對戰表;**第 2 層 `match_menu`** = 選定某場後在裡面連續做 賠率板 / 即時盤口 / 球隊數據 / 完整分析。選一場 → 可連續看多個分析,不用重選。賽程**只列 24 小時內**(或進行中)的場次。
最上層 ← 不動作、只有 **q** 離開;其餘層 ← 返回上一層。各動作結束以 `wait_back()`(讀鍵、不回顯方向鍵亂碼)等 ← 返回。
需要選比賽時,從 `/fixture/active` 撈清單讓使用者用**編號**挑,resolve 成 eventId(如 `L19315552`),
不用手動輸入。底層用 subprocess 呼叫下面各工具(各工具仍可單獨呼叫)。即時追蹤有 `SX_API_KEY` 走
`live_ws.py`,否則 fallback 到 `live.py`。

**換賭盤(通用化)**:選單有「換賭盤」→ 選運動(`/sports`)→ 選聯賽(`/leagues/active?sportId=`)→
更新全域 `board`,之後賽程/賠率/即時都跟著換。預設世界盃(leagueId 1715)。賽程列表顯示**全部**進行中場次(不再限天數)。
**世界盃結束後**:賽程會空,程式提示改用換賭盤,不會出錯。**`stats.py` 綁 football-data World Cup,只支援世界盃**;
非世界盃聯賽時選項 4/5 會自動略過球隊數據並提示。`sx.py games/odds` 也支援 `--league` 單獨指定聯賽。

## 主要工具:`sx.py`(唯讀 CLI)

純 Python 標準庫,免安裝、**免 API key、免錢包**(讀取端點不需認證)。
使用流程:使用者用自然語言問 → 跑 `sx.py` → 解讀並給建議。

```bash
python3 sx.py games --days 4            # 列未來 N 天賽程(預設 3)
python3 sx.py games --date 2026-07-04   # 指定台北日的賽程
python3 sx.py odds --date 2026-07-01    # 某天所有場次的完整賠率板
python3 sx.py odds L19307196 [--all]    # 指定 eventId;--all 顯示全部讓分/大小盤
```

賠率板每場含:Match Winner(獨贏)+ vig、1X2、不輸 double chance、To Qualify(晉級)、
主讓分、主大小;每項都附 **taker 十進位賠率、隱含機率、可下注流動性**,以及**總成交量**。
時間一律 **Taipei (UTC+8)**;`--date` 以台北日曆日篩選。

## 關鍵事實(動程式前必讀)

- **賠率換算方向(頭號 bug 來源,已校準)**:`/orders/odds/best` 的 `outcomeOne/Two.percentageOdds`
  是各自 outcome 的 **maker 機率**。某邊的 **taker 十進位賠率 = `1 / (1 - 對面 outcome 的 p)`**。
  (單筆 `/orders` order:taker 賠率 = `1/(1 - percentageOdds/1e20)`。)`percentageOdds` 是 10^20 scale 字串,用整數運算。
- **ID**:Soccer = `sportId 5`(官方文件誤寫成 1);World Cup 正賽 = `leagueId 1715`;冠軍盤 = `10048`。
- **Market type**(soccer 實測):`52`=獨贏兩面、`1`=單隊 yes/no(組成 1X2)、`3`=讓分、`2`=大小、`88`=晉級。
  type 整數**跨運動不一致**,分類優先用 `betGroup`。
- **手續費**:單場直注 maker/taker 皆 **0%**;串關 5% on profit。vig 極低(~1%)→ sharp 市場。
- **金額單位**:USDC 6 decimals(`raw/1e6`)。地址須 EIP-55 checksum。
- **⚠️ 無歷史賠率 API**:賠率走勢要自己錄(輪詢/訂閱落地)。唯一可回溯歷史是 `GET /trades`。
- **即時資料**:用 Centrifugo(`wss://realtime.sx.bet/connection/websocket`),需 API key 換 token。
  舊的 Ably 已於 2026-07-01 棄用,勿用。
- **下單**:API 可程式化下單,但靠**錢包私鑰 EIP-712 簽名**(非 API key);本專案刻意不做,改人工。

## 即時盤口追蹤:`live.py`(in-play dashboard,輪詢版)

```bash
python3 live.py L19315552 [--interval 4] [--iterations 0]
# 使用者自己在終端機持續看:  ! python3 live.py <eventId>
```

清屏重繪 dashboard:即時比分/時間、獨贏+大小+讓分+晉級的 taker 賠率(附 ↑↓ 移動箭頭
與 vs 開盤變化)、近 10 分鐘成交筆數、最新成交流水(大單=動能訊號)。
**免金鑰**(輪詢 REST,非 websocket)。`/orders` 限速 20 次/10 秒 → interval 勿低於 2 秒。
**已知 in-play 現象**:開賽瞬間訂單簿可能短暫變空(莊家撤/重掛),幾秒後回補;
`/trades` 預設**最舊在前**,拿最新要用 `startDate` 過濾再 client 端排序(live.py 已處理)。

### 真 websocket 版:`live_ws.py`(已完成,Centrifugo push)

```bash
python3 live_ws.py L19315552 [--seconds 0]
# 使用者持續看:  ! python3 live_ws.py <eventId>
```

真正的 push(實測 ~4 次/秒),非輪詢。流程:`SX_API_KEY`(.env)換 realtime JWT
(`GET /user/realtime-token/api-key`,header `x-api-key`)→ 連 `wss://realtime.sx.bet/connection/websocket`
→ connect(帶 token)→ 訂 `order_book:event_{eventId}` + `fixtures:live_scores`
→ REST 抓初始全量 snapshot,再套用增量 order push(依 `orderHash` upsert,status≠ACTIVE 則移除)。
用 `websocket-client` 套件(已 pip install)。server 送空 `{}` = ping,回 `{}`。斷線自動重連。
**靜默偵測**:任何 frame(含 ping)都算收到;silence bucket(每 10 秒一格)進 sig,≥10s 頂列顯示紅色警告,≥45s 視為死連線主動 `ws.close()` 強制重連。
**防止選取被打斷**:只有內容簽章(比分+各盤賠率/量,不含時鐘)變動才重繪;`sig` 相同就不重畫。
另有**空白鍵暫停**(cbreak + `select` 非阻塞讀鍵)凍結畫面供滑鼠複製,再按恢復。走勢 sparkline **只在賠率變動時記點**(近 18 個變化點、cap 120、自動縮放),非歷史;本次連線期間累積。
`live.py`(輪詢版)仍保留當免安裝 fallback。

### 事件監看版:`watch_ev.py`(盤中下注警報用)

```bash
python3 watch_ev.py <SX_eventId> <lastH> <lastA> [API-Football_fixtureId]
# 例: python3 watch_ev.py L19427178 1 0 1582681
```

輕量的**盤中下注監看**(非 dashboard):每 `CYCLE`(180)秒印一次 STATUS(比分 + Over/Under 1.5·2.5 賠率 + 選填 API 數據),或**進球/終場即時印出並退出**——設計成由上層 `run_in_background` 啟動,退出後上層讀輸出、重新啟動接續(進球即通知的模式)。進球判定含 **4 秒 debounce**(防 feed 抖動/VAR 誤報,實測擋過假的倒退分)。API-Football 數據**只在要印出時呼叫一次**(省額度,別放迴圈每 10 秒打)。用於「賽前下注 → 盤中盯賠率移動找鎖利/對沖窗口」的工作流(見 middle-play skill)。

## 對戰表:`bracket.py`(終端機 knockout bracket,世界盃)

`python3 bracket.py` → 畫出 32 強→16強→8強→4強→決賽的對戰樹,含各場比分、未賽場次顯示 `⏱ ~Nd/~Nh` 概略倒數、狀態,勝方自動晉級。
資料來自 football-data.org(需 `FOOTBALL_DATA_TOKEN`)。對戰樹**葉節點順序硬編官方 2026 bracket**(`LEAVES`);
各輪結果由 API 依隊名配對即時填入,勝方自己往上傳遞(不依賴 API 的 next-round 傳遞,較即時)。左右手排版:
單向左→右樹,單行一場,圓角 box-drawing 連接線,輪次表頭。**勝方用顏色(綠)標記,不用 ✓**;
藍=已出線待戰、紅=進行中、灰=未定、金=冠軍。顏色用 span 記錄座標後上色,避免 escape code 破壞對齊。只支援世界盃(結構是 2026 專屬)。

## 球隊數據工具:`stats.py`(唯讀 CLI,多源交叉驗證)

SX API **沒有**球隊/球員表現數據,得靠外部體育數據 API。已實測免費來源:

```bash
python3 stats.py matchup "Mexico" "Ecuador"   # 兩隊 WC2026 戰績並排 + 交叉驗證
python3 stats.py team "Mexico"                 # 單隊
```

- **主源 football-data.org**:免費 tier **已確認涵蓋 World Cup**;需免費 token(`X-Auth-Token` header,10 calls/min)。放在專案根 `.env` 的 `FOOTBALL_DATA_TOKEN`(已 gitignore)。
- **交叉驗證源 TheSportsDB**:key `3` 免註冊即用,但**免費版覆蓋不完整**(整屆只回部分場次);資料正確,當比對用。
- **API-Football**(api-sports.io v3,`APISPORTS_KEY` **已設定於 `.env`**):SX 沒有的**盤面/深度數據**都靠它。免費 100 req/日,**實測 WC2026 有 live 覆蓋**。base `https://v3.football.api-sports.io`,header `x-apisports-key`。用法:
  - 先 `/fixtures?live=all` 從 `teams.*.name` 找到目標,拿 **API-Football fixture id**(≠ SX 的 eventId,如 Argentina-Egypt = SX `L19391927` / API `1576804`,兩邊要各自查)。
  - **in-play 盤面**:`/fixtures/statistics?fixture=<id>` → 射正(Shots on Goal)/總射/禁區內外射門/控球%/傳球成功率/犯規/越位(⚠ 此 feed 有數十秒延遲,進球後統計會慢一拍)。
  - **時間軸**:`/fixtures/events?fixture=<id>` → 進球/紅黃牌/換人/**PK 進失**(如實測抓到 Messi 21' Missed Penalty)。
  - **賽前**:`/predictions`、`/fixtures/lineups`、`/injuries`、`/fixtures/headtohead`、`/fixtures/players`(個人評分/數據)。
  - **省額度**:in-play 別每 3 分鐘拉滿;約每 3–6 分鐘或進球時一次,一場 ~15–33 次即夠(使用者一天只看一場)。盤中監看腳本 `watch_ev.py <SXid> <h> <a> <APIfixtureid>` 已能在每次回報夾帶「射正/總射/控球」。
- 不適用:Understat(只有俱樂部聯賽)、FBref(無 API 只能爬)、OpenFootball(2026 無現成檔)。
- 交叉驗證原則:同一場比分兩源一致才採信;`stats.py` 會自動比對並標 ⚠ mismatch。

## 共用工具:`common.py`

`flag(name)` 隊名→國旗 emoji(regional-indicator,2 字元=2 欄,終端機格網天然對齊;無 ISO 者如 England→GB)。
`countdown(iso)` 概略開賽倒數(`<1h`/`~7h`/`~2d`,不精確到分);`hours_until(iso)` 回小時數。
`wlen/wljust/wrjust` 全形寬度對齊。`sparkline(vals)` 迷你走勢線。`Spinner` 載入動畫。`human/bar` 縮寫與長條。`odds_color(dec)` 賠率五段 256 色(<1.5 亮綠→…→≥5 灰),賠率板與 live_ws 共用。
被 sx.py / main.py / bracket.py / live_ws.py 共用。

## 參考

- Base URL: mainnet `https://api.sx.bet`(rate limit `GET /orders` 20 req/10s、`/trades` 200/min)。
- 官方文件:`https://docs.sx.bet`(全文索引 `llms.txt`;每頁加 `.md` 可取純文字版)。
- 深入的 API 研究筆記留在本機(gitignore),不進本 repo。

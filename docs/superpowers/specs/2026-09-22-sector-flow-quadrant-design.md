# 族群輪動改「法人 × 大戶」資金流向四象限 設計

日期：2026-09-22　狀態：使用者已核准設計

## 問題

族群輪動頁（`#view-rotation`）的主圖是一張「近 N 日各類股累計漲跌%」表格，**從 `bfd0a53`（main.py 拆成
APIRouter）之後就再也畫不出來**：那次重寫把 `/api/sectors/rotation` 的 `sectors` 從陣列 `[{name, series, sum}]`
改成 dict `{name: [...]}`，前端 `loadRotation` 沒跟著改，`d.sectors.length` 恆為 `undefined` → 永遠顯示
「尚無類股資料」。本機實測端點其實有 37 個類股、20 天完整資料。

即使修好，那張表也只回答「哪些類股漲比較多」，回答不了使用者真正要的：**錢現在正流向哪些族群、
正在離開哪些族群、往哪個方向移動**。使用者提供「潮汐」app 作參考：它以三大法人買賣超為資金流向訊號，
把板塊依「水位 × 加速度」分成漲潮／輪動／觀望／退潮四區，用泡泡圖＋排行呈現。

## 使用者的決定（2026-09-22）

1. 訊號用**法人與大戶的資金流向**，不用類股價格動能，也不合成手訂權重的分數。
2. 法人（每日）與大戶（每週）節奏不同，**兩軸各放一個訊號**：X＝法人、Y＝大戶；泡泡帶尾巴顯示上一期位置。
3. 設計整段（§設計）已核准，含「移除壞掉的表格與其端點」「顏色不用紅綠」「四區排行當鍵盤替代」。

## 設計

### 1. 類股分類與母體

- 類股＝**32 個官方產業別**（`_industry_map`＝上市、`_otc_industry`＝上櫃，兩者合併、代號不衝突），與同頁下方
  「交叉選股」同一套，也是 37 個類股指數名稱的乾淨子集（多出的「水泥窯製」「塑膠化工」「機電」「化學生技醫療」
  「電子工業」是合成類，不用）。
- 母體只收 **4 碼、非 `00` 開頭**的普通股（同 `top_movers`／`change_histogram` 既有過濾）。ETF、權證、
  特別股不進任何一邊的加總。

### 2. 算式（`analysis.sector_flow`，純函式，無 I/O）

輸入（全部由端點組好傳入）：

| 參數 | 內容 | 來源 |
|---|---|---|
| `universe` | `{code: {sector, shares}}` | `_industry_map` ∪ `_otc_industry` |
| `closes` | `{code: close}` | `_quotes_for(flow_date)` 的收盤（上市）∪ `_otc_quotes_for`（上櫃） |
| `flow_cur` | `{code: net_lots}`，近 `FLOW_DAYS`=5 個交易日 `(foreign+trust+dealer)` 加總 | `stock_flow_daily` |
| `flow_prev` | 同上，再往前 5 個交易日；不足時 `None` | `stock_flow_daily` |
| `cust_cur` | `{code: Δbig400_pct}`，最近兩個**完整**週相減 | `custody_dist`，週由 `custody_compare_weeks` 挑 |
| `cust_prev` | 第 2、3 完整週相減；不足時 `None` | 同上 |
| `sector_chg` | `{sector: 當日類股指數漲跌%}` | `_sectors_for(flow_date)`，只給 tooltip 用 |

逐檔：

- `mcap_i = shares_i × close_i`。缺股數或缺收盤 → 該檔**整檔排除**（法人金額也需要收盤換算，沒有收盤兩邊都算不出），
  計入 `excluded.no_price`；查不到類股計入 `excluded.no_sector`。
- 法人金額 `inst_i = net_lots_i × 1000 × close_i`（元）。
- 大戶金額 `big_i = Δbig400_pct_i / 100 × shares_i × close_i`（元）——**與 `selfcheck.build_self_screen` 的
  `buy_value` 同一條算式**；實作時把那條抽成共用函式 `analysis.big_holder_amount(delta_pct, shares, close)`，
  兩處都呼叫它，不留第二份。

逐類股：

- `mcap_s = Σ mcap_i`；`x_s = Σ inst_i / mcap_s × 100`；`y_s = Σ big_i / mcap_s × 100`（**除以市值換成 %**——
  用絕對金額的話半導體永遠在最右邊，圖只會告訴你「半導體很大」）。
- `x_prev_s`／`y_prev_s` 同式用 `flow_prev`／`cust_prev`；對應輸入為 `None` 時輸出 `None`（不用 0 頂替，0 是
  「有資料且淨額為零」）。
- 大戶那一側缺某檔的 Δ（該檔某週沒有集保列）＝該檔不進 `Σ big_i`，**但仍進 `mcap_s`**（分母是類股規模，不隨分子
  的缺漏縮小）；`n_cust_s` 另外回報「有集保 Δ 的檔數」，讓前端能標「大戶樣本 12/104 檔」這種殘缺。
- `mcap_s == 0`（整類股都算不出）→ 該類股不輸出，計入 `excluded.sectors_no_mcap`。
- `top3_s`＝該類股 `inst_i` 最大的 3 檔 `{code, name, amount}`（tooltip 用）。
- 輸出依 `mcap_s` 降冪（畫圖時大泡泡先畫、小泡泡疊在上面才點得到）。

象限（前端依 `x`／`y` 符號判定，不進 API）：`x>0 ∧ y>0` 雙流入、`x>0 ∧ y≤0` 法人買・大戶減、
`x≤0 ∧ y>0` 大戶增・法人賣、其餘 雙流出。剛好 0 視為非正。

### 3. 端點 `GET /api/sectors/flow?days=5`（`api/market.py`）

- `days` 夾在 `[3, 20]`，預設 `FLOW_DAYS`=5。
- 交易日曆＝`stock_flow_daily` 的 distinct `date`（**不是** `market_daily`：法人窗口要的是「有法人資料的日子」；
  `market_daily` 當天早上就有列而法人 16:00 後才公布，用它會把今天的空列算進窗口）。本期＝最近 `days` 日，
  前期＝再往前 `days` 日；不足 `2×days` 日 → `flow_prev=None`、回應 `has_tail=false`。
- 集保：`custody_compare_weeks(c, as_of=flow_dates[-1], limit=3)`（**要帶 `as_of`**：法人窗口落後時不可拿更新的
  集保週）回的前兩週給 `cust_cur`，第 2、3 週給 `cust_prev`；不足兩週 → `cust_cur=None`、回應 `has_custody=false`；
  兩週但不足三週 → 只 `cust_prev=None`。
  **第 2、3 週不保證相鄰**：`custody_compare_weeks` 會略過殘缺週，真實 DB 是 09-18／09-11／**08-21**
  （09-04 只有 6 檔、08-28 只有 4 檔）——照用的話 `y` 是 1 週 delta 而 `y_prev` 是 3 週 delta，尾巴長度沒有意義
  （實測 34 個類股 `|y_prev|/|y|` 中位數 3.39、最大 40 倍）。`_weeks_adjacent(newer, older)` ＝ `0 < 天數差 ≤ 10`（TDCC 週日期遇
  假日會位移到週四；14 天＝中間少一週），**weeks[0]-weeks[1] 與 weeks[1]-weeks[2] 兩段都要相鄰**才算 `cust_prev`；
  否則 `cust_prev=None`、`custody_prev_weeks=[]`、回應帶 `custody_prev_skipped="weeks_not_adjacent"` 與
  `custody_prev_gap=[較新, 較舊]`（不相鄰的那一對），前端說明列印「上一期集保不相鄰（MM-DD→MM-DD 跨 N 週），
  尾巴省略」——安靜地少一條尾巴跟「這週大戶沒動」長得一模一樣。`y` 本身維持 weeks[0]-weeks[1]（與全站大戶增比
  `custody_change_map` 同一定義）。
  （2026-09-23 修訂：原文只寫「前三週給 `cust_prev`」，最終審查抓到相鄰性沒判。）
- 回應：

  ```
  {
    "flow_dates": [...5 日...], "flow_prev_dates": [...]|[],
    "custody_weeks": ["2026-09-18","2026-09-11"]|[], "custody_prev_weeks": [...]|[],
    "custody_prev_skipped": "weeks_not_adjacent"|null, "custody_prev_gap": [新,舊]|null,
    "has_custody": bool, "has_tail": bool,
    "sectors": [{"sector","x","y","x_prev","y_prev","mcap","n","n_cust","top3","chg_pct"}],
    "excluded": {"no_price": int, "no_sector": int, "sectors_no_mcap": int}
  }
  ```
  `y`／`y_prev` 在 `has_custody=false` 時為 `None`。
- 快取：`ai_cache` 鍵
  `sectorflow:v3:{flow_dates[-1]}:{stock_flow_fingerprint(cur+prev)}:{'-'.join(全部完整週，最多 3 個) or "none"}:{days}`。
  **鍵要編進計算真正用到的每一個輸入**，不是只放最顯眼的那一個。三種踩過的：(a) 只放最新週的話，完整週從 2 變 3
  而最新週不變時（回補讓舊週跨過門檻）會沿用舊鍵、吃到沒有 `y_prev` 的舊快取（Task 4 審查抓到）；(b) **回補在
  窗口中段補進一天**——日期集合變、最新日不變；(c) **上櫃單邊重抓**——日期集合也不變，只有筆數／淨額變。
  (b)(c) 只靠 `flow_dates[-1]` 完全看不出來，所以加 `db.stock_flow_fingerprint(conn, dates)`＝窗口內逐日
  `COUNT(*)` 與 `Σ(外資+投信+自營)` 串成字串取 md5 前 10 碼（空 `dates` 回 `"none"`；SQL 自己 `ORDER BY date`，
  與傳入順序無關）。鍵完整描述了輸入，所以**不需要**另外比對 `has_custody`——同一把鍵之下它不可能不一致，
  那道守衛是死碼。payload 仍帶 `has_custody` 給前端分支用。
  （2026-09-22 修訂：原文寫 v1 鍵只含最新週＋讀取端比對 `has_custody`，經審查證明守衛不可能觸發。
  2026-09-23 修訂：v2 → v3，加窗口指紋，最終審查抓到 (b)(c)。）
- **不連外**：全部輸入都來自本地表與既有的逐日快取（`_quotes_for`／`_otc_quotes_for`／`_sectors_for` 快取沒中會
  抓當天一次，那是既有行為且只針對最新一日，不是逐日迴圈）。

### 4. 前端（`web/app.js`、`web/index.html`、`web/styles.css`）

**移除**：`#rotation` 那個 `div`、`loadRotation()`、後端 `/api/sectors/rotation` 與 `rotation2:` 快取寫入
（唯一呼叫者就是那張表；`grep` 全 repo 確認）。`picks-command-copy` 的說明文字改成本圖的說明。

**新增 markup**（取代 `#rotation`，位置不變、仍在交叉選股上方）：

```html
<div id="rotation-flow" class="rotation-flow">
  <div id="flow-chart" class="chart flow-chart"></div>
  <div id="flow-quadrants" class="flow-quadrants"></div>
</div>
```

**主圖**（ECharts scatter，走 `initChart`）：
- 泡泡＝類股，`symbolSize = clamp(k·√mcap, 10, 56)`（同熱力圖用平方根壓縮，台積電那一類不會獨大）。
- 兩軸**對稱於 0**：`max = ceil(max(|x|)·1.1)`、`min = -max`（Y 同理），四象限面積才相等、原點在正中央。
- `markLine` 畫 x=0、y=0；`markArea` 四塊淡底（雙流入那塊略亮），象限標籤放各角落。
- **尾巴**：`has_tail` 時每個類股一條兩點 `line`（`x_prev,y_prev → x,y`，寬 1、半透明）＋前期位置一個 4px 小點；
  `x_prev` 或 `y_prev` 為 `None` 的類股不畫尾巴。
- **顏色不用紅綠**：泡泡與尾巴一律 `C.info`（藍），透明度隨 `|x|+|y|` 略增；紅綠鎖給行情漲跌是全站規則，
  資金流向不是漲跌（自算選股泡泡圖走過「全紅太刺眼」那一遭後就是這個決定）。價格漲跌只出現在 tooltip 的
  `chg_pct` ▲▼，那才是行情。
- tooltip（`financeTooltip`，`confine: true`）：類股名、`法人 5 日 +0.42%（09-15～09-21）`、
  `大戶週增 +0.08%（09-11→09-18，樣本 87/104 檔）`、市值、當日類股 ▲1.2%、法人買最多前 3 檔。
- 軸標籤：X「法人近 5 日淨買賣 ÷ 市值（%）」、Y「大戶 400張↑ 週增 ÷ 市值（%）」。
- 容器**先寫 inline height → setOption → resize**（既有教訓）；加進 `window` resize 清單與 `showView` 重新進頁的
  resize（既有規矩：每一張圖都要列）。

**四區排行**（`#flow-quadrants`）：四欄，標題依序 雙流入／大戶增・法人賣／法人買・大戶減／雙流出，各欄列該
象限類股，依 `√(x²+y²)` 降冪；每列 `<button class="flow-row" data-sector aria-pressed="false">類股名 ＋ x／y</button>`。
這是 canvas 的鍵盤替代（同 ui29 chip 的慣例），不是裝飾。

**Drill-down**：點泡泡或排行列 → `rotationSectorFilter = sector`，`loadCross()` 只顯示該類股的 `cross-grp`
（其餘加 `hidden`，不重打 API），`#cross-note` 加「顯示全部」鈕；再點同一個取消。狀態不持久化。

**說明列**（`#rotation-note`）：`法人 09-15～09-21（5 日）・集保 09-11→09-18・31 類股`，缺口照實：
`（3 檔查不到收盤、1 類算不出市值未列）`。

**降級**：
- `has_custody=false`：Y 軸整個沒有 → 不畫散點，改畫**單軸水平長條**（類股依 `x` 排序，同 `.hm-bar-*` 樣式），
  說明列寫「集保不足兩個完整週，暫以法人單軸顯示」。
- `has_tail=false`：不畫尾巴，說明列加「法人資料不足 10 日，尚無上一期」。
- `sectors` 為空：「尚無法人資料（`stock_flow_daily` 尚未累積）」。

### 5. 版面

- 桌機：`.rotation-flow { display:grid; grid-template-columns: 8fr 4fr; gap:16px }`，圖高 **460px**；≤1180px 改單欄、
  排行在圖下方；≤600px 圖高 **360px**、排行單欄。
- 觸控目標：排行列 `min-height: 28px`（≥ WCAG 2.2 的 24px）。
- 手機頂欄與交叉選股既有規則不動。

### 6. 快取版號與文件

- `web/index.html`×2、`api/public.py`×2 行、`tests/test_api.py`×4 處：`20260817-ui68` → `20260817-ui69`。
- `CLAUDE.md`、`AGENTS.md` 各補一節（含「bfd0a53 弄壞的表格」這件事、為什麼除以市值、為什麼不用紅綠、
  `has_custody` 讀寫雙守衛）。

### 7. 測試

`tests/test_analysis_sector_flow.py`（純函式）：
- 兩檔同類股 → `x`＝金額和 ÷ 市值和，不是 % 的平均。
- 缺收盤的檔整檔排除且 `excluded.no_price` +1；缺類股 `no_sector` +1；整類股無市值不輸出且 `sectors_no_mcap` +1。
- `flow_prev=None` → 每個類股 `x_prev is None`（不是 0）。
- 大戶缺某檔 Δ：不進分子、仍進分母，`n_cust` 少 1。
- 輸出依 `mcap` 降冪；`top3` 是法人金額最大的三檔。
- `big_holder_amount` 與 `build_self_screen` 用的是同一支（用 monkeypatch 讓它 raise，兩邊都要炸）。

`tests/test_api.py`：
- `/api/sectors/flow` 交易日曆來自 `stock_flow_daily`（seed `market_daily` 多一個今天的空列，窗口不得含它）。
- 只收 4 碼非 `00` 普通股（seed 一檔 `0050`、一檔 `00878`、一檔 5 碼，皆不得進加總）。
- 集保只有一週 → `has_custody=false`、`y` 全 `None`；集保週換了 → 快取鍵不同（seed 舊鍵的假 payload，斷言沒被拿來用）。
- `has_custody` 讀取守衛：預先塞一份 `has_custody=false` 的舊快取、集保已有兩週 → 必須重算，不得回舊的。
- `days` 夾值 `[3, 20]`。
- `/api/sectors/rotation` 已不存在（404）。
- 版號四處一致（既有 `test_public_overview_shares_internal_frontend` 會抓）。

瀏覽器實測（無前端自動測試）：1904／1280／375 三寬度零頁面溢出；注入四種象限的假資料驗落點與排行分欄；
點泡泡與點排行列都會篩交叉選股、再點取消；`has_custody=false` 的降級長條實際渲染；切走再切回圖不為 0 尺寸。

### 8. 刻意不做

- 潮汐的 108 板塊：本站細分類（`sub_industry_ref`，530 種）太碎、32 個官方產業別剛好一屏；細分類之後可加 toggle。
- 合成分數／手訂權重（PRODUCT.md：不把手訂權重包裝成訊號）。
- 加速度四區（漲潮／退潮）：尾巴已經表達方向；要再加是 v2。
- 盤中即時：盤後版，資料日以法人最新日為準。
- 修 `/api/sectors/rotation` 的契約：直接移除，20 日類股漲跌若之後需要，從 `_sectors_for` 重做。

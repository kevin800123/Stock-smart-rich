"""股票期貨（股期）官方盤後資料：純解析 ＋ 薄網路包裝。

與 `taifex.py` 分開的理由：CSV 欄位形狀、下載表單、主力月規則與 tick 級距全都不同，
混進去會讓兩邊都難讀（`taifex.py` 已 440 行）。同一個資料來源、不同的產品族。
"""
from __future__ import annotations

import csv
import io
import logging
import re
from decimal import Decimal

import httpx

log = logging.getLogger("spr")

SSF_HEADER = ("交易日期,契約,到期月份(週別),開盤價,最高價,最低價,收盤價,漲跌價,漲跌%,"
              "成交量,結算價,未沖銷契約數,最後最佳買價,最後最佳賣價,歷史最高價,歷史最低價,"
              "是否因訊息面暫停交易,交易時段,價差對單式委託成交量")

SSF_FORM = "https://www.taifex.com.tw/cht/3/futDataDown"
SSF_DOWN = "https://www.taifex.com.tw/cht/3/dlFutDataDown"
# 一天正常有 1,629 列非價差的一般列。抓到的比這個少一截就是資料還沒齊，
# 不是「今天比較冷清」——寧可不寫，也不要寫進半套。
MIN_GENERAL_ROWS = 1200


def fetch_ssf_daily(start: str, end: str) -> list[dict]:
    """抓指定區間（`YYYY/MM/DD`，**不可超過一個月**）的股期日檔。

    三種「HTTP 200 但不算成功」的假象逐一擋掉，全部回空 list 並留一行 warning
    （呼叫端據此略過、不寫入）：
    1. Content-Type 不是 MS950 → 區間超過一個月的 UTF-8 HTML 警告頁
    2. 表頭不符或只有表頭 → 非交易日
    3. 「一般、非價差」列數**逐日**檢查不足 → 該日日盤資料還沒發佈（早上打只會有
       盤後列，且日期算在下一個交易日）。**逐日判定，不是整個回應加總**：
       `start`/`end` 常是排程重疊補最近幾個交易日的多日區間，若用加總，13 個完整
       交易日（每天 1,629 列）＋ 今天只有盤後列（一般 0 列）加起來遠超門檻，
       今天那半天的資料反而會被其他日期的量掩蓋掉、照樣寫進 DB——這正是這道守衛
       原本要防的事。逐日判定讓某一天資料不足時只剔除那一天，不連累其他已齊全的
       日期，也不會被它們的量沖淡。

    連線層例外（timeout／連線中斷／TLS 等）刻意不在這裡攔截，原樣往上拋給呼叫端：
    呼叫端是排程 Job，經 `run_job` 記錄失敗狀態與例外訊息；若在這裡吞掉，「網路
    不通」與「日盤還沒發佈」會變得無法分辨（同月營收那次「例外被吞兩層、事後查
    不出原因」的教訓，見 CLAUDE.md 2026-09「月營收告警查不出原因」一節）。
    """
    with httpx.Client(timeout=60, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0"}) as cli:
        cli.get(SSF_FORM, headers={"Referer": SSF_FORM})
        r = cli.post(SSF_DOWN, headers={"Referer": SSF_FORM}, data={
            "down_type": "1", "commodity_id": "specialid", "commodity_id2": "all",
            "queryStartDate": start, "queryEndDate": end,
        })
    ct = (r.headers.get("content-type") or "").upper()
    if "MS950" not in ct:
        log.warning("[ssf] %s~%s 回應不是 MS950（多半是區間超過一個月的警告頁）：%s",
                    start, end, ct)
        return []
    try:
        rows = parse_ssf_daily_csv(r.content.decode("ms950", errors="replace"))
    except ValueError as e:
        log.warning("[ssf] %s~%s 解析失敗：%s", start, end, e)
        return []
    if not rows:
        log.warning("[ssf] %s~%s 一般列只有 0 列（<%d），視為資料未發佈",
                    start, end, MIN_GENERAL_ROWS)
        return []
    # 逐日分組計數，而非整個回應加總——見上方 docstring 第 3 點。
    dates_in_order = list(dict.fromkeys(x["date"] for x in rows))
    general_counts = {d: 0 for d in dates_in_order}
    for x in rows:
        if x["session"] == "一般" and not x["is_spread"]:
            general_counts[x["date"]] += 1
    qualifying = {d for d, n in general_counts.items() if n >= MIN_GENERAL_ROWS}
    dropped = {d: n for d, n in general_counts.items() if d not in qualifying}
    if dropped:
        log.warning("[ssf] %s~%s 以下日期一般列數不足（<%d），視為當日資料未發佈已剔除：%s",
                    start, end, MIN_GENERAL_ROWS, dropped)
    if not qualifying:
        return []
    return [x for x in rows if x["date"] in qualifying]


def _f(s) -> float | None:
    """'-'／空字串／'1.37%' 都要接得住。算不出就回 None（全站慣例）。"""
    s = (s or "").strip().replace(",", "").rstrip("%")
    if not s or s == "-":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _i(s) -> int | None:
    v = _f(s)
    return None if v is None else int(v)


def parse_ssf_daily_csv(text: str) -> list[dict]:
    """把官方股期日檔 CSV 解析成逐列 dict。表頭不符一律丟 ValueError。"""
    first = (text or "").splitlines()[0].strip() if text.strip() else ""
    if first != SSF_HEADER:
        raise ValueError(f"股期日檔表頭與預期不符：{first[:120]!r}")
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        month = (r.get("到期月份(週別)") or "").strip()
        d = (r.get("交易日期") or "").strip().replace("/", "-")
        out.append({
            "date": d,
            "contract": (r.get("契約") or "").strip(),
            "month": month,
            "session": (r.get("交易時段") or "").strip(),
            "is_spread": "/" in month,
            "open": _f(r.get("開盤價")), "high": _f(r.get("最高價")),
            "low": _f(r.get("最低價")), "close": _f(r.get("收盤價")),
            "chg": _f(r.get("漲跌價")), "chg_pct": _f(r.get("漲跌%")),
            "volume": _i(r.get("成交量")),
            "settlement": _f(r.get("結算價")), "oi": _i(r.get("未沖銷契約數")),
        })
    return out


def root_of(contract: str) -> str:
    """母合約＝代碼前 2 碼。正常合約 root+F、調整後 root+1 自動歸在一起。"""
    return (contract or "")[:2]


def summarize_ssf_day(rows: list[dict]) -> list[dict]:
    """逐列 → 每個 root 每日一列摘要。

    量與價**刻意用不同的母體**：
    - 量：所有非價差列、全月份、一般＋盤後、含調整後合約（官方 STFTop10 口徑）
    - 價：只取 `root+F` 的一般、非價差列（調整後合約乘數非標準，價格不可混用）
    """
    vol: dict[str, int] = {}
    price_rows: dict[str, list[dict]] = {}
    dates: dict[str, str] = {}
    for r in rows:
        if r["is_spread"]:
            continue
        root = root_of(r["contract"])
        if not root:
            continue
        vol[root] = vol.get(root, 0) + (r["volume"] or 0)
        dates.setdefault(root, r["date"])
        if r["session"] == "一般" and r["contract"] == root + "F":
            price_rows.setdefault(root, []).append(r)

    out = []
    for root, cands in price_rows.items():
        # 結算價為 0 的是到期腳：價格與價差都不可採用（但量已經算進去了）
        usable = [r for r in cands if (r["settlement"] or 0) > 0] or cands
        traded = [r for r in usable if (r["volume"] or 0) > 0]
        main = (max(traded, key=lambda r: r["volume"]) if traded
                else min(usable, key=lambda r: r["month"]))
        out.append({
            "date": dates.get(root) or main["date"],
            "root": root, "main_month": main["month"],
            "open": main["open"], "high": main["high"],
            "low": main["low"], "close": main["close"],
            "chg": main["chg"], "chg_pct": main["chg_pct"],
            "settlement": main["settlement"], "oi": main["oi"],
            "volume": vol.get(root, 0), "main_volume": main["volume"] or 0,
        })
    return out


# (上限, 一檔) —— 上限為開區間。股期自 2026-07-06 起與現貨不同：
# 現貨在 1000–2500 跳 5 元，股期跳 1 元。
_TICKS_STOCK = ((Decimal("10"), Decimal("0.01")), (Decimal("50"), Decimal("0.05")),
                (Decimal("100"), Decimal("0.1")), (Decimal("500"), Decimal("0.5")),
                (Decimal("2500"), Decimal("1")), (None, Decimal("5")))
_TICKS_ETF = ((Decimal("50"), Decimal("0.01")), (None, Decimal("0.05")))


def _bands(is_etf: bool):
    """回傳該資產類別的 tick 級距表。"""
    return _TICKS_ETF if is_etf else _TICKS_STOCK


def ssf_tick_size(price: float, is_etf: bool = False) -> float:
    """給定價格，回傳 1 檔等於幾元。"""
    p = Decimal(str(price))
    for hi, tick in _bands(is_etf):
        if hi is None or p < hi:
            return float(tick)
    return float(_bands(is_etf)[-1][1])


def _grid_index(price: Decimal, is_etf: bool) -> Decimal:
    """從 0 走到 price 共幾檔。跨級距時每一段各用自己的檔位累加。"""
    left, idx = Decimal("0"), Decimal("0")
    for hi, tick in _bands(is_etf):
        if hi is None or price < hi:
            return idx + (price - left) / tick
        idx += (hi - left) / tick
        left = hi
    return idx


def ssf_basis_ticks(fut, spot, is_etf: bool = False) -> int | None:
    """期現價差換算成「幾檔」。

    **必須沿網格走**：兩端落在不同級距時，除以單一 tick 一定錯——實測聯茂
    現貨 495／期貨 501，走網格 11 檔，除以期貨端得 6、除以現貨端得 12。
    """
    if fut is None or spot is None:
        return None
    diff = _grid_index(Decimal(str(fut)), is_etf) - _grid_index(Decimal(str(spot)), is_etf)
    return int(diff.to_integral_value())


STOCK_LISTS_URL = "https://www.taifex.com.tw/cht/2/stockLists"

_TR = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_TD = re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)
_SR_ONLY = re.compile(r"<span[^>]*class=\"sr-only\"[^>]*>.*?</span>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")


def _cell(html: str) -> str:
    """去掉螢幕閱讀器專用文字再剝標籤——直接讀文字會把那個『是』當成標記。"""
    return _TAG.sub("", _SR_ONLY.sub("", html)).replace("&nbsp;", " ").strip()


def parse_stock_lists(html: str) -> dict:
    """stockLists 表 → {root: {code, stock_name, name, multiplier, is_etf, is_mini}}。

    一個來源就給齊代號、簡稱與契約乘數；小型與 ETF 由乘數與 ◎ 標記判定。
    """
    out: dict[str, dict] = {}
    for tr in _TR.findall(html):
        tds = [_cell(x) for x in _TD.findall(tr)]
        if len(tds) < 12:
            continue
        root, code, short = tds[0], tds[2], tds[3]
        if not re.fullmatch(r"[A-Z]{2}", root) or not code:
            continue
        mult = _i(tds[11])
        if not mult:
            continue
        is_etf = "◎" in tds[9] or "◎" in tds[10]
        is_mini = mult in (100, 1000)
        # 交易時段：306 檔到 13:45，但有 14 檔（成分股在海外的 ETF）到 16:15。
        # 那 14 檔的收盤比現貨晚 2.5 小時，期現價差的誤導程度遠大於一般的 15 分鐘落差。
        session = tds[12].replace(" ", "")
        out[root] = {"code": code, "stock_name": short,
                     "name": ("小型" if is_mini else "") + short,
                     "multiplier": mult, "is_etf": is_etf, "is_mini": is_mini,
                     "session_end": session.split("~")[-1] if "~" in session else "",
                     "late_session": "13:45" not in session}
    return out


def fetch_ssf_contract_map() -> dict:
    """抓合約對照表。失敗回空 dict——呼叫端的月快取兩端都擋空值，不會把失敗永久化。"""
    try:
        with httpx.Client(timeout=30, follow_redirects=True,
                          headers={"User-Agent": "Mozilla/5.0"}) as cli:
            r = cli.get(STOCK_LISTS_URL)
        return parse_stock_lists(r.content.decode("utf-8", errors="replace"))
    except Exception as e:  # noqa: BLE001
        log.warning("[ssf] 合約對照表抓取失敗：%s: %s", type(e).__name__, e)
        return {}

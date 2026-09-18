"""一次性診斷：確認「股期概況」需要的期交所端點從 Zeabur production 打不打得到。

**驗完就刪**（沿用已移除的 `net-check`／`tv-check` 先例）。本專案已被「本機通、雲端不通」
咬過三次（Yahoo 被 IP 封、mopsfin `/compare/report`、`/api/ohlc/backfill` 熔斷），
每次都是寫完才發現，所以先花一次部署確認。

兩條紀律：

1. **目標網址全部寫死在這個檔案裡，不吃任何使用者輸入。**做成通用取回器就是一個 SSRF。
2. **純讀**：不寫資料庫、不寫快取、不改任何狀態。

同步跑會逾時（六項加起來在共用 CPU 上可能破分鐘），而**探測自己 502 會讓結果無法判讀**
（分不出是端點逾時還是期交所打不到），所以走背景執行緒＋輪詢，同
`financials/backfill-report` 的既有模式。
"""
from __future__ import annotations

import csv
import io
import json
import threading
import time
from datetime import date, timedelta

import httpx

# --- 寫死的目標（不吃輸入） -------------------------------------------------
_FUT_FORM = "https://www.taifex.com.tw/cht/3/futDataDown"
_FUT_DOWN = "https://www.taifex.com.tw/cht/3/dlFutDataDown"
_STOCK_LISTS = "https://www.taifex.com.tw/cht/2/stockLists"
_STOCK_MARGIN = "https://www.taifex.com.tw/cht/5/stockMarginingDown"
_INDEX_MARGIN = "https://www.taifex.com.tw/cht/5/indexMargingDown"
_MIS_QUOTE = "https://mis.taifex.com.tw/futures/api/getQuoteList"

_UA = {"User-Agent": "Mozilla/5.0"}
# 本機已驗證這天有完整資料（1,922 列），拿它當「可達性」的定錨，
# 才不會在國定假日把「今天沒開盤」誤讀成「打不到」。
_KNOWN_GOOD_DAY = "2026/09/17"

_lock = threading.Lock()
_state: dict = {"status": "idle", "results": [], "started_at": None,
                "finished_at": None, "fatal": None}


def _summarize_csv(raw: bytes, content_type: str) -> dict:
    """把 CSV 回應濃縮成可判讀的幾個數字。

    三種「失敗長得像成功」都要分得出來（全部都是 HTTP 200 且有 body）：
    區間超過一個月回 UTF-8 的 HTML 警告頁、非交易日回只有表頭的 197 B、正常回 MS950 CSV。
    """
    out: dict = {"bytes": len(raw)}
    if "MS950" not in (content_type or "").upper():
        # UTF-8 的 HTML 警告頁走這條；照 MS950 解碼會變亂碼，所以改用 UTF-8 取前幾個字
        out["looks_like"] = "not-ms950"
        out["head"] = raw[:160].decode("utf-8", errors="replace").replace("\n", " ")
        return out
    text = raw.decode("ms950", errors="replace")
    rows = list(csv.DictReader(io.StringIO(text)))
    plain = [r for r in rows
             if (r.get("交易時段") or "").strip() == "一般"
             and "/" not in (r.get("到期月份(週別)") or "")]
    dates = sorted({(r.get("交易日期") or "").strip() for r in rows if (r.get("交易日期") or "").strip()})
    sessions: dict[str, int] = {}
    for r in rows:
        k = (r.get("交易時段") or "?").strip()
        sessions[k] = sessions.get(k, 0) + 1
    out.update({
        "looks_like": "csv" if rows else "header-only",
        "rows": len(rows),
        "sessions": sessions,          # 早上跑會看到只有「盤後」——日盤還沒出，不是壞掉
        "plain_general_rows": len(plain),
        "roots": len({(r.get("契約") or "").strip()[:2] for r in plain}),
        "dates": dates[:40],
        "date_count": len(dates),
    })
    return out


def _probe(name: str, fn) -> dict:
    """跑一項探測。任何一項失敗都只影響它自己，不中斷其餘。"""
    t0 = time.monotonic()
    try:
        info = fn()
        info.setdefault("ok", True)
    except Exception as e:  # noqa: BLE001 — 探測的重點就是把失敗原樣記下來
        info = {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}
    info["name"] = name
    info["elapsed_ms"] = int((time.monotonic() - t0) * 1000)
    return info


def _fut_csv(client: httpx.Client, start: str, end: str) -> dict:
    client.get(_FUT_FORM, headers={"Referer": _FUT_FORM})
    r = client.post(_FUT_DOWN, headers={"Referer": _FUT_FORM}, data={
        "down_type": "1", "commodity_id": "specialid", "commodity_id2": "all",
        "queryStartDate": start, "queryEndDate": end,
    })
    ct = r.headers.get("content-type", "")
    return {"http": r.status_code, "content_type": ct, **_summarize_csv(r.content, ct)}


def _plain_get(client: httpx.Client, url: str, encoding: str) -> dict:
    r = client.get(url, headers=_UA)
    text = r.content.decode(encoding, errors="replace")
    return {
        "http": r.status_code,
        "content_type": r.headers.get("content-type", ""),
        "bytes": len(r.content),
        "head": text[:200].replace("\n", " ").replace("\r", " "),
    }


def _mis(client: httpx.Client) -> dict:
    # body 必須是 UTF-8（「全部」若送成 Big5，對方回 400 Invalid UTF-8 start byte）
    body = json.dumps({"MarketType": "0", "SymbolType": "F", "KindID": "4", "CID": "",
                       "ExpireMonth": "", "RowSize": "全部", "PageNo": "",
                       "SortColumn": "", "AscDesc": "A"}, ensure_ascii=False).encode("utf-8")
    r = client.post(_MIS_QUOTE, headers={**_UA, "Content-Type": "application/json"}, content=body)
    out = {"http": r.status_code, "bytes": len(r.content)}
    try:
        j = r.json()
        quotes = (j.get("RtData") or {}).get("QuoteList") or []
        out.update({"rt_code": j.get("RtCode"), "quotes": len(quotes)})
        if quotes:
            q = quotes[0]
            out["sample"] = {k: q.get(k) for k in ("SymbolID", "DispCName", "CLastPrice", "CDate")}
    except Exception as e:  # noqa: BLE001
        out["parse_error"] = f"{type(e).__name__}: {e}"[:200]
        out["head"] = r.content[:160].decode("utf-8", errors="replace")
    return out


def _run() -> None:
    # 整支包 try/finally：`_probe` 之外若丟例外（例如連 Client 都建不起來），狀態會永遠
    # 停在 running，而「永遠跑不完」正是這支探測最該避免的那種無法判讀的結果。
    results: list[dict] = []
    fatal = None
    try:
        results = _run_all()
    except Exception as e:  # noqa: BLE001
        fatal = f"{type(e).__name__}: {e}"[:300]
    finally:
        with _lock:
            _state["results"] = results
            _state["fatal"] = fatal
            _state["status"] = "error" if fatal else "done"
            _state["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")


def _run_all() -> list[dict]:
    today = date.today()
    results = []
    # 每項各自一個短逾時：慢到 25 秒就算「實質上不可用」，不值得再等
    with httpx.Client(timeout=25, follow_redirects=True, headers=_UA) as cli:
        results.append(_probe(
            "1. 股期日檔（已知有資料的定錨日 %s）" % _KNOWN_GOOD_DAY,
            lambda: _fut_csv(cli, _KNOWN_GOOD_DAY, _KNOWN_GOOD_DAY)))
        results.append(_probe(
            "2. 股期日檔（今天 %s — 順便量發佈時間）" % today.strftime("%Y/%m/%d"),
            lambda: _fut_csv(cli, today.strftime("%Y/%m/%d"), today.strftime("%Y/%m/%d"))))
        results.append(_probe(
            "3. 股期 14 天區間（約 1.8MB，測回補大回應）",
            lambda: _fut_csv(cli, (today - timedelta(days=14)).strftime("%Y/%m/%d"),
                             today.strftime("%Y/%m/%d"))))
        results.append(_probe("4. stockLists 契約乘數對照表",
                              lambda: _plain_get(cli, _STOCK_LISTS, "utf-8")))
        results.append(_probe("5. 股票期貨保證金比例 CSV",
                              lambda: _plain_get(cli, _STOCK_MARGIN, "ms950")))
        results.append(_probe("6. 指數期貨保證金 CSV（微台）",
                              lambda: _plain_get(cli, _INDEX_MARGIN, "ms950")))
        results.append(_probe("7. MIS 即時報價（只為 v2 探路，失敗不影響 v1）",
                              lambda: _mis(cli)))
    return results


def start_or_status(restart: bool = False) -> dict:
    """啟動探測或查詢進度。永遠立刻回傳（探測自己逾時的話結果就無法判讀了）。"""
    with _lock:
        if _state["status"] == "running":
            return {"status": "running", "started_at": _state["started_at"]}
        if _state["status"] in ("done", "error") and not restart:
            return {k: _state[k]
                    for k in ("status", "results", "started_at", "finished_at", "fatal")}
        _state.update({"status": "running", "results": [], "fatal": None,
                       "started_at": time.strftime("%Y-%m-%d %H:%M:%S"), "finished_at": None})
    threading.Thread(target=_run, name="spr-ssf-probe", daemon=True).start()
    return {"status": "running", "started_at": _state["started_at"]}

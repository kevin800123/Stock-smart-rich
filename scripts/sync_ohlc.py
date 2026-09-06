r"""本機抓全市場個股日 OHLC → 匯入 production（繞過 Zeabur 打不動官方來源）。

為什麼需要這支（實測證據，非推測）：
  * 本機打 TWSE MI_INDEX / 櫃買 dailyQuotes，每個測到的日期都拿得到資料
    （2026-09-04：上市 1,083 檔、上櫃 861 檔；缺口中的 2026-03-02 也有 865 檔）。
  * 同一份程式在 Zeabur 上跑 /api/ohlc/backfill 卻連續失敗到兩個市場都熔斷、一天都補不進，
    症狀是 stock_ohlc 停在 2026-04-06，而 market_daily 仍每天更新（兩者打不同端點）。
  → 這與 mopsfin 完整報表是同一類問題（雲端出站打不動、本機打得動），故沿用同一套已驗證的
    解法：重活留本機，雲端只負責回報缺口與收資料。

流程（自動迴圈到補完，可隨時中斷，重跑會從剩下的續補）：
  1. GET  {base}/api/ohlc/pending?limit=N   → 還缺的交易日（新的排前面）
  2. 本機 twse.fetch_stock_daily / tpex.fetch_otc_daily 逐日抓（不碰本機 DB）
  3. POST {base}/api/ohlc/import            → 上 production 的 stock_ohlc
  4. 重複，直到 remaining 不再下降

用法（repo 根目錄、專案 venv）：
  .venv\Scripts\python scripts\sync_ohlc.py --user admin
  （--base-url 預設正式站；密碼不帶就提示輸入，或設環境變數 SPR_BASIC_PASS）
或直接雙擊 scripts\sync_ohlc.bat（帳密走 DPAPI 快取，與其他兩支共用）。
"""
import argparse
import getpass
import os
import sys
import time
import warnings
from datetime import date as _date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

warnings.filterwarnings("ignore")          # 櫃買憑證缺 SKI，verify=False 會噴 InsecureRequestWarning

import httpx  # noqa: E402

from stocks_power_rich.sources import tpex, twse  # noqa: E402

# Windows 主控台預設是 cp950，編不出 ✓／✗／⚠ 這類符號——print 會直接拋 UnicodeEncodeError，
# 把整支腳本**已經做完的工作**在最後一行炸掉（實測：資料都匯入成功了，卻以 traceback 收場，
# 看起來像失敗）。errors="replace" 讓最壞情況只是顯示成 ?，不會中斷。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001 — 舊版 Python 或非 TextIO 就維持原樣
    pass

DEFAULT_BASE = "https://stock-power-rich.zeabur.app"


def fetch_one_day(ds: str, timeout: float) -> dict:
    """抓單一交易日的上市＋上櫃全市場 OHLC，合併成 {代號: {...}}。

    兩個市場**各自 try**：一邊掛掉不該讓另一邊的資料一起丟掉（同 updater 逐市場獨立的慣例）。
    某一天真的沒有資料（國定假日）就回空，呼叫端據此略過、不會誤判成錯誤。
    """
    d = _date.fromisoformat(ds)
    out: dict = {}
    for label, fn in (("上市", twse.fetch_stock_daily), ("上櫃", tpex.fetch_otc_daily)):
        try:
            rows = fn(d) or {}
            out.update(rows)
        except Exception as e:  # noqa: BLE001
            print(f"    ! {label} {ds} 抓取失敗：{type(e).__name__}: {str(e)[:70]}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="本機抓個股日 OHLC 並匯入 production")
    ap.add_argument("--base-url", default=DEFAULT_BASE)
    ap.add_argument("--user", default=os.getenv("SPR_BASIC_USER", ""))
    ap.add_argument("--password", default=os.getenv("SPR_BASIC_PASS", ""))
    ap.add_argument("--days", type=int, default=400, help="往回看幾個日曆天找缺口")
    ap.add_argument("--force-days", type=int, default=0,
                    help="強制重抓最近 N 個交易日（不管系統認為有沒有缺）。"
                         "用於「日期有、但某些個股缺列或值是錯的」——匯入會覆蓋，所以直接重抓即可修好")
    ap.add_argument("--batch", type=int, default=5, help="每輪補幾個交易日（每日約 1 萬列，別設太大）")
    ap.add_argument("--throttle", type=float, default=0.4, help="每個日期之間的間隔秒（對官方溫柔）")
    ap.add_argument("--timeout", type=float, default=60)
    ap.add_argument("--max-rounds", type=int, default=200)
    args = ap.parse_args()

    user = args.user or input("Basic Auth 帳號: ").strip()
    password = args.password or getpass.getpass("Basic Auth 密碼: ")
    base = args.base_url.rstrip("/")
    client = httpx.Client(timeout=args.timeout, auth=(user, password),
                          headers={"User-Agent": "spr-sync-ohlc/1.0"})

    def call(method: str, path: str, retries: int = 5, **kw):
        """打端點，5xx／連線錯誤自動重試（Zeabur 重新部署與代理抖動是常態，長工作不能一撞就死）。
        401 是帳密問題、重試無用 → 直接放棄；其他 4xx 同樣不重試。"""
        for i in range(retries):
            try:
                r = client.request(method, base + path, **kw)
                if r.status_code == 401:
                    print("✗ 401：帳號或密碼不對"); return None
                if 400 <= r.status_code < 500:
                    print(f"✗ {r.status_code}：{r.text[:120]}"); return None
                if r.status_code >= 500:
                    raise httpx.HTTPError(f"HTTP {r.status_code}")
                return r.json()
            except Exception as e:  # noqa: BLE001
                if i == retries - 1:
                    print(f"✗ {path} 連續失敗：{type(e).__name__}: {str(e)[:70]}"); return None
                time.sleep(2 * (i + 1))
        return None

    prev_remaining, stall = None, 0
    before = ""          # 強制模式的往回分頁游標
    forced_left = args.force_days
    for rnd in range(1, args.max_rounds + 1):
        p = call("GET", f"/api/ohlc/pending?days={args.days}&limit={args.batch}"
                        f"&force_days={forced_left}&before={before}")
        if p is None:
            return 1
        dates, remaining = p.get("dates") or [], p.get("remaining", 0)
        if rnd == 1:
            print(f"起始：缺 {remaining} 個交易日（上市缺 {p.get('missing_twse')}、"
                  f"上櫃缺 {p.get('missing_otc')}），最新已存 {p.get('latest_stored')}")
        if not dates:
            print(f"✓ 完成：已無待補交易日（剩餘 {remaining}）"); return 0

        payload, got = {}, 0
        for ds in dates:
            rows = fetch_one_day(ds, args.timeout)
            if rows:
                payload[ds] = rows
                got += len(rows)
            else:
                print(f"    - {ds} 無資料（國定假日或來源未提供），略過")
            time.sleep(max(0.0, args.throttle))

        if payload:
            r = call("POST", "/api/ohlc/import", json={"data": payload})
            if r is None:
                return 1
            print(f"[{rnd}] 補 {list(payload)[-1]}~{list(payload)[0]}："
                  f"抓 {got} 列 → 匯入 {r.get('imported')} 列"
                  + (f"，強制模式尚餘 {max(0, forced_left - len(dates))} 天"
                     if forced_left else f"，剩餘 {remaining - len(payload)}"))
        else:
            print(f"[{rnd}] 這批 {len(dates)} 天都沒有資料，跳過")

        if forced_left:
            # 強制模式沒有「remaining 會下降」可依靠（那些日期本來就都在），所以自己往回
            # 分頁：游標移到這輪最舊的一天，並扣掉已重抓的天數，補滿要求的量就收工。
            before = min(dates)
            forced_left = max(0, forced_left - len(dates))
            if forced_left == 0:
                print(f"✓ 強制重抓完成（已回補到 {before}）"); return 0
            continue

        # 進度停滯偵測：連續 3 輪 remaining 不動就收工（那些日期官方本來就沒有，
        # 再跑下去只是每輪重抓同一批——同 sync_report 的 plateau 判定）
        if prev_remaining is not None and remaining >= prev_remaining:
            stall += 1
            if stall >= 3:
                print(f"⚠ 連續 {stall} 輪沒有進展（剩餘 {remaining} 天官方應無資料），收工")
                return 0
        else:
            stall = 0
        prev_remaining = remaining
    print("⚠ 達到 max-rounds 上限，請重跑續補")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

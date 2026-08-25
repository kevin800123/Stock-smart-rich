"""本機抓季報完整報表 → 匯入 production（繞過 Zeabur 打不動 mopsfin 報表端點）。

Zeabur 便宜方案打 mopsfin 的完整報表端點（/compare/report）每個請求太慢/被擋，逾時到一筆
都提交不了（實測 remaining 卡住、0 提交）。但**本機**抓同一個端點 ~6 秒沒問題。這支腳本因此
在本機做重活、把算好的結果 POST 上雲，和每天上傳 CSV 同一個哲學（本機做 Zeabur 做不到的事）。

流程（自動迴圈到補完為止，可隨時中斷、重跑會從剩下的續補）：
  1. GET  {base}/api/financials/report-pending?limit=N  → 還缺報表指標的代號 + anchor 季
  2. 本機 updater.compute_report_indicators(codes, anchor) 抓報表、反推單季（不碰本機 DB）
  3. POST {base}/api/financials/import  → 上 production 的 stock_financials
  4. 重複，直到 remaining 連續數輪不再下降

用法（在 repo 根目錄、用專案 venv）：
  .venv\\Scripts\\python scripts\\sync_report_financials.py --user admin
  （--base-url 預設正式站；密碼不帶就跳出提示輸入，或設環境變數 SPR_BASIC_PASS）

季報一季才更新一次，所以這支是「偶爾手動跑一次、可能跑一兩小時」的工作，不是每天的事。
"""
import argparse
import getpass
import os
import sys
import time

# 讓腳本能 import 專案套件（從 repo 根目錄執行）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402

from stocks_power_rich import updater  # noqa: E402

DEFAULT_BASE = "https://stock-power-rich.zeabur.app"


def main() -> int:
    ap = argparse.ArgumentParser(description="本機抓季報完整報表並匯入 production")
    ap.add_argument("--base-url", default=DEFAULT_BASE, help="雲端網址（預設正式站）")
    ap.add_argument("--user", default=os.getenv("SPR_BASIC_USER", ""), help="Basic Auth 帳號")
    ap.add_argument("--password", default=os.getenv("SPR_BASIC_PASS", ""), help="Basic Auth 密碼（不帶則提示輸入）")
    ap.add_argument("--batch", type=int, default=30, help="每輪抓幾檔（30 是 mopsfin 報表端點上限）")
    ap.add_argument("--patience", type=int, default=2, help="連續幾輪 remaining 沒下降才算補完")
    ap.add_argument("--throttle", type=float, default=0.2, help="本機請求間隔秒（本機不會被限流，可小）")
    ap.add_argument("--max-rounds", type=int, default=200, help="安全上限，避免異常時無限跑")
    args = ap.parse_args()

    user = args.user or input("Basic Auth 帳號: ").strip()
    password = args.password or getpass.getpass("Basic Auth 密碼: ")
    base = args.base_url.rstrip("/")
    auth = (user, password)
    updater._REPORT_THROTTLE = max(0.0, args.throttle)  # 本機抓，節流可放小加速

    client = httpx.Client(timeout=60, auth=auth, headers={"User-Agent": "spr-sync/1.0"})

    def call(method: str, path: str, **kw):
        """打端點並把常見失敗講清楚：401=帳密錯、非 2xx=印狀態＋內容片段，才不會只看到
        一句無意義的 JSON 解析錯誤。回傳解析好的 JSON dict，或 None（呼叫端判斷後 return）。"""
        try:
            r = client.request(method, f"{base}{path}", **kw)
        except Exception as exc:  # noqa: BLE001 — 連線層失敗（DNS/逾時/TLS）
            print(f"[!] 連線失敗（{path}）：{exc}")
            return None
        if r.status_code == 401:
            print("[!] HTTP 401：Basic Auth 帳密錯誤或未設定。確認 --user 與密碼對應雲端的 "
                  "SPR_BASIC_USER / SPR_BASIC_PASS。")
            return None
        if r.status_code // 100 != 2:
            print(f"[!] HTTP {r.status_code}（{path}）：{r.text[:200]}")
            return None
        try:
            return r.json()
        except Exception:  # noqa: BLE001 — 2xx 但不是 JSON（理論上不該發生）
            print(f"[!] 回應非 JSON（{path}）：{r.text[:200]}")
            return None

    best = float("inf")
    stale = 0
    total_imported = 0
    for rnd in range(1, args.max_rounds + 1):
        pend = call("GET", "/api/financials/report-pending", params={"limit": args.batch})
        if pend is None:
            return 2
        codes = pend.get("codes") or []
        remaining, universe = pend.get("remaining", 0), pend.get("universe", 0)
        if not codes:
            print(f"完成：沒有待補代號（remaining={remaining}/{universe}）。")
            break
        ay, aseason = pend["anchor_year"], pend["anchor_season"]
        t0 = time.time()
        by_indicator = updater.compute_report_indicators(codes, ay, aseason)  # 本機重活
        res = call("POST", "/api/financials/import", json={"data": by_indicator})
        if res is None:
            return 2
        imported = res.get("imported", 0)
        total_imported += imported
        dt = time.time() - t0
        print(f"round {rnd}: 抓 {len(codes)} 檔、匯入 {imported} 列、"
              f"remaining {remaining}/{universe}（{dt:.0f}s，累計匯入 {total_imported}）")

        if remaining < best:            # 有進展 → 重置耐心
            best, stale = remaining, 0
        else:
            stale += 1
            if stale >= max(1, args.patience):
                print(f"補完（remaining 連續 {args.patience} 輪未下降，剩 {remaining} 檔多為缺科目的代號）。")
                break
    client.close()
    print(f"結束。本次共匯入 {total_imported} 列。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

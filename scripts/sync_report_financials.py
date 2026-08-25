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
from stocks_power_rich.sources import financials  # noqa: E402

DEFAULT_BASE = "https://stock-power-rich.zeabur.app"


def main() -> int:
    ap = argparse.ArgumentParser(description="本機抓季報完整報表並匯入 production")
    ap.add_argument("--base-url", default=DEFAULT_BASE, help="雲端網址（預設正式站）")
    ap.add_argument("--user", default=os.getenv("SPR_BASIC_USER", ""), help="Basic Auth 帳號")
    ap.add_argument("--password", default=os.getenv("SPR_BASIC_PASS", ""), help="Basic Auth 密碼（不帶則提示輸入）")
    ap.add_argument("--batch", type=int, default=15, help="每輪抓幾檔（越大越省請求數，但單次回應越大越可能逾時；15 較穩）")
    ap.add_argument("--throttle", type=float, default=0.2, help="本機請求間隔秒（本機不會被限流，可小）")
    ap.add_argument("--timeout", type=float, default=45, help="每個報表請求逾時秒（本機批次回應較大，給寬一點）")
    ap.add_argument("--max-rounds", type=int, default=400, help="安全上限，避免異常時無限跑")
    args = ap.parse_args()

    user = args.user or input("Basic Auth 帳號: ").strip()
    password = args.password or getpass.getpass("Basic Auth 密碼: ")
    base = args.base_url.rstrip("/")
    auth = (user, password)
    updater._REPORT_THROTTLE = max(0.0, args.throttle)  # 本機抓，節流可放小加速
    financials.REPORT_TIMEOUT = max(10.0, args.timeout)  # 本機批次回應較大，逾時放寬（雲端才要短）
    print(f"設定：base={base} batch={args.batch} timeout={financials.REPORT_TIMEOUT:.0f}s，開始…")

    client = httpx.Client(timeout=60, auth=auth, headers={"User-Agent": "spr-sync/1.0"})

    def call(method: str, path: str, retries: int = 5, **kw):
        """打端點，遇 5xx/連線錯誤**自動重試**（Zeabur 重新部署／代理抖動是常態，8 小時
        的長工作不能一撞就死、把本輪抓好的資料白白丟掉）。401 是帳密問題、重試也沒用 →
        直接放棄；其他 4xx 同樣不重試。回傳 JSON dict，或 None（連續失敗後呼叫端 return）。"""
        for attempt in range(1, retries + 1):
            try:
                r = client.request(method, f"{base}{path}", **kw)
            except Exception as exc:  # noqa: BLE001 — 連線層失敗（DNS/逾時/TLS）
                wait = min(60, 5 * attempt)
                print(f"    [重試 {attempt}/{retries}] 連線失敗（{path}）：{exc}；{wait}s 後重試…", flush=True)
                time.sleep(wait)
                continue
            if r.status_code == 401:
                print("[!] HTTP 401：Basic Auth 帳密錯誤或未設定。確認 --user 與密碼對應雲端的 "
                      "SPR_BASIC_USER / SPR_BASIC_PASS。")
                return None
            if r.status_code // 100 == 5:   # 502/503/504…雲端暫時性 → 退避重試
                wait = min(60, 5 * attempt)
                print(f"    [重試 {attempt}/{retries}] HTTP {r.status_code}（{path}，多為 Zeabur "
                      f"重啟/抖動）；{wait}s 後重試…", flush=True)
                time.sleep(wait)
                continue
            if r.status_code // 100 != 2:   # 其他 4xx＝用戶端錯誤，重試無益
                print(f"[!] HTTP {r.status_code}（{path}）：{r.text[:200]}")
                return None
            try:
                return r.json()
            except Exception:  # noqa: BLE001 — 2xx 但不是 JSON（理論上不該發生）
                print(f"[!] 回應非 JSON（{path}）：{r.text[:200]}")
                return None
        print(f"[!] {path} 連續 {retries} 次失敗，放棄本次（重跑會從剩下的續補）。")
        return None

    # 用 after 游標一路往後掃過整份 pending 清單，每個代號一輪只碰一次——不能用「remaining
    # 沒下降就停」，因為一撞到一群缺 capex 的金融股（永遠湊不齊 4 指標）remaining 就不動、
    # 會誤判補完，而那群後面還能補的代號永遠掃不到（見 report-pending 端點的 after 說明）。
    after = ""
    total_imported = 0
    for rnd in range(1, args.max_rounds + 1):
        pend = call("GET", "/api/financials/report-pending",
                    params={"limit": args.batch, "after": after})
        if pend is None:
            return 2
        codes = pend.get("codes") or []
        remaining, universe = pend.get("remaining", 0), pend.get("universe", 0)
        if not codes:
            print(f"補完：整份清單已掃完一輪（remaining={remaining}/{universe}，剩下多為缺 capex "
                  f"的金融股／尚未公布的代號）。累計匯入 {total_imported} 列。")
            break
        ay, aseason = pend["anchor_year"], pend["anchor_season"]
        print(f"round {rnd}: 抓 {len(codes)} 檔（{codes[0]}–{codes[-1]}，remaining {remaining}/{universe}）…",
              flush=True)
        t0 = time.time()

        def _tick(report, q, hit, secs):   # 即時進度：每抓一季印一格，才不會整批看起來凍住
            print(f"    {report[:6]:6s} {q} {'✓' if hit else '·'} {secs:.0f}s", flush=True)

        by_indicator = updater.compute_report_indicators(codes, ay, aseason, on_fetch=_tick)  # 本機重活
        res = call("POST", "/api/financials/import", json={"data": by_indicator})
        if res is None:
            return 2
        imported = res.get("imported", 0)
        total_imported += imported
        dt = time.time() - t0
        print(f"  → 匯入 {imported} 列（本輪 {dt:.0f}s，累計匯入 {total_imported}）", flush=True)
        after = codes[-1]   # 游標往後推，下一輪抓 code > after 的，不重掃前面卡住的
    else:
        print(f"[!] 達 max_rounds={args.max_rounds} 上限仍未掃完，先停（重跑會從頭再掃一遍、續補）。")
    client.close()
    print(f"結束。本次共匯入 {total_imported} 列。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

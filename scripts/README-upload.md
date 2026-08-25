# 每日選股 CSV 上傳（雙擊即用）

目標：你在 XQ全球贏家 手動匯出選股 CSV，存到固定資料夾 → 雙擊一個檔案上傳到雲端
`/api/csv/upload` → 雲端立即匯入，並在選股表自動算出蘭質/蘭值（沿用 XQ/蘭弦匯出值）與
木質/木率（見 `CLAUDE.md`「木質／木率」段）。

雲端這端**不需要任何改動**——`/api/csv/upload` 早就存在，收到檔就會做完整套匯入。缺的只是
「檔案怎麼從你的電腦送過去」這一段，這支腳本就是補這一段。

> **這是 Stage 1**：仍然依賴你每天在 XQ 手動匯出。蘭質/蘭值是蘭弦的付費專有指標，無法
> 由本站重算，因此這條路徑保留了「你每天要在 XQ 跑選股並匯出」這個前提；本站無法上網
> 取得蘭弦資料，也不會嘗試代為登入或抓取 XQ。

## 每天要做的事

1. 在 XQ 匯出今天的選股結果，存到（已幫你建好這個資料夾）：
   ```
   C:\Users\kevin\XQExport
   ```
   檔名不必每天不同——上傳器只認「這個資料夾裡**修改時間最新**的檔」，同檔名被覆寫也抓
   得到最新內容。避免存到 `Documents`/`Desktop`（常被重新導向到 OneDrive 同步，寫檔中途
   可能被鎖住或延遲）或本 repo 資料夾內（會被 git 注意到）。
2. 雙擊 [`scripts\upload_today.bat`](upload_today.bat)。
3. 視窗印出 `上傳成功：<檔名> → snap_date=... count=...` 就完成了，按任意鍵關閉視窗。

**第一次雙擊**會跳出 Windows 的帳密輸入視窗，要你輸入雲端的 Basic Auth 帳密（對應伺服器
的 `SPR_BASIC_USER`/`SPR_BASIC_PASS`）。輸入一次之後，帳密會用 **DPAPI**（Windows 內建、
綁定你這個登入帳號的加密機制）加密存在
`%LOCALAPPDATA%\StocksPowerRich\spr_cred.xml`——**不是明文、不會進 git、離開這台電腦或
換一個 Windows 帳號就解不開**。之後雙擊不會再問，除非你手動清掉這個檔或帳密輸入錯誤。

若帳密打錯想重輸，開 PowerShell 執行：
```powershell
powershell -NoProfile -File "C:\Users\kevin\Desktop\AI\Claude\股力智富\scripts\upload_xq_click.ps1" -ResetCredential
```
之後再雙擊 `upload_today.bat` 就會重新詢問。

## 怎麼確認上傳真的成功

不需要另外查 log——打開雲端頁面，看**頂欄的資料新鮮度徽章**：交易日白天顯示今天日期
（非琥珀色）就代表今天的上傳成功了。也可以看選股表（總覽/選股頁）的**木率/木質欄**是否
與蘭值/蘭質一起隨每日新資料更新。

## 疑難排解

| 症狀 | 可能原因 |
|---|---|
| `找不到檔` / exit 2 | `C:\Users\kevin\XQExport` 裡沒有 `.csv/.xlsx/.xlsm`，或你存到別的資料夾了 |
| `上傳失敗 HTTP 401` | Basic Auth 帳密錯——用上面的 `-ResetCredential` 重輸 |
| `僅接受 .csv/.xlsx/.xlsm 檔案` | XQ 匯出格式不是這三種之一 |
| `檔案過大（上限 10MB）` | 匯出欄位/列數異常暴增，先檢查 XQ 那邊的選股條件 |
| 一直印「已上傳過同一檔，略過」 | 資料夾裡還是昨天的舊檔——回頭確認今天有沒有在 XQ 真的匯出 |

## 進階：想要連「雙擊」都自動化

目前設計是「你控制匯出的時機，雙擊觸發上傳」，這是刻意的權衡——換成排程觸發表示上傳器
要在你還沒匯出新檔時也定時檢查一次，多一層「排程時間 vs. 你實際匯出時間」要對齊的心智
負擔。如果之後想改成 Windows 工作排程器在固定時間自動跑（不必雙擊），可以參考
[`upload_xq.ps1`](upload_xq.ps1)（`upload_today.bat` 背後呼叫的同一支底層腳本，接受
`-BaseUrl`/`-User`/`-Pass`/`-Folder` 參數，`upload_xq_click.ps1` 只是幫你把帳密改成
DPAPI 快取、`-BaseUrl` 也固定指向這個部署）和 [`upload_xq.example.ps1`](upload_xq.example.ps1)
（帳密走環境變數而非 DPAPI 快取，更適合排程器情境）——把工作排程器的觸發條件設成每天
固定時間即可，細節與 XQ 端「每日自動執行選股」（[官方教學](https://www.xq.com.tw/learning/%E9%81%B8%E8%82%A1%E4%B8%AD%E5%BF%83%EF%BC%9A%E5%A6%82%E4%BD%95%E5%9F%B7%E8%A1%8C%E8%87%AA%E5%8B%95%E9%81%B8%E8%82%A1/)，讓 XQ
自己每天重跑選股，你仍需手動匯出成檔）搭配的做法，等真的想要再回頭研究即可。

## 季報完整報表：本機抓 → 匯入雲端（偶爾跑一次，非每天）

木質的「自算財報分」（`lan_score`）需要季報裡的**稅前淨利、營業費用、所得稅、資本支出**，
這 4 個要打 mopsfin 的完整報表端點（`/compare/report`）。**這個端點從 Zeabur 出站打不動**
（每個請求逾時、一筆都提交不了；同一主機的 ratios 端點卻通），但**本機**打 ~6 秒沒問題。
所以這 4 個指標走「本機抓好、POST 上雲」，其餘 8 個 ratios 指標仍直接在雲端回補。

在 repo 根目錄、用專案 venv 執行：

```
.venv\Scripts\python scripts\sync_report_financials.py --user admin
```

- `--base-url` 預設正式站；密碼不帶會跳出提示輸入（或設環境變數 `SPR_BASIC_PASS`）
- 它會自動迴圈：向雲端問「還缺哪些代號」→ 本機抓報表、反推單季 → POST 上雲，直到補完
- **季報一季才更新一次**，所以這支是「偶爾手動跑一次、可能跑一兩小時」的工作，不是每天的事
- 可隨時 Ctrl+C 中斷，重跑會從剩下的續補（雲端記得已補到哪）

跑之前，雲端要先有 ratios（8 個 JSON 指標）：那個直接在雲端跑就好，
`GET /api/financials/backfill?max_batches=6&batch_size=50`（重複呼叫到 `remaining` 不再下降）。

## 下一階段（Stage 2，尚未開始）

長期要讓本站不再依賴 XQ/蘭弦逐日匯出，改用公開資料源（月營收、季報財務、集保、K 線）
自行組出選股輸入，木質/木率取代蘭質/蘭值成為唯一品質分。這是後續多階段工程，目前仍
建議用本文件的雙擊上傳。

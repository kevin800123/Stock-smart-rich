<#
.SYNOPSIS
  雙擊即用的季報更新：本機抓完整季報 → 匯入雲端股力智富（繞過 Zeabur 打不動 mopsfin 報表端點）。

.DESCRIPTION
  一季一次、財報公布後跑一次。第一次執行會跳出 Windows 帳密輸入視窗，之後用 DPAPI
  （Windows 內建、綁定你這個登入帳號的加密機制）把帳密加密存在本機使用者設定檔資料夾，
  不是明文、不進 git，離開這台電腦／這個 Windows 帳號就解不開——**與上傳工具共用同一份**，
  所以若你已經雙擊過 upload_today.bat，這支不會再問。

  實際抓取/匯入邏輯與 sync_report_financials.py 相同（呼叫同一支）。可能跑一兩小時，
  中途關掉沒關係，重跑會從剩下的續補。

.PARAMETER CredFile
  加密帳密存放位置，預設 %LOCALAPPDATA%\StocksPowerRich\spr_cred.xml（與上傳工具共用）。

.PARAMETER ResetCredential
  刪除已儲存的帳密，強制下次執行重新詢問（帳密輸入錯誤時用這個重設）。

.PARAMETER BaseUrl
  雲端網址。預設固定指向目前這個部署；正常雙擊不需帶這個參數。
#>
[CmdletBinding()]
param(
  [string]$CredFile = "$env:LOCALAPPDATA\StocksPowerRich\spr_cred.xml",
  [switch]$ResetCredential,
  [string]$BaseUrl = 'https://stock-power-rich.zeabur.app'
)

function Fail([string]$Message) {
  [Console]::Error.WriteLine($Message)
  exit 1
}

if ($ResetCredential -and (Test-Path -LiteralPath $CredFile)) {
  Remove-Item -LiteralPath $CredFile -Force
  Write-Host "已清除已儲存的帳密。"
}

$credDir = Split-Path -Parent $CredFile
if (-not (Test-Path -LiteralPath $credDir)) {
  New-Item -ItemType Directory -Path $credDir -Force | Out-Null
}

if (-not (Test-Path -LiteralPath $CredFile)) {
  Write-Host "第一次執行，請輸入股力智富雲端帳密（與上傳工具共用同一份，之後會安全記住，只有這台電腦這個 Windows 帳號解得開，不會存成明文）："
  $cred = Get-Credential -Message '股力智富雲端登入'
  if (-not $cred) { Fail '未輸入帳密，已取消。' }
  try {
    $cred | Export-Clixml -LiteralPath $CredFile
  } catch {
    Fail "帳密儲存失敗：$($_.Exception.Message)"
  }
}

try {
  $cred = Import-Clixml -LiteralPath $CredFile
} catch {
  Fail "已儲存的帳密讀取失敗（$($_.Exception.Message)）。用 -ResetCredential 重新輸入，或手動刪除：$CredFile"
}

$user = $cred.UserName
$pass = $cred.GetNetworkCredential().Password

$py = Join-Path $PSScriptRoot '..\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $py)) {
  Fail "找不到專案 venv：$py（請先在 repo 根目錄建立 .venv 並安裝相依：python -m venv .venv 後 pip install -r requirements.txt）。"
}
$script = Join-Path $PSScriptRoot 'sync_report_financials.py'

Write-Host "開始季報更新（本機抓 → 匯入 $BaseUrl）。這會跑一段時間（全市場約一兩小時），中途可關、重跑會續補…"
Write-Host ""

# 用環境變數把密碼傳給 python，不放進命令列參數（避免出現在行程清單）。
$env:SPR_BASIC_PASS = $pass
try {
  & $py $script --base-url $BaseUrl --user $user
  $code = $LASTEXITCODE
} finally {
  Remove-Item Env:\SPR_BASIC_PASS -ErrorAction SilentlyContinue
}

if ($code -ne 0) {
  Write-Host ""
  Write-Host "如果是帳密錯誤導致失敗，執行：powershell -File `"$PSCommandPath`" -ResetCredential 清掉重輸。"
}
exit $code

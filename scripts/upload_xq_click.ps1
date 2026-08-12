<#
.SYNOPSIS
  雙擊即用的上傳器：把 C:\Users\<你>\XQExport 裡最新的選股檔傳給雲端股力智富。

.DESCRIPTION
  給不想設 Windows 工作排程器、只想「匯出完 CSV 就雙擊上傳」的用法。
  第一次執行會跳出 Windows 帳密輸入視窗，之後用 DPAPI（Windows 內建、綁定你這個
  登入帳號的加密機制）把帳密加密存在本機使用者設定檔資料夾，不是明文、不進 git、
  也離開這台電腦／這個 Windows 帳號就解不開。之後執行不會再問。

  實際上傳邏輯與 upload_xq.ps1 相同（呼叫同一支）。

.PARAMETER CredFile
  加密帳密存放位置，預設 %LOCALAPPDATA%\StocksPowerRich\spr_cred.xml（測試/除錯可覆寫）。

.PARAMETER ResetCredential
  刪除已儲存的帳密，強制下次執行重新詢問（帳密輸入錯誤時用這個重設）。

.PARAMETER BaseUrl
  雲端網址。預設固定指向目前這個部署；正常雙擊使用不需要帶這個參數，只有換部署
  網址或測試時才需要覆寫。
#>
[CmdletBinding()]
param(
  [string]$CredFile = "$env:LOCALAPPDATA\StocksPowerRich\spr_cred.xml",
  [switch]$ResetCredential,
  [string]$BaseUrl = 'https://stock-power-rich.zeabur.app'
)

$Folder = "$env:USERPROFILE\XQExport"

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
  Write-Host "第一次執行，請輸入股力智富雲端帳密（之後會安全記住，只有這台電腦這個 Windows 帳號解得開，不會存成明文）："
  $cred = Get-Credential -Message '股力智富雲端登入'
  if (-not $cred) { Fail '未輸入帳密，已取消上傳。' }
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

& "$PSScriptRoot\upload_xq.ps1" -BaseUrl $BaseUrl -User $user -Pass $pass -Folder $Folder
$code = $LASTEXITCODE
if ($code -eq 1) {
  Write-Host ""
  Write-Host "如果是帳密錯誤導致失敗，執行：powershell -File `"$PSCommandPath`" -ResetCredential 清掉重輸。"
}
exit $code

<#
.SYNOPSIS
  把 XQ全球贏家 匯出的最新選股檔（.csv/.xlsx/.xlsm）自動上傳到雲端股力智富。

.DESCRIPTION
  取 -Folder 內 mtime 最新的檔，POST 到 {BaseUrl}/api/csv/upload（欄位名 file，multipart/form-data），
  帶 HTTP Basic Auth（對應伺服器的 SPR_BASIC_USER / SPR_BASIC_PASS）。伺服器收檔後立即匯入並產生
  蘭質/蘭值/木質/木率。用 state 檔記住上次上傳的檔，重跑同一檔會略過（冪等）。

  相容 Windows PowerShell 5.1（用 .NET HttpClient 手動組 multipart，不依賴 PS7 的 -Form）。

.PARAMETER BaseUrl
  雲端網址，例如 https://your-app.zeabur.app（結尾斜線可有可無）。

.PARAMETER User
  雲端 Basic Auth 帳號（SPR_BASIC_USER）。本機無認證時可留空。

.PARAMETER Pass
  雲端 Basic Auth 密碼（SPR_BASIC_PASS）。建議用環境變數：預設讀 $env:SPR_BASIC_PASS，
  避免把密碼寫進排程指令或檔案。

.PARAMETER Folder
  XQ 匯出 CSV 的資料夾。

.PARAMETER StateFile
  記錄上次上傳簽章的檔（預設在本腳本旁 .upload_xq_state.json）。

.PARAMETER Force
  忽略 state 檔，強制重新上傳最新檔。

.EXAMPLE
  $env:SPR_BASIC_PASS = 'your-pass'
  .\upload_xq.ps1 -BaseUrl https://your-app.zeabur.app -User admin -Folder 'D:\XQ\Export'

.NOTES
  離開碼：0 成功或略過；1 上傳/伺服器錯誤；2 找不到檔或參數問題。排程器可據此判定失敗。
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)][string]$BaseUrl,
  [string]$User = '',
  [string]$Pass = $env:SPR_BASIC_PASS,
  [Parameter(Mandatory = $true)][string]$Folder,
  [string]$StateFile = "$PSScriptRoot\.upload_xq_state.json",
  [switch]$Force
)

$ErrorActionPreference = 'Stop'
$exts = @('.csv', '.xlsx', '.xlsm')

# Write-Error 在 $ErrorActionPreference='Stop' 下會變成終止例外，導致緊接著的
# `exit N` 永遠不會執行（離開碼會變成 PowerShell 自己判定的值，不是我們要的 N），
# 且會印出一整段 stack trace 而非單行訊息。改寫到 stderr＋明確 exit，不經過
# Write-Error／$ErrorActionPreference。
function Fail([string]$Message, [int]$Code) {
  [Console]::Error.WriteLine($Message)
  exit $Code
}

# --- 找最新檔 ---
if (-not (Test-Path -LiteralPath $Folder)) {
  Fail "資料夾不存在：$Folder" 2
}
$file = Get-ChildItem -LiteralPath $Folder -File |
  Where-Object { $exts -contains $_.Extension.ToLower() } |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1
if (-not $file) {
  Fail "資料夾找不到 CSV/Excel（.csv/.xlsx/.xlsm）：$Folder" 2
}

# --- 大小上限（與伺服器 MAX_UPLOAD_BYTES 一致：10MB）---
$maxBytes = 10 * 1024 * 1024
if ($file.Length -gt $maxBytes) {
  Fail "檔案過大（$([math]::Round($file.Length/1MB,2)) MB，上限 10MB）：$($file.Name)" 2
}

# --- 冪等：同一檔（名稱+修改時間+大小）已上傳過就略過 ---
$sig = "{0}|{1}|{2}" -f $file.Name, $file.LastWriteTimeUtc.Ticks, $file.Length
if (-not $Force -and (Test-Path -LiteralPath $StateFile)) {
  try { $prev = (Get-Content -LiteralPath $StateFile -Raw | ConvertFrom-Json).sig } catch { $prev = $null }
  if ($prev -eq $sig) {
    Write-Host "已上傳過同一檔，略過：$($file.Name)（用 -Force 可強制重送）"
    exit 0
  }
}

# --- 組 multipart 並上傳（PS 5.1 相容）---
try { Add-Type -AssemblyName System.Net.Http -ErrorAction Stop } catch { }

$client = $null; $content = $null; $fileContent = $null
try {
  $client = New-Object System.Net.Http.HttpClient
  $client.Timeout = [TimeSpan]::FromSeconds(180)

  if ($User -and $Pass) {
    $b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("$User`:$Pass"))
    $client.DefaultRequestHeaders.Authorization =
      New-Object System.Net.Http.Headers.AuthenticationHeaderValue('Basic', $b64)
  }
  elseif ($User -or $Pass) {
    Write-Warning '只提供了帳號或密碼其中之一，本次將不帶 Basic Auth。'
  }

  $bytes = [IO.File]::ReadAllBytes($file.FullName)
  $fileContent = New-Object System.Net.Http.ByteArrayContent (, $bytes)
  $fileContent.Headers.ContentType =
    [System.Net.Http.Headers.MediaTypeHeaderValue]::Parse('application/octet-stream')
  $content = New-Object System.Net.Http.MultipartFormDataContent
  $content.Add($fileContent, 'file', $file.Name)

  $url = ($BaseUrl.TrimEnd('/')) + '/api/csv/upload'
  Write-Host "上傳中：$($file.Name) → $url"
  $resp = $client.PostAsync($url, $content).GetAwaiter().GetResult()
  $body = $resp.Content.ReadAsStringAsync().GetAwaiter().GetResult()
}
catch {
  Fail "上傳失敗（連線/例外）：$($_.Exception.Message)" 1
}
finally {
  if ($content) { $content.Dispose() }
  if ($client) { $client.Dispose() }
}

if (-not $resp.IsSuccessStatusCode) {
  Fail "上傳失敗 HTTP $([int]$resp.StatusCode)：$body" 1
}

# 伺服器對副檔名不符/過大等會回 200 但帶 error 欄位
$json = $null
try { $json = $body | ConvertFrom-Json } catch { }
if ($json -and $json.error) {
  Fail "伺服器回報：$($json.error)" 1
}

$snap = if ($json) { $json.snap_date } else { $null }
$count = if ($json) { $json.count } else { $null }
Write-Host "上傳成功：$($file.Name) → snap_date=$snap count=$count"

# --- 寫 state ---
try {
  @{ sig = $sig; file = $file.Name; snap_date = $snap; count = $count
     uploaded_at = (Get-Date).ToString('s') } |
    ConvertTo-Json | Set-Content -LiteralPath $StateFile -Encoding UTF8
} catch {
  Write-Warning "state 檔寫入失敗（不影響本次上傳）：$($_.Exception.Message)"
}
exit 0

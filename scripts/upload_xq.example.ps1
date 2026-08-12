<#
  upload_xq.ps1 呼叫範例。複製這份成你自己的 run 檔（例如 run_upload.ps1），
  填好參數即可；也可以直接在 Windows 工作排程器的「動作」裡呼叫 upload_xq.ps1 帶同樣參數。

  密碼不要寫死在這個檔裡——用環境變數 SPR_BASIC_PASS（見下）。
  這個檔本身可以放心進 git（不含真實密碼）；只有真的填了密碼的複本才不要進 git。
#>

# 雲端網址（Zeabur 網域，結尾斜線可有可無）
$BaseUrl = 'https://your-app.zeabur.app'

# 雲端 Basic Auth 帳號（對應伺服器的 SPR_BASIC_USER；本機沒設認證就留空字串）
$User = 'admin'

# 密碼從環境變數讀，執行前先設一次：
#   $env:SPR_BASIC_PASS = 'your-pass'
# 或在工作排程器的「動作」加一道先設環境變數的指令。
$Pass = $env:SPR_BASIC_PASS

# XQ全球贏家 每天匯出 CSV 的資料夾——在 XQ 的「另存新檔」與這裡都用同一個路徑。
# 建議放使用者目錄底下、不要放在 Documents/Desktop（常被 OneDrive 重新導向同步，
# 檔案寫入中途可能被鎖住或延遲）、也不要放進本 repo 資料夾（會被 git 注意到）。
$Folder = "$env:USERPROFILE\XQExport"

& "$PSScriptRoot\upload_xq.ps1" -BaseUrl $BaseUrl -User $User -Pass $Pass -Folder $Folder
exit $LASTEXITCODE

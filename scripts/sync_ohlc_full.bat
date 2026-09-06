@echo off
REM 密集重抓最近 250 個交易日（約一年）。用於資料稀疏、K線出現假斷崖時。
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0sync_ohlc_click.ps1" -ForceDays 250
echo.
pause

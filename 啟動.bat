@echo off
chcp 65001 >nul
cd /d %~dp0
if not exist ".venv\Scripts\python.exe" (
  echo [STOCKS POWER RICH] 第一次執行，建立虛擬環境並安裝套件...
  python -m venv .venv
  .venv\Scripts\python -m pip install --upgrade pip
  .venv\Scripts\python -m pip install -r requirements.txt
)
start "" http://127.0.0.1:8000
.venv\Scripts\python -m uvicorn stocks_power_rich.main:app --host 127.0.0.1 --port 8000

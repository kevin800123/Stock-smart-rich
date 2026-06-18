"""應用設定載入：從 .env / 環境變數讀取金鑰、排程時間、DB 路徑與國際指數代碼表。"""
import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

INTL_TICKERS = {
    "sox": "^SOX",      # 費城半導體
    "n225": "^N225",    # 日經
    "kospi": "^KS11",   # 韓股 KOSPI
    "gold": "GC=F",     # 黃金期貨
    "btc": "BTC-USD",   # 比特幣
}


@dataclass
class Config:
    gemini_api_key: str = ""
    schedule_time: str = "15:30"
    db_path: str = "data/spr.sqlite"
    data_dir: str = "Date"
    intl_tickers: dict = field(default_factory=lambda: dict(INTL_TICKERS))


def load_config() -> Config:
    return Config(
        gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
        schedule_time=os.getenv("SPR_SCHEDULE_TIME", "15:30"),
        db_path=os.getenv("SPR_DB_PATH", "data/spr.sqlite"),
        data_dir=os.getenv("SPR_DATA_DIR", "Date"),
    )

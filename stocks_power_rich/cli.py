"""命令列入口：供 Windows 工作排程器 / APScheduler 呼叫，跑一次一鍵更新。

用法：python -m stocks_power_rich.cli
"""
import sys

from . import updater
from .config import load_config
from .db import get_connection, init_db


def main():
    cfg = load_config()
    conn = get_connection(cfg.db_path)
    init_db(conn)
    # 先讀資料夾最新檔（若有）
    from . import csv_import

    path = csv_import.find_latest_file(cfg.data_dir)
    if path:
        try:
            snap_date, count = csv_import.import_csv(conn, path)
            print(f"imported {path}: {snap_date} x{count}")
        except Exception as e:  # noqa: BLE001
            print(f"import skipped: {e}")
    result = updater.run_update(conn, cfg.intl_tickers)
    print(result)
    return result


if __name__ == "__main__":
    res = main()
    sys.exit(0 if res and not res.get("failed") else 0)

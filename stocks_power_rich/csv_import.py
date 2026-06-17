"""解析使用者每日上傳的 Big5 編碼籌碼 CSV → 標準化欄位 → 寫入 chip_snapshot。

CSV 結構：第 1 列標題、第 2 列含「資料日期」、第 3 列策略名、第 4 列欄名、之後為資料。
"""
import csv
import io
import json
import os
import re
import shutil
from datetime import datetime

COLMAP = {
    "代碼": "code", "商品": "name", "成交": "close", "漲幅%": "change_pct",
    "總量": "volume", "市值(億)": "market_cap", "股本(億)": "capital",
    "推估獲利": "est_profit", "LPE": "lpe", "W55": "w55", "集保": "custody",
    "大戶增比": "big_holder_ratio", "人數降比": "holder_drop_ratio",
    "月增": "month_inc", "年增": "rev_yoy", "累增": "accum_inc",
    "投三": "trust_3d", "外三": "foreign_3d", "產業": "industry",
    "細產業": "sub_industry", "所有細產業": "all_sub_industry",
    "產業地位": "industry_position",
}
NUMERIC = {
    "close", "change_pct", "volume", "market_cap", "capital", "est_profit",
    "lpe", "w55", "custody", "big_holder_ratio", "holder_drop_ratio",
    "month_inc", "rev_yoy", "accum_inc", "trust_3d", "foreign_3d",
}


def _to_float(v):
    try:
        return float(str(v).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _normalize_field_count(row: list, n: int) -> list:
    """調整欄數至 n：欄數過多時，把溢出欄位併回最後一欄（未加引號的「產業地位」
    可能含 ASCII 逗號而被拆開）；欄數不足時補空字串。"""
    if len(row) > n:
        return row[: n - 1] + [",".join(row[n - 1:])]
    if len(row) < n:
        return row + [""] * (n - len(row))
    return row


def parse_csv(path: str):
    raw = open(path, "rb").read().decode("big5", errors="replace")
    lines = raw.splitlines()
    m = re.search(r"(\d+)\s*年\s*(\d+)\s*月\s*(\d+)\s*日", lines[1]) if len(lines) > 1 else None
    if m:
        snap_date = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    else:
        snap_date = datetime.now().strftime("%Y-%m-%d")

    table = list(csv.reader(io.StringIO("\n".join(lines[3:]))))
    if not table:
        return snap_date, []
    header = [str(c).strip().strip('"') for c in table[0]]
    n = len(header)

    rows = []
    for raw_row in table[1:]:
        if not raw_row:
            continue
        rec = dict(zip(header, _normalize_field_count(raw_row, n)))
        d = {}
        for src, dst in COLMAP.items():
            if src in rec:
                val = rec[src]
                d[dst] = _to_float(val) if dst in NUMERIC else (val.strip() if val else None)
        if not d.get("code"):
            continue
        d["raw_json"] = json.dumps(rec, ensure_ascii=False)
        rows.append(d)
    return snap_date, rows


def import_csv(conn, path: str, store_dir: str = "data/csv"):
    from .db import insert_chip_snapshot

    snap_date, rows = parse_csv(path)
    insert_chip_snapshot(conn, snap_date, rows)
    os.makedirs(store_dir, exist_ok=True)
    stored = os.path.join(store_dir, f"{snap_date}.csv")
    shutil.copyfile(path, stored)
    conn.execute(
        "INSERT INTO csv_files (snap_date, stored_path, imported_at) VALUES (?,?,?)",
        (snap_date, stored, datetime.now().isoformat()),
    )
    conn.commit()
    return snap_date, len(rows)

"""把選股清單匯出成 Excel（.xlsx）位元組，供下載。"""
import io

import openpyxl

EXPORT_COLS = [
    ("code", "代碼"), ("name", "名稱"), ("lan_value", "蘭值"), ("lan_score", "蘭質"),
    ("lpe", "本益比"), ("est_profit", "推估EPS"), ("rev_yoy", "營收年增%"),
    ("accum_inc", "營收累增"), ("holder_drop_ratio", "人數降比"),
    ("big_holder_ratio", "大戶增比"), ("sub_industry", "細產業"),
]


def picks_to_xlsx(picks: list, snap_date: str) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "選股清單"
    ws.append([f"資料日期 {snap_date}　篩選：W55翻多＋大戶增＞0＋營收年增＞0＋推估EPS＞0，依蘭值排序"])
    ws.append([label for _, label in EXPORT_COLS])
    for p in picks:
        ws.append([p.get(k) for k, _ in EXPORT_COLS])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()

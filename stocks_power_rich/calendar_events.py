"""下週行事曆的組裝層：純函式，不碰網路也不碰 DB。

**這個模組的核心紀律是「不憑推算給日期」。** 行事曆一旦寫錯日期，讀者沒有任何
辦法發現——它看起來跟正確的日期一模一樣。所以：

- CPI／非農／FOMC 一律**抓官方排程**（`sources/econ_calendar.py`），不用「每月
  第一個週五」這種規則。實測 2026 年那條規則 12 個月會錯 4 個月。
- 台指期結算日（每月第三個星期三）**刻意不納入**。那條規則本身是期交所明訂的，
  但遇到國定假日會位移，性質與上面那條被否決的規則相同，而且使用者並沒有要求。
- MSCI／富時台灣指數的調整日**沒有可靠的免費端點**（MSCI 頁面是 JS 渲染、
  FTSE 的行事曆頁 404），只能手填——使用者決定**不放**，所以這裡沒有那張表。
  要加回來的話，唯一可接受的做法仍是「手填＋標明涵蓋到哪一天」，不是憑推算。
"""
from __future__ import annotations

import re
from datetime import date, timedelta

# 「大型權值股」的門檻。美股用美元市值、台股用新台幣市值。
US_MEGACAP_MIN = 50e9          # 500 億美元
TW_LARGECAP_MIN = 3000e8       # 3,000 億新台幣
# 每個市場最多列幾檔——行事曆是「掃一眼」的東西，列滿 20 檔就沒人看了。
EARNINGS_LIMIT = 6

_ZH_WEEKDAY = "一二三四五六日"
# 同一天多筆時的顯示順序：總經在前、個股財報在後。
_KIND_ORDER = {"macro": 0, "conference": 1, "earnings": 2}


def next_week_range(today: date) -> tuple[str, str]:
    """下一個「週一～週日」的起訖（ISO）。

    以 `today` 之後的第一個週一為起點，所以**不論今天星期幾都會落在同一週**
    ——排程只在週日跑，但手動觸發可能落在任何一天。
    """
    monday = today + timedelta(days=7 - today.weekday())
    return monday.isoformat(), (monday + timedelta(days=6)).isoformat()


def zh_weekday(iso: str) -> str:
    return _ZH_WEEKDAY[date.fromisoformat(iso).weekday()]


def sort_events(events: list) -> list:
    return sorted(events, key=lambda e: (e.get("date") or "",
                                         _KIND_ORDER.get(e.get("kind"), 9)))


def pick_big_caps(rows: list, min_cap: float, limit: int) -> list:
    """市值過門檻的前 N 檔（由大到小）。門檻沒人過就回空，不遞補。"""
    big = [r for r in rows if (r.get("market_cap") or 0) >= min_cap]
    return sorted(big, key=lambda r: -(r.get("market_cap") or 0))[:limit]


def dedupe_by_day(rows: list, key: str) -> list:
    """同一天同一個 key 只留第一筆，順序不變。

    實跑抓到廣達在 2026-09-16 有兩場法說會（09:00 與 14:00，中英文場各一）。
    行事曆要回答的是「那天誰要開法說會」，列兩次只是重複，而且會吃掉一個顯示名額
    ——所以**去重要排在限額之前**。不同日期的同一檔是兩件事，要保留。
    """
    seen, out = set(), []
    for r in rows:
        sig = (r.get("date"), r.get(key))
        if sig in seen:
            continue
        seen.add(sig)
        out.append(r)
    return out


# Nasdaq 的 name 帶法律後綴（Apple Inc.／Exxon Mobil Corporation），在行事曆裡是
# 純噪音又會把一行擠長。長後綴要排在短的前面，否則 "Corp." 會先吃掉 "Corporation"
# 的前半、留下一個孤零零的 "oration"（同推播關鍵詞那條「複合詞排前面」的教訓）。
_LEGAL_SUFFIX = re.compile(
    r"[,\s]+(?:and\s+Company|Corporation|Incorporated|Company|Holdings|Corp\.?|"
    r"Inc\.?|Ltd\.?|LLC|L\.P\.|plc|N\.V\.|S\.A\.|Co\.)\s*$",
    re.IGNORECASE)
_LEADING_THE = re.compile(r"^The\s+", re.IGNORECASE)
COMPANY_NAME_MAX = 24


def clean_company_name(name: str, limit: int = COMPANY_NAME_MAX) -> str:
    """公司名去掉法律後綴與開頭的 The，太長就截斷。

    **清成空字串時退回原名**——只由後綴組成的怪名字寧可原樣顯示，也不要在
    行事曆上留一個「（AAPL）財報」這種沒有主詞的條目。
    """
    out = (name or "").strip()
    if not out:
        return ""
    prev = None
    while prev != out:                       # 連續後綴：Group Inc. → Group → …
        prev = out
        out = _LEGAL_SUFFIX.sub("", out).strip(" ,")
    out = _LEADING_THE.sub("", out).strip()
    if not out:
        return name.strip()
    return out if len(out) <= limit else out[:limit] + "…"

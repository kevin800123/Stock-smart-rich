"""自算選股「新進榜」Telegram 推播的訊息組裝（純函式，資料由 api/helpers 準備）。

兩則（使用者規格，2026-09）：
- 平日 21:40「今日新進榜」：只列今天才進的——✦（上一個集保週期也沒有）與 NEW（掉出後重新進榜）
  分兩段，各依木率排序；沒有就送「今日無新進榜」。
- 週六 18:00「本週新進榜」：本週新進（Week NEW），漲跌% 是本週（週五收盤 vs 上週最後一個交易日），
  外加「本週大戶買進前三子產業」——大戶增比一週才更新一次，放在週報才不會連續五天內容一樣。

版面決定（第二版，依第一則實際推播的截圖改）：
- **每列一行單行等寬（inline code）＋名稱在後面**，不用 ``` 區塊：區塊在 Telegram 手機上每塊底下都有一顆
  「複製程式碼」大按鈕，前後還留大片空白，一則被拉得很長。數字欄仍在等寬字型裡對齊。
- **表頭移到列表外面當說明行**：表頭含中文，而 Telegram 等寬字型裡中文字寬不是英數字的 2 倍，
  第一版照「中文算 2」補空白，實際表頭整排偏左；數字列全是 ASCII，所以只有表頭歪。
- **「集中：」一行**：列表裡同一子產業 ≥2 檔的，取前 3 個，一眼看出今天新進集中在哪。都只有 1 檔就不印。
- **不提網頁**（使用者決定）：超過上限寫「另 N 檔未列出」。
- 欄位是收盤／漲跌%／木率／木質。大戶增比、營收年增是週更／月更，每天推一樣的數字沒有意義（使用者決定）。
- 缺值用 ASCII `--`：em dash 在東亞字寬是 Ambiguous，手機字型常畫成全形，那一列會歪。
- 一般文字全經 `escape_mdv2`；inline code 內依 Telegram 規定只跳脫 ` 與 \\。結尾固定帶非投資建議聲明。
"""
from collections import Counter
from datetime import date

from .telegram_push import escape_mdv2 as _e

_WEEKDAYS = "一二三四五六日"
DEFAULT_LIMIT = 20
_DISCLAIMER = "⚠️ 自算篩選結果整理，非投資建議"
MISSING = "--"
_UNCLASSIFIED = {"", "未分類"}


def fmt_price(v) -> str:
    if v is None:
        return MISSING
    if v >= 1000:
        return f"{v:.0f}"
    return f"{v:.2f}".rstrip("0").rstrip(".")


def fmt_pct(v) -> str:
    return MISSING if v is None else f"{v:+.2f}%"


def _fmt_int(v) -> str:
    return MISSING if v is None else f"{v:.0f}"


# 欄寬＝該欄最長可能值＋1 格間隔（代號 4 碼／收盤最長 "999.95"／漲跌最長 "-10.00%"／木率最長 4 位／木質 0–19）。
# **總寬度要壓在手機一行內**：第一版 34 字元，實際推播在手機上（實測一行約 39 個等寬字元寬、中文約 1.7 格）
# 只剩 2 個中文字的空間，「有成精密」「愛派司」都被折到下一行。改成 27 字元後名稱約有 6 個中文字的空間。
ROW_WIDTHS = (4, 7, 8, 5, 3)


def format_pick_row(it: dict) -> str:
    """一列的數字部分（純 ASCII、未跳脫）：代號／收盤／漲跌／木率／木質，欄寬見 ROW_WIDTHS。"""
    wc, wp, wg, wv, ws = ROW_WIDTHS
    return (f"{str(it['code']):<{wc}}{fmt_price(it.get('close')):>{wp}}{fmt_pct(it.get('chg_pct')):>{wg}}"
            f"{_fmt_int(it.get('mu_value')):>{wv}}{_fmt_int(it.get('mu_score')):>{ws}}")


def _row_line(it: dict) -> str:
    code = format_pick_row(it).replace("\\", "\\\\").replace("`", "\\`")
    return f"`{code}`  {_e(str(it.get('name') or ''))}"


def _legend(pct_label: str) -> str:
    return _e(f"代號｜收盤｜{pct_label}｜木率｜木質｜名稱")


def concentration_line(items: list, top: int = 3) -> str | None:
    """同一子產業 ≥2 檔的前 `top` 個，例：「集中：DRAM 3 檔、IC封裝 2 檔」（未跳脫）。都只有 1 檔回 None。"""
    counts = Counter(str(it.get("sector") or "").strip() for it in items)
    groups = [(s, n) for s, n in counts.most_common() if s not in _UNCLASSIFIED and n >= 2][:top]
    if not groups:
        return None
    return "集中：" + "、".join(f"{s} {n} 檔" for s, n in groups)


def _md(day: str) -> str:
    return day[5:] if day else "--"


def _day_label(day: str) -> str:
    try:
        return f"{_md(day)}（{_WEEKDAYS[date.fromisoformat(day).weekday()]}）"
    except (TypeError, ValueError):
        return _md(day)


def _ready_line(ready_at: str | None) -> str | None:
    return _e(f"名單 {ready_at[5:16].replace('T', ' ')} 算好") if ready_at else None


def _footer(ready_at: str | None) -> list:
    ready = _ready_line(ready_at)
    return [""] + ([ready] if ready else []) + [_e(_DISCLAIMER)]


def compose_daily_new_picks(day: str, total: int, n_day: int, n_week: int, prev_date: str | None,
                            star_items: list, renew_items: list, ready_at: str | None,
                            limit: int = DEFAULT_LIMIT) -> str:
    parts = [f"📋 *{_e('自算選股｜今日新進榜')}*　{_e(_day_label(day))}",
             _e(f"入選 {total}｜今日新進 {n_day}｜本週新進 {n_week}")]
    if prev_date:
        parts.append(_e(f"（相較前一份名單 {_md(prev_date)}）"))
    stars = star_items[:limit]
    renews = renew_items[:max(0, limit - len(stars))]
    hidden = len(star_items) + len(renew_items) - len(stars) - len(renews)
    if not stars and not renews:
        parts += ["", _e("今日無新進榜")]
        return "\n".join(parts + _footer(ready_at))
    conc = concentration_line(star_items + renew_items)
    if conc:
        parts.append(_e(conc))
    parts += ["", _legend("漲跌%")]
    if stars:
        parts += ["", f"✦ *{_e('今天才進榜，上一個集保週期也沒有')}*{_e(f'（{len(star_items)} 檔）')}"]
        parts += [_row_line(it) for it in stars]
    if renews:
        parts += ["", f"🆕 *{_e('NEW：掉出名單後今天重新進榜')}*{_e(f'（{len(renew_items)} 檔）')}"]
        parts += [_row_line(it) for it in renews]
    if hidden > 0:
        parts.append(_e(f"另 {hidden} 檔未列出"))
    return "\n".join(parts + _footer(ready_at))


def compose_weekly_new_picks(day: str, week_start: str, total: int, n_week: int, items: list,
                             basis: dict | None, top_sectors: list, ready_at: str | None,
                             limit: int = DEFAULT_LIMIT) -> str:
    parts = [f"📋 *{_e('自算選股｜本週新進榜')}*　{_e(f'{_md(week_start)}～{_md(day)}')}",
             _e(f"入選 {total}｜本週新進 {n_week}")]
    if basis:
        parts.append(_e(f"（相較上一個集保週期 {_md(basis.get('from'))}～{_md(basis.get('to'))} 的名單）"))
    shown = items[:limit]
    if shown:
        conc = concentration_line(items)
        if conc:
            parts.append(_e(conc))
        parts += ["", _legend("本週%")]
        parts += [_row_line(it) for it in shown]
        if len(items) > len(shown):
            parts.append(_e(f"另 {len(items) - len(shown)} 檔未列出"))
    else:
        parts += ["", _e("本週無新進榜")]
    if top_sectors:
        parts += ["", f"💰 *{_e('本週大戶買進前三子產業')}*"]
        for i, (name, value) in enumerate(top_sectors[:3], 1):
            parts.append(_e(f"{i}. {name} {value / 1e8:.1f} 億"))
    return "\n".join(parts + _footer(ready_at))

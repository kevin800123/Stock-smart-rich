"""自算選股「新進榜」Telegram 推播的訊息組裝（純函式，資料由 api/helpers 準備）。

兩則（使用者規格，2026-09）：
- 平日 21:40「今日新進榜」：只列今天才進的——✦（上一個集保週期也沒有）與 NEW（掉出後重新進榜）
  分兩段，各依木率排序；沒有就送「今日無新進榜」。
- 週六 18:00「本週新進榜」：本週新進（Week NEW），漲跌% 是本週（週五收盤 vs 上週最後一個交易日），
  外加「本週大戶買進前三子產業」——大戶增比一週才更新一次，放在週報才不會連續五天內容一樣。

版面決定：
- 表格放 MarkdownV2 的 ``` 區塊（等寬字型）。**名稱放最後一欄**：Telegram 的等寬字型裡中文字寬
  並不穩定（手機／桌面不一樣），名稱放中間後面的數字欄會歪；數字全是 ASCII，放前面一定對齊。
- 欄位是收盤／漲跌%／木率／木質。大戶增比、營收年增是週更／月更，每天推一樣的數字沒有意義（使用者決定）。
- 每則最多列 `limit` 檔（預設 20），超過寫「另 N 檔見網頁」，不靜默截斷。
- 所有一般文字都經 `escape_mdv2`；``` 區塊內依 Telegram 規定只跳脫 ` 與 \\。
- 結尾固定帶非投資建議聲明（同財經新聞推播）。
"""
import unicodedata
from datetime import date

from .telegram_push import escape_mdv2 as _e

_WEEKDAYS = "一二三四五六日"
DEFAULT_LIMIT = 20
_DISCLAIMER = "⚠️ 自算篩選結果整理，非投資建議"


def display_width(s: str) -> int:
    """等寬字型下的顯示寬度：全形／寬字元算 2。"""
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in str(s))


def _rjust(s: str, width: int) -> str:
    return " " * max(0, width - display_width(s)) + s


def _ljust(s: str, width: int) -> str:
    return s + " " * max(0, width - display_width(s))


# 缺值用 ASCII "--" 而不是「—」：em dash 在東亞字寬是 Ambiguous，Telegram 手機字型常把它畫成全形，
# 那一列的數字欄就會歪掉（表格放 ``` 區塊就是為了對齊）。
MISSING = "--"


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


def render_pick_table(items: list, pct_label: str = "漲跌%") -> str:
    """純文字表格（未跳脫）。欄寬：代號 6／收盤 8／漲跌 9／木率 6／木質 5，名稱在最後。"""
    head = (_ljust("代號", 6) + _rjust("收盤", 8) + _rjust(pct_label, 9)
            + _rjust("木率", 6) + _rjust("木質", 5) + "  名稱")
    lines = [head]
    for it in items:
        lines.append(_ljust(str(it["code"]), 6) + _rjust(fmt_price(it.get("close")), 8)
                     + _rjust(fmt_pct(it.get("chg_pct")), 9) + _rjust(_fmt_int(it.get("mu_value")), 6)
                     + _rjust(_fmt_int(it.get("mu_score")), 5) + "  " + str(it.get("name") or ""))
    return "\n".join(lines)


def _pre(text: str) -> str:
    return "```\n" + text.replace("\\", "\\\\").replace("`", "\\`") + "\n```"


def _md(day: str) -> str:
    return day[5:] if day else "—"


def _day_label(day: str) -> str:
    try:
        return f"{_md(day)}（{_WEEKDAYS[date.fromisoformat(day).weekday()]}）"
    except (TypeError, ValueError):
        return _md(day)


def _ready_line(ready_at: str | None) -> str:
    stamp = f"名單 {ready_at[5:16].replace('T', ' ')} 算好｜" if ready_at else ""
    return _e(stamp + "完整表格見網頁「自算籌碼／基本選股」")


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
        parts.append("")
        parts.append(_e("今日無新進榜"))
    if stars:
        parts.append("")
        parts.append(f"✦ *{_e('今天才進榜，上一個集保週期也沒有')}*{_e(f'（{len(star_items)} 檔）')}")
        parts.append(_pre(render_pick_table(stars)))
    if renews:
        parts.append("")
        parts.append(f"🆕 *{_e('NEW：掉出名單後今天重新進榜')}*{_e(f'（{len(renew_items)} 檔）')}")
        parts.append(_pre(render_pick_table(renews)))
    if hidden > 0:
        parts.append(_e(f"另 {hidden} 檔見網頁"))
    parts += ["", _ready_line(ready_at), _e(_DISCLAIMER)]
    return "\n".join(parts)


def compose_weekly_new_picks(day: str, week_start: str, total: int, n_week: int, items: list,
                             basis: dict | None, top_sectors: list, ready_at: str | None,
                             limit: int = DEFAULT_LIMIT) -> str:
    parts = [f"📋 *{_e('自算選股｜本週新進榜')}*　{_e(f'{_md(week_start)}～{_md(day)}')}",
             _e(f"入選 {total}｜本週新進 {n_week}")]
    if basis:
        parts.append(_e(f"（相較上一個集保週期 {_md(basis.get('from'))}～{_md(basis.get('to'))} 的名單）"))
    shown = items[:limit]
    parts.append("")
    if shown:
        parts.append(_pre(render_pick_table(shown, pct_label="本週%")))
        if len(items) > len(shown):
            parts.append(_e(f"另 {len(items) - len(shown)} 檔見網頁"))
    else:
        parts.append(_e("本週無新進榜"))
    if top_sectors:
        parts += ["", f"💰 *{_e('本週大戶買進前三子產業')}*"]
        for i, (name, value) in enumerate(top_sectors[:3], 1):
            parts.append(_e(f"{i}. {name} {value / 1e8:.1f} 億"))
    parts += ["", _ready_line(ready_at), _e(_DISCLAIMER)]
    return "\n".join(parts)

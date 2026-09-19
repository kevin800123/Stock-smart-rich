"""自算選股新進榜 Telegram 推播：訊息組裝（純函式）與資料準備。

平日 21:40 列「今天才進」（✦ 與 NEW 分兩段）、週六 18:00–21:30（等本週集保，最晚 21:30）列「本週新進」
＋大戶買進前三子產業。
版面第二版：每列一行單行等寬（不用 ``` 區塊，手機上會多一顆「複製程式碼」按鈕）、表頭改成列表外的
說明行（含中文的表頭在等寬字型裡對不齊）、加「集中：」一行、不提網頁。
"""
import re

from stocks_power_rich import pick_push as pp

BT = chr(96)          # 反引號


def _item(code, name, close=100.0, chg=1.5, mv=200.0, ms=12, sector="DRAM"):
    return {"code": code, "name": name, "close": close, "chg_pct": chg, "mu_value": mv, "mu_score": ms,
            "sector": sector}


def _unescaped_reserved(text):
    """MarkdownV2 保留字元沒有被反斜線保護的位置。inline code（反引號之間）只需跳脫反引號與反斜線，
    粗體用的 * 是刻意的語法，不算。"""
    out, in_code, i = [], False, 0
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == BT:
            in_code = not in_code
            i += 1
            continue
        if not in_code and ch in "_[]()~>#+-=|{}.!":
            out.append((i, ch))
        i += 1
    return out


def _rows(msg):
    """訊息裡的資料列：以反引號開頭的行。"""
    return [ln for ln in msg.splitlines() if ln.startswith(BT)]


def test_price_and_pct_formatting():
    assert pp.fmt_price(158.5) == "158.5"
    assert pp.fmt_price(1185.0) == "1185"
    assert pp.fmt_price(26.35) == "26.35"
    assert pp.fmt_price(None) == "--"
    assert pp.fmt_pct(3.214) == "+3.21%"
    assert pp.fmt_pct(-0.5) == "-0.50%"
    assert pp.fmt_pct(None) == "--"


def test_row_numbers_have_fixed_width_so_columns_line_up():
    a = pp.format_pick_row(_item("3260", "威剛", 399.5, 2.83, 558.4, 12))
    b = pp.format_pick_row(_item("9945", "潤泰新", 28.05, None, 70.0, 10))
    assert len(a) == len(b) == sum(pp.ROW_WIDTHS)
    assert a.isascii() and b.isascii()                      # 等寬區只放 ASCII，對齊才可靠
    assert "+2.83%" in a and "--" in b


def test_row_fits_a_phone_line_and_extreme_values_stay_separated():
    """手機一行實測約 39 個等寬字元寬：第一版 34 字元讓名稱只剩 2 個中文字、被折到下一行。
    壓到 27 以內；但縮欄寬不能讓最長的值黏在一起（黏起來就讀錯欄）。"""
    assert sum(pp.ROW_WIDTHS) <= 27
    worst = pp.format_pick_row(_item("9999", "x", 999.95, -10.0, 1900.0, 19))
    assert worst.split() == ["9999", "999.95", "-10.00%", "1900", "19"]
    big = pp.format_pick_row(_item("2330", "x", 12345.0, 10.0, 999.0, 9))
    assert big.split() == ["2330", "12345", "+10.00%", "999", "9"]


def test_concentration_line_only_counts_groups_with_two_or_more():
    items = [_item("1", "a", sector="DRAM"), _item("2", "b", sector="DRAM"), _item("3", "c", sector="DRAM"),
             _item("4", "d", sector="IC封裝"), _item("5", "e", sector="IC封裝"),
             _item("6", "f", sector="銀行"), _item("7", "g", sector="未分類"), _item("8", "h", sector="未分類")]
    assert pp.concentration_line(items) == "集中：DRAM 3 檔、IC封裝 2 檔"
    assert pp.concentration_line([_item("1", "a", sector="DRAM"), _item("2", "b", sector="銀行")]) is None


def test_daily_message_layout():
    msg = pp.compose_daily_new_picks(
        day="2026-09-16", total=55, n_day=3, n_week=30, prev_date="2026-09-15",
        star_items=[_item("3260", "威剛"), _item("2344", "華邦電")],
        renew_items=[_item("6651", "全宇昕", sector="MLCC")], ready_at="2026-09-16T17:31:00")
    assert "今日新進榜" in msg and "09\-16（三）" in msg
    assert "入選 55｜今日新進 3｜本週新進 30" in msg
    assert "集中：DRAM 2 檔" in msg
    assert "代號｜收盤｜漲跌%｜木率｜木質｜名稱" in msg
    assert msg.index("集中") < msg.index("代號｜") < msg.index("✦") < msg.index("NEW：")
    assert "```" not in msg                                     # 不用程式碼區塊（手機會多一顆複製鈕）
    rows = _rows(msg)
    assert len(rows) == 3 and rows[0].endswith("威剛") and rows[2].endswith("全宇昕")
    assert "17:31 算好" in msg and "非投資建議" in msg
    assert "網頁" not in msg                                     # 使用者決定不提網頁
    assert _unescaped_reserved(msg) == []


def test_daily_message_omits_an_empty_section():
    msg = pp.compose_daily_new_picks(
        day="2026-09-16", total=55, n_day=1, n_week=30, prev_date="2026-09-15",
        star_items=[], renew_items=[_item("6651", "全宇昕")], ready_at=None)
    assert "✦" not in msg and "NEW：" in msg and len(_rows(msg)) == 1
    assert "集中" not in msg                                     # 只有 1 檔，不印集中度


def test_daily_message_says_no_new_entries_when_both_sections_are_empty():
    msg = pp.compose_daily_new_picks(
        day="2026-09-16", total=55, n_day=0, n_week=30, prev_date="2026-09-15",
        star_items=[], renew_items=[], ready_at=None)
    assert "今日無新進榜" in msg and _rows(msg) == [] and "代號｜" not in msg
    assert _unescaped_reserved(msg) == []


def test_daily_message_caps_rows_at_the_limit_star_first():
    stars = [_item(f"{1000 + i}", f"股{i}") for i in range(15)]
    renews = [_item(f"{2000 + i}", f"再{i}") for i in range(10)]
    msg = pp.compose_daily_new_picks(
        day="2026-09-16", total=80, n_day=25, n_week=40, prev_date="2026-09-15",
        star_items=stars, renew_items=renews, ready_at=None, limit=20)
    rows = _rows(msg)
    assert len(rows) == 20
    assert any("1014 " in r for r in rows) and any("2004 " in r for r in rows)
    assert not any("2005 " in r for r in rows)                 # ✦ 15 檔全列，NEW 只剩 5 格
    assert "另 5 檔未列出" in msg and "網頁" not in msg


def _row_codes(msg):
    return [ln[1:].split()[0] for ln in _rows(msg)]


def test_listed_codes_are_exactly_the_rows_in_the_message():
    """「使用者看過的代號」＝內文列出的代號（fix wave 3 #A）：listed_codes_* 與 compose 共用同一段切片，
    ✦ 優先、上限內才算；超過上限寫「另 N 檔未列出」的不算。"""
    stars = [_item(f"{1000 + i}", f"股{i}") for i in range(3)]
    renews = [_item(f"{2000 + i}", f"再{i}") for i in range(3)]
    for limit in (2, 4, 20):
        msg = pp.compose_daily_new_picks(
            day="2026-09-16", total=10, n_day=6, n_week=3, prev_date="2026-09-15",
            star_items=stars, renew_items=renews, ready_at=None, limit=limit)
        assert pp.listed_codes_daily(stars, renews, limit) == _row_codes(msg)
        wmsg = pp.compose_weekly_new_picks(
            day="2026-09-18", week_start="2026-09-14", total=10, n_week=6, items=stars + renews,
            basis=None, top_sectors=[], ready_at=None, limit=limit)
        assert pp.listed_codes_weekly(stars + renews, limit) == _row_codes(wmsg)
    assert pp.listed_codes_daily(stars, renews, 4) == ["1000", "1001", "1002", "2000"]
    assert pp.listed_codes_weekly(stars + renews, 2) == ["1000", "1001"]
    assert pp.listed_codes_daily([], [], 20) == [] and pp.listed_codes_weekly([], 20) == []


def test_weekly_message_lists_week_change_and_top_sectors():
    msg = pp.compose_weekly_new_picks(
        day="2026-09-18", week_start="2026-09-14", total=55, n_week=2,
        items=[_item("3006", "晶豪科", 120.0, 8.3), _item("2408", "南亞科", 60.2, -2.0)],
        basis={"from": "2026-09-07", "to": "2026-09-11"},
        top_sectors=[("晶圓代工", 753.7e8), ("DRAM", 175e8), ("手機晶片相關", 167.1e8)],
        ready_at="2026-09-18T17:31:00")
    assert "本週新進榜" in msg and "09\-14～09\-18" in msg
    assert "代號｜收盤｜本週%｜木率｜木質｜名稱" in msg and "+8.30%" in msg
    assert "集中：DRAM 2 檔" in msg
    assert "晶圓代工 753\.7 億" in msg and "DRAM 175\.0 億" in msg
    assert "09\-07～09\-11" in msg and "```" not in msg and "網頁" not in msg
    assert _unescaped_reserved(msg) == []


def test_weekly_message_says_no_new_entries_but_keeps_sectors():
    msg = pp.compose_weekly_new_picks(
        day="2026-09-18", week_start="2026-09-14", total=55, n_week=0, items=[],
        basis={"from": "2026-09-07", "to": "2026-09-11"},
        top_sectors=[("晶圓代工", 753.7e8)], ready_at=None)
    assert "本週無新進榜" in msg and "晶圓代工" in msg and _rows(msg) == []


# ---------------------------------------------------------------- 資料準備（api/helpers）
from datetime import datetime

import pytest

from stocks_power_rich import db, selfcheck
from stocks_power_rich.api import helpers

_VALS = {"rev_yoy": 10.0, "w55": 1, "big_holder_ratio": 1.0, "holder_drop_ratio": -1.0,
         "trust_3d": 0, "foreign_3d": 0, "lan_score": 12, "est_profit": 2.0, "mu_score": 15,
         "margin_3d": 0}


@pytest.fixture
def conn(tmp_path, monkeypatch):
    path = str(tmp_path / "t.sqlite")
    monkeypatch.setenv("SPR_DB_PATH", path)
    c = db.get_connection(path)
    db.init_db(c)
    return c


def _setup(c, list_day, rows, cal, closes, ledger, weeks=("2026-09-04", "2026-09-11"), heatmap=()):
    for d in cal:
        db.upsert_market_daily(c, {"date": d, "taiex": 20000.0})
    for code, by_day in closes.items():
        for d, px in by_day.items():
            c.execute("INSERT INTO stock_ohlc (date, code, close) VALUES (?,?,?)", (d, code, px))
    for d, code in ledger:
        c.execute("INSERT INTO signal_ledger (signal_date, code, name, source, entry_ref_price) "
                  "VALUES (?,?,?,?,1)", (d, code, code, "self_screen"))
    for wk in weeks:
        db.bulk_upsert_custody(c, wk, {"9999": {"big400_pct": 1.0, "total_holders": 100}})
    c.commit()
    selfcheck.save_precomputed(c, {
        "date": list_day, "ready_at": f"{list_day}T17:31:00", "heatmap": list(heatmap),
        "coverage": {}, "rows": [{"code": code, "name": name, "sector": "x",
                                  "vals": {**_VALS, "mu_value": mv}} for code, name, mv in rows]})


def test_daily_payload_splits_star_and_renew_and_prices_against_previous_trading_day(conn, monkeypatch):
    monkeypatch.setattr(helpers, "_now", lambda: datetime(2026, 9, 16, 21, 40))
    _setup(conn, "2026-09-16",
           rows=[("9999", "新來的", 300.0), ("9998", "回來的", 200.0), ("9997", "老面孔", 100.0)],
           cal=["2026-09-10", "2026-09-15", "2026-09-16"],
           closes={"9999": {"2026-09-15": 100.0, "2026-09-16": 110.0},
                   "9998": {"2026-09-10": 50.0, "2026-09-16": 49.0}},    # 9998 缺 09-15 → 漲跌算不出
           ledger=[("2026-09-10", "9998"), ("2026-09-10", "9997"), ("2026-09-15", "9997")])
    p = helpers.new_picks_push_payload(conn, "daily")
    assert p.get("skipped") is None
    assert p["counts"] == {"total": 3, "day": 2, "week": 1}
    text = p["text"]
    star, renew = text.split("NEW：")
    assert "9999" in star and "+10.00%" in star and "9998" not in star
    assert "9998" in renew and "9997" not in text
    row_9998 = next(ln for ln in renew.splitlines() if "9998" in ln)
    assert "--" in row_9998                                   # 前一交易日缺價：不拿更早的 09-10 頂替


def test_daily_payload_skips_when_the_list_is_not_today(conn, monkeypatch):
    monkeypatch.setattr(helpers, "_now", lambda: datetime(2026, 9, 17, 21, 40))
    _setup(conn, "2026-09-16", rows=[("9999", "x", 300.0)], cal=["2026-09-16"], closes={}, ledger=[])
    assert helpers.new_picks_push_payload(conn, "daily")["skipped"] == "list_not_today"
    assert "text" in helpers.new_picks_push_payload(conn, "daily", force=True)   # 預覽可強制


def test_weekly_payload_uses_week_change_and_top_sectors(conn, monkeypatch):
    monkeypatch.setattr(helpers, "_now", lambda: datetime(2026, 9, 19, 18, 0))      # 週六
    _setup(conn, "2026-09-18",
           rows=[("9999", "本週新", 300.0), ("9997", "上週就在", 100.0)],
           cal=["2026-09-11", "2026-09-14", "2026-09-18"],
           closes={"9999": {"2026-09-11": 80.0, "2026-09-14": 90.0, "2026-09-18": 100.0}},
           ledger=[("2026-09-10", "9997")],
           heatmap=[{"sector": "DRAM", "buy_value": 175e8}, {"sector": "晶圓代工", "buy_value": 753.7e8},
                    {"sector": "MLCC", "buy_value": 64e8}, {"sector": "銀行", "buy_value": 1e8}])
    p = helpers.new_picks_push_payload(conn, "weekly")
    text = p["text"]
    assert p["counts"]["week"] == 1
    assert "9999" in text and "+25.00%" in text and "9997" not in text   # 100 vs 上週五 80
    assert text.index("晶圓代工") < text.index("DRAM") < text.index("MLCC") and "銀行" not in text


def test_weekly_payload_skips_when_no_list_this_week(conn, monkeypatch):
    monkeypatch.setattr(helpers, "_now", lambda: datetime(2026, 9, 26, 18, 0))
    _setup(conn, "2026-09-18", rows=[("9999", "x", 300.0)], cal=["2026-09-18"], closes={}, ledger=[])
    assert helpers.new_picks_push_payload(conn, "weekly")["skipped"] == "no_list_this_week"


def test_job_sends_payload_and_does_not_send_when_skipped(conn, monkeypatch):
    from stocks_power_rich import telegram_push
    from stocks_power_rich.config import Config
    sent = []
    monkeypatch.setattr(telegram_push, "send_message",
                        lambda tok, chat, text: sent.append(text) or {"ok": True, "parse_mode_used": "MarkdownV2"})
    cfg = Config(telegram_token="t", telegram_chat_id="c")
    monkeypatch.setattr(helpers, "_now", lambda: datetime(2026, 9, 16, 21, 40))
    _setup(conn, "2026-09-16", rows=[("9999", "x", 300.0)], cal=["2026-09-16"], closes={}, ledger=[])
    r = helpers.telegram_new_picks_job(conn, cfg, "daily")
    assert r["sent"] is True and len(sent) == 1
    monkeypatch.setattr(helpers, "_now", lambda: datetime(2026, 9, 17, 21, 40))
    r = helpers.telegram_new_picks_job(conn, cfg, "daily")
    assert r["skipped"] == "list_not_today" and len(sent) == 1

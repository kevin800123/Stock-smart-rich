"""自算選股新進榜 Telegram 推播：訊息組裝（純函式）。

平日 21:40 列「今天才進」（✦ 與 NEW 分兩段）、週六 18:00 列「本週新進」＋大戶買進前三子產業。
名稱放最後一欄：Telegram 等寬字型裡中文寬度不穩，數字欄在前才對得齊。
"""
import re

from stocks_power_rich import pick_push as pp


def _item(code, name, close=100.0, chg=1.5, mv=200.0, ms=12):
    return {"code": code, "name": name, "close": close, "chg_pct": chg, "mu_value": mv, "mu_score": ms}


def _unescaped_reserved(text):
    """MarkdownV2 保留字元沒有被反斜線保護的位置（``` 區塊內只需跳脫 ` 與 \\，另外判斷）。"""
    out, in_pre, i = [], False, 0
    while i < len(text):
        if text.startswith("```", i):
            in_pre = not in_pre
            i += 3
            continue
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if not in_pre and ch in "_*[]()~`>#+-=|{}.!" and ch != "*":
            out.append((i, ch))
        i += 1
    return out


def test_display_width_counts_cjk_as_two():
    assert pp.display_width("2330") == 4
    assert pp.display_width("台積電") == 6
    assert pp.display_width("漲跌%") == 5


def test_price_and_pct_formatting():
    assert pp.fmt_price(158.5) == "158.5"
    assert pp.fmt_price(1185.0) == "1185"
    assert pp.fmt_price(26.35) == "26.35"
    assert pp.fmt_price(None) == "--"
    assert pp.fmt_pct(3.214) == "+3.21%"
    assert pp.fmt_pct(-0.5) == "-0.50%"
    assert pp.fmt_pct(None) == "--"


def test_table_numbers_line_up_and_name_is_last():
    table = pp.render_pick_table([_item("3260", "威剛", 158.5, 3.2, 558.4, 12),
                                  _item("2344", "華邦電", 26.35, -1.1, 252.0, 17)])
    lines = table.splitlines()
    assert lines[0].startswith("代號")
    rows = lines[1:]
    # 名稱之前的部分（全是 ASCII）長度一致＝數字欄對齊
    prefix = [r.rsplit("  ", 1)[0] for r in rows]
    assert len({len(p) for p in prefix}) == 1
    assert rows[0].endswith("威剛") and rows[1].endswith("華邦電")
    assert "558" in rows[0] and "+3.20%" in rows[0] and "-1.10%" in rows[1]


def test_daily_message_has_two_sections_and_escapes_markdown():
    msg = pp.compose_daily_new_picks(
        day="2026-09-16", total=55, n_day=3, n_week=30, prev_date="2026-09-15",
        star_items=[_item("3260", "威剛"), _item("2344", "華邦電")],
        renew_items=[_item("6651", "全宇昕")], ready_at="2026-09-16T17:31:00")
    assert "今日新進榜" in msg and "09\\-16（三）" in msg
    assert "入選 55｜今日新進 3｜本週新進 30" in msg
    assert msg.index("✦") < msg.index("NEW：")                     # ✦ 段在前
    assert msg.count("```") == 4                                     # 兩個表格區塊
    assert "威剛" in msg and "全宇昕" in msg
    assert "17:31 算好" in msg and "非投資建議" in msg
    assert _unescaped_reserved(msg) == []


def test_daily_message_omits_an_empty_section():
    msg = pp.compose_daily_new_picks(
        day="2026-09-16", total=55, n_day=1, n_week=30, prev_date="2026-09-15",
        star_items=[], renew_items=[_item("6651", "全宇昕")], ready_at=None)
    assert "✦" not in msg and "NEW：" in msg and msg.count("```") == 2


def test_daily_message_says_no_new_entries_when_both_sections_are_empty():
    msg = pp.compose_daily_new_picks(
        day="2026-09-16", total=55, n_day=0, n_week=30, prev_date="2026-09-15",
        star_items=[], renew_items=[], ready_at=None)
    assert "今日無新進榜" in msg and "```" not in msg
    assert _unescaped_reserved(msg) == []


def test_daily_message_caps_rows_at_the_limit_star_first():
    stars = [_item(f"{1000 + i}", f"股{i}") for i in range(15)]
    renews = [_item(f"{2000 + i}", f"再{i}") for i in range(10)]
    msg = pp.compose_daily_new_picks(
        day="2026-09-16", total=80, n_day=25, n_week=40, prev_date="2026-09-15",
        star_items=stars, renew_items=renews, ready_at=None, limit=20)
    listed = re.findall(r"^\d{4} ", msg, flags=re.M)
    assert len(listed) == 20
    assert "1014 " in msg and "2004 " in msg and "2005 " not in msg   # ✦ 15 檔全列，NEW 只剩 5 格
    assert "另 5 檔見網頁" in msg


def test_weekly_message_lists_week_change_and_top_sectors():
    msg = pp.compose_weekly_new_picks(
        day="2026-09-18", week_start="2026-09-14", total=55, n_week=2,
        items=[_item("3006", "晶豪科", 120.0, 8.3), _item("2408", "南亞科", 60.2, -2.0)],
        basis={"from": "2026-09-07", "to": "2026-09-11"},
        top_sectors=[("晶圓代工", 753.7e8), ("DRAM", 175e8), ("手機晶片相關", 167.1e8)],
        ready_at="2026-09-18T17:31:00")
    assert "本週新進榜" in msg and "09\\-14～09\\-18" in msg
    assert "本週%" in msg and "+8.30%" in msg
    assert "晶圓代工 753\\.7 億" in msg and "DRAM 175\\.0 億" in msg
    assert "09\\-07～09\\-11" in msg
    assert _unescaped_reserved(msg) == []


def test_weekly_message_says_no_new_entries_but_keeps_sectors():
    msg = pp.compose_weekly_new_picks(
        day="2026-09-18", week_start="2026-09-14", total=55, n_week=0, items=[],
        basis={"from": "2026-09-07", "to": "2026-09-11"},
        top_sectors=[("晶圓代工", 753.7e8)], ready_at=None)
    assert "本週無新進榜" in msg and "晶圓代工" in msg and "```" not in msg


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
    assert "--" in renew.split("9998")[1].splitlines()[0]     # 前一交易日缺價：不拿更早的 09-10 頂替


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

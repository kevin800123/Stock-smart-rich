"""週集保一公布就反映到自算選股（docs/superpowers/specs/2026-09-19-custody-watch-design.md）。"""
import json
from datetime import date, datetime, timedelta

import pytest

from stocks_power_rich import db, ledger, pick_push, selfcheck, telegram_push, updater
from stocks_power_rich.api import helpers
from stocks_power_rich.config import Config
from stocks_power_rich.sources import tdcc


@pytest.fixture
def conn(tmp_path, monkeypatch):
    path = str(tmp_path / "t.sqlite")
    monkeypatch.setenv("SPR_DB_PATH", path)
    c = db.get_connection(path)
    db.init_db(c)
    return c


FULL = [f"{1000 + i}" for i in range(10)]


def _week(c, week, codes):
    db.bulk_upsert_custody(c, week, {code: {"big400_pct": 50.0, "total_holders": 100} for code in codes})


def test_complete_week_ignores_single_stock_backfill(conn):
    """逐檔回補把一檔寫進新週：那一週不算完整，最新完整週仍是上一週。"""
    _week(conn, "2026-09-11", FULL)
    _week(conn, "2026-09-18", ["2330"])
    assert db.latest_complete_custody_week(conn) == "2026-09-11"
    assert db.custody_week_complete(conn, "2026-09-11") is True
    assert db.custody_week_complete(conn, "2026-09-18") is False
    assert db.custody_week_complete(conn, "2026-09-25") is False   # 完全沒有列


def test_accumulate_custody_is_not_blocked_by_a_partial_week(conn, monkeypatch):
    """新週只有逐檔回補的一檔時，全市場那一批仍要寫入（舊寫法會被 6 天節流與「已存在」擋一整週）。"""
    prev = (date.today() - timedelta(days=8)).isoformat()
    new = (date.today() - timedelta(days=1)).isoformat()
    _week(conn, prev, FULL)
    _week(conn, new, ["2330"])
    monkeypatch.setattr(updater.tdcc, "fetch_custody_distribution", lambda: {
        "week_date": new, "data": {code: {"big400_pct": 60.0, "total_holders": 120} for code in FULL}})
    assert updater._accumulate_custody(conn) == new
    n = conn.execute("SELECT COUNT(*) FROM custody_dist WHERE week=?", (new,)).fetchone()[0]
    assert n == len(FULL) + 1          # 全市場 10 檔＋原本那一檔（2330 不在 FULL 裡）
    assert db.custody_week_complete(conn, new) is True


def test_accumulate_custody_records_first_fetch_time_once(conn, monkeypatch):
    new = date.today().isoformat()
    monkeypatch.setattr(updater.tdcc, "fetch_custody_distribution", lambda: {
        "week_date": new, "data": {code: {"big400_pct": 60.0, "total_holders": 120} for code in FULL}})
    assert updater._accumulate_custody(conn) == new
    stamp = db.get_ai_cache(conn, f"custody_fetched:{new}")
    assert stamp and datetime.fromisoformat(stamp["at"])
    db.set_ai_cache(conn, f"custody_fetched:{new}", {"at": "2000-01-01T00:00:00"})
    assert updater._accumulate_custody(conn) is None                    # 同一週：節流擋下
    assert db.get_ai_cache(conn, f"custody_fetched:{new}")["at"] == "2000-01-01T00:00:00"

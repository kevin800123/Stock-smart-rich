from stocks_power_rich import analysis
from stocks_power_rich.db import (add_trade, close_trade, delete_trade, get_connection,
                                  init_db, list_trades)


def test_trades_crud_roundtrip(tmp_path):
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    tid = add_trade(conn, {"code": "2330", "name": "台積電", "shares": 1000,
                           "entry_date": "2026-06-01", "entry_price": 100.0, "note": "杯柄突破"})
    assert tid > 0
    rows = list_trades(conn)
    assert len(rows) == 1 and rows[0]["code"] == "2330" and rows[0]["exit_date"] is None
    assert close_trade(conn, tid, "2026-06-10", 110.0) is True
    assert list_trades(conn)[0]["exit_price"] == 110.0
    assert close_trade(conn, 999, "2026-06-10", 1.0) is False   # 不存在
    assert delete_trade(conn, tid) is True
    assert list_trades(conn) == []


def test_trade_stats_closed_open_and_alpha():
    """勝率/賺賠比/期望值＝淨值（預設扣 0.585% 來回費用）；未平倉以最新收盤估；
    同期大盤報酬取「≤ 該日的最近一個交易日」加權值（非交易日也對得到）。"""
    trades = [
        {"id": 1, "code": "2330", "shares": 1000, "entry_date": "2026-06-01",
         "entry_price": 100.0, "exit_date": "2026-06-10", "exit_price": 110.0,
         "fee_pct": None},                                    # 毛+10% → 淨+9.415%
        {"id": 2, "code": "1101", "shares": 2000, "entry_date": "2026-06-02",  # 非交易日→取06-01
         "entry_price": 50.0, "exit_date": "2026-06-10", "exit_price": 47.0,
         "fee_pct": 0.0},                                     # 淨−6%（費用0）
        {"id": 3, "code": "8069", "shares": 1000, "entry_date": "2026-06-10",
         "entry_price": 200.0, "exit_date": None, "exit_price": None, "fee_pct": None},
    ]
    out = analysis.trade_stats(trades, closes={"8069": 210.0},
                               taiex_by_date={"2026-06-01": 45000.0, "2026-06-10": 45900.0})
    t1, t2, t3 = out["trades"]
    assert t1["status"] == "closed" and t1["net_pct"] == 9.41 and t1["pnl"] == 9415
    assert t1["mkt_pct"] == 2.0 and t1["alpha"] == 7.41       # 同期大盤 +2%
    assert t2["net_pct"] == -6.0 and t2["pnl"] == -6000 and t2["mkt_pct"] == 2.0
    assert t3["status"] == "open" and t3["mark"] == 210.0
    assert t3["net_pct"] == 4.42 and t3["pnl"] == 8830        # 未實現已扣費用
    s = out["stats"]
    assert s["closed_n"] == 2 and s["win_rate"] == 50.0
    assert s["avg_win"] == 9.41 and s["avg_loss"] == -6.0
    assert s["payoff"] == 1.57                                # 9.415 / 6
    assert s["expectancy"] == 1.71                            # 0.5×9.415 + 0.5×(−6)
    assert s["realized_pnl"] == 3415 and s["open_pnl"] == 8830
    assert s["avg_alpha"] == -0.29                            # (7.415 − 8.0) / 2


def test_trade_stats_empty_and_missing_refs():
    out = analysis.trade_stats([], closes={}, taiex_by_date={})
    assert out["stats"]["closed_n"] == 0 and out["stats"]["win_rate"] is None
    # 無大盤資料 → alpha 缺值不炸；未平倉且無現價 → 損益缺值
    out2 = analysis.trade_stats(
        [{"id": 1, "code": "9999", "shares": 1000, "entry_date": "2026-06-01",
          "entry_price": 10.0, "exit_date": None, "exit_price": None, "fee_pct": None}])
    t = out2["trades"][0]
    assert t["mark"] is None and t["net_pct"] is None and t["alpha"] is None

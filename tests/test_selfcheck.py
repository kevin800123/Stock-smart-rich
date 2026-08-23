from datetime import date, timedelta
from stocks_power_rich import selfcheck
from stocks_power_rich.db import get_connection, init_db


def _seed(conn):
    # 一天 CSV 快照（chip_snapshot），兩檔。W55 用 60 根遞增 OHLC → 站上中點。
    conn.execute("INSERT INTO chip_snapshot(snap_date,code,name,rev_yoy,w55,big_holder_ratio,"
                 "est_profit,lan_score,lpe) VALUES(?,?,?,?,?,?,?,?,?)",
                 ("2026-08-20", "2330", "台積電", 44.0, 1.0, 0.30, 22.0, 6.0, 56.0))
    conn.execute("INSERT INTO chip_snapshot(snap_date,code,name,rev_yoy,w55,big_holder_ratio,"
                 "est_profit,lan_score,lpe) VALUES(?,?,?,?,?,?,?,?,?)",
                 ("2026-08-20", "1101", "台泥", -5.0, 0.0, -0.10, 1.0, 3.0, 40.0))
    ds = [(date(2026, 6, 1) + timedelta(days=n)).isoformat() for n in range(60)]
    for i, d in enumerate(ds):        # 2330 遞增 → %R(55) 高 → w55 self = 1
        conn.execute("INSERT INTO stock_ohlc(code,date,high,low,close) VALUES('2330',?,?,?,?)",
                     (d, 100 + i, 99 + i, 100 + i))
    for i, d in enumerate(ds):        # 1101 遞減 → %R(55) 低 → w55 self = 0
        conn.execute("INSERT INTO stock_ohlc(code,date,high,low,close) VALUES('1101',?,?,?,?)",
                     (d, 200 - i, 199 - i, 200 - i))
    conn.commit()


def test_build_selfcheck_live_fields_and_blocked_fields(tmp_path):
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    _seed(conn)
    out = selfcheck.build_selfcheck(conn, "2026-08-20")

    assert out["date"] == "2026-08-20"
    assert out["fields"] == ["rev_yoy", "w55", "big_holder_ratio", "holder_drop_ratio",
                             "est_profit", "mu_score", "mu_value"]
    by_code = {r["code"]: r for r in out["rows"]}

    # rev_yoy：自算來自 revenue_yoy_map（本測試沒建月營收 → self None → self_na），不炸
    assert by_code["2330"]["fields"]["rev_yoy"]["status"] in ("match", "diff", "self_na")

    # w55：2330 遞增 → self=1、與 CSV(1) 一致；1101 遞減 → self=0、與 CSV(0) 一致
    assert by_code["2330"]["fields"]["w55"]["self"] == 1.0
    assert by_code["2330"]["fields"]["w55"]["status"] == "match"
    assert by_code["1101"]["fields"]["w55"]["self"] == 0.0
    assert by_code["1101"]["fields"]["w55"]["status"] == "match"

    # 三個 blocked 欄：self 恆 None、status 恆 self_na（本案不自算）
    for f in ("est_profit", "mu_score", "mu_value"):
        assert by_code["2330"]["fields"][f]["self"] is None
        assert by_code["2330"]["fields"][f]["status"] == "self_na"
        assert f in out["blocked_reason"]

    # 容差揭露來自 analysis 常數（防前端另寫一份）
    from stocks_power_rich import analysis
    assert out["tolerances"]["SELFCHECK_TOL"] == analysis.SELFCHECK_TOL

    # coverage：w55 兩檔皆可自算
    assert out["coverage"]["w55"]["computable"] == 2
    assert out["coverage"]["w55"]["total"] == 2


def test_build_selfcheck_strips_tw_suffix_before_self_source_lookup(tmp_path):
    """迴歸（production 全 0/N）：chip_snapshot 的 code 帶 .TW 後綴（XQ CSV 一律加 .TW），
    但自算來源（OHLC／月營收／集保）一律 bare code。join 前若不去後綴，每一列都 miss →
    所有欄位永遠「尚無自算」（正是 production 實況）。本測試刻意讓兩邊格式不一致，鎖住修正。"""
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    conn.execute("INSERT INTO chip_snapshot(snap_date,code,name,w55) "
                 "VALUES('2026-08-20','2330.TW','台積電',1.0)")   # 後綴碼
    ds = [(date(2026, 6, 1) + timedelta(days=n)).isoformat() for n in range(60)]
    for i, d in enumerate(ds):                                    # bare code、遞增 → w55=1
        conn.execute("INSERT INTO stock_ohlc(code,date,high,low,close) VALUES('2330',?,?,?,?)",
                     (d, 100 + i, 99 + i, 100 + i))
    conn.commit()
    out = selfcheck.build_selfcheck(conn, "2026-08-20")
    row = out["rows"][0]
    assert row["code"] == "2330.TW"                       # 顯示／個股連結仍用原始後綴碼
    assert row["fields"]["w55"]["self"] == 1.0            # 去後綴後 join 命中 → 自算成功
    assert row["fields"]["w55"]["status"] == "match"      # CSV w55=1 與自算 1 一致
    assert out["coverage"]["w55"]["computable"] == 1


def test_build_selfcheck_holder_drop_ratio_self_from_custody(tmp_path):
    """人數降比是集保自算的另一半（custody_change_map 本來就回傳 holder_drop_ratio）。
    自算值＝(本週總持股人數−上週)/上週×100，與 CSV 對照。"""
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    conn.execute("INSERT INTO chip_snapshot(snap_date,code,name,holder_drop_ratio) "
                 "VALUES('2026-08-20','2330.TW','台積電',-1.0)")
    # 兩週集保，總持股人數 990 → 1000 前一週（降 1%）；big400 兩週皆有讓週被視為完整
    for w, th in (("2026-08-14", 990.0), ("2026-08-07", 1000.0)):
        conn.execute("INSERT INTO custody_dist(week,code,big400_pct,total_holders) VALUES(?,?,?,?)",
                     (w, "2330", 50.0, th))
    conn.commit()
    cell = selfcheck.build_selfcheck(conn, "2026-08-20")["rows"][0]["fields"]["holder_drop_ratio"]
    assert cell["self"] == -1.0        # (990-1000)/1000*100
    assert cell["status"] == "match"   # 與 CSV −1.0 一致


def test_build_selfcheck_custody_diag_two_weeks(tmp_path):
    """大戶增比要靠 as_of 前最近兩週集保；診斷回報用了哪兩週＋big400_pct 交集檔數，
    讓 production 頁面直接看得出集保覆蓋（為何大戶增比只亮少數檔）。"""
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    conn.execute("INSERT INTO chip_snapshot(snap_date,code,name) VALUES('2026-08-20','2330.TW','台積電')")
    for w in ("2026-08-14", "2026-08-07"):
        for code in ("2330", "1101"):
            conn.execute("INSERT INTO custody_dist(week,code,big400_pct,total_holders) VALUES(?,?,?,?)",
                         (w, code, 50.0, 1000.0))
    conn.commit()
    diag = selfcheck.build_selfcheck(conn, "2026-08-20")["custody_diag"]
    assert diag["weeks"] == ["2026-08-14", "2026-08-07"]
    assert diag["overlap"] == 2


def test_build_selfcheck_custody_diag_flags_single_week(tmp_path):
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    conn.execute("INSERT INTO chip_snapshot(snap_date,code,name) VALUES('2026-08-20','2330.TW','台積電')")
    conn.execute("INSERT INTO custody_dist(week,code,big400_pct,total_holders) VALUES('2026-08-14','2330',50.0,1000.0)")
    conn.commit()
    diag = selfcheck.build_selfcheck(conn, "2026-08-20")["custody_diag"]
    assert len(diag["weeks"]) == 1
    assert diag["overlap"] == 0


def test_build_selfcheck_defaults_to_latest_snap_date(tmp_path):
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    _seed(conn)
    conn.execute("INSERT INTO chip_snapshot(snap_date,code,name) VALUES('2026-08-21','2317','鴻海')")
    conn.commit()
    out = selfcheck.build_selfcheck(conn, None)     # 不帶 date → 最新
    assert out["date"] == "2026-08-21"
    assert out["dates"][0] == "2026-08-21"           # 新到舊

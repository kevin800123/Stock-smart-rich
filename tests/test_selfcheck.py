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
                             "trust_3d", "foreign_3d", "lan_score", "est_profit", "mu_score", "mu_value"]
    by_code = {r["code"]: r for r in out["rows"]}

    # rev_yoy：自算來自 revenue_yoy_map（本測試沒建月營收 → self None → self_na），不炸
    assert by_code["2330"]["fields"]["rev_yoy"]["status"] in ("match", "diff", "self_na")

    # w55：2330 遞增 → self=1、與 CSV(1) 一致；1101 遞減 → self=0、與 CSV(0) 一致
    assert by_code["2330"]["fields"]["w55"]["self"] == 1.0
    assert by_code["2330"]["fields"]["w55"]["status"] == "match"
    assert by_code["1101"]["fields"]["w55"]["self"] == 0.0
    assert by_code["1101"]["fields"]["w55"]["status"] == "match"

    # 10 欄全部接上自算，BLOCKED_REASON 空。本測試沒建季報/月營收 → est_profit/mu_score/mu_value
    # 的 self 為 None，但那是「資料不足」而非「blocked」。
    assert out["blocked_reason"] == {}
    for f in ("est_profit", "mu_score", "mu_value"):
        assert by_code["2330"]["fields"][f]["self"] is None

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


def test_build_selfcheck_institutional_3d_self_from_stock_flow(tmp_path):
    """投信/外資近3日是木質缺的最後兩個籌碼訊號，自算＝stock_flow_daily 近3交易日
    trust_lots/foreign_lots 加總，對照 CSV 的 投三/外三。chip_snapshot 帶後綴、
    stock_flow_daily 為 bare code——同樣走去後綴 join（同其他自算來源）。"""
    from stocks_power_rich.db import bulk_upsert_stock_flow
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    conn.execute("INSERT INTO chip_snapshot(snap_date,code,name,trust_3d,foreign_3d) "
                 "VALUES('2026-08-20','2330.TW','台積電',6.0,3.0)")
    bulk_upsert_stock_flow(conn, "2026-08-18", "TWSE", {"2330": {"trust_lots": 1, "foreign_lots": 2}})
    bulk_upsert_stock_flow(conn, "2026-08-19", "TWSE", {"2330": {"trust_lots": 2, "foreign_lots": -3}})
    bulk_upsert_stock_flow(conn, "2026-08-20", "TWSE", {"2330": {"trust_lots": 3, "foreign_lots": 4}})
    conn.commit()
    fields = selfcheck.build_selfcheck(conn, "2026-08-20")["rows"][0]["fields"]
    assert fields["trust_3d"]["self"] == 6.0        # 1+2+3
    assert fields["trust_3d"]["status"] == "match"  # CSV 6.0
    assert fields["foreign_3d"]["self"] == 3.0      # 2-3+4
    assert fields["foreign_3d"]["status"] == "match"  # CSV 3.0


def test_build_selfcheck_lan_score_self_from_financials(tmp_path):
    """財報分：自算 lan_score（蘭質 15 項）從 stock_financials 組裝所有 _LAN_USED 指標，
    對照 CSV 蘭質（chip_snapshot.lan_score）。這是木質的財報主幹、季報回補後解鎖。"""
    from stocks_power_rich.db import bulk_upsert_financials
    from stocks_power_rich import analysis
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    conn.execute("INSERT INTO chip_snapshot(snap_date,code,name,lan_score) "
                 "VALUES('2026-08-20','2330.TW','台積電',8.0)")
    quarters = ["2026Q2", "2026Q1", "2025Q4", "2025Q3", "2025Q2", "2025Q1", "2024Q4", "2024Q3"]
    for ind in analysis._LAN_USED:      # 每個指標 8 季（capex 最遠用到 [7]）
        bulk_upsert_financials(conn, ind, {"2330": {q: float(10 + i) for i, q in enumerate(quarters)}})
    conn.commit()

    fields = selfcheck.build_selfcheck(conn, "2026-08-20")["rows"][0]["fields"]
    fin = {ind: [float(10 + i) for i in range(8)] for ind in analysis._LAN_USED}  # 新到舊
    expected = analysis.lan_score(fin)["score"]
    assert fields["lan_score"]["self"] == expected   # 與獨立呼叫 lan_score 一致（去後綴 join 命中）
    assert fields["lan_score"]["csv"] == 8.0          # CSV 蘭質
    assert fields["lan_score"]["status"] in ("match", "diff")
    # 木質也解鎖：無籌碼（沒建集保/3日）→ 木質＝財報分＋0 bonus；CSV 無木質欄 → csv_na
    assert fields["mu_score"]["self"] == expected
    assert fields["mu_score"]["status"] == "csv_na"


def test_build_selfcheck_est_profit_self_from_call_le(tmp_path):
    """推估EPS：自算 Call_LE 從月營收(億元)＋季毛利率＋近4季營業費用/所得稅(仟元→百萬元)＋
    股數(股本×1e7) 組裝，對照 CSV 推估獲利。用 test_analysis_call_le 的合成值鎖住單位換算。"""
    from stocks_power_rich.db import bulk_upsert_financials, bulk_upsert_revenue
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    conn.execute("INSERT INTO chip_snapshot(snap_date,code,name,est_profit,capital) "
                 "VALUES('2026-08-20','2330.TW','台積電',1.5,100.0)")   # capital 100億 → shares 1e9
    yms = ["2026-07", "2026-06", "2026-05", "2026-04", "2026-03", "2026-02"]   # 新到舊
    revs = [1e6, 1e6, 1e6, 8e5, 8e5, 8e5]   # 仟元＝億元×1e5 → 10,10,10,8,8,8 億
    for ym, rv in zip(yms, revs):
        bulk_upsert_revenue(conn, "TWSE", {"2330": {"year_month": ym, "report_date": ym + "-10", "revenue": rv}})
    q4 = ["2026Q2", "2026Q1", "2025Q4", "2025Q3"]
    bulk_upsert_financials(conn, "gross_margin", {"2330": {"2026Q2": 65.0, "2026Q1": 60.0}})
    bulk_upsert_financials(conn, "opex", {"2330": dict(zip(q4, [2e5, 1.5e5, 1.8e5, 1.9e5]))})   # 仟元＝百萬×1000
    bulk_upsert_financials(conn, "income_tax", {"2330": dict(zip(q4, [1e5, 8e4, 9e4, 9.5e4]))})
    conn.commit()

    cell = selfcheck.build_selfcheck(conn, "2026-08-20")["rows"][0]["fields"]["est_profit"]
    assert cell["self"] == 1.575    # 與 test_analysis_call_le 手算合成值一致（單位換算正確）
    assert cell["csv"] == 1.5


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


# ========== 自算籌碼/基本選股（全市場自算池，零 CSV 依賴） ==========

def _seed_full_market(conn):
    """2330 全條件通過（財報/月營收/集保/OHLC/成交額齊全）；1101 只有 大戶增比>0＋成交額
    （財報缺 → 推估EPS/木率算不出、人數降比>0 → 不入選，但仍進熱力圖）。"""
    from stocks_power_rich.db import bulk_upsert_financials, bulk_upsert_revenue
    from stocks_power_rich import analysis
    # 2330：60 根遞增日 K → W55=1；價格供本業PE
    ds = [(date(2026, 6, 1) + timedelta(days=n)).isoformat() for n in range(60)]
    for i, d in enumerate(ds):
        conn.execute("INSERT INTO stock_ohlc(code,date,high,low,close) VALUES('2330',?,?,?,?)",
                     (d, 100 + i, 99 + i, 100 + i))
    # 本週(08-17/18/19) / 上週(08-10/11/12) 成交額（只寫 amount、不含 high/low → 不進 w55 序列）
    for d, a in (("2026-08-17", 100), ("2026-08-18", 200), ("2026-08-19", 300),
                 ("2026-08-10", 100), ("2026-08-11", 100), ("2026-08-12", 100)):
        conn.execute("INSERT INTO stock_ohlc(code,date,close,amount_twd) VALUES('2330',?,?,?)", (d, 100, a))
    # 月營收：yoy>0 且 6 個月供 Call_LE（10,10,10,8,8,8 億）
    yms = ["2026-07", "2026-06", "2026-05", "2026-04", "2026-03", "2026-02"]
    revs = [1e6, 1e6, 1e6, 8e5, 8e5, 8e5]
    for ym, rv in zip(yms, revs):
        bulk_upsert_revenue(conn, "TWSE", {"2330": {"year_month": ym, "report_date": ym + "-10",
                                                     "revenue": rv, "yoy_pct": 44.0}})
    # 季報：_LAN_USED 8 季（完美形態→蘭質高）＋ opex/income_tax 供 Call_LE
    perfect = {
        "revenue":       [500, 400, 300, 200, 100, 100, 100, 100],
        "pretax_income": [200, 100, 100, 100, 100, 100, 100, 100],
        "gross_margin":  [50, 45, 40, 35, 30, 30, 30, 30],
        "ar_turnover":   [10, 9, 8, 7, 6, 6, 6, 6],
        "inv_turnover":  [5, 4, 3, 2, 1, 1, 1, 1],
        "debt_ratio":    [30, 40, 35, 50, 45, 45, 45, 45],
        "ocf":           [100, 80, 60, 40, 20, 20, 20, 20],
        "net_income":    [50, 40, 30, 20, 10, 10, 10, 10],
        "roe":           [20, 15, 10, 8, 5, 5, 5, 5],
        "capex":         [-100, -100, -100, -50, -50, -50, -50, -50],
    }
    quarters = ["2026Q2", "2026Q1", "2025Q4", "2025Q3", "2025Q2", "2025Q1", "2024Q4", "2024Q3"]
    for ind, series in perfect.items():
        bulk_upsert_financials(conn, ind, {"2330": {q: float(v) for q, v in zip(quarters, series)}})
    q4 = ["2026Q2", "2026Q1", "2025Q4", "2025Q3"]
    bulk_upsert_financials(conn, "opex", {"2330": dict(zip(q4, [2e5, 1.5e5, 1.8e5, 1.9e5]))})
    bulk_upsert_financials(conn, "income_tax", {"2330": dict(zip(q4, [1e5, 8e4, 9e4, 9.5e4]))})
    # 集保兩週：2330 大戶增比 +1(50>49)、人數降比 -1(990<1000)；1101 大戶增比 +1 但人數降比 +1
    for w, b2330, th2330, b1101, th1101 in (("2026-08-14", 50.0, 990.0, 50.0, 1010.0),
                                            ("2026-08-07", 49.0, 1000.0, 49.0, 1000.0)):
        conn.execute("INSERT INTO custody_dist(week,code,big400_pct,total_holders) VALUES(?,?,?,?)",
                     (w, "2330", b2330, th2330))
        conn.execute("INSERT INTO custody_dist(week,code,big400_pct,total_holders) VALUES(?,?,?,?)",
                     (w, "1101", b1101, th1101))
    # 1101：本週成交額 500、上週 0（WoW 無從算）；無財報 → 推估EPS/木率 None
    conn.execute("INSERT INTO stock_ohlc(code,date,close,amount_twd) VALUES('1101','2026-08-17',100,500)")
    # 細分類（子產業）只存在 XQ CSV（chip_snapshot），code 帶 .TW（CSV 慣例）。2330 標「IC設計」，
    # 1101 沒標 → 退回 universe 的官方類股「水泥」。
    conn.execute("INSERT INTO chip_snapshot(snap_date,code,name,sub_industry) "
                 "VALUES('2026-08-20','2330.TW','台積電','IC設計')")
    conn.commit()


_UNIVERSE = {
    "2330": {"sector": "半導體", "name": "台積電", "shares": 1e9},
    "1101": {"sector": "水泥", "name": "台泥", "shares": 5e8},
}


def test_build_self_screen_filters_sorts_and_builds_heatmap(tmp_path):
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    _seed_full_market(conn)

    out = selfcheck.build_self_screen(conn, "2026-08-19", _UNIVERSE, 0, 0)

    # 篩選：只有 2330 全條件通過（1101 人數降比>0、且無推估EPS → 淘汰）
    assert [r["code"] for r in out["rows"]] == ["2330"]
    row = out["rows"][0]
    assert row["name"] == "台積電"
    assert row["sector"] == "IC設計"                # 細分類流進入選列（drill-down 才對得上泡泡）
    assert set(row["vals"]) == {"rev_yoy", "w55", "big_holder_ratio", "holder_drop_ratio",
                                "trust_3d", "foreign_3d", "lan_score", "est_profit",
                                "mu_score", "mu_value"}
    assert row["vals"]["mu_value"] is not None and row["vals"]["mu_value"] > 0

    # 分群用細分類（子產業）：2330→「IC設計」(CSV sub_industry)、1101→「水泥」(退回官方類股)
    hm = {g["sector"]: g for g in out["heatmap"]}
    assert set(hm) == {"IC設計", "水泥"}
    assert hm["IC設計"]["amount"] == 600            # 2330 本週 100+200+300（成交額仍保留給 tooltip）
    assert hm["IC設計"]["wow_pct"] == 100.0         # (600-300)/300*100，同期比較
    assert hm["水泥"]["amount"] == 500              # 1101 本週
    assert hm["水泥"]["wow_pct"] is None            # 上週無成交額 → 算不出

    # 大戶淨買進金額估計＝大戶增比% × 市值（股數×收盤）。2330：1%×(1e9×159)=1.59e9；
    # 1101 無 55 根 K（只有成交額列、無 high/low）→ 無收盤 → 算不出、該類股 buy_value=0。
    assert hm["IC設計"]["buy_value"] == 1590000000
    assert hm["水泥"]["buy_value"] == 0
    assert out["heatmap"][0]["sector"] == "IC設計"  # heatmap 依 buy_value 由大到小

    # coverage：universe 2、大戶增比>0 2、有成交額 2、能算市值 1、有細分類 1（只有 2330 標 IC設計）、入選 1
    assert out["coverage"] == {"universe": 2, "big_holder_pos": 2, "with_amount": 2,
                               "with_mcap": 1, "with_subindustry": 1, "picked": 1}


def test_build_self_screen_thresholds_exclude_by_mu_value(tmp_path):
    """木率門檻嚴格 >：把門檻設到高於該檔實際木率 → 被排除（驗證門檻可調且生效）。"""
    conn = get_connection(str(tmp_path / "t.sqlite"))
    init_db(conn)
    _seed_full_market(conn)
    mv = selfcheck.build_self_screen(conn, "2026-08-19", _UNIVERSE, 0, 0)["rows"][0]["vals"]["mu_value"]
    hi = selfcheck.build_self_screen(conn, "2026-08-19", _UNIVERSE, mv + 1, 0)
    assert [r["code"] for r in hi["rows"]] == []

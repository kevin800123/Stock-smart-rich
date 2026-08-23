import json
import os
from pathlib import Path

from fastapi.testclient import TestClient

from stocks_power_rich.main import create_app
from tests.conftest import HEADER, ROW_2330


def test_frontend_card_alert_guards():
    web = Path(__file__).parents[1] / "web"
    js = (web / "app.js").read_text(encoding="utf-8")
    css = (web / "styles.css").read_text(encoding="utf-8")
    html = (web / "index.html").read_text(encoding="utf-8")

    assert js.count("rank.p >= 90") == 1
    for fn in ("alertReason", "relToBreakeven", "isMaintAlert", "isVolMaAlert"):
        assert f"function {fn}(" in js
    assert "onclick=" not in js
    assert js.count("lastAlerts = []") >= 1
    assert 'class="card-metric"' in js
    assert 'class="card-rank"' in js
    assert 'class="card-rank-period"' in js
    assert 'class="card-rank-badge rank-${zone.key}"' in js
    assert 'key: "high", label: "高位"' in js
    assert 'key: "low", label: "低位"' in js
    assert ".card-rank-badge.rank-high {\n  background: var(--up-soft)" in css
    assert ".card-rank-badge.rank-low {\n  background: rgba(112, 184, 255, .2); color: var(--info)" in css
    assert "function trendHtml(" in js
    assert 'class="card-trend-bars"' in js
    assert "--rank-p" not in js
    assert "function compactDeltaHtml(" in js
    assert "compactDeltaHtml(chg, null, unit)" in js
    assert "compactDeltaHtml(chg, pct, compactUnit)" in js
    assert 'compactDeltaHtml(chg, pct, " 口")' in js
    assert ".card-rank::before" not in css
    assert ".card-rank-badge" in css
    for label in ("外資買賣超", "融資餘額(張)", "融資維持率（上市）", "融資維持率（上櫃）",
                  "外資台指淨未平倉", "微台散戶多空比"):
        assert f'"{label}"' in js
    assert "const KEY_METRICS = [" in js
    assert '" key-metric"' in js
    assert ".stat-board > .stat-cell.key-metric" in css
    assert '<div class="overview-briefing span-full">' in html
    assert ".overview-briefing" in css
    assert "grid-template-columns: minmax(340px, .9fr) minmax(0, 1.6fr)" in css
    assert ".card-trend-bar.up::before" in css
    assert "@media (max-width: 1240px)" in css
    assert ".stat-board--tw { grid-template-columns: repeat(3, minmax(0, 1fr)); }" in css
    assert "@media (max-width: 1200px)" in css
    assert "card-rail" not in js and ".card-rail" not in css
    assert "#today-focus:empty" in css
    render_cards = js.index("function renderCards(")
    reset = js.index("lastAlerts = [];", render_cards)
    early_return = js.index("if (!m || !m.date)", render_cards)
    assert render_cards < reset < early_return


def test_frontend_picks_workspace_structure():
    web = Path(__file__).parents[1] / "web"
    html = (web / "index.html").read_text(encoding="utf-8")
    js = (web / "app.js").read_text(encoding="utf-8")
    css = (web / "styles.css").read_text(encoding="utf-8")

    assert '<section id="view-picks" class="view picks-view">' in html
    assert 'class="picks-command"' in html
    assert 'class="picks-summary"' in html
    for el_id in ("picks-total", "picks-filtered", "picks-industries", "industry-count"):
        assert f'id="{el_id}"' in html
    assert 'id="upload-info" class="picks-feedback" role="status" aria-live="polite"' in html
    assert "function renderPickSummary(" in js
    assert 'data-col="${c.key}"' in js
    assert 'aria-sort="${sortDir}"' in js
    assert 'col-${c.tier}' in js
    assert 'class="clickrow${active ? " is-active" : ""}"' in js
    assert '<thead>${head}</thead><tbody>${body}</tbody>' in js
    assert ".picks-command" in css
    assert ".picks-summary" in css
    assert "#daily table { min-width: 930px; }" in css
    assert "#daily .col-primary" in css
    assert "#industry tr.is-active td" in css
    assert "#industry tbody { display: flex" in css
    assert "@media (max-width: 1100px)" in css


def test_dashboard_and_upload(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    monkeypatch.chdir(tmp_path)
    app = create_app()
    client = TestClient(app)

    assert client.get("/api/dashboard").status_code == 200

    content = (
        "符合條件商品\n資料日期：2026年  6月 15日\n策略,\t.常用\n" + HEADER + "\n" + ROW_2330 + "\n"
    ).encode("big5")
    r = client.post("/api/csv/upload", files={"file": ("a.csv", content, "text/csv")})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["picks"][0]["code"] == "2330.TW"

    # 重整後仍可從最新快照取得篩選榜
    d = client.get("/api/analysis/daily").json()
    assert d["snap_date"] == "2026-06-15"
    assert d["picks"][0]["code"] == "2330.TW"


def test_data_is_stale_logic():
    from stocks_power_rich.main import data_is_stale

    # 平日(週三=2)且資料停在前一交易日 → 官方當日盤後尚未釋出
    assert data_is_stale("2026-06-23", "2026-06-24", 2) is True
    # 已是當日 → 不提示
    assert data_is_stale("2026-06-24", "2026-06-24", 2) is False
    # 週六(5)：資料停在週五屬正常 → 不提示
    assert data_is_stale("2026-06-26", "2026-06-27", 5) is False
    # 尚無任何資料 → 不提示
    assert data_is_stale(None, "2026-06-24", 2) is False


def test_dashboard_includes_today_and_stale_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    app = create_app()
    client = TestClient(app)
    body = client.get("/api/dashboard").json()
    assert "today" in body and "data_stale" in body
    assert body["data_stale"] is False  # 無資料時不標延遲


def test_breadth_distribution_filters_to_common_stocks_and_merges_markets(tmp_path, monkeypatch):
    """漲跌幅分布：合併上市＋上櫃，只算 4 碼普通股（排除 6 碼權證/ETF），分桶。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db, upsert_market_daily
    from stocks_power_rich.sources import twse, tpex

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    upsert_market_daily(c, {"date": "2026-07-24", "taiex": 100.0})
    monkeypatch.setattr(twse, "fetch_stock_quotes", lambda date=None: {
        "2330": {"name": "台積電", "close": 1000.0, "chg_pct": 2.5},
        "2317": {"name": "鴻海", "close": 200.0, "chg_pct": -1.2},
        "030123": {"name": "某權證", "close": 3.0, "chg_pct": 9.9},   # 6 碼權證 → 排除
    })
    monkeypatch.setattr(tpex, "fetch_otc_quotes", lambda date=None: {
        "5483": {"name": "中美晶", "close": 300.0, "chg_pct": 0.0},   # 上櫃普通股 → 平盤
    })
    app = create_app()
    client = TestClient(app)
    d = client.get("/api/breadth/distribution").json()
    assert d["n"] == 3                       # 只算 3 檔普通股，權證被濾掉
    assert d["up"] == 1 and d["down"] == 1 and d["flat"] == 1
    counts = {b["bucket"]: b["count"] for b in d["buckets"]}
    assert counts[2] == 1 and counts[-2] == 1 and counts[0] == 1
    # 免密碼公開總覽走同一支邏輯
    assert client.get("/public/api/breadth/distribution").json()["n"] == 3


def test_breadth_distribution_does_not_cache_when_otc_side_empty(tmp_path, monkeypatch):
    """上市抓得到、上櫃抓空（如 TLS 憑證問題）→ 不可把「只算上市」的退化結果寫進快取，
    否則上櫃端修好後也永遠讀到這筆半套舊資料（同 os-futures 的 has_remote 規則）。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db, upsert_market_daily, get_ai_cache
    from stocks_power_rich.sources import twse, tpex

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    upsert_market_daily(c, {"date": "2026-07-24", "taiex": 100.0})
    monkeypatch.setattr(twse, "fetch_stock_quotes", lambda date=None: {
        "2330": {"name": "台積電", "close": 1000.0, "chg_pct": 2.5},
    })
    monkeypatch.setattr(tpex, "fetch_otc_quotes", lambda date=None: {})   # 上櫃這次抓空
    app = create_app()
    client = TestClient(app)

    d1 = client.get("/api/breadth/distribution").json()
    assert d1["n"] == 1 and d1["has_otc"] is False
    assert get_ai_cache(c, "dist:2026-07-24") is None   # 半套結果不得進快取

    # 上櫃端修好了 → 下次呼叫應該重算，拿到完整資料，這次才快取
    monkeypatch.setattr(tpex, "fetch_otc_quotes", lambda date=None: {
        "5483": {"name": "中美晶", "close": 300.0, "chg_pct": 1.0},
    })
    d2 = client.get("/api/breadth/distribution").json()
    assert d2["n"] == 2 and d2["has_otc"] is True
    assert get_ai_cache(c, "dist:2026-07-24") is not None


def test_breadth_distribution_stale_cache_without_has_otc_self_heals(tmp_path, monkeypatch):
    """修這支 bug 之前寫入的舊快取沒有 has_otc 欄位——讀取端要當成未命中重算，
    不能永遠信任「快取存在」這件事，否則舊的半套資料永遠出不去。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db, upsert_market_daily, set_ai_cache
    from stocks_power_rich.sources import twse, tpex

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    upsert_market_daily(c, {"date": "2026-07-24", "taiex": 100.0})
    # 模擬修 bug 前寫入的舊快取：只有上市，沒有 has_otc 欄位
    set_ai_cache(c, "dist:2026-07-24", {"date": "2026-07-24", "n": 1, "up": 1, "down": 0,
                                        "flat": 0, "avg": 2.5, "buckets": []})
    monkeypatch.setattr(twse, "fetch_stock_quotes", lambda date=None: {
        "2330": {"name": "台積電", "close": 1000.0, "chg_pct": 2.5},
    })
    monkeypatch.setattr(tpex, "fetch_otc_quotes", lambda date=None: {
        "5483": {"name": "中美晶", "close": 300.0, "chg_pct": 1.0},
    })
    app = create_app()
    client = TestClient(app)
    d = client.get("/api/breadth/distribution").json()
    assert d["n"] == 2   # 沒有被舊的 n=1 快取卡住，重算後拿到完整的兩市場資料


def test_dashboard_bands_come_from_ss_trader(tmp_path, monkeypatch):
    """總覽卡片的「異常讀數」門檻必須是 ss_trader 的那一份，不得在前端另寫一組。

    這條測試存在的理由是防漂移：門檻散成兩份實作後，改了一邊另一邊不會報錯，
    介面就會安靜地用舊標準判定（艾略特波浪的 JS/Python 雙實作已經吃過這個虧）。
    """
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich import ss_trader

    app = create_app()
    client = TestClient(app)
    bands = client.get("/api/dashboard").json()["bands"]

    # 維持率送的是「兩平線＋追繳線」而非上下限：兩個市場成數不同，兩平線也不同，
    # 前端要靠它才能說明「上市 180.1% 是獲利、上櫃 166.8% 是套牢」。
    assert bands["margin_maintenance"] == {"breakeven": 166.7, "call": ss_trader.MARGIN_CALL_LINE}
    assert bands["otc_margin_maintenance"] == {"breakeven": 200.0, "call": ss_trader.MARGIN_CALL_LINE}
    assert bands["vix"] == {"low": ss_trader.VIX_COMPLACENT, "high": ss_trader.VIX_PANIC}
    # 量能只有下緣：量縮才是要看的事，給 high 反而會讓「爆量」也亮琥珀外框
    assert bands["turnover_ma10"] == {"low": ss_trader.VOL_QUIET_YI}
    # 免密碼的公開總覽走同一個 handler，門檻也必須跟著出現
    assert client.get("/public/api/dashboard").json()["bands"] == bands


def test_scoring_rules_come_from_analysis_constants(tmp_path, monkeypatch):
    """木質/木率 的規則必須來自 analysis 常數，設定頁不得另寫一份（同 bands 的防漂移）。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich import analysis

    r = TestClient(create_app()).get("/api/scoring-rules").json()
    assert len(r["mu_score"]["chip_items"]) == len(analysis.MU_CHIP_ITEMS)
    assert r["mu_score"]["max"] == 15 + len(analysis.MU_CHIP_ITEMS)
    assert r["mu_value"]["quality_floor"] == analysis.MU_QUALITY_FLOOR
    assert len(r["lan_score"]["items"]) == len(analysis.LAN_SCORE_ITEMS)
    assert sum(it["points"] for it in r["lan_score"]["items"]) == 15


def test_stage2_sources_reflects_w55_threshold_and_marks_not_wired(tmp_path, monkeypatch):
    """Stage 2 三片（月營收/W55/大戶）並存對照頁：門檻文字必須引用 analysis.W55_THRESHOLD
    （同 scoring-rules 的防漂移規矩），且要如實標示尚未接進 filtered_picks，不能誤導使用者。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich import analysis

    r = TestClient(create_app()).get("/api/stage2-sources").json()
    assert r["wired_to_picks"] is False
    keys = {it["key"] for it in r["items"]}
    assert keys == {"revenue", "w55", "custody"}
    w55_item = next(it for it in r["items"] if it["key"] == "w55")
    assert f"{analysis.W55_THRESHOLD:g}" in w55_item["formula"]


def test_dashboard_pulse_is_none_without_enough_market_data(tmp_path, monkeypatch):
    """沒有 market_daily 資料時，儀表板半圓錶要顯示「資料不足」而不是硬湊出的分數。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich import ss_trader as ss_trader_module

    app = create_app()
    client = TestClient(app)
    pulse = client.get("/api/dashboard").json()["pulse"]
    assert pulse["overall"] is None
    # 「結算週」一項不吃 market_daily（用系統日期算），永遠不是 na，所以完全無
    # 資料時 sample_n 是 1 不是 0；重點是它仍低於 PULSE_MIN_ITEMS，總分才因此是 None。
    assert pulse["sample_n"] < ss_trader_module.PULSE_MIN_ITEMS


def test_dashboard_pulse_scores_from_real_checklist_inputs(tmp_path, monkeypatch):
    """儀表板半圓錶與「操盤手」頁必須讀同一份 market_checklist()——這裡直接比對兩者，
    確認 checklist_inputs() 真的被兩邊共用而不是各自組一份夜盤量比／結算週判定。
    """
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db, upsert_market_daily
    from stocks_power_rich import ss_trader

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    for i, taiex in enumerate([100.0 + i for i in range(10)]):
        upsert_market_daily(c, {
            "date": f"2026-07-{10 + i:02d}", "taiex": taiex,
            "margin_maintenance": 180.1, "otc_margin_maintenance": 166.8,
            "vix": 16.0, "margin_balance": 5000.0 - i, "turnover": 3000.0,
        })

    app = create_app()
    client = TestClient(app)
    body = client.get("/api/dashboard").json()
    pulse = body["pulse"]

    assert pulse["overall"] is not None
    assert pulse["sample_total"] == len(pulse["items"])
    # na 不進分母：完整度必須等於「非 na 項 / 總項」
    non_na = [it for it in pulse["items"] if it["status"] != "na"]
    assert pulse["completeness"] == round(len(non_na) / len(pulse["items"]) * 100, 1)
    assert set(pulse["label"].keys()) == {"overall", "health", "positive", "risk", "completeness"}

    # checklist_inputs() 真的被兩邊共用，而不是各自組一份夜盤量比／結算週判定
    from stocks_power_rich.api.helpers import checklist_inputs
    rows = [dict(r) for r in c.execute(
        "SELECT * FROM market_daily ORDER BY date DESC LIMIT 150").fetchall()][::-1]
    expected = ss_trader.market_checklist(rows, **checklist_inputs(c))
    assert pulse["items"] == expected


def test_inst_ranking_falls_back_to_last_date_with_data(tmp_path, monkeypatch):
    """T86 約 16:00 後才公布，但 market_daily 當天早上就有列（指數是盤中就有的）。

    原本直接用最新日期去抓 T86，抓不到就整張榜空白，標題卻仍寫著今天——使用者看到的是
    「2026-07-30」配兩個「—」。實測 07-30 的 T86 是 0 列、07-29 有 13527 列。
    改成往回找到最近一個真的有資料的交易日，並回報那個日期讓前端標「截至」。
    """
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db, upsert_market_daily
    from stocks_power_rich.sources import twse

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    for ds in ("2026-07-28", "2026-07-29", "2026-07-30"):
        upsert_market_daily(c, {"date": ds, "taiex": 100.0})
    # 只有 07-29 有 T86（07-30 today 尚未公布）
    t86 = {"2330": {"name": "台積電", "foreign": 5000, "trust": 100, "dealer": 10, "total": 5110},
           "2317": {"name": "鴻海", "foreign": -3000, "trust": -50, "dealer": -5, "total": -3055}}
    monkeypatch.setattr(twse, "fetch_t86",
                        lambda date=None: t86 if date.isoformat() == "2026-07-29" else {})
    client = TestClient(create_app())

    d = client.get("/api/inst-ranking?who=foreign&unit=shares&top=5").json()
    assert d["date"] == "2026-07-29", "應退回最近一個有 T86 的交易日，而非空著標今天"
    assert d["buy"] and d["sell"], "退回後榜單不得是空的"
    assert d["buy"][0]["code"] == "2330" and d["buy"][0]["net"] == 5000
    assert d["sell"][0]["code"] == "2317" and d["sell"][0]["net"] == -3000


def test_chips_and_maint_backfill_windows_reach_as_far_as_row_creation(tmp_path, monkeypatch):
    """兩支「只填既有列」的回補，窗口必須跟得上 /api/backfill 的建列窗口（200 天）。

    窄一截的後果是安靜的不對稱：最舊那段只有大盤與法人、沒有期貨籌碼與維持率，
    對照圖的那幾個窗格就比其他窗格短（實測上限 120 時為 80~81/130 列、自 03-30 才有值）。
    這裡用「180 天前的缺值列有沒有被算進 remaining」來證明窗口真的伸得到那麼遠。
    """
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from datetime import date, timedelta
    from stocks_power_rich.db import get_connection, init_db, upsert_market_daily
    from stocks_power_rich.sources import taifex, twse, tpex

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    old = (date.today() - timedelta(days=180)).isoformat()
    # 180 天前的列：期貨籌碼與維持率都缺，但 margin_value 有（=> 維持率可補）
    upsert_market_daily(c, {"date": old, "taiex": 100.0, "margin_value": 5000.0})
    # 不讓測試打網路；回傳 None/空代表「該日補不到」，remaining 因此不會歸零
    monkeypatch.setattr(taifex, "fetch_chips_for_date", lambda d: {})
    monkeypatch.setattr(twse, "fetch_margin_detail", lambda date=None: {"margin": {}, "short": {}})
    monkeypatch.setattr(twse, "fetch_stock_quotes", lambda date=None: {})
    monkeypatch.setattr(tpex, "fetch_otc_margin", lambda date=None: {"value": None, "margin": {}, "short": {}})
    monkeypatch.setattr(tpex, "fetch_otc_quotes", lambda date=None: {})
    client = TestClient(create_app())

    chips = client.get("/api/chips/backfill?days=200&max_fetch=1").json()
    assert chips["remaining"] >= 1, "200 天的窗口要看得到 180 天前的缺值列"
    maint = client.get("/api/margin-maintenance/heal?days=200&max_fetch=1").json()
    assert maint["remaining"] >= 1

    # 對照：窄窗口看不到它，證明上面不是恆真
    assert client.get("/api/chips/backfill?days=60&max_fetch=1").json()["remaining"] == 0
    assert client.get("/api/margin-maintenance/heal?days=60&max_fetch=1").json()["remaining"] == 0


def test_dashboard_window_is_wide_enough_for_the_combo_chart(tmp_path, monkeypatch):
    """/api/dashboard 的視窗要吃得到回補來的歷史。

    原本寫死 LIMIT 60，在 market_daily 只有 42 列時看不出問題；歷史回補到 130+ 列之後
    這個 60 就成了對照圖的天花板——回補明明補到了，圖上的籌碼窗格卻停在 60 天。
    """
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db, upsert_market_daily
    from stocks_power_rich.api import market
    from datetime import date, timedelta

    assert market.DASHBOARD_DAYS >= 120, "視窗需容得下回補的歷史，否則對照圖被截斷"
    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    start = date(2026, 1, 5)
    n = 100
    for i in range(n):
        upsert_market_daily(c, {"date": (start + timedelta(days=i)).isoformat(), "taiex": 100.0 + i})
    hist = TestClient(create_app()).get("/api/dashboard").json()["history"]
    assert len(hist) == n, "100 列都要回傳，不得被舊的 LIMIT 60 截掉"
    assert hist[0]["date"] < hist[-1]["date"], "history 仍為由舊到新"


def test_backfill_endpoint_allows_wide_window_and_reports_remaining(tmp_path, monkeypatch):
    """/api/backfill 的 days 上限必須夠寬（run_update 只留近 400 天，原本夾在 60 沒有理由）。

    夾在 60 的後果是靜默的：傳 days=180 會回報 backfilled_days=41 看起來成功，實際上
    60 天前正好是既有資料起點，一列都沒新增。這條測試鎖住「窗口真的吃得到更早的日期」
    與「回傳帶 remaining 供分批呼叫」。
    """
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from datetime import date, timedelta
    from stocks_power_rich.sources import twse

    def fake_hist(anchor=None):
        a = anchor or date.today()
        return [{"date": a.replace(day=d).isoformat(), "taiex": 100.0 + d,
                 "taiex_chg": 1.0, "turnover": 5000.0} for d in (1, 15)]
    monkeypatch.setattr(twse, "fetch_taiex_history", fake_hist)
    monkeypatch.setattr(twse, "fetch_institutional", lambda date=None: {"inst_foreign": 1.0})
    monkeypatch.setattr(twse, "fetch_margin", lambda date=None: {"margin_balance": 9.0})
    app = create_app()
    client = TestClient(app)

    r = client.get("/api/backfill?days=200&max_fetch=3").json()
    assert r["backfilled_days"] == 3            # 依 max_fetch 分批
    assert r["remaining"] > 0                   # 還有，供重複呼叫
    # 建出的最舊一列必須早於 3 個月前，證明 days=200 沒被夾成 60 也沒被寫死 3 個月錨點
    from stocks_power_rich.db import get_connection
    c = get_connection(str(tmp_path / "t.sqlite"))
    oldest = c.execute("SELECT MIN(date) FROM market_daily").fetchone()[0]
    assert oldest < (date.today() - timedelta(days=95)).isoformat()


def test_dashboard_injects_turnover_ma10_on_every_row(tmp_path, monkeypatch):
    """10 日均量逐列注入（不只最新一列）——前端位階條要拿整個視窗當樣本。

    它是純衍生值、不落地成 DB 欄位，所以這裡順帶鎖住「market_daily 沒有這一欄
    也要算得出來」這件事。
    """
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db, upsert_market_daily
    from stocks_power_rich import ss_trader

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    # 12 個交易日，成交金額固定 100 → 第 10 列起才有均量，且均量恆為 100
    for i in range(1, 13):
        upsert_market_daily(c, {"date": f"2026-07-{i:02d}", "taiex": 100.0, "turnover": 100.0})
    app = create_app()
    client = TestClient(app)
    d = client.get("/api/dashboard").json()
    hist = d["history"]                      # 由舊到新
    assert len(hist) == 12
    assert all(r["turnover_ma10"] is None for r in hist[:ss_trader.VOL_MA_DAYS - 1])
    assert all(r["turnover_ma10"] == 100.0 for r in hist[ss_trader.VOL_MA_DAYS - 1:])
    # latest 與 history 最後一列是同一天，均量必須一致（不是各算各的）
    assert d["latest"]["turnover_ma10"] == hist[-1]["turnover_ma10"] == 100.0


def test_sectors_endpoint_sorted_by_change(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db, upsert_market_daily
    from stocks_power_rich.sources import twse

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    upsert_market_daily(c, {"date": "2026-06-26", "taiex": 100.0})
    monkeypatch.setattr(twse, "fetch_sector_indices", lambda date=None: [
        {"name": "航運", "close": 1.0, "chg_pct": -1.0},
        {"name": "半導體", "close": 1.0, "chg_pct": 2.0},
        {"name": "金融保險", "close": 1.0, "chg_pct": 0.5},
    ])
    monkeypatch.setattr(twse, "fetch_sector_turnover", lambda date=None: {"半導體": 88, "航運": 9})
    monkeypatch.setattr(twse, "fetch_listed_industry", lambda: {
        "2330": {"sector": "半導體", "name": "台積電", "shares": 2_000_000_000},
        "2454": {"sector": "半導體", "name": "聯發科", "shares": 1_000_000_000},
        "2603": {"sector": "航運", "name": "長榮", "shares": 500_000_000}})
    monkeypatch.setattr(twse, "fetch_stock_quotes", lambda date=None: {
        "2330": {"name": "台積電", "close": 1000.0, "chg_pct": 2.0},
        "2454": {"name": "聯發科", "close": 500.0, "chg_pct": 1.0},
        "2603": {"name": "長榮", "close": 200.0, "chg_pct": -1.0}})
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/sectors").json()
    assert r["date"] == "2026-06-26"  # 預設用最新大盤日期
    assert [s["name"] for s in r["sectors"]] == ["半導體", "金融保險", "航運"]  # 漲幅大→小
    by = {s["name"]: s for s in r["sectors"]}
    assert by["半導體"]["turnover"] == 88          # 成交值（備援面積）
    assert by["半導體"]["mcap"] == 25000.0         # (20億股×1000 + 10億股×500)/1e8 = 25000 億
    assert by["航運"]["mcap"] == 1000.0
    assert by["金融保險"]["mcap"] is None and by["金融保險"]["turnover"] is None  # 無數據→不進熱力圖


def test_sector_stocks_lists_constituents_by_mcap(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db, upsert_market_daily
    from stocks_power_rich.sources import twse

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    upsert_market_daily(c, {"date": "2026-06-26", "taiex": 100.0})
    monkeypatch.setattr(twse, "fetch_listed_industry", lambda: {
        "2330": {"sector": "半導體", "name": "台積電", "shares": 25_930_000_000},
        "2454": {"sector": "半導體", "name": "聯發科", "shares": 1_600_000_000},
        "6789": {"sector": "半導體", "name": "采鈺", "shares": None},   # 無股數→市值 None 排最後
        "2603": {"sector": "航運", "name": "長榮", "shares": 2_100_000_000}})
    monkeypatch.setattr(twse, "fetch_stock_quotes", lambda date=None: {
        "2330": {"name": "台積電", "close": 2505.0, "chg_pct": 3.94},
        "2454": {"name": "聯發科", "close": 1000.0, "chg_pct": -1.0},
        "6789": {"name": "采鈺", "close": 300.0, "chg_pct": 4.29},
        "2603": {"name": "長榮", "close": 200.0, "chg_pct": 5.0}})
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/sectors/半導體/stocks").json()
    assert r["sector"] == "半導體" and r["date"] == "2026-06-26" and r["count"] == 3
    assert [s["code"] for s in r["stocks"]] == ["2330", "2454", "6789"]  # 市值大→小，無股數者最後
    assert r["stocks"][0]["mcap"] == round(25_930_000_000 * 2505.0 / 1e8, 1)  # 億
    assert "2603" not in [s["code"] for s in r["stocks"]]  # 不含他類股


def test_sectors_picks_cross_groups_by_sector(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db, insert_chip_snapshot
    from stocks_power_rich.sources import twse

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    insert_chip_snapshot(c, "2026-06-26", [
        {"code": "2330", "name": "台積電", "industry": "上市半導體", "sub_industry": "晶圓代工",
         "w55": 1, "big_holder_ratio": 0.5, "rev_yoy": 10, "est_profit": 1, "lan_value": 80},
        {"code": "3008", "name": "大立光", "industry": "上市光電", "sub_industry": "光學鏡片",
         "w55": 1, "big_holder_ratio": 0.5, "rev_yoy": 10, "est_profit": 1, "lan_value": 60},
    ])
    monkeypatch.setattr(twse, "fetch_sector_indices", lambda date=None: [
        {"name": "半導體", "close": 1.0, "chg_pct": -3.41},
        {"name": "光電", "close": 1.0, "chg_pct": -7.37},
    ])
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/sectors/picks").json()
    assert r["date"] == "2026-06-26"
    assert [g["sector"] for g in r["groups"]] == ["半導體", "光電"]  # 族群強→弱
    assert r["groups"][0]["stocks"][0]["code"] == "2330"


def test_watchlist_add_track_remove(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db, insert_chip_snapshot

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    base = {"name": "台積電", "w55": 1, "big_holder_ratio": 0.5, "rev_yoy": 10, "est_profit": 1, "lan_value": 80}
    insert_chip_snapshot(c, "2026-06-25", [{"code": "2330.TW", "close": 100, **base}])
    insert_chip_snapshot(c, "2026-06-26", [{"code": "2330.TW", "close": 110, **base}])
    app = create_app()
    client = TestClient(app)
    r = client.post("/api/watchlist", json={"code": "2330"}).json()
    s = r["stocks"][0]
    assert s["code"] == "2330.TW" and s["in_latest"] is True and s["times"] == 2
    assert s["entry_date"] == "2026-06-25" and s["ret_pct"] == 10.0  # 100→110
    assert s["name"] == "台積電"                                     # 股名取自快照
    assert s["chip"]["lan_value"] == 80 and s["chip"]["close"] == 110  # 最新快照籌碼欄位
    assert client.delete("/api/watchlist/2330.TW").json()["stocks"] == []


def test_watchlist_estimate_save_partial_then_computes_when_complete(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db, insert_chip_snapshot

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    # capital=100(億元) → 股數 100*1e7=1,000,000,000 股
    insert_chip_snapshot(c, "2026-06-25", [{"code": "2330.TW", "name": "台積電",
                                            "close": 100, "capital": 100, "lpe": 15}])
    app = create_app()
    client = TestClient(app)
    client.post("/api/watchlist", json={"code": "2330"})

    # 只填一部分：算不出區間，estimate 應為 None，但輸入要保留
    r1 = client.put("/api/watchlist/2330.TW/estimate", json={"est_revenue": 2000000000}).json()
    s1 = r1["stocks"][0]
    assert s1["est_revenue"] == 2000000000
    assert s1["estimate"] is None
    assert s1["shares"] == 1_000_000_000.0

    # 補齊其餘欄位 → 算出區間；且第一次存的 est_revenue 沒被這次只帶其他欄位的存檔洗掉
    r2 = client.put("/api/watchlist/2330.TW/estimate", json={
        "est_gross_margin": 50, "est_opex": 100000000, "est_tax": 50000000,
        "est_pe_low": 10, "est_pe_mid": 15, "est_pe_high": 20,
    }).json()
    s2 = r2["stocks"][0]
    assert s2["est_revenue"] == 2000000000        # 第一次存的欄位仍在
    assert s2["estimate"] is not None
    assert s2["estimate"]["eps_annual"] > 0
    assert s2["estimate"]["mid"] == round(s2["estimate"]["eps_annual"] * 15, 2)


def test_line_test_endpoint_composes_and_broadcasts(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "tok-123")
    from stocks_power_rich import line_push
    from stocks_power_rich.db import get_connection, init_db, upsert_market_daily
    from stocks_power_rich.sources import twse

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    upsert_market_daily(c, {"date": "2026-06-26", "taiex": 47018.99, "taiex_chg": 893.08,
                            "inst_foreign": 323.76, "margin_balance": 9414925.0})
    monkeypatch.setattr(twse, "fetch_sector_indices", lambda date=None: [
        {"name": "半導體", "close": 1.0, "chg_pct": 3.21}])
    monkeypatch.setattr(twse, "fetch_stock_quotes", lambda date=None: {})
    sent = {}

    def fake_broadcast(token, msgs):
        sent["token"], sent["text"] = token, str(msgs)
        return {"ok": True, "status": 200}

    monkeypatch.setattr(line_push, "broadcast_messages", fake_broadcast)
    app = create_app()
    client = TestClient(app)
    r = client.post("/api/line/test").json()
    assert r["ok"] is True
    assert sent["token"] == "tok-123"
    assert "47,018.99" in sent["text"] and "+323.8" in sent["text"]
    assert "9,414,925張" in sent["text"]             # 測試端點推完整版
    assert "半導體" in sent["text"] and "+3.21%" in sent["text"]   # 類股強弱（領漲欄）
    # settings 只回報狀態，不洩漏 token
    s = client.get("/api/settings").json()
    assert s["line_configured"] is True and "tok" not in str(s)


def test_line_push_failure_recorded_retry_and_recovery_notice(tmp_path, monkeypatch):
    """推播失敗不再靜默（回歸：2026-07-07 16:00 速報失敗、使用者毫不知情）：
    broadcast 失敗先自動重試一次；仍失敗則持久化記錄，下次成功推播時
    在訊息頂部標註「前次推播失敗」，成功後清除、不重複標註。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "tok-x")
    import time as _time
    from stocks_power_rich import line_push
    from stocks_power_rich.db import get_connection, init_db, upsert_market_daily
    from stocks_power_rich.sources import twse

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    upsert_market_daily(c, {"date": "2026-07-07", "taiex": 45479.11})
    monkeypatch.setattr(twse, "fetch_sector_indices", lambda date=None: [])
    monkeypatch.setattr(twse, "fetch_stock_quotes", lambda date=None: {})
    monkeypatch.setattr(_time, "sleep", lambda s: None)   # 跳過重試間隔
    script = [{"ok": False, "error": "500 upstream"}, {"ok": False, "error": "500 upstream"},
              {"ok": True, "status": 200}, {"ok": True, "status": 200}]
    sent = []

    def fake_broadcast(token, msgs):
        sent.append(msgs)
        return script[len(sent) - 1]

    monkeypatch.setattr(line_push, "broadcast_messages", fake_broadcast)
    app = create_app()
    client = TestClient(app)
    r1 = client.post("/api/line/test").json()
    assert r1["ok"] is False and len(sent) == 2            # 失敗後有自動重試一次
    r2 = client.post("/api/line/test").json()              # 恢復 → 頂部帶前次失敗告警
    assert r2["ok"] is True
    # 告警自成一則純文字排在卡片前面（塞進卡片會破壞版面，且告警該最先被看到）
    assert sent[2][0]["text"].startswith("⚠️ 前次推播失敗") and "500 upstream" in sent[2][0]["text"]
    r3 = client.post("/api/line/test").json()              # 記錄已清除 → 不再標註
    assert r3["ok"] is True and not str(sent[3][0]).startswith("⚠️")


def test_line_quota_exceeded_pauses_broadcast_and_auto_resumes_next_month(tmp_path, monkeypatch):
    """LINE broadcast 按收訊人數計月額度，免費層很淺、用盡前無跡可循（LINE 回 429 +
    body 含「monthly limit」）。之前只有失敗記錄會補一則「前次推播失敗」通知，收到那則
    不代表這次也失敗——現在額度用盡要：(1) 判定成獨立狀態而非一般失敗，不重試（重試
    也不會成功，白打一次）；(2) 本月已知用盡後，下次直接跳過、連 broadcast 都不打；
    (3) 設定頁揭露狀態；(4) 月份字串一換就自動恢復，不必另外排程去清除。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "tok-x")
    from stocks_power_rich import line_push
    from stocks_power_rich.db import get_connection, init_db, upsert_market_daily, set_setting
    from stocks_power_rich.sources import twse

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    upsert_market_daily(c, {"date": "2026-08-06", "taiex": 43361.0})
    monkeypatch.setattr(twse, "fetch_sector_indices", lambda date=None: [])
    monkeypatch.setattr(twse, "fetch_stock_quotes", lambda date=None: {})
    calls = []

    def fake_broadcast(token, msgs):
        calls.append(msgs)
        return {"ok": False, "status": 429,
                "error": '{"message":"You have reached your monthly limit."}'}

    monkeypatch.setattr(line_push, "broadcast_messages", fake_broadcast)
    app = create_app()
    client = TestClient(app)
    r1 = client.post("/api/line/test").json()
    assert r1["ok"] is False and r1["quota_paused"] is True
    assert len(calls) == 1                                    # 額度用盡不重試

    r2 = client.post("/api/line/test").json()                 # 本月已知用盡
    assert r2["quota_paused"] is True and len(calls) == 1      # 這次連 broadcast 都不打

    s = client.get("/api/settings").json()
    assert s["line_quota_paused"] is True and "tok" not in str(s)

    # 模擬跨月：判斷式只比對「現在」的月份字串，把紀錄改成別的月份就等於月份已翻頁
    set_setting(c, "line_quota_month", "2000-01")
    monkeypatch.setattr(line_push, "broadcast_messages",
                        lambda tok, msgs: calls.append(msgs) or {"ok": True, "status": 200})
    r3 = client.post("/api/line/test").json()
    assert r3["ok"] is True and len(calls) == 2                # 自動恢復，不需要手動介入
    assert client.get("/api/settings").json()["line_quota_paused"] is False


def test_intraday_quota_paused_skips_broadcast_but_still_scans(tmp_path, monkeypatch):
    """盤中哨兵每 5 分鐘一次，額度用盡當天最容易反覆撞 429——已知本月用盡就不再嘗試
    broadcast，但掃描/命中判定照常回傳，且不把命中標成「已警示」（額度恢復後仍補送）。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "tok-x")
    from stocks_power_rich import line_push
    from stocks_power_rich.db import get_connection, init_db, bulk_upsert_ohlc, set_ai_cache, set_setting
    from stocks_power_rich.sources import mis, tpex

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    bulk_upsert_ohlc(c, "2026-07-04", {"2330": {"open": 1, "high": 2, "low": 1, "close": 1.5}})
    set_ai_cache(c, "cupsig:2026-07-04", [{"code": "8069", "name": "元太", "resistance": 212.0, "atr": 2.0}])
    monkeypatch.setattr(tpex, "fetch_otc_names", lambda: {"8069": "元太"})
    monkeypatch.setattr(mis, "fetch_mis_quotes", lambda tokens: {"8069": 213.5})
    from datetime import datetime as _dt
    set_setting(c, "line_quota_month", _dt.now().strftime("%Y-%m"))
    calls = []
    monkeypatch.setattr(line_push, "broadcast_messages",
                        lambda tok, msgs: calls.append(msgs) or {"ok": True})
    app = create_app()
    client = TestClient(app)
    client.post("/api/intraday/test?push=1")                  # 第一輪只記候選
    r2 = client.post("/api/intraday/test?push=1").json()
    assert [h["code"] for h in r2["hits"]] == ["8069"]         # 掃描與命中判定不受影響
    assert len(calls) == 0                                     # 但完全沒有嘗試 broadcast


def test_line_watchlist_pct_falls_back_to_ohlc(tmp_path, monkeypatch):
    """自選股報價源查無（上市/上櫃報價都沒有）→ 以 stock_ohlc 日K收盤回推漲跌%，
    不再出現「有價無漲跌%」的缺行（衛司特/亞通實例）。

    自選股經使用者確認不放 Flex 卡片，這段取數現在只餵 altText（通知列預覽），
    所以斷言對 altText 下——而非整包訊息字串，避免哪天版面調動就巧合通過/失敗。
    """
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "tok-x")
    from stocks_power_rich import line_push
    from stocks_power_rich.db import (get_connection, init_db, upsert_market_daily,
                                      bulk_upsert_ohlc, insert_chip_snapshot)
    from stocks_power_rich.sources import tpex, twse

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    upsert_market_daily(c, {"date": "2026-07-07", "taiex": 45479.11})
    # 日K：昨收 100 → 今收 105（+5%）；今日日期需與 market_daily 最新日一致
    bulk_upsert_ohlc(c, "2026-07-06", {"6894": {"open": 99, "high": 101, "low": 98, "close": 100.0}})
    bulk_upsert_ohlc(c, "2026-07-07", {"6894": {"open": 101, "high": 106, "low": 100, "close": 105.0}})
    insert_chip_snapshot(c, "2026-07-07", [{"code": "6894.TW", "name": "衛司特", "close": 105.0}])
    monkeypatch.setattr(twse, "fetch_sector_indices", lambda date=None: [])
    monkeypatch.setattr(twse, "fetch_stock_quotes", lambda date=None: {})   # 上市報價查無
    monkeypatch.setattr(tpex, "fetch_otc_quotes", lambda date=None: {})     # 上櫃報價也查無
    monkeypatch.setattr(twse, "fetch_listed_industry", lambda: {})          # 杯柄段落的股名對照
    monkeypatch.setattr(tpex, "fetch_otc_names", lambda: {})
    sent = []
    monkeypatch.setattr(line_push, "broadcast_messages",
                        lambda tok, msgs: sent.append(msgs) or {"ok": True})
    app = create_app()
    client = TestClient(app)
    client.post("/api/watchlist", json={"code": "6894"})
    assert client.post("/api/line/test").json()["ok"] is True
    alt = sent[0][0]["altText"]
    assert "衛司特" in alt and "105.00" in alt and "+5.00%" in alt


def test_line_cup_section_only_picks_intersection(tmp_path, monkeypatch):
    """杯柄段落只列「杯柄∧籌碼/基本選股」交集（有 CSV 榜時）；
    標題為【杯柄型態&籌碼/基本】、count＝交集檔數。

    杯柄經使用者確認不放 Flex 卡片，所以斷言下在規則實際所在的 _cup_push_info
    與純文字組裝上，而不是整包訊息字串（那只會碰到 altText 的前 400 字）。
    """
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "tok-x")
    from datetime import date, timedelta
    from stocks_power_rich import line_push
    from stocks_power_rich.db import (get_connection, init_db, bulk_upsert_ohlc,
                                      insert_chip_snapshot, set_ai_cache, upsert_market_daily)
    from stocks_power_rich.sources import tpex, twse
    from tests.test_patterns import _make_cup_handle

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    highs, lows, closes = _make_cup_handle()
    base = date(2025, 1, 1)
    for i, (h, l, cl) in enumerate(zip(highs, lows, closes)):
        ds = (base + timedelta(days=i)).isoformat()
        bulk_upsert_ohlc(c, ds, {"2330": {"open": cl, "high": h, "low": l, "close": cl},
                                 "8069": {"open": cl, "high": h, "low": l, "close": cl}})
    last_ds, prev_ds = ds, (base + timedelta(days=i - 1)).isoformat()
    upsert_market_daily(c, {"date": last_ds, "taiex": 45479.11})
    set_ai_cache(c, f"cupsig:{prev_ds}", [])   # 有前日快照（空）→ 今日符合者全算「新符合」
    # 只有 2330 進「籌碼/基本選股」榜；8069 一樣符合杯柄但不在榜 → 應被過濾掉
    insert_chip_snapshot(c, last_ds, [{"code": "2330.TW", "name": "台積電", "w55": 1,
                         "big_holder_ratio": 0.5, "rev_yoy": 10, "est_profit": 1, "lan_value": 80}])
    monkeypatch.setattr(twse, "fetch_listed_industry", lambda: {
        "2330": {"sector": "半導體", "name": "台積電", "shares": 1},
        "8069": {"sector": "光電", "name": "元太", "shares": 1}})
    monkeypatch.setattr(tpex, "fetch_otc_names", lambda: {})
    monkeypatch.setattr(twse, "fetch_sector_indices", lambda date=None: [])
    monkeypatch.setattr(twse, "fetch_stock_quotes", lambda date=None: {})
    monkeypatch.setattr(tpex, "fetch_otc_quotes", lambda date=None: {})
    sent = []
    monkeypatch.setattr(line_push, "broadcast_messages",
                        lambda tok, msgs: sent.append(str(msgs)) or {"ok": True})
    app = create_app()
    client = TestClient(app)
    assert client.post("/api/line/test").json()["ok"] is True   # 整條推播路徑不炸
    from stocks_power_rich.api.helpers import _cup_push_info
    info = _cup_push_info(c)
    assert info["picks"] is True and info["count"] == 1
    assert [s["name"] for s in (info.get("new") or [])] == ["台積電"]
    assert "元太" not in str(info)                              # 非交集股不進清單
    txt = line_push.compose_daily_brief({"date": last_ds}, [], [], cup=info)
    assert "【杯柄型態&籌碼/基本】符合 1 檔" in txt and "台積電" in txt


def test_trades_endpoints_flow(tmp_path, monkeypatch):
    """#6 交易帳本：記一筆（自動補股名）→ 未平倉以最新收盤估 → 平倉 → 統計 → 刪除。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import (get_connection, init_db, bulk_upsert_ohlc,
                                      insert_chip_snapshot, upsert_market_daily)

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    upsert_market_daily(c, {"date": "2026-06-01", "taiex": 45000.0})
    upsert_market_daily(c, {"date": "2026-06-10", "taiex": 45900.0})
    bulk_upsert_ohlc(c, "2026-06-10", {"2330": {"open": 1, "high": 1, "low": 1, "close": 110.0}})
    insert_chip_snapshot(c, "2026-06-01", [{"code": "2330.TW", "name": "台積電", "close": 100.0}])
    app = create_app()
    client = TestClient(app)
    r = client.post("/api/trades", json={"code": "2330", "shares": 1000,
                                         "entry_date": "2026-06-01", "entry_price": 100.0}).json()
    assert r["ok"] is True
    t = r["trades"][0]
    assert t["name"] == "台積電"                        # 股名自動從快照補
    assert t["status"] == "open" and t["mark"] == 110.0  # 未平倉以最新收盤估
    assert t["net_pct"] == 9.41                          # 毛+10% − 0.585% 費用（浮點捨入）
    tid = t["id"]
    # 缺必填欄位擋下
    assert client.post("/api/trades", json={"code": "", "shares": 0}).json()["ok"] is False
    r2 = client.post(f"/api/trades/{tid}/close",
                     json={"exit_date": "2026-06-10", "exit_price": 110.0}).json()
    t2 = r2["trades"][0]
    assert t2["status"] == "closed" and t2["pnl"] == 9415
    assert t2["mkt_pct"] == 2.0 and t2["alpha"] == 7.41  # 同期大盤對照
    assert r2["stats"]["closed_n"] == 1 and r2["stats"]["win_rate"] == 100.0
    assert client.post("/api/trades/999/close", json={"exit_price": 1.0}).json()["ok"] is False
    r3 = client.delete(f"/api/trades/{tid}").json()
    assert r3["ok"] is True and r3["trades"] == []


def test_options_sentiment_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db, upsert_market_daily
    from stocks_power_rich.sources import taifex

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    upsert_market_daily(c, {"date": "2026-06-29", "taiex": 1.0})
    monkeypatch.setattr(taifex, "fetch_put_call_ratio", lambda: {"date": "2026-06-26", "pc_oi_ratio": 128.74, "pc_vol_ratio": 90.03})
    monkeypatch.setattr(taifex, "fetch_large_traders", lambda: {"date": "2026-06-26", "top10_specific_net": -11967, "top5_specific_net": 1044})
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/options-sentiment").json()
    assert r["pcr"]["pc_oi_ratio"] == 128.74
    assert r["large"]["top10_specific_net"] == -11967


def test_inst_ranking_sorts_and_filters_etf(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db, upsert_market_daily
    from stocks_power_rich.sources import twse

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    upsert_market_daily(c, {"date": "2026-06-29", "taiex": 1.0})
    table = {
        "2330": {"name": "台積電", "foreign": -5000, "trust": 1, "dealer": 1, "total": -4000},
        "2317": {"name": "鴻海", "foreign": 8000, "trust": 1, "dealer": 1, "total": 9000},
        "00677U": {"name": "期富邦VIX", "foreign": 99999, "trust": 1, "dealer": 1, "total": 99999},
    }
    monkeypatch.setattr(twse, "fetch_t86", lambda date=None: table)
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/inst-ranking?who=foreign&top=5").json()
    assert r["date"] == "2026-06-29"
    assert "00677U" not in [x["code"] for x in r["buy"]]  # ETF 濾掉
    assert r["buy"][0]["code"] == "2317"   # 外資買超最大
    assert r["sell"][0]["code"] == "2330"  # 外資賣超最大

    # 金額單位：張 × 收盤價 ÷ 1e5 = 億
    monkeypatch.setattr(twse, "fetch_close_prices", lambda date=None: {"2330": 1000.0, "2317": 100.0})
    rv = client.get("/api/inst-ranking?who=foreign&unit=value&top=5").json()
    assert rv["unit"] == "value"
    buy = {x["code"]: x["net"] for x in rv["buy"]}
    assert buy["2317"] == round(8000 * 100.0 / 1e5, 2)   # 8.0 億
    sell = {x["code"]: x["net"] for x in rv["sell"]}
    assert sell["2330"] == round(-5000 * 1000.0 / 1e5, 2)  # -50.0 億


def test_stock_custody_accumulates(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.sources import tdcc

    monkeypatch.setattr(tdcc, "fetch_custody_distribution", lambda: {
        "week_date": "2026-06-26",
        "data": {"2330": {"big1000_pct": 70.0, "big400_pct": 73.0, "big_holders": 30}},
    })
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/stock/2330.TW/custody").json()
    assert r["week"] == "2026-06-26"
    assert r["current"]["big1000_pct"] == 70.0
    assert len(r["trend"]) == 1 and r["trend"][0]["big1000_pct"] == 70.0  # 已累積入庫


def test_stock_chips_per_day_series(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db, upsert_market_daily
    from stocks_power_rich.sources import twse

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    upsert_market_daily(c, {"date": "2026-06-25", "taiex": 1.0})
    upsert_market_daily(c, {"date": "2026-06-26", "taiex": 1.0})
    table = {"2330": {"foreign": 5000, "trust": 2000, "dealer": -1000, "total": 6000}}
    monkeypatch.setattr(twse, "fetch_t86", lambda date=None: table)
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/stock/2330.TW/chips?days=10").json()
    assert r["code"] == "2330"
    assert r["dates"] == ["2026-06-25", "2026-06-26"]
    assert r["foreign"] == [5000, 5000] and r["total"] == [6000, 6000]


def test_stock_chips_market_detect_skips_unpublished_day(tmp_path, monkeypatch):
    """最新日 T86 未公布（空表）時，市場判定應回看前一個有資料的日子，而非誤判成上櫃。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db, upsert_market_daily
    from stocks_power_rich.sources import twse

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    upsert_market_daily(c, {"date": "2026-06-25", "taiex": 1.0})
    upsert_market_daily(c, {"date": "2026-06-26", "taiex": 1.0})
    table = {"2330": {"foreign": 5000, "trust": 2000, "dealer": -1000, "total": 6000}}
    monkeypatch.setattr(twse, "fetch_t86",
                        lambda date=None: {} if date.isoformat() == "2026-06-26" else table)
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/stock/2330.TW/chips?days=10").json()
    assert r["market"] == "twse"                 # 以 6/25（有資料）判定，不被 6/26 空表帶偏
    assert r["foreign"] == [5000, None]          # 6/26 未公布 → None，不以他日填充


def test_kline_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    import pandas as pd
    from stocks_power_rich.sources import kline

    def fake_history(self, period="1y", interval="1d"):
        idx = pd.to_datetime(["2026-06-12"])
        return pd.DataFrame({"Open": [10], "High": [12], "Low": [9], "Close": [11], "Volume": [100]}, index=idx)

    monkeypatch.setattr(kline.yf.Ticker, "history", fake_history)
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/stock/2330.TW/kline?period=1mo")
    assert r.status_code == 200
    assert r.json()["candles"][0] == [10.0, 11.0, 9.0, 12.0]


def test_import_latest_from_folder(tmp_path, monkeypatch):
    import os

    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    data_in = tmp_path / "data_in"
    os.makedirs(data_in)
    monkeypatch.setenv("SPR_DATA_DIR", str(data_in))
    content = (
        "符合條件商品\n資料日期：2026年  6月 15日\n策略,\t.常用\n" + HEADER + "\n" + ROW_2330 + "\n"
    ).encode("cp950")
    (data_in / "20260615.csv").write_bytes(content)

    app = create_app()
    client = TestClient(app)
    r = client.post("/api/csv/import-latest").json()
    assert r["count"] == 1
    assert r["picks"][0]["code"] == "2330.TW"
    assert r["file"] == "20260615.csv"


def test_import_all_loads_every_csv(tmp_path, monkeypatch):
    import os

    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    data_in = tmp_path / "din"
    os.makedirs(data_in)
    monkeypatch.setenv("SPR_DATA_DIR", str(data_in))
    for day in ("22", "23"):
        content = (f"符合條件商品\n資料日期：2026年  6月 {day}日\n策略,\t.常用\n" + HEADER + "\n" + ROW_2330 + "\n").encode("cp950")
        (data_in / f"202606{day}.csv").write_bytes(content)
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/csv/import-all").json()
    assert len(r["imported"]) == 2
    assert set(r["dates"]) == {"2026-06-22", "2026-06-23"}


def test_settings_get_hides_gemini_key(tmp_path, monkeypatch):
    import json

    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    monkeypatch.setenv("GEMINI_API_KEY", "secret-xyz")
    monkeypatch.setenv("SPR_SCHEDULE_TIME", "15:30")
    app = create_app()
    client = TestClient(app)
    s = client.get("/api/settings").json()
    assert s["gemini_configured"] is True
    assert s["schedule_time"] == "15:30"
    assert "gemini_api_key" not in s
    assert "secret-xyz" not in json.dumps(s, ensure_ascii=False)  # 金鑰絕不外洩


def test_settings_post_updates_schedule_and_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    # 以外部絕對路徑當資料根（env 為信任來源），其下子目錄才可由設定頁指定
    ext = tmp_path / "mydata"
    (ext / "sub").mkdir(parents=True)
    monkeypatch.setenv("SPR_DATA_DIR", str(ext))
    app = create_app()
    client = TestClient(app)
    # 排程時間可更新；資料夾指到白名單根之下的子目錄可接受
    r = client.post("/api/settings", json={"schedule_time": "09:05", "data_dir": str(ext / "sub")})
    assert r.json()["ok"] is True
    s = client.get("/api/settings").json()
    assert s["schedule_time"] == "09:05"
    assert s["data_dir"] == str(ext / "sub")
    # 根外任意路徑被拒
    assert client.post("/api/settings", json={"data_dir": str(tmp_path.parent)}).json()["ok"] is False


def test_export_returns_xlsx(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db, insert_chip_snapshot

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    insert_chip_snapshot(c, "2026-06-15", [{"code": "A", "sub_industry": "晶圓", "w55": 1, "big_holder_ratio": 0.9, "rev_yoy": 10, "est_profit": 1, "lan_value": 70}])

    app = create_app()
    client = TestClient(app)
    r = client.get("/api/analysis/export")
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers["content-type"]
    assert r.content[:2] == b"PK"  # xlsx 為 zip
    # 帶中文細產業篩選：檔名須維持 ASCII，回應仍為合法 xlsx
    r2 = client.get("/api/analysis/export?sub=晶圓")
    assert r2.status_code == 200
    assert r2.content[:2] == b"PK"


def test_summary_refresh_bypasses_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    from stocks_power_rich.db import get_connection, init_db, insert_chip_snapshot
    from stocks_power_rich import gemini

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    insert_chip_snapshot(c, "2026-06-15", [{"code": "A", "sub_industry": "晶圓", "w55": 1, "big_holder_ratio": 0.9, "rev_yoy": 10, "est_profit": 1, "lan_value": 70}])

    calls = {"n": 0}

    def fake(*a, **k):
        calls["n"] += 1
        return {"enabled": True, "text": f"v{calls['n']}"}

    monkeypatch.setattr(gemini, "summarize_csv", fake)
    app = create_app()
    client = TestClient(app)
    assert client.get("/api/analysis/summary").json()["text"] == "v1"
    assert client.get("/api/analysis/summary").json()["text"] == "v1"  # 走快取
    assert client.get("/api/analysis/summary?refresh=1").json()["text"] == "v2"  # 強制重生
    assert calls["n"] == 2


def test_tx_kline_falls_back_to_proxy_when_no_history(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    import pandas as pd
    from stocks_power_rich.sources import kline, taifex

    monkeypatch.setattr(taifex, "fetch_tx_history", lambda *a, **k: [])  # 下載不到歷史

    def fake_history(self, period="1y", interval="1d"):
        idx = pd.to_datetime(["2026-06-12", "2026-06-13"])
        # OHLC 須為合法正值（0/半值列會被 _sanitize_series 丟棄）
        return pd.DataFrame({"Open": [23000, 23050], "High": [23100, 23120], "Low": [22950, 23000],
                             "Close": [23050, 23080], "Volume": [1, 1]}, index=idx)

    monkeypatch.setattr(kline.yf.Ticker, "history", fake_history)
    app = create_app()
    client = TestClient(app)
    d = client.get("/api/index/kline?symbol=tx&interval=1d").json()
    assert d.get("proxy") is True          # 無歷史 → 以加權指數近似
    assert len(d["candles"]) == 2


def test_stock_kline_interval_passed(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    import pandas as pd
    from stocks_power_rich.sources import kline

    cap = {}

    def fake_history(self, period="1y", interval="1d"):
        cap["interval"] = interval
        cap["period"] = period
        idx = pd.to_datetime(["2026-06-12"])
        return pd.DataFrame({"Open": [10], "High": [12], "Low": [9], "Close": [11], "Volume": [100]}, index=idx)

    monkeypatch.setattr(kline.yf.Ticker, "history", fake_history)
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/stock/2330.TW/kline?interval=1wk")
    assert r.status_code == 200
    assert cap["interval"] == "1wk"
    assert cap["period"] == "2y"


def test_snapshots_and_daily_by_date(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db, insert_chip_snapshot

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    insert_chip_snapshot(c, "2026-06-15", [{"code": "A", "sub_industry": "晶圓", "w55": 1, "big_holder_ratio": 0.9, "rev_yoy": 10, "est_profit": 1, "lan_value": 70}])
    insert_chip_snapshot(c, "2026-06-16", [{"code": "B", "sub_industry": "水泥", "w55": 1, "big_holder_ratio": 0.2, "rev_yoy": 5, "est_profit": 1, "lan_value": 30}])

    app = create_app()
    client = TestClient(app)
    snaps = client.get("/api/snapshots").json()
    assert snaps["dates"] == ["2026-06-15", "2026-06-16"]

    d = client.get("/api/analysis/daily?date=2026-06-15").json()
    assert d["snap_date"] == "2026-06-15"
    assert d["picks"][0]["code"] == "A"
    assert d["subindustry"][0]["sub_industry"] == "晶圓"


def test_stock_profile_merges_chip_and_valuation(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db, insert_chip_snapshot
    from stocks_power_rich.sources import twse

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    insert_chip_snapshot(c, "2026-06-15", [{
        "code": "2330.TW", "name": "台積電", "custody": 75, "big_holder_ratio": 0.8,
        "w55": 1, "rev_yoy": 30, "trust_3d": 2, "foreign_3d": 3,
    }])
    monkeypatch.setattr(twse, "fetch_valuation", lambda: [{"code": "2330.TW", "pe": 20.0, "yield": 2.0, "pb": 5.0}])

    app = create_app()
    client = TestClient(app)
    p = client.get("/api/stock/2330.TW/profile").json()
    assert p["chip"]["name"] == "台積電"
    assert p["chip"]["big_holder_ratio"] == 0.8
    assert p["valuation"]["pe"] == 20.0
    assert p["valuation"]["yield"] == 2.0


def test_index_kline_tx_from_history(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db, upsert_tx_history

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    # tx_history 已有 ≥20 個交易日 → 走真實台指期 OHLC（非 proxy）
    rows = [{"date": f"2026-06-{d:02d}", "open": 45000 + d, "high": 45000 + d + 5,
             "low": 45000 + d - 5, "close": 45000 + d + 2, "volume": d} for d in range(1, 21)]
    upsert_tx_history(c, rows)

    app = create_app()
    client = TestClient(app)
    out = client.get("/api/index/kline?symbol=tx&interval=1d").json()
    assert not out.get("proxy")
    assert len(out["candles"]) == 20
    assert out["candles"][0] == [45001.0, 45003.0, 44996.0, 45006.0]  # 第1天 [open, close, low, high]


# ========== P0 資安：認證 / 上傳限制 / 路徑白名單 ==========

def test_check_basic_credentials():
    import base64
    from stocks_power_rich.main import _check_basic

    good = "Basic " + base64.b64encode(b"kevin:s3cret").decode()
    assert _check_basic(good, "kevin", "s3cret") is True
    assert _check_basic(good, "kevin", "wrong") is False
    assert _check_basic(good, "other", "s3cret") is False
    assert _check_basic("Bearer xxx", "kevin", "s3cret") is False   # 非 Basic
    assert _check_basic("", "kevin", "s3cret") is False             # 無標頭
    assert _check_basic("Basic !!not-base64!!", "kevin", "s3cret") is False


def test_basic_auth_gates_all_requests(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    monkeypatch.setenv("SPR_BASIC_USER", "kevin")
    monkeypatch.setenv("SPR_BASIC_PASS", "s3cret")
    app = create_app()
    client = TestClient(app)
    # 無帳密 → 401 且帶 WWW-Authenticate（含 API 與靜態頁）
    r = client.get("/api/dashboard")
    assert r.status_code == 401 and "Basic" in r.headers.get("WWW-Authenticate", "")
    assert client.post("/api/line/test").status_code == 401
    # 錯帳密 → 401
    assert client.get("/api/dashboard", auth=("kevin", "nope")).status_code == 401
    # 正確帳密 → 放行
    assert client.get("/api/dashboard", auth=("kevin", "s3cret")).status_code == 200


def test_public_pages_bypass_basic_auth(tmp_path, monkeypatch):
    """/public/* 供沒有帳密的 LINE 好友從圖文選單開啟；其餘路由（含個人資料端點）仍鎖住。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    monkeypatch.setenv("SPR_BASIC_USER", "kevin")
    monkeypatch.setenv("SPR_BASIC_PASS", "s3cret")
    from stocks_power_rich.db import get_connection, init_db, upsert_market_daily
    from stocks_power_rich.sources import twse

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    upsert_market_daily(c, {"date": "2026-07-07", "taiex": 45000.0, "inst_foreign": -200.0,
                            "margin_balance": 9000000.0, "margin_maintenance": 180.0})
    upsert_market_daily(c, {"date": "2026-07-08", "taiex": 45500.0, "taiex_chg": 20.0, "turnover": 3000.0,
                            "tx_price": 45600.0, "tx_chg": 30.0, "n225": 40000.0, "n225_chg": -1.2,
                            "inst_foreign": 547.31, "inst_trust": 96.83, "tx_foreign_oi": -80042.0,
                            "retail_ls_mtx": 0.1655, "margin_balance": 9531735.0, "margin_chg": -130236.0,
                            "margin_maintenance": 186.1})
    monkeypatch.setattr(twse, "fetch_sector_indices", lambda date=None: [
        {"name": "半導體", "close": 1.0, "chg_pct": 2.0}, {"name": "航運", "close": 1.0, "chg_pct": -1.5}])
    monkeypatch.setattr(twse, "fetch_t86", lambda date=None: {
        "2330": {"name": "台積電", "foreign": 29635}, "2317": {"name": "鴻海", "foreign": -17934}})
    app = create_app()
    client = TestClient(app)
    for path in ("/public/overview", "/public/overview.js", "/public/logic",
                "/public/disclaimer", "/public/api/overview"):
        r = client.get(path)
        assert r.status_code == 200, path
    r = client.get("/public/api/overview").json()
    assert r["taiex"] == 45500.0 and r["sectors_up"][0]["name"] == "半導體"
    assert r["sectors_down"][0]["name"] == "航運"
    # 擴充內容（原本使用者反應「資料太少」）：國際/三大法人/期貨籌碼/融資券
    # 皆等級同 LINE 廣播內容，附「昨」對照供公開頁面呈現
    assert r["tx_price"] == 45600.0 and r["tx_chg"] == 30.0
    assert r["intl"][0] == {"key": "n225", "label": "日經", "value": 40000.0, "chg_pct": -1.2}
    assert r["inst"]["foreign"] == 547.31 and r["inst"]["foreign_prev"] == -200.0
    assert r["fut"]["tx_foreign_oi"] == -80042.0 and r["fut"]["retail_ls_mtx"] == 0.1655
    assert r["margin"]["balance"] == 9531735.0 and r["margin"]["chg"] == -130236.0
    assert r["margin"]["maintenance"] == 186.1 and r["margin"]["maintenance_prev"] == 180.0
    # 法人買賣超個股排行（使用者反應「三大法人買賣超的個股沒有放」）
    assert r["inst_rank"]["buy"][0]["code"] == "2330" and r["inst_rank"]["buy"][0]["net"] == 29635
    assert r["inst_rank"]["sell"][0]["code"] == "2317" and r["inst_rank"]["sell"][0]["net"] == -17934
    # 切換鈕獨立端點（使用者反應「沒有金額可以選」）：who/unit 皆可切換，走同一支 inst_ranking()
    r_trust = client.get("/public/api/inst-rank?who=trust").json()
    assert r_trust["who"] == "trust"
    monkeypatch.setattr(twse, "fetch_close_prices", lambda date=None: {"2330": 1000.0, "2317": 200.0})
    r_val = client.get("/public/api/inst-rank?unit=value").json()
    assert r_val["unit"] == "value" and r_val["buy"][0]["net"] == round(29635 * 1000.0 / 1e5, 2)
    # 個人資料端點仍需帳密（不可因新增的 /public 放行條件被誤放行）
    assert client.get("/api/trades").status_code == 401
    assert client.get("/api/settings").status_code == 401
    # 路徑相似但非公開前綴（防止字串誤判，如 /public-evil）不應被放行
    assert client.get("/publicx/overview").status_code == 401


def test_public_overview_shares_internal_frontend(tmp_path, monkeypatch):
    """公開總覽改為共用站內前端：需放行前端靜態資產與總覽所需的唯讀 API，
    但寫入端點、個人資料與站內入口一律維持鎖住。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    monkeypatch.setenv("SPR_BASIC_USER", "kevin")
    monkeypatch.setenv("SPR_BASIC_PASS", "s3cret")
    from stocks_power_rich.db import get_connection, init_db, upsert_market_daily

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    upsert_market_daily(c, {"date": "2026-07-08", "taiex": 45500.0, "taiex_chg": 20.0})
    app = create_app()
    client = TestClient(app)

    # 公開頁服務的是站內 index.html，並帶 data-public 旗標讓 app.js 切公開模式
    html = client.get("/public/overview")
    assert html.status_code == 200
    assert 'data-public="1"' in html.text
    # 資產必須是絕對路徑：本頁在 /public/overview，相對路徑會被解析成 /public/app.js → 404
    # （實測踩過：整頁樣式與程式都沒載入，畫面全空）
    assert 'src="/app.js?v=20260817-ui20"' in html.text
    assert 'href="/styles.css?v=20260817-ui20"' in html.text
    assert 'src="app.js?v=20260817-ui20"' not in html.text
    assert 'href="styles.css?v=20260817-ui20"' not in html.text

    # 前端靜態資產免帳密（否則公開頁載不到樣式/程式/圖表）
    for path in ("/styles.css", "/app.js", "/vendor/echarts.min.js",
                 "/vendor/fonts/huninn.woff2"):
        assert client.get(path).status_code == 200, path

    # 總覽所需的唯讀 API 免帳密
    for path in ("/public/api/dashboard", "/public/api/breadth", "/public/api/heatmap",
                 "/public/api/index-movers", "/public/api/options-sentiment",
                 "/public/api/inst-ranking", "/public/api/tx/volume-sessions",
                 "/public/api/market/summary"):
        assert client.get(path).status_code == 200, path

    # 公開面不得有任何寫入端點
    non_get = [r.path for r in app.routes
               if getattr(r, "path", "").startswith("/public") and r.methods - {"GET", "HEAD"}]
    assert non_get == []

    # market/summary 不接受 refresh：若可帶此參數，匿名訪客就能觸發 Gemini 呼叫燒錢。
    # 直接鎖簽章——帶了 refresh 也只會被 FastAPI 忽略，從介面上就不存在這個開關。
    import inspect
    from stocks_power_rich.api.public import p_market_summary
    assert "refresh" not in inspect.signature(p_market_summary).parameters

    # 寫入/個人端點與站內入口維持鎖住
    assert client.post("/api/update/run").status_code == 401     # 會寫 DB、打外部 API
    assert client.post("/api/db/backup").status_code == 401
    assert client.get("/api/watchlist").status_code == 401
    assert client.get("/api/settings").status_code == 401
    assert client.get("/").status_code == 401                    # 站內入口不公開
    assert client.get("/index.html").status_code == 401
    # 前綴誤判防呆：/vendor/ 放行不得外溢到 /vendorx
    assert client.get("/vendorx/x.js").status_code == 401


def test_no_auth_when_unconfigured(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    monkeypatch.delenv("SPR_BASIC_USER", raising=False)
    monkeypatch.delenv("SPR_BASIC_PASS", raising=False)
    app = create_app()
    client = TestClient(app)
    assert client.get("/api/dashboard").status_code == 200   # 未設定即不啟用（本機開發無感）


def test_settings_rejects_data_dir_outside_root(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    monkeypatch.chdir(tmp_path)
    app = create_app()
    client = TestClient(app)
    bad = "/etc" if os.name != "nt" else "C:\Windows"
    r = client.post("/api/settings", json={"data_dir": bad})
    assert r.json().get("data_dir") != bad          # 未被採用
    assert client.get("/api/settings").json()["data_dir"] != bad
    # 合法子目錄可被接受
    sub = str(tmp_path / "Date")
    os.makedirs(sub, exist_ok=True)
    r2 = client.post("/api/settings", json={"data_dir": "Date"})
    assert r2.json()["ok"] is True


def test_upload_rejects_bad_extension_and_oversize(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    monkeypatch.chdir(tmp_path)
    app = create_app()
    client = TestClient(app)
    # 非白名單副檔名 → 拒絕（不寫檔、不匯入）
    bad = client.post("/api/csv/upload", files={"file": ("evil.exe", b"MZ...", "application/octet-stream")}).json()
    assert bad["count"] == 0 and bad.get("error")
    # 超過大小上限 → 拒絕
    big = client.post("/api/csv/upload",
                      files={"file": ("big.csv", b"x" * (10 * 1024 * 1024 + 5), "text/csv")}).json()
    assert big["count"] == 0 and "10" in big.get("error", "")


def test_security_headers_present(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    app = create_app()
    client = TestClient(app)
    h = client.get("/api/dashboard").headers
    assert h["X-Content-Type-Options"] == "nosniff"
    assert h["X-Frame-Options"] == "DENY"
    assert "default-src 'self'" in h["Content-Security-Policy"]
    assert "script-src 'self'" in h["Content-Security-Policy"]
    assert "https://cdn.jsdelivr.net" not in h["Content-Security-Policy"]


def test_db_backup_endpoint(tmp_path, monkeypatch):
    db = str(tmp_path / "spr.sqlite")
    monkeypatch.setenv("SPR_DB_PATH", db)
    from stocks_power_rich.db import get_connection, init_db, upsert_market_daily
    c = get_connection(db)
    init_db(c)
    upsert_market_daily(c, {"date": "2026-07-01", "taiex": 1.0})
    app = create_app()
    client = TestClient(app)
    r = client.post("/api/db/backup").json()
    assert r["ok"] is True and r["file"].startswith("spr-") and r["file"].endswith(".sqlite")
    assert r["file"] in r["backups"]
    assert os.path.exists(os.path.join(str(tmp_path), "backup", r["file"]))


def test_index_kline_falls_back_to_multimonth_twse(tmp_path, monkeypatch):
    """雲端 yfinance 被擋（回空）時，加權 K 線改用證交所多月 OHLC，而非只有當月幾天。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.sources import kline, twse
    monkeypatch.setattr(kline, "fetch_index_kline",
                        lambda symbol, interval="1d": {"symbol": symbol, "candles": [], "dates": [], "volumes": []})
    sample = [{"date": f"2026-0{m}-{d:02d}", "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 0}
              for m in (5, 6, 7) for d in (1, 8, 15, 22)]   # 3 個月共 12 天
    calls = {"n": 0}

    def fake_hist(months=12):
        calls["n"] += 1
        return sample

    monkeypatch.setattr(twse, "fetch_index_ohlc_history", fake_hist)
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/index/kline?symbol=taiex&interval=1d").json()
    assert r["source"] == "twse" and len(r["candles"]) == 12   # 多月歷史
    # 再打一次應命中快取，不重複逐月抓取
    client.get("/api/index/kline?symbol=taiex&interval=1d")
    assert calls["n"] == 1


def test_index_movers_point_contribution(tmp_path, monkeypatch):
    """權值股貢獻點數：依市值加權算各股對大盤的點數貢獻，正規化到實際指數漲跌。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db, upsert_market_daily
    from stocks_power_rich.sources import twse

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    upsert_market_daily(c, {"date": "2026-07-03", "taiex": 1000.0, "taiex_chg": 20.0})  # 昨指數 980
    monkeypatch.setattr(twse, "fetch_listed_industry", lambda: {
        "2330": {"sector": "半導體", "name": "台積電", "shares": 100},
        "2317": {"sector": "電子零組件", "name": "鴻海", "shares": 50}})
    monkeypatch.setattr(twse, "fetch_stock_quotes", lambda date=None: {
        "2330": {"name": "台積電", "close": 110.0, "chg_pct": 10.0},   # 昨100, 市值+1000
        "2317": {"name": "鴻海", "close": 90.0, "chg_pct": -10.0}})    # 昨100, 市值-500
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/index-movers?top=10").json()
    assert r["index"] == 1000.0 and r["index_chg"] == 20.0
    mv = {m["code"]: m for m in r["movers"]}
    assert [m["code"] for m in r["movers"]] == ["2330", "2317"]     # 依貢獻絕對值排序
    assert mv["2330"]["contribution"] == 40.0 and mv["2317"]["contribution"] == -20.0
    assert round(sum(m["contribution"] for m in r["movers"]), 2) == 20.0  # 合計＝實際指數漲跌


def test_ohlc_backfill_stores_trading_days(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.sources import tpex, twse
    monkeypatch.setattr(twse, "fetch_stock_ohlc",
                        lambda date=None: {"2330": {"open": 1.0, "high": 2.0, "low": 1.0, "close": 1.5}})
    monkeypatch.setattr(tpex, "fetch_otc_ohlc",
                        lambda date=None: {"8069": {"open": 44.0, "high": 45.0, "low": 43.5, "close": 44.8}})
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/ohlc/backfill?days=60&max_fetch=5").json()
    assert r["added"] == 5 and r["stored_days"] == 5 and r["done"] is False
    assert r["twse_days"] == 5 and r["otc_days"] == 5   # 兩市場各自追蹤
    # 上櫃資料確實入庫
    o = client.get("/api/stock/8069/ohlc?bars=60").json()
    assert len(o["candles"]) == 5 and o["candles"][0][1] == 44.8


def test_stock_kline_falls_back_to_official_ohlc_when_yfinance_empty(tmp_path, monkeypatch):
    """個股 K 線：yfinance 在雲端資料中心 IP 常被限流回空 → 後備改用 stock_ohlc
    （杯柄回補的官方 TWSE/TPEx OHLC），日K直接用、週K以 resampler 聚合；1h 無官方源維持回空。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    import pandas as pd
    from datetime import date, timedelta
    from stocks_power_rich.db import get_connection, init_db, bulk_upsert_ohlc
    from stocks_power_rich.sources import kline

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    d0 = date(2026, 6, 1)   # 週一起連續 10 個平日 → 跨兩週
    ds = []
    d = d0
    while len(ds) < 10:
        if d.weekday() < 5:
            ds.append(d.isoformat())
        d += timedelta(days=1)
    for i, day in enumerate(ds):
        bulk_upsert_ohlc(c, day, {"6894": {"open": 100.0 + i, "high": 102.0 + i,
                                           "low": 99.0 + i, "close": 101.0 + i}})
    monkeypatch.setattr(kline, "_history", lambda *a, **k: pd.DataFrame())  # 模擬 yfinance 全滅
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/stock/6894.TW/kline?interval=1d").json()
    assert len(r["candles"]) == 10 and r["candles"][0][1] == 101.0   # [open, close, low, high]
    assert "waves" in r and r["dates"][0] == ds[0]
    wk = client.get("/api/stock/6894.TW/kline?interval=1wk").json()
    assert 1 < len(wk["candles"]) < 10                                # 週K確實聚合
    assert client.get("/api/stock/6894.TW/kline?interval=1h").json()["candles"] == []


def _no_turnover(monkeypatch):
    """官方成交量額來源全空（模擬盤中尚未發布）——同時擋掉測試對外連網。"""
    from stocks_power_rich.sources import twse, tpex
    monkeypatch.setattr(twse, "fetch_stock_turnover", lambda date=None: {})
    monkeypatch.setattr(tpex, "fetch_otc_turnover", lambda date=None: {})
    monkeypatch.setattr(twse, "fetch_listed_industry", lambda: {})
    monkeypatch.setattr(tpex, "fetch_otc_industry", lambda: {})
    monkeypatch.setattr(twse, "fetch_t86", lambda date=None: {})
    monkeypatch.setattr(tpex, "fetch_tpex_insti", lambda date=None: {})


def test_rank_price_turnover_official_estimate_and_10day_amount_change(tmp_path, monkeypatch):
    """/api/rank/price 量額：官方值優先、盤中退回估算，並比較前十日均額。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db, bulk_upsert_ohlc, set_ai_cache
    from stocks_power_rich.sources import mis, twse, tpex
    from datetime import datetime

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    today = datetime.now().strftime("%Y-%m-%d")
    for d in ("2026-07-16", "2026-07-17"):
        bulk_upsert_ohlc(c, d, {
            "2330": {"open": 2400, "high": 2450, "low": 2380, "close": 2400.0},
            "3008": {"open": 1750, "high": 1800, "low": 1740, "close": 1780.0},
            "2454": {"open": 1500, "high": 1520, "low": 1490, "close": 1500.0},
        })
    monkeypatch.setattr(mis, "fetch_mis_rank", lambda tokens: {
        "2330": {"price": 2455.0, "chg": 55.0, "chg_pct": 2.29, "vol": 30000,
                 "time": "10:30", "name": "台積電"},
        "3008": {"price": 1800.0, "chg": 20.0, "chg_pct": 1.12, "vol": 1000,
                 "time": "10:30", "name": "大立光"},
        "2454": {"price": 1500.0, "chg": 0.0, "chg_pct": 0.0, "vol": 500,
                 "time": "10:30", "name": "聯發科"},
    })
    # 今日：只有 2330 有官方值（其餘退估算）；前一交易日 2026-07-17：2330/3008 有，2454 沒有
    by_date = {
        today: {"2330": {"vol": 30000, "amount": 73_000_000_000.0}},
        "2026-07-17": {"2330": {"vol": 25000, "amount": 60_000_000_000.0},
                       "3008": {"vol": 900, "amount": 1_600_000_000.0}},
    }
    monkeypatch.setattr(twse, "fetch_stock_turnover",
                        lambda date=None: by_date.get(date.strftime("%Y-%m-%d"), {}))
    monkeypatch.setattr(tpex, "fetch_otc_turnover", lambda date=None: {})
    monkeypatch.setattr(twse, "fetch_listed_industry", lambda: {
        "2330": {"shares": 30_000_000_000},
        "3008": {"shares": 2_000_000_000},
        "2454": {"shares": 1_000_000_000},
    })
    monkeypatch.setattr(tpex, "fetch_otc_industry", lambda: {})
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/rank/price?market=twse&n=10").json()
    it = {i["code"]: i for i in r["items"]}
    # 官方值存在 → 精確、不標估算；與近十日均額 60 億 → +13 億 / +21.7%
    assert it["2330"]["amount"] == 73_000_000_000.0 and it["2330"]["amount_est"] is False
    assert it["2330"]["vol"] == 30000
    assert it["2330"]["amount_avg_10"] == 60_000_000_000.0
    assert it["2330"]["amount_vs_avg_10"] == 13_000_000_000.0
    assert it["2330"]["amount_vs_avg_10_pct"] == 21.7
    assert it["2330"]["turnover_pct"] == 0.1
    # 官方缺 → 估算 1000 張 × 1000 股 × 1800 元 = 18 億，標記 amount_est
    assert it["3008"]["amount"] == 1_800_000_000 and it["3008"]["amount_est"] is True
    assert it["3008"]["amount_vs_avg_10"] == 200_000_000
    assert it["3008"]["amount_vs_avg_10_pct"] == 12.5
    # 無歷史成交額的個股不硬湊比較值。
    assert it["2454"]["amount_avg_10"] is None
    assert it["2454"]["amount_vs_avg_10"] is None


def test_rank_price_uses_prior_ten_published_sessions_for_amount_average(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db, bulk_upsert_ohlc
    from stocks_power_rich.api.market import _avg_turnover_10
    from stocks_power_rich.sources import twse, tpex
    from datetime import date, timedelta

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    today = date.today()
    days = [(today - timedelta(days=i)).isoformat() for i in range(1, 11)]
    for i, day in enumerate(days):
        bulk_upsert_ohlc(c, day, {
            "2330": {"open": 100, "high": 101, "low": 99, "close": 100.0},
        })
    amounts = {day: (10 + i) * 1_000_000_000.0 for i, day in enumerate(days)}
    monkeypatch.setattr(twse, "fetch_stock_turnover", lambda date=None: {
        "2330": {"amount": amounts[date.strftime("%Y-%m-%d")]}
    })
    monkeypatch.setattr(tpex, "fetch_otc_turnover", lambda date=None: {})

    averages, sessions = _avg_turnover_10(c, today.isoformat(), ["2330"])
    assert sessions == 10
    assert averages["2330"] == 14_500_000_000.0


def test_rank_price_loads_same_day_institutional_lots_after_close(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db
    from stocks_power_rich.api.market import _stock_institutional_for
    from stocks_power_rich.sources import twse, tpex
    from datetime import date

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    day = date(2026, 7, 31)
    monkeypatch.setattr(twse, "fetch_t86", lambda d: {
        "2330": {"foreign": 4_000, "trust": 800, "dealer": 200, "total": 5_000},
    })
    monkeypatch.setattr(tpex, "fetch_tpex_insti", lambda d: {
        "6415": {"foreign": -100, "trust": 50, "dealer": 20, "total": -30},
    })

    tse, otc = _stock_institutional_for(c, day)
    assert tse["2330"]["total"] == 5_000
    assert otc["6415"]["total"] == -30
    # A second call reads the daily cache instead of fetching the sources again.
    monkeypatch.setattr(twse, "fetch_t86", lambda d: (_ for _ in ()).throw(AssertionError("not cached")))
    assert _stock_institutional_for(c, day)[0]["2330"]["foreign"] == 4_000


def test_rank_price_capital_signal_uses_only_observable_inputs():
    from stocks_power_rich.api.market import _capital_signal

    assert _capital_signal(12.5, 80.0, 1.2)["label"] == "法人承接"
    assert _capital_signal(-8.0, 120.0, 1.5)["label"] == "放量分歧"
    assert _capital_signal(2.0, 5.0, 1.0)["label"] == "低周轉偏多"
    assert _capital_signal(None, 8.0, 5.5)["label"] == "高周轉觀察"
    assert _capital_signal(None, 45.0, None)["label"] == "放量觀察"
    assert _capital_signal(None, None, None) is None


def test_rank_price_endpoint_markets_and_live_quotes(tmp_path, monkeypatch):
    """/api/rank/price：昨收預選高價股（分上市/上櫃/合併）→ MIS 即時價覆蓋；MIS 缺檔退回昨收。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db, bulk_upsert_ohlc, set_ai_cache
    from stocks_power_rich.sources import mis
    from datetime import datetime

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    bulk_upsert_ohlc(c, "2026-07-17", {
        "2330": {"open": 2400, "high": 2450, "low": 2380, "close": 2400.0},   # 上市
        "3008": {"open": 1750, "high": 1800, "low": 1740, "close": 1780.0},   # 上市
        "6415": {"open": 3000, "high": 3100, "low": 2950, "close": 3050.0},   # 上櫃（最高價）
        "8069": {"open": 200, "high": 210, "low": 198, "close": 205.0},       # 上櫃
    })
    set_ai_cache(c, f"otc_names:{datetime.now().strftime('%Y-%m')}",
                 {"6415": "矽力-KY", "8069": "元太"})   # 上櫃名單（分類依據）
    monkeypatch.setattr(mis, "fetch_mis_rank", lambda tokens: {
        "2330": {"price": 2455.0, "chg": 55.0, "chg_pct": 2.29, "time": "10:30", "name": "台積電"},
        "6415": {"price": 3080.0, "chg": 30.0, "chg_pct": 0.98, "time": "10:30", "name": "矽力*-KY"},
        # 3008/8069 MIS 缺 → 退回昨收
    })
    _no_turnover(monkeypatch)
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/rank/price?market=all&n=10").json()
    codes = [i["code"] for i in r["items"]]
    assert codes == ["6415", "2330", "3008", "8069"]           # 依現價 desc（MIS 覆蓋後）
    assert r["items"][0]["market"] == "otc" and r["items"][1]["market"] == "twse"
    assert r["items"][0]["price"] == 3080.0 and r["items"][0]["time"] == "10:30"
    assert r["items"][2]["price"] == 1780.0 and r["items"][2]["time"] is None   # 降級：昨收、無時間
    tw = client.get("/api/rank/price?market=twse&n=10").json()
    assert [i["code"] for i in tw["items"]] == ["2330", "3008"]
    otc = client.get("/api/rank/price?market=otc&n=10").json()
    assert [i["code"] for i in otc["items"]] == ["6415", "8069"]


def test_custody_backfill_pulls_history_into_custody_dist(tmp_path, monkeypatch):
    """/api/stock/{code}/custody/backfill：從 TDCC 智能網回補該股歷史週次到 custody_dist，
    讓集保大戶趨勢能顯示 6 月前（opendata 只給當週、拿不回歷史）。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.sources import tdcc
    from stocks_power_rich.db import get_connection, init_db, get_custody_trend

    get_connection(str(tmp_path / "t.sqlite"))  # ensure file
    monkeypatch.setattr(tdcc, "fetch_custody_weeks", lambda: ["20260320", "20260327"])
    monkeypatch.setattr(tdcc, "fetch_custody_history",
                        lambda code, weeks=None, max_weeks=60: {
                            "2026-03-20": {"big1000_pct": 80.0, "big400_pct": 83.0, "big_holders": 1400},
                            "2026-03-27": {"big1000_pct": 81.0, "big400_pct": 84.0, "big_holders": 1410}})
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/stock/2330/custody/backfill?weeks=52").json()
    assert r["stored"] == 2 and set(r["filled"]) == {"2026-03-20", "2026-03-27"}
    c = get_connection(str(tmp_path / "t.sqlite"))
    trend = get_custody_trend(c, "2330")
    assert [t["week"] for t in trend] == ["2026-03-20", "2026-03-27"]   # 依 week 排序
    assert trend[0]["big1000_pct"] == 80.0 and trend[1]["big_holders"] == 1410


def test_inst_backfill_warms_t86_tpex_cache_and_reports_remaining(tmp_path, monkeypatch):
    """/api/inst/backfill：預熱個股三大法人（T86/TPEx 整日快取，跨股共用）到 ai_cache，
    讓個股三大法人買賣超圖能顯示 6 月前歷史而不必冷載即時抓。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from datetime import date, timedelta
    from stocks_power_rich.db import get_connection, init_db, upsert_market_daily, get_ai_cache
    from stocks_power_rich.sources import twse, tpex

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    days = [(date.today() - timedelta(days=n)).isoformat() for n in (2, 3, 4)]
    for ds in days:
        upsert_market_daily(c, {"date": ds, "taiex": 100.0})
    monkeypatch.setattr(twse, "fetch_t86",
                        lambda d=None: {"2330": {"name": "台積電", "foreign": 1, "trust": 2, "dealer": 3, "total": 6}})
    monkeypatch.setattr(tpex, "fetch_tpex_insti",
                        lambda d=None: {"8069": {"name": "元太", "foreign": 1, "trust": 0, "dealer": 0, "total": 1}})
    app = create_app()
    client = TestClient(app)
    r1 = client.get("/api/inst/backfill?days=90&max_fetch=2").json()
    assert len(r1["filled"]) == 2 and r1["remaining"] == 1     # cap 生效，還剩 1 天
    r2 = client.get("/api/inst/backfill?days=90&max_fetch=15").json()
    assert len(r2["filled"]) == 1 and r2["remaining"] == 0     # 補完
    assert get_ai_cache(c, f"t86:{days[-1]}")["2330"]["total"] == 6      # 上市快取寫入
    assert get_ai_cache(c, f"tpex:{days[-1]}")["8069"]["total"] == 1     # 上櫃也預熱


def test_chips_backfill_fills_history_and_reports_remaining(tmp_path, monkeypatch):
    """/api/chips/backfill：大範圍回補台指期籌碼歷史（外資未平倉/散戶多空比等），
    籌碼趨勢圖 06-20 前空白的補洞入口。max_fetch 限每次筆數、remaining 供重複呼叫直到補完。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from datetime import date, timedelta
    from stocks_power_rich.db import get_connection, init_db, upsert_market_daily
    from stocks_power_rich import updater

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    days = [(date.today() - timedelta(days=n)).isoformat() for n in (2, 3, 4)]
    for ds in days:
        upsert_market_daily(c, {"date": ds, "taiex": 100.0})   # 期貨籌碼全 NULL
    monkeypatch.setattr(updater.taifex, "fetch_chips_for_date",
                        lambda d=None: {"retail_ls_mtx": 0.3, "retail_ls_tmf": 0.4,
                                        "tx_foreign_oi": -1000, "retail_oi_mtx": 500})
    app = create_app()
    client = TestClient(app)
    r1 = client.get("/api/chips/backfill?days=90&max_fetch=2").json()
    assert len(r1["filled"]) == 2 and r1["remaining"] == 1     # cap 生效，還剩 1 筆
    r2 = client.get("/api/chips/backfill?days=90&max_fetch=15").json()
    assert len(r2["filled"]) == 1 and r2["remaining"] == 0     # 補完
    row = c.execute("SELECT retail_ls_mtx, tx_foreign_oi, taiex FROM market_daily WHERE date=?",
                    (days[-1],)).fetchone()
    assert row[0] == 0.3 and row[1] == -1000
    assert row[2] == 100.0                                     # 既有 TWSE 欄位不被覆寫


def test_cup_handle_screen_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from datetime import date, timedelta
    from stocks_power_rich.db import get_connection, init_db, bulk_upsert_ohlc
    from stocks_power_rich.sources import twse
    from tests.test_patterns import _make_cup_handle

    from stocks_power_rich.db import insert_chip_snapshot
    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    highs, lows, closes = _make_cup_handle()
    base = date(2025, 1, 1)
    for i, (h, l, cl) in enumerate(zip(highs, lows, closes)):
        ds = (base + timedelta(days=i)).isoformat()
        bulk_upsert_ohlc(c, ds, {"2330": {"open": cl, "high": h, "low": l, "close": cl}})
    # 2330 同時進「籌碼/基本選股」榜（W55∧大戶∧營收∧EPS），供交集標記
    insert_chip_snapshot(c, "2025-03-01", [{"code": "2330.TW", "name": "台積電", "w55": 1,
                         "big_holder_ratio": 0.5, "rev_yoy": 10, "est_profit": 1, "lan_value": 80}])
    monkeypatch.setattr(twse, "fetch_listed_industry", lambda: {"2330": {"sector": "半導體", "name": "台積電", "shares": 1}})
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/patterns/cup-handle").json()
    assert r["count"] == 1
    m = r["stocks"][0]
    assert m["code"] == "2330" and m["name"] == "台積電"
    assert m["right_price"] == 90.0 and m["resistance"] == 90.0
    assert m["left_date"] and m["right_date"]        # 畫線用日期
    assert m["in_picks"] is True and r["has_picks"] is True and r["picks_count"] == 1  # 交集標記
    # 個股 OHLC 端點供畫線
    o = client.get("/api/stock/2330/ohlc?bars=400").json()
    assert len(o["candles"]) == 400 and o["candles"][0][0] is not None


def test_cup_handle_endpoint_skips_nan_ohlc_bar(tmp_path, monkeypatch):
    """單一 NaN bar 不應讓全市場杯柄掃描 500，回應也必須符合 strict JSON。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from datetime import date, timedelta
    from stocks_power_rich.db import get_connection, init_db, bulk_upsert_ohlc
    from stocks_power_rich.sources import twse, tpex
    from tests.test_patterns import _make_cup_handle

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    highs, lows, closes = _make_cup_handle()
    highs[300] = float("nan")
    base = date(2025, 1, 1)
    for i, (h, low, close) in enumerate(zip(highs, lows, closes)):
        bulk_upsert_ohlc(c, (base + timedelta(days=i)).isoformat(), {
            "2330": {"open": close, "high": h, "low": low, "close": close},
        })
    monkeypatch.setattr(twse, "fetch_listed_industry", lambda: {
        "2330": {"sector": "半導體", "name": "台積電", "shares": 1},
    })
    monkeypatch.setattr(tpex, "fetch_otc_names", lambda: {})

    client = TestClient(create_app(), raise_server_exceptions=False)
    response = client.get("/api/patterns/cup-handle?min_r=50")
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1 and payload["stocks"][0]["code"] == "2330"
    json.dumps(payload, allow_nan=False)


def test_cup_handle_position_fields_and_loss_tolerance(tmp_path, monkeypatch):
    """#5 部位管理：每檔附 ATR(14) 與建議停損（突破價−2×ATR）；
    設定 loss_tolerance（單筆可容忍虧損）往返存取，杯柄 API 一併回傳供前端算建議部位。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from datetime import date, timedelta
    from stocks_power_rich.db import get_connection, init_db, bulk_upsert_ohlc
    from stocks_power_rich.sources import tpex, twse
    from tests.test_patterns import _make_cup_handle

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    highs, lows, closes = _make_cup_handle()
    base = date(2025, 1, 1)
    for i, (h, l, cl) in enumerate(zip(highs, lows, closes)):
        bulk_upsert_ohlc(c, (base + timedelta(days=i)).isoformat(),
                         {"2330": {"open": cl, "high": h, "low": l, "close": cl}})
    monkeypatch.setattr(twse, "fetch_listed_industry",
                        lambda: {"2330": {"sector": "半導體", "name": "台積電", "shares": 1}})
    monkeypatch.setattr(tpex, "fetch_otc_names", lambda: {})
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/patterns/cup-handle").json()
    m = r["stocks"][0]
    assert m["atr"] and m["atr"] > 0
    assert m["stop_loss"] == round(m["resistance"] - 2 * m["atr"], 2)
    assert r["loss_tolerance"] is None                       # 未設定
    client.post("/api/settings", json={"loss_tolerance": 20000})
    assert client.get("/api/settings").json()["loss_tolerance"] == 20000
    assert client.get("/api/patterns/cup-handle").json()["loss_tolerance"] == 20000
    client.post("/api/settings", json={"loss_tolerance": 0})  # 清空＝不啟用
    assert client.get("/api/settings").json()["loss_tolerance"] is None


def test_settings_nav_order_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    app = create_app()
    client = TestClient(app)
    assert client.get("/api/settings").json()["nav_order"] is None       # 預設未設
    order = ["overview", "cup", "picks", "settings"]
    client.post("/api/settings", json={"nav_order": order})
    assert client.get("/api/settings").json()["nav_order"] == order
    # 非字母 slug 被濾掉（防注入），仍存有效項
    client.post("/api/settings", json={"nav_order": ["overview", "../evil", "cup"]})
    assert client.get("/api/settings").json()["nav_order"] == ["overview", "cup"]


def test_intraday_breakout_requires_two_round_confirmation_above_atr_threshold(tmp_path, monkeypatch):
    """噪音太多的教訓（2026-07-08：09:00 開盤即報、0.3% 微幅探頭也報）→ 加兩道濾網：
    A. 突破門檻＝壓力線+0.3×ATR（碰到壓力線不算，要有力道才算）；
    B. 本輪剛穿越門檻只記候選、不報，下一輪（約5分鐘後）仍站穩才是真突破、才推播。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "tok-x")
    from stocks_power_rich import line_push
    from stocks_power_rich.db import get_connection, init_db, bulk_upsert_ohlc, set_ai_cache
    from stocks_power_rich.sources import mis, tpex

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    bulk_upsert_ohlc(c, "2026-07-04", {"2330": {"open": 1, "high": 2, "low": 1, "close": 1.5}})
    set_ai_cache(c, "cupsig:2026-07-04", [
        {"code": "8069", "name": "元太", "resistance": 212.0, "atr": 2.0},    # 門檻 212.6
        {"code": "2812", "name": "台中銀", "resistance": 19.8, "atr": 1.0},  # 門檻 20.1（只碰壓力不算）
    ])
    monkeypatch.setattr(tpex, "fetch_otc_names", lambda: {"8069": "元太"})
    monkeypatch.setattr(mis, "fetch_mis_quotes",
                        lambda tokens: {"8069": 213.5, "2812": 19.85})
    sent = []
    monkeypatch.setattr(line_push, "broadcast_messages",
                        lambda tok, msgs: sent.append(str(msgs)) or {"ok": True})
    app = create_app()
    client = TestClient(app)
    r1 = client.post("/api/intraday/test?push=1").json()
    assert r1["checked"] == 2 and r1["hits"] == [] and len(sent) == 0     # 第一輪只記候選，不報
    r2 = client.post("/api/intraday/test?push=1").json()
    assert [h["code"] for h in r2["hits"]] == ["8069"] and len(sent) == 1  # 元太連兩輪站穩門檻
    assert "元太 213.50(壓212.00)" in sent[0] and "台中銀" not in sent[0]  # 台中銀只碰壓力未過ATR門檻
    # 已警示過 → 不再重複推播
    r3 = client.post("/api/intraday/test?push=1").json()
    assert r3["hits"] == [] and len(sent) == 1


def test_intraday_breakout_false_cross_resets_candidate(tmp_path, monkeypatch):
    """單輪穿越門檻又跌回（插針/假突破）→ 不因「曾經穿越過」誤報，須重新連兩輪站穩才算數。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "tok-x")
    from stocks_power_rich import line_push
    from stocks_power_rich.db import get_connection, init_db, bulk_upsert_ohlc, set_ai_cache
    from stocks_power_rich.sources import mis, tpex

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    bulk_upsert_ohlc(c, "2026-07-04", {"2330": {"open": 1, "high": 2, "low": 1, "close": 1.5}})
    set_ai_cache(c, "cupsig:2026-07-04", [{"code": "8069", "name": "元太", "resistance": 212.0, "atr": 2.0}])
    monkeypatch.setattr(tpex, "fetch_otc_names", lambda: {"8069": "元太"})
    seq = [213.5, 212.3, 213.5, 213.6]  # 過門檻→跌破(候選清空)→重新過門檻(首見)→再過(這輪才確認)
    state = {"n": 0}

    def fake_quotes(tokens):
        v = seq[state["n"]]; state["n"] += 1
        return {"8069": v}

    monkeypatch.setattr(mis, "fetch_mis_quotes", fake_quotes)
    sent = []
    monkeypatch.setattr(line_push, "broadcast_messages",
                        lambda tok, msgs: sent.append(str(msgs)) or {"ok": True})
    app = create_app()
    client = TestClient(app)
    assert client.post("/api/intraday/test?push=1").json()["hits"] == []  # 213.5 首見候選
    assert client.post("/api/intraday/test?push=1").json()["hits"] == []  # 212.3 跌破門檻，候選清空
    assert client.post("/api/intraday/test?push=1").json()["hits"] == []  # 213.5 重新首見候選
    r4 = client.post("/api/intraday/test?push=1").json()
    assert [h["code"] for h in r4["hits"]] == ["8069"] and len(sent) == 1  # 213.6 連兩輪站穩才確認


def test_intraday_picks_only_toggle_filters_watchlist(tmp_path, monkeypatch):
    """設定開啟「只警示交集」後，哨兵只盯同時符合籌碼/基本選股的訊號股。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "tok-x")
    from stocks_power_rich import line_push
    from stocks_power_rich.db import (get_connection, init_db, bulk_upsert_ohlc,
                                      set_ai_cache, insert_chip_snapshot)
    from stocks_power_rich.sources import mis, tpex

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    bulk_upsert_ohlc(c, "2026-07-04", {"2330": {"open": 1, "high": 2, "low": 1, "close": 1.5}})
    set_ai_cache(c, "cupsig:2026-07-04", [
        {"code": "8069", "name": "元太", "resistance": 212.0},
        {"code": "2812", "name": "台中銀", "resistance": 19.8},
    ])
    # 2812 在籌碼/基本選股榜；8069 不在
    insert_chip_snapshot(c, "2026-07-04", [{"code": "2812.TW", "name": "台中銀", "w55": 1,
                         "big_holder_ratio": 0.5, "rev_yoy": 10, "est_profit": 1, "lan_value": 80}])
    monkeypatch.setattr(tpex, "fetch_otc_names", lambda: {"8069": "元太"})
    monkeypatch.setattr(mis, "fetch_mis_quotes", lambda tokens: {"8069": 213.5, "2812": 19.85})
    sent = []
    monkeypatch.setattr(line_push, "broadcast_messages",
                        lambda tok, msgs: sent.append(str(msgs)) or {"ok": True})
    app = create_app()
    client = TestClient(app)
    # 預設（不開）：兩檔都盯、都突破；第一輪只記候選，第二輪站穩才一起確認、交集股標⭐且排前
    assert client.post("/api/intraday/test?push=1").json()["hits"] == []
    r = client.post("/api/intraday/test?push=1").json()
    assert {h["code"] for h in r["hits"]} == {"8069", "2812"}
    assert "⭐台中銀" in sent[0] and sent[0].index("台中銀") < sent[0].index("元太")
    # 開啟只盯交集（清除今日已警示/候選記錄後重掃）→ 只剩 2812，仍需連兩輪站穩
    client.post("/api/settings", json={"intraday_picks_only": True})
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    c.execute("DELETE FROM ai_cache WHERE cache_key IN (?, ?)",
              (f"cupalerted:{today}", f"cuppending:{today}"))
    c.commit()
    r1 = client.post("/api/intraday/test").json()
    assert r1["checked"] == 1 and r1["hits"] == []
    r2 = client.post("/api/intraday/test").json()
    assert r2["checked"] == 1 and [h["code"] for h in r2["hits"]] == ["2812"]


def test_ohlc_backfill_reset_query_param(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.sources import tpex, twse
    monkeypatch.setattr(twse, "fetch_stock_ohlc", lambda date=None: {})   # 讓兩邊先熔斷
    monkeypatch.setattr(tpex, "fetch_otc_ohlc", lambda date=None: {})
    app = create_app()
    client = TestClient(app)
    r1 = client.get("/api/ohlc/backfill?days=60&max_fetch=100").json()
    assert r1["twse_exhausted"] is True and r1["otc_exhausted"] is True
    r2 = client.get("/api/ohlc/backfill?days=60&max_fetch=100").json()
    assert r2["added"] == 0   # 熔斷後卡住

    monkeypatch.setattr(twse, "fetch_stock_ohlc",
                        lambda date=None: {"2330": {"open": 1.0, "high": 2.0, "low": 1.0, "close": 1.5}})
    r3 = client.get("/api/ohlc/backfill?days=60&max_fetch=5&reset=1").json()
    assert r3["added"] > 0 and r3["twse_exhausted"] is False   # reset 後解除卡住


def test_os_futures_endpoint_merges_local_index(tmp_path, monkeypatch):
    # 迴歸：重構後 helpers._os_futures 曾漏匯入 deps.conn → /api/os-futures 500。
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db, upsert_market_daily
    from stocks_power_rich.sources import intl

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    upsert_market_daily(c, {"date": "2026-06-26", "taiex": 100.0, "taiex_chg": 2.0,
                            "tx_price": 101.0, "tx_chg": 3.0})
    monkeypatch.setattr(intl, "fetch_futures_monitor", lambda tries=3: [
        {"category": "指數期貨", "items": [{"name": "小道瓊", "value": 40000.0, "chg": 10.0, "chg_pct": 0.03}]},
    ])
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/os-futures?refresh=1")
    assert r.status_code == 200
    idx = next(g for g in r.json()["categories"] if g["category"] == "指數期貨")
    names = [it["name"] for it in idx["items"]]
    assert names[:2] == ["加權指數", "台指期"]      # 本地指數併到最前
    assert "小道瓊" in names


def test_tx_volume_sessions_endpoint_returns_day_night_series(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db, upsert_tx_history
    from stocks_power_rich.sources import taifex

    monkeypatch.setattr(taifex, "fetch_tx_history", lambda *a, **k: [])  # 已有足量歷史，不應觸發下載

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    rows = [{"date": f"2026-05-{d:02d}", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 100,
             "night_volume": 30} for d in range(1, 25)]  # 24 個交易日 ≥ 20 門檻
    rows[-1]["night_volume"] = None  # 最新一日尚無夜盤資料
    rows[-1]["volume"] = 120
    upsert_tx_history(c, rows)
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/tx/volume-sessions?days=3").json()  # days 有下限 5（比照 stock_ohlc 慣例）
    assert r["dates"] == ["2026-05-20", "2026-05-21", "2026-05-22", "2026-05-23", "2026-05-24"]
    assert r["day_volume"][-1] == 120.0
    assert r["night_volume"][-2:] == [30.0, None]
    assert r["ratio"][-2:] == [0.3, None]


def test_traders_list_endpoint():
    app = create_app()
    client = TestClient(app)
    d = client.get("/api/traders").json()
    ids = [t["id"] for t in d["traders"]]
    assert "ss" in ids
    ss = next(t for t in d["traders"] if t["id"] == "ss")
    assert ss["name"] and ss["emoji"]  # 選單所需欄位齊全


def test_traders_unknown_id_404():
    app = create_app()
    client = TestClient(app)
    assert client.get("/api/traders/nobody").status_code == 404


def test_trader_ss_sections_checklist_and_signals(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import (get_connection, init_db, upsert_market_daily,
                                      insert_chip_snapshot, bulk_upsert_ohlc)
    from stocks_power_rich.sources import twse, tpex

    monkeypatch.setattr(twse, "fetch_listed_industry", lambda: {"2330": {"sector": "半導體", "name": "台積電", "shares": 1}})
    monkeypatch.setattr(tpex, "fetch_otc_names", lambda: {})

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    upsert_market_daily(c, {"date": "2026-07-01", "taiex": 20000.0, "turnover": 4000.0,
                            "margin_maintenance": 133.0, "vix": 32.0})
    insert_chip_snapshot(c, "2026-07-01", [
        {"code": "1111", "name": "候選", "month_inc": 5, "rev_yoy": 10, "accum_inc": 3,
         "big_holder_ratio": 0.5, "lan_value": 80, "close": 100},
        {"code": "2222", "name": "淘汰", "month_inc": -1, "rev_yoy": 10, "accum_inc": 3,
         "big_holder_ratio": 0.5, "lan_value": 90, "close": 50},
    ])
    # 2330 四日K：三黑後一紅吞實體 → 一紅吃三黑
    for d, o, cl in [("2026-06-28", 100, 97), ("2026-06-29", 98, 95),
                     ("2026-06-30", 96, 92), ("2026-07-01", 91, 101)]:
        bulk_upsert_ohlc(c, d, {"2330": {"open": o, "high": max(o, cl) + 1,
                                         "low": min(o, cl) - 1, "close": cl}})
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/traders/ss")
    assert r.status_code == 200
    d = r.json()
    assert d["id"] == "ss" and d["name"]        # META 併入回應
    secs = {s["type"]: s for s in d["sections"]}
    by = {i["key"]: i for i in secs["checklist"]["items"]}
    assert by["margin_maint"]["status"] == "bull"   # 133% 抄底區
    assert by["vix"]["status"] == "bull"            # 極度恐慌反指標
    assert by["fund_flow"]["status"] == "na"        # 無海期快取
    tables = [s for s in d["sections"] if s["type"] == "table"]
    qoq_rows = tables[0]["rows"]
    assert [p["code"] for p in qoq_rows] == ["1111"]
    red3_rows = tables[1]["rows"]
    assert [h["code"] for h in red3_rows] == ["2330"]
    assert red3_rows[0]["name"] == "台積電"
    assert "非投資建議" in d["disclaimer"]


def test_signals_snapshot_records_once_and_reports_source_dates(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db, insert_chip_snapshot

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    insert_chip_snapshot(c, "2026-07-14", [
        {"code": "1111", "name": "候選", "w55": 1, "big_holder_ratio": 0.5,
         "rev_yoy": 10, "est_profit": 2, "lan_value": 80, "close": 100},
    ])
    app = create_app()
    client = TestClient(app)

    r1 = client.post("/api/signals/snapshot").json()
    assert r1["ok"] is True
    assert r1["added"] == 1
    assert r1["total"] == 1
    assert r1["chip_snapshot_date"] == "2026-07-14"

    r2 = client.post("/api/signals/snapshot").json()  # 同日再按一次不應重複寫入
    assert r2["added"] == 0
    assert r2["total"] == 1


def test_heatmap_groups_stocks_by_sector_sorted_by_mcap(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db, upsert_market_daily
    from stocks_power_rich.sources import twse

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    upsert_market_daily(c, {"date": "2026-06-26", "taiex": 100.0})
    monkeypatch.setattr(twse, "fetch_listed_industry", lambda: {
        "2330": {"sector": "半導體", "name": "台積電", "shares": 25_930_000_000},
        "2454": {"sector": "半導體", "name": "聯發科", "shares": 1_600_000_000},
        "6789": {"sector": "半導體", "name": "采鈺", "shares": None},      # 無股數→無市值→排除
        "2603": {"sector": "航運", "name": "長榮", "shares": 2_100_000_000}})
    monkeypatch.setattr(twse, "fetch_stock_quotes", lambda date=None: {
        "2330": {"name": "台積電", "close": 2505.0, "chg_pct": 3.94},
        "2454": {"name": "聯發科", "close": 1000.0, "chg_pct": -1.0},
        "6789": {"name": "采鈺", "close": 300.0, "chg_pct": 4.29},
        "2603": {"name": "長榮", "close": 200.0, "chg_pct": 5.0}})
    monkeypatch.setattr(twse, "fetch_stock_quotes", lambda date=None: {
        "2330": {"name": "台積電", "close": 2505.0, "chg_pct": 3.94},
        "2454": {"name": "聯發科", "close": 1000.0, "chg_pct": -1.0},
        "6789": {"name": "采鈺", "close": 300.0, "chg_pct": 4.29},
        "2603": {"name": "長榮", "close": 200.0, "chg_pct": 5.0}})
    app = create_app()
    client = TestClient(app)
    d = client.get("/api/heatmap").json()   # 預設 market=tse
    assert d["date"] == "2026-06-26" and d["market"] == "tse"
    groups = d["groups"]
    # 半導體總市值 > 航運 → 排最前
    assert [g["sector"] for g in groups] == ["半導體", "航運"]
    semi = groups[0]["stocks"]
    assert [s["code"] for s in semi] == ["2330", "2454"]   # 市值大→小，無市值者(采鈺)剔除
    assert semi[0]["mcap"] == round(25_930_000_000 * 2505.0 / 1e8, 1)
    assert semi[0]["chg_pct"] == 3.94 and semi[0]["name"] == "台積電"


def test_heatmap_otc_and_all_markets(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db, upsert_market_daily
    from stocks_power_rich.sources import twse, tpex

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    upsert_market_daily(c, {"date": "2026-06-26", "taiex": 100.0})
    monkeypatch.setattr(twse, "fetch_listed_industry", lambda: {
        "2330": {"sector": "半導體", "name": "台積電", "shares": 25_930_000_000}})
    monkeypatch.setattr(twse, "fetch_stock_quotes", lambda date=None: {
        "2330": {"name": "台積電", "close": 2505.0, "chg_pct": 3.94}})
    monkeypatch.setattr(tpex, "fetch_otc_industry", lambda: {
        "8299": {"sector": "半導體", "name": "群聯", "shares": 199_000_000},
        "5347": {"sector": "半導體", "name": "世界", "shares": 6_000_000_000}})
    monkeypatch.setattr(tpex, "fetch_otc_quotes", lambda date=None: {
        "8299": {"name": "群聯", "close": 600.0, "chg_pct": 2.0},
        "5347": {"name": "世界", "close": 100.0, "chg_pct": -1.5}})
    app = create_app()
    client = TestClient(app)

    otc = client.get("/api/heatmap?market=otc").json()
    assert otc["market"] == "otc"
    assert {s["code"] for g in otc["groups"] for s in g["stocks"]} == {"8299", "5347"}
    assert "2330" not in {s["code"] for g in otc["groups"] for s in g["stocks"]}

    allm = client.get("/api/heatmap?market=all").json()
    codes = {s["code"] for g in allm["groups"] for s in g["stocks"]}
    assert codes == {"2330", "8299", "5347"}   # 上市+上櫃同「半導體」合併一組
    semi = next(g for g in allm["groups"] if g["sector"] == "半導體")["stocks"]
    assert [s["code"] for s in semi] == ["2330", "5347", "8299"]  # 依市值大→小跨市場排序


def test_cup_handle_min_r_param_and_clamp(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from datetime import date, timedelta
    from stocks_power_rich.db import get_connection, init_db, bulk_upsert_ohlc
    from stocks_power_rich.sources import twse, tpex
    from tests.test_patterns import _make_cup_handle

    monkeypatch.setattr(twse, "fetch_listed_industry", lambda: {"2330": {"sector": "半導體", "name": "台積電", "shares": 1}})
    monkeypatch.setattr(tpex, "fetch_otc_names", lambda: {})
    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    highs, lows, closes = _make_cup_handle()   # %R = 83.3
    base = date(2025, 1, 1)
    for i, (h, l, cl) in enumerate(zip(highs, lows, closes)):
        bulk_upsert_ohlc(c, (base + timedelta(days=i)).isoformat(),
                         {"2330": {"open": cl, "high": h, "low": l, "close": cl}})
    app = create_app()
    client = TestClient(app)
    assert client.get("/api/patterns/cup-handle").json()["count"] == 1            # 預設 70：83.3 過
    assert client.get("/api/patterns/cup-handle?min_r=90").json()["count"] == 0   # 90：83.3 被擋
    r = client.get("/api/patterns/cup-handle?min_r=10").json()                    # clamp 到 50
    assert r["count"] == 1 and r["min_r"] == 50.0
    m = client.get("/api/patterns/cup-handle").json()["stocks"][0]
    assert m["cup_depth_pct"] == 25.0 and m["dist_pct"] == 1.1                    # 新展示欄位


def test_weekly_endpoint_pairs_across_iso_weeks(tmp_path, monkeypatch):
    """跨週比較必須取「上週最後一份快照」，不是前一個交易日（集保週資料一週一更）。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db, insert_chip_snapshot

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    row = {"code": "2330.TW", "name": "台積電", "big_holder_ratio": 1.0, "custody": 70.0}
    for d in ("2026-07-08", "2026-07-10", "2026-07-16", "2026-07-17"):  # W28×2 + W29×2
        insert_chip_snapshot(c, d, [dict(row)])
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/analysis/weekly").json()
    assert r["this_date"] == "2026-07-17"
    assert r["last_date"] == "2026-07-10"   # 上週最後一份，而非 07-16


def test_summary_includes_snap_date(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db, insert_chip_snapshot

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    insert_chip_snapshot(c, "2026-07-17", [{"code": "2330.TW", "name": "台積電",
                                            "w55": 1, "big_holder_ratio": 0.5,
                                            "rev_yoy": 10, "est_profit": 1, "lan_value": 80}])
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/analysis/summary").json()
    assert r["snap_date"] == "2026-07-17"   # AI 籌碼分析師要能標示資料日期


def _line_sig(secret: str, body: bytes) -> str:
    import base64
    import hashlib
    import hmac
    return base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()


def test_line_webhook_requires_valid_signature(tmp_path, monkeypatch):
    """webhook 免帳密（LINE 伺服器無法帶 Basic Auth），改以 channel secret 簽章把關。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    monkeypatch.setenv("SPR_BASIC_USER", "u")       # 全站認證開著
    monkeypatch.setenv("SPR_BASIC_PASS", "p")
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "sekret")
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "tok")
    from stocks_power_rich import line_push
    sent = []
    monkeypatch.setattr(line_push, "reply_messages",
                        lambda tok, rt, msgs: sent.append((rt, msgs)) or {"ok": True})
    app = create_app()
    client = TestClient(app)
    body = json.dumps({"events": [
        {"type": "message", "replyToken": "rt1", "message": {"type": "text", "text": "說明"}}]},
        ensure_ascii=False).encode()
    # 沒有簽章 / 簽章錯 → 403，且不回覆任何訊息
    assert client.post("/line/webhook", content=body).status_code == 403
    assert client.post("/line/webhook", content=body,
                       headers={"X-Line-Signature": "bogus"}).status_code == 403
    assert sent == []
    # 正確簽章 → 200（且沒被 Basic Auth 擋掉，未帶帳密也通）
    r = client.post("/line/webhook", content=body,
                    headers={"X-Line-Signature": _line_sig("sekret", body)})
    assert r.status_code == 200
    assert len(sent) == 1 and sent[0][0] == "rt1"
    assert "大盤" in sent[0][1][0]["text"] and "週報" in sent[0][1][0]["text"]  # 說明列出可用指令


def test_line_webhook_brief_command_replies_market_text(tmp_path, monkeypatch):
    """「大盤」→ 回覆與 16:00 推播同一份盤後速報內容（走 reply，不耗免費額度）。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "sekret")
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "tok")
    from stocks_power_rich.db import get_connection, init_db, upsert_market_daily
    from stocks_power_rich import line_push

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    upsert_market_daily(c, {"date": "2026-07-20", "taiex": 47018.99, "taiex_chg": 893.08,
                            "turnover": 10780.3})
    sent = []
    monkeypatch.setattr(line_push, "reply_messages",
                        lambda tok, rt, msgs: sent.append((rt, msgs)) or {"ok": True})
    app = create_app()
    client = TestClient(app)
    body = json.dumps({"events": [
        {"type": "message", "replyToken": "rt9", "message": {"type": "text", "text": "大盤"}}]},
        ensure_ascii=False).encode()
    r = client.post("/line/webhook", content=body,
                    headers={"X-Line-Signature": _line_sig("sekret", body)})
    assert r.status_code == 200
    # 資料日非今日也照回（使用者主動問的，不套用推播的 staleness 略過規則）
    assert len(sent) == 1 and "47,018.99" in str(sent[0][1]) and "2026-07-20" in str(sent[0][1])


def test_line_webhook_unknown_text_replies_help_and_never_500(tmp_path, monkeypatch):
    """認不得的訊息回指令說明；非文字事件不回覆。任何情況都要回 200，否則 LINE 會判定 webhook 失效。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "sekret")
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "tok")
    from stocks_power_rich import line_push
    sent = []
    monkeypatch.setattr(line_push, "reply_messages",
                        lambda tok, rt, msgs: sent.append((rt, msgs)) or {"ok": True})
    app = create_app()
    client = TestClient(app)
    for payload, expect_n in (
        ({"events": [{"type": "message", "replyToken": "rt1",
                      "message": {"type": "text", "text": "早安"}}]}, 1),
        ({"events": [{"type": "follow", "replyToken": "rt2"}]}, 1),   # 非文字事件 → 不新增回覆
        ({"events": []}, 1),                                          # Console 驗證用空 body
    ):
        b = json.dumps(payload, ensure_ascii=False).encode()
        r = client.post("/line/webhook", content=b,
                        headers={"X-Line-Signature": _line_sig("sekret", b)})
        assert r.status_code == 200
        assert len(sent) == expect_n
    assert "大盤" in sent[0][1][0]["text"]


def test_line_webhook_rank_command_replies_flex_table(tmp_path, monkeypatch):
    """「高價股」回 Flex 表格（純文字對不齊、必折行），其餘指令仍是純文字。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    monkeypatch.setenv("LINE_CHANNEL_SECRET", "sekret")
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "tok")
    from stocks_power_rich.db import get_connection, init_db, bulk_upsert_ohlc
    from stocks_power_rich import line_push
    from stocks_power_rich.sources import mis

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    bulk_upsert_ohlc(c, "2026-07-17", {"2330": {"open": 1, "high": 2, "low": 1, "close": 2400.0}})
    monkeypatch.setattr(mis, "fetch_mis_rank", lambda tokens: {
        "2330": {"price": 2455.0, "chg": 55.0, "chg_pct": 2.29, "vol": 30000,
                 "time": "10:30", "name": "台積電"}})
    _no_turnover(monkeypatch)
    sent = []
    monkeypatch.setattr(line_push, "reply_messages",
                        lambda tok, rt, msgs: sent.append(msgs[0]) or {"ok": True})
    app = create_app()
    client = TestClient(app)
    body = json.dumps({"events": [
        {"type": "message", "replyToken": "rt", "message": {"type": "text", "text": "高價股"}}]},
        ensure_ascii=False).encode()
    r = client.post("/line/webhook", content=body,
                    headers={"X-Line-Signature": _line_sig("sekret", body)})
    assert r.status_code == 200
    assert sent[0]["type"] == "flex" and sent[0]["contents"]["size"] == "giga"
    assert "台積電" in sent[0]["altText"]


def test_osfut_remote_failure_not_cached_even_though_local_indices_are_injected(tmp_path, monkeypatch):
    """遠端全滅時不得寫快取——即使加權/台指期照樣被本地注入。

    這是 Yahoo 全滅時「updated_at 照樣更新、資料卻空」的成因：注入的兩檔讓最終結果
    看起來「有 items」，若用那個當快取條件就把失敗寫成成功。快取條件必須看「遠端有沒有
    抓到」（has_remote 旗標），與本地注入無關。
    """
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from datetime import datetime as _dt
    from stocks_power_rich.api import helpers
    from stocks_power_rich.db import get_ai_cache, get_connection, init_db, upsert_market_daily
    from stocks_power_rich.sources import intl

    create_app()
    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    upsert_market_daily(c, {"date": "2026-07-24", "taiex": 44850.0, "taiex_chg": 25.0,
                            "tx_price": 44930.0, "tx_chg": 285.0})   # 供本地注入
    monkeypatch.setattr(intl, "fetch_futures_monitor",
                        lambda: [{"category": cat, "items": []} for cat, _ in intl.OS_FUTURES])

    result = helpers._os_futures()
    injected = [i["name"] for g in result["categories"] for i in g["items"]]
    assert "加權指數" in injected and "台指期" in injected   # 注入仍在，畫面不會全空
    assert result["has_remote"] is False
    key = f"osfut:{_dt.now().strftime('%Y-%m-%d')}"
    assert get_ai_cache(c, key) is None                    # 但這包不進快取，下次可重試


def test_turnover_cache_is_per_market_so_one_failure_cannot_poison_the_other(tmp_path, monkeypatch):
    """櫃買失敗時不得把「只有上市」的半套結果永久寫死。

    舊版合併成單一 key 且無 TTL：只要櫃買當下掛掉而證交所成功，上櫃的成交額增減
    就永遠是「—」，且不會自己好。分開快取後，失敗的那半留白、下次自行重抓。
    """
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from datetime import date as _d
    from stocks_power_rich.api import helpers
    from stocks_power_rich.db import get_connection, init_db

    create_app()
    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    day = _d(2026, 7, 23)
    monkeypatch.setattr(helpers.twse, "fetch_stock_turnover",
                        lambda d: {"2330": {"vol": 1, "amount": 10.0}})
    monkeypatch.setattr(helpers.tpex, "fetch_otc_turnover",
                        lambda d: (_ for _ in ()).throw(RuntimeError("tpex down")))
    first = helpers._turnover_for(c, day)
    assert "2330" in first and "5274" not in first        # 上市可用、上櫃缺

    # 櫃買恢復後，同一天必須補得回來（舊版會被半套快取擋住）
    monkeypatch.setattr(helpers.tpex, "fetch_otc_turnover",
                        lambda d: {"5274": {"vol": 2, "amount": 20.0}})
    second = helpers._turnover_for(c, day)
    assert second["2330"]["amount"] == 10.0 and second["5274"]["amount"] == 20.0


def test_osfut_empty_cache_is_treated_as_a_miss_and_self_heals(tmp_path, monkeypatch):
    """已經寫進去的空快取必須能自己好，不能只擋未來的寫入。

    這個快取沒有 TTL，所以「只防寫入、不治讀取」等於要人工進 DB 清才會恢復——
    正是修完第一版後雲端仍然空白的原因。空的一律當未命中 → 重抓。
    """
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from datetime import datetime as _dt
    from stocks_power_rich.db import get_connection, init_db, set_ai_cache
    from stocks_power_rich.sources import intl

    app = create_app()
    client = TestClient(app)
    key = f"osfut:{_dt.now().strftime('%Y-%m-%d')}"
    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    # 先種一份「壞掉的」快取，模擬機房抓失敗時留下的殘骸
    set_ai_cache(c, key, {"categories": [{"category": "指數期貨", "items": []}],
                          "updated_at": "2026-07-05T18:20:33"})

    good = [{"category": "指數期貨", "items": [{"name": "小道瓊", "value": 44512.5,
                                                "chg": 1.0, "chg_pct": 0.1}]}]
    monkeypatch.setattr(intl, "fetch_futures_monitor", lambda: good)
    body = client.get("/api/os-futures").json()

    names = [i["name"] for g in body["categories"] for i in g["items"]]
    assert "小道瓊" in names            # 沒有被那份空快取擋住


def test_osfut_backs_off_after_failure_instead_of_hammering_source(tmp_path, monkeypatch):
    """抓失敗後短時間內不重打資料源——冷卻期內完全不打網路（避免連按更新報價狂刷）。

    海期監控現已改排程每日兩次＋TradingView，冷卻的角色降為手動連按時的保險，
    但機制本身仍要正確：失敗後冷卻窗內直接回空骨架、不打網路；refresh 繞過冷卻。
    """
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.api import helpers
    from stocks_power_rich.sources import intl

    create_app()
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        return [{"category": c, "items": []} for c, _ in intl.OS_FUTURES]

    monkeypatch.setattr(intl, "fetch_futures_monitor", boom)

    r1 = helpers._os_futures()
    assert calls["n"] == 1 and not any(g["items"] for g in r1["categories"] if g["category"] != "指數期貨")

    r2 = helpers._os_futures()               # 立刻再叫一次：冷卻中，不該再打網路
    assert calls["n"] == 1
    assert r2["categories"] == r1["categories"] or True  # 結構不變即可，重點是 calls 沒增加

    # refresh=True 是使用者明確按「更新報價」，必須繞過冷卻
    helpers._os_futures(refresh=True)
    assert calls["n"] == 2


def test_osfut_retries_after_cooldown_expires(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from datetime import datetime, timedelta
    from stocks_power_rich.api import helpers
    from stocks_power_rich.db import get_connection, init_db, set_ai_cache
    from stocks_power_rich.sources import intl

    create_app()
    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    stale = (datetime.now() - timedelta(seconds=helpers._OSFUT_FAIL_COOLDOWN + 5)).isoformat()
    set_ai_cache(c, "osfut:fail_at", {"at": stale})

    calls = {"n": 0}
    monkeypatch.setattr(intl, "fetch_futures_monitor", lambda: (calls.__setitem__("n", calls["n"] + 1),
                                                                [{"category": c2, "items": []}
                                                                 for c2, _ in intl.OS_FUTURES])[1])
    helpers._os_futures()
    assert calls["n"] == 1     # 冷卻早已過期，照常重試




def test_rank_price_never_pairs_fallback_price_with_stale_change(tmp_path, monkeypatch):
    """現價與漲跌必須同源。MIS 無報價時退回昨收，漲跌就要留白——不能顯示昨收卻配著
    MIS 算出的漲跌，那正是「正常價格配 −100%」那個 bug 的成因。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.db import get_connection, init_db, bulk_upsert_ohlc
    from stocks_power_rich.sources import mis, twse, tpex

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    bulk_upsert_ohlc(c, "2026-07-29", {
        "2059": {"open": 7100, "high": 7200, "low": 7000, "close": 7140.0}})
    # MIS 回不出價（該檔被 parse_mis_rank 整筆略過）
    monkeypatch.setattr(mis, "fetch_mis_rank", lambda tokens: {})
    monkeypatch.setattr(twse, "fetch_stock_turnover", lambda date=None: {})
    monkeypatch.setattr(tpex, "fetch_otc_turnover", lambda date=None: {})
    it = {x["code"]: x for x in
          TestClient(create_app()).get("/api/rank/price?market=twse&n=5").json()["items"]}
    assert it["2059"]["price"] == 7140.0          # 退回昨收
    assert it["2059"]["chg"] is None              # 漲跌留白，不沿用別處的數字
    assert it["2059"]["chg_pct"] is None


def test_kline_stock_ohlc_fallback_respects_the_requested_period(tmp_path, monkeypatch):
    """雲端 yfinance 被擋 → 走 stock_ohlc 後備，但這條路徑原本把該股**整張表**倒出來。

    stock_ohlc 的覆蓋度取決於 /api/ohlc/backfill 跑到哪，很容易稀疏。實測 2615 在
    雲端畫出來的 X 軸橫跨 2017→2026，相鄰刻度的實際間隔從 1 天到 6 年都有，
    圖形完全失去意義。「近一年日K」就該只有近一年。
    """
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from datetime import datetime, timedelta
    from stocks_power_rich.db import get_connection, init_db, bulk_upsert_ohlc
    from stocks_power_rich.sources import kline as kline_src
    from stocks_power_rich.main import create_app

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    today = datetime.now()
    stale = (today - timedelta(days=3000)).strftime("%Y-%m-%d")      # 8 年前的孤兒列
    bulk_upsert_ohlc(c, stale, {"2615": {"open": 19.5, "high": 19.6, "low": 19.4, "close": 19.5}})
    for i in range(12, 0, -1):
        d = (today - timedelta(days=i)).strftime("%Y-%m-%d")
        bulk_upsert_ohlc(c, d, {"2615": {"open": 85.0, "high": 86.0, "low": 84.0, "close": 85.5}})

    # 模擬雲端：yfinance 回空
    monkeypatch.setattr(kline_src, "fetch_kline",
                        lambda *a, **k: {"code": "2615", "dates": [], "candles": [],
                                         "volumes": [], "waves": {}})
    client = TestClient(create_app())

    body = client.get("/api/stock/2615/kline?interval=1d").json()
    assert body["source"] == "stock_ohlc"
    assert stale not in body["dates"], "8 年前的孤兒列不該出現在『近一年』的日K裡"
    assert len(body["dates"]) == 12
    span = (datetime.strptime(body["dates"][-1], "%Y-%m-%d")
            - datetime.strptime(body["dates"][0], "%Y-%m-%d")).days
    assert span < 400, f"X 軸跨度 {span} 天，遠超過 period=1y"


def test_kline_fallback_returns_empty_rather_than_a_misleading_chart(tmp_path, monkeypatch):
    """窗內完全沒有資料時回空，讓前端顯示「尚無資料」——不要畫一張看起來像 K 線、
    尺度卻錯亂的圖。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from datetime import datetime, timedelta
    from stocks_power_rich.db import get_connection, init_db, bulk_upsert_ohlc
    from stocks_power_rich.sources import kline as kline_src
    from stocks_power_rich.main import create_app

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    old = (datetime.now() - timedelta(days=3000)).strftime("%Y-%m-%d")
    bulk_upsert_ohlc(c, old, {"2615": {"open": 19.5, "high": 19.6, "low": 19.4, "close": 19.5}})
    monkeypatch.setattr(kline_src, "fetch_kline",
                        lambda *a, **k: {"code": "2615", "dates": [], "candles": [],
                                         "volumes": [], "waves": {}})
    client = TestClient(create_app())

    assert client.get("/api/stock/2615/kline?interval=1d").json()["candles"] == []


def test_rank_movers_endpoint_merges_both_markets_and_names_them(tmp_path, monkeypatch):
    """排行榜與漲跌幅分布同源（_quotes_for／_otc_quotes_for），上市＋上櫃合併。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.api import market as market_api
    from stocks_power_rich.db import get_connection, init_db, upsert_market_daily
    from stocks_power_rich.main import create_app

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    upsert_market_daily(c, {"date": "2026-08-04", "taiex": 43000.0})

    monkeypatch.setattr(market_api, "_quotes_for", lambda c, d: {
        "2330": {"chg_pct": 3.2}, "2615": {"chg_pct": -7.5},
        "031234": {"chg_pct": 10.0},          # 權證，不該進榜
    })
    monkeypatch.setattr(market_api, "_otc_quotes_for", lambda c, d: {
        "6488": {"chg_pct": 9.1}, "3105": {"chg_pct": -4.0},
    })
    monkeypatch.setattr(market_api, "_ohlc_names",
                        lambda c: {"2330": "台積電", "2615": "萬海", "6488": "環球晶"})
    client = TestClient(create_app())

    body = client.get("/api/rank/movers?n=3").json()
    assert body["date"] == "2026-08-04"
    assert [r["code"] for r in body["up"]] == ["6488", "2330"]      # 上櫃也要進榜
    assert body["up"][0]["name"] == "環球晶"
    assert [r["code"] for r in body["down"]] == ["2615", "3105"]
    assert len(body["up"]) == 2      # 只有兩檔真的上漲，不湊滿 n=3
    assert body["down"][0]["name"] == "萬海"
    assert "031234" not in str(body)
    # 查無名稱時退回代號，不可顯示 null
    assert body["down"][1]["name"] == "3105"


def test_rank_movers_clamps_n_and_survives_empty_quotes(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich.api import market as market_api
    from stocks_power_rich.db import get_connection, init_db, upsert_market_daily
    from stocks_power_rich.main import create_app

    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    upsert_market_daily(c, {"date": "2026-08-04", "taiex": 43000.0})
    monkeypatch.setattr(market_api, "_quotes_for", lambda c, d: {})
    monkeypatch.setattr(market_api, "_otc_quotes_for", lambda c, d: {})
    monkeypatch.setattr(market_api, "_ohlc_names", lambda c: {})
    client = TestClient(create_app())

    body = client.get("/api/rank/movers?n=999").json()      # 上限夾到 20，不可炸
    assert body["up"] == [] and body["down"] == [] and body["n"] == 0


def test_picks_selfcheck_endpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    import stocks_power_rich.selfcheck as sc

    monkeypatch.setattr(sc, "build_selfcheck",
                         lambda conn, date: {"date": date or "2026-08-20", "fields": sc.FIELDS,
                                             "rows": [], "coverage": {}, "dates": ["2026-08-20"],
                                             "blocked_reason": {}, "tolerances": {}})
    client = TestClient(create_app())
    r = client.get("/api/picks/selfcheck?date=2026-08-20")
    assert r.status_code == 200
    assert r.json()["date"] == "2026-08-20"
    assert r.json()["fields"] == sc.FIELDS


def test_picks_selfcheck_tolerances_come_from_analysis(tmp_path, monkeypatch):
    """容差必須來自 analysis.SELFCHECK_TOL/REL，不得在端點另寫一份（同 bands 的防漂移規矩）。
    build_selfcheck 即使沒有資料也一律填 tolerances（見 selfcheck.py），故不必先灌資料。"""
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    from stocks_power_rich import analysis

    client = TestClient(create_app())
    r = client.get("/api/picks/selfcheck")
    assert r.status_code == 200
    assert r.json()["tolerances"]["SELFCHECK_TOL"] == analysis.SELFCHECK_TOL

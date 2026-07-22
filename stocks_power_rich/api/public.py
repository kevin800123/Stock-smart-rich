import os

from fastapi import APIRouter, Response
from fastapi.responses import HTMLResponse
from datetime import datetime
from .deps import conn
from .helpers import (
    _PUBLIC_INTL_FIELDS,
    _sectors_for,
    get_ai_cache,
    set_ai_cache,
    _latest_date,
    WEB_DIR
)
from .market import (
    market_summary_logic,
    dashboard as _dashboard,
    breadth as _breadth,
    index_movers as _index_movers,
    heatmap as _heatmap,
    options_sentiment as _options_sentiment,
    inst_ranking as _inst_ranking,
)
from .stock import _insti_for, index_kline as _index_kline, tx_volume_sessions as _tx_volume_sessions
from ..db import get_snapshot_dates, get_snapshot
from ..sources import twse
from .. import analysis, gemini, exporter
from ..config import load_config

router = APIRouter()

def summary_logic(c, refresh: int = 0):
    cfg = load_config()
    dates = get_snapshot_dates(c)
    if not dates:
        return gemini.summarize_csv([], {}, [], cfg.gemini_api_key)
    key = f"csv:{dates[-1]}"
    cached = get_ai_cache(c, key)
    if cached and not refresh:
        cached.setdefault("snap_date", dates[-1])   # 舊快取無此欄，讀取時回填（鍵即該日期）
        return cached
    picks = analysis.filtered_picks(get_snapshot(c, dates[-1]))
    result = gemini.summarize_csv(
        picks, {}, analysis.subindustry_counts(picks), cfg.gemini_api_key
    )
    result["snap_date"] = dates[-1]   # 前端「AI 籌碼分析師」旁顯示資料日期
    if result.get("enabled"):
        set_ai_cache(c, key, result)
    return result

@router.get("/api/analysis/daily")
def daily(date: str | None = None):
    c = conn()
    dates = get_snapshot_dates(c)
    if not dates:
        return {"snap_date": None, "picks": [], "subindustry": []}
    snap = date if date in dates else dates[-1]
    picks = analysis.filtered_picks(get_snapshot(c, snap))
    return {"snap_date": snap, "picks": picks,
            "subindustry": analysis.subindustry_counts(picks)}

@router.get("/api/analysis/export")
def export(date: str | None = None, sub: str | None = None):
    c = conn()
    dates = get_snapshot_dates(c)
    snap = date if date in dates else (dates[-1] if dates else None)
    picks = analysis.filtered_picks(get_snapshot(c, snap)) if snap else []
    if sub:
        picks = [p for p in picks if p.get("sub_industry") == sub]
    data = exporter.picks_to_xlsx(picks, snap or "")
    fname = f"picks_{snap or 'empty'}.xlsx"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )

@router.get("/api/analysis/weekly")
def weekly():
    c = conn()
    dates = get_snapshot_dates(c)
    if len(dates) < 2:
        return {"stocks": [], "industry": [], "note": "需至少兩週快照才能比較"}
    # 週對週：上期取「上週的最後一份快照」而非前一個交易日——集保週資料一週一更，
    # 日對日比較的集保Δ沒有意義（曾顯示 07-16 vs 07-17 這種假跨週）
    this_date, last_date = analysis.pick_weekly_pair(dates)
    this_rows = get_snapshot(c, this_date)
    last_rows = get_snapshot(c, last_date)
    result = analysis.weekly_comparison(this_rows, last_rows)
    result["industry"] = analysis.industry_aggregate(this_rows)
    result["highlights"] = analysis.weekly_highlights(this_rows)   # LINE 週報卡：重點類股＋前五個股
    result["this_date"] = this_date
    result["last_date"] = last_date
    return result

@router.get("/api/analysis/summary")
def summary(refresh: int = 0):
    c = conn()
    return summary_logic(c, refresh=refresh)

@router.get("/public/api/overview")
def public_overview():
    c = conn()
    rows = c.execute("SELECT * FROM market_daily ORDER BY date DESC LIMIT 2").fetchall()
    m = dict(rows[0]) if rows else {}
    pv = dict(rows[1]) if len(rows) > 1 else {}
    secs = [s for s in _sectors_for(c, m["date"])
            if s.get("chg_pct") is not None] if m.get("date") else []
    ups = sorted([s for s in secs if (s.get("chg_pct") or 0) > 0], key=lambda s: -s["chg_pct"])[:3]
    downs = sorted([s for s in secs if (s.get("chg_pct") or 0) < 0], key=lambda s: s["chg_pct"])[:3]
    ai = market_summary_logic(c, refresh=0)
    intl = [{"key": k, "label": lb, "value": m.get(k), "chg_pct": m.get(k + "_chg")}
            for k, lb in _PUBLIC_INTL_FIELDS if m.get(k) is not None]
    
    rank = public_inst_rank(who="foreign", unit="shares")
    return {"date": m.get("date"),
            "inst_rank": {"buy": rank["buy"], "sell": rank["sell"]},
            "taiex": m.get("taiex"), "taiex_chg": m.get("taiex_chg"), "turnover": m.get("turnover"),
            "tx_price": m.get("tx_price"), "tx_chg": m.get("tx_chg"),
            "intl": intl,
            "inst": {"foreign": m.get("inst_foreign"), "trust": m.get("inst_trust"),
                    "dealer": m.get("inst_dealer"), "foreign_prev": pv.get("inst_foreign"),
                    "trust_prev": pv.get("inst_trust"), "dealer_prev": pv.get("inst_dealer")},
            "fut": {"tx_foreign_oi": m.get("tx_foreign_oi"), "tx_foreign_oi_prev": pv.get("tx_foreign_oi"),
                   "retail_ls_mtx": m.get("retail_ls_mtx"), "retail_ls_mtx_prev": pv.get("retail_ls_mtx"),
                   "retail_ls_tmf": m.get("retail_ls_tmf"), "retail_ls_tmf_prev": pv.get("retail_ls_tmf")},
            "margin": {"balance": m.get("margin_balance"), "chg": m.get("margin_chg"),
                      "value": m.get("margin_value"), "value_chg": m.get("margin_value_chg"),
                      "short_balance": m.get("short_balance"), "short_chg": m.get("short_chg"),
                      "maintenance": m.get("margin_maintenance"), "maintenance_prev": pv.get("margin_maintenance")},
            "sectors_up": [{"name": s["name"], "chg_pct": s["chg_pct"]} for s in ups],
            "sectors_down": [{"name": s["name"], "chg_pct": s["chg_pct"]} for s in downs],
            "ai_text": (ai.get("text") or "") if ai.get("enabled") else ""}

# ===== 公開總覽所需的唯讀端點 =====
# 一律直接呼叫站內既有 handler（純函式），不複製任何邏輯——公開頁與站內共用同一份渲染碼，
# 資料來源也必須是同一份，否則兩邊又會漂移。全部只註冊 GET，公開面不得有任何寫入端點。

@router.get("/public/api/dashboard")
def p_dashboard():
    return _dashboard()

@router.get("/public/api/breadth")
def p_breadth(date: str | None = None):
    return _breadth(date=date)

@router.get("/public/api/index-movers")
def p_index_movers(date: str | None = None, top: int = 20):
    return _index_movers(date=date, top=top)

@router.get("/public/api/heatmap")
def p_heatmap(date: str | None = None, market: str = "tse"):
    return _heatmap(date=date, market=market)

@router.get("/public/api/options-sentiment")
def p_options_sentiment():
    return _options_sentiment()

@router.get("/public/api/inst-ranking")
def p_inst_ranking(who: str = "foreign", date: str | None = None, top: int = 20, unit: str = "shares"):
    return _inst_ranking(who=who, date=date, top=top, unit=unit)

@router.get("/public/api/index/kline")
def p_index_kline(symbol: str = "taiex", interval: str = "1d"):
    return _index_kline(symbol=symbol, interval=interval)

@router.get("/public/api/tx/volume-sessions")
def p_tx_volume_sessions(days: int = 60):
    return _tx_volume_sessions(days=days)

@router.get("/public/api/market/summary")
def p_market_summary():
    """刻意不接受 refresh 參數：refresh=1 會觸發 Gemini 呼叫，
    公開端點若可帶此參數，等於任何匿名訪客都能替你燒 API 費用。只讀當日快取。"""
    return market_summary_logic(conn(), refresh=0)

@router.get("/public/api/inst-rank")
def public_inst_rank(who: str = "foreign", unit: str = "shares"):
    c = conn()
    date = _latest_date(c)
    if not date:
        return {"date": None, "who": who, "unit": unit, "buy": [], "sell": []}
    t = get_ai_cache(c, f"t86:{date}")
    if t is None:
        try:
            t = twse.fetch_t86(datetime.fromisoformat(date).date())
            if t:
                set_ai_cache(c, f"t86:{date}", t)
        except Exception:  # noqa: BLE001
            t = {}
    prices = {}
    if unit == "value":
        prices = get_ai_cache(c, f"close:{date}")
        if prices is None:
            try:
                prices = twse.fetch_close_prices(datetime.fromisoformat(date).date())
                if prices:
                    set_ai_cache(c, f"close:{date}", prices)
            except Exception:  # noqa: BLE001
                prices = {}
    items = []
    for code, v in (t or {}).items():
        if not (len(code) == 4 and code.isdigit() and not code.startswith("00")):
            continue
        lots = v.get(who)
        if lots is None:
            continue
        if unit == "value":
            close = (prices or {}).get(code)
            if close is None:
                continue
            net = round(lots * close / 1e5, 2)
        else:
            net = lots
        items.append({"code": code, "name": v.get("name") or code, "net": net})
    buy = sorted(items, key=lambda x: -x["net"])[:15]
    sell = sorted(items, key=lambda x: x["net"])[:15]
    return {"date": date, "who": who, "unit": unit, "buy": buy, "sell": sell}

_PUBLIC_CSS = """
:root{--bg:#0f1419;--panel:#1a2029;--border:#2b3038;--up:#e04545;--down:#2ea043;--accent:#f0a500;--text:#e6e6e6;--muted:#8a919c}
*{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--text);font-family:-apple-system,"Segoe UI",Roboto,"Noto Sans TC",sans-serif;line-height:1.7}
.wrap{max-width:640px;margin:0 auto;padding:20px 16px 40px}
h1{font-size:19px;color:var(--accent);margin:0 0 4px} .sub{color:var(--muted);font-size:13px;margin-bottom:18px}
.card{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:16px;margin-bottom:14px}
.card-title{color:var(--muted);font-size:13px;margin-bottom:8px}
.up{color:var(--up)} .down{color:var(--down)} .big{font-size:26px;font-weight:700}
.row{display:flex;justify-content:space-between;align-items:baseline;padding:5px 0;border-bottom:1px solid var(--border)}
.row:last-child{border-bottom:none}
.yd{color:var(--muted);font-size:11px;margin-left:4px}
.muted{color:var(--muted);font-size:13px} .ai{white-space:pre-wrap;font-size:14px}
a{color:var(--accent)}
.rank-grid{display:flex;gap:14px}
.rank-col{flex:1;min-width:0}
.rank-col h4{margin:2px 0 4px;font-size:12px;font-weight:400}
.rank-row{display:flex;justify-content:space-between;font-size:13px;padding:3px 0}
.rank-row .code{color:var(--muted);font-size:11px;margin-right:3px}
.tbtn{background:var(--panel);color:var(--text);border:1px solid var(--border);padding:5px 12px;
border-radius:6px;cursor:pointer;font-size:13px;margin:2px 4px 8px 0}
.tbtn.active{background:var(--accent);color:#1a1a1a;border-color:var(--accent);font-weight:700}
.tsep{color:var(--border);margin:0 4px}
"""

def _public_shell(title: str, body: str) -> str:
    return (f"<!doctype html><html lang='zh-Hant'><head><meta charset='utf-8'>"
            f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{title}｜股力智富</title><style>{_PUBLIC_CSS}</style></head>"
            f"<body><div class='wrap'>{body}</div></body></html>")

@router.get("/public/logic", response_class=HTMLResponse)
def public_logic_page():
    body = """
    <h1>🏆 選股邏輯說明</h1><div class="sub">亞當杯柄型態，全市場上市＋上櫃每日掃描</div>
    <div class="card">
    <p>同時滿足四個條件才入選：</p>
    <p>① <b>杯的左緣</b>：近 377 天（約 1.5 年）的大高點仍未被超越——曾經的強勢股。</p>
    <p>② <b>杯身夠寬</b>：左緣比近 55 天高點（右緣）早 55 根 K 棒以上——排除雙頂、確保是「杯」。</p>
    <p>③ <b>柄：回檔淺而守穩</b>：近 13 天沒再創 55 天新高（從右緣回檔中），且近 8 天低點高於近
    21 天低點（沒破低、賣壓收斂）。</p>
    <p>④ <b>強度濾網</b>：收盤位於近 55 天高低區間的上半部——弱勢整理不要。</p>
    <p><span style="color:var(--accent)">●</span> <b>趨勢線</b>＝左緣→右緣（杯口斜率）；
    <span style="color:#6cb6ff">●</span> <b>壓力線</b>＝右緣水平延伸，<b>突破壓力線＝進場訊號</b>。</p>
    </div>
    <div class="card">
    <p><b>盤中突破警示</b>：09:00–13:35 每 5 分鐘掃描一次，現價需同時通過兩道濾網才推播——
    突破幅度需超過「壓力線 + 0.3×ATR」（不是碰到就算，要有力道）；且需連續兩輪（約5分鐘）
    都站穩門檻之上，避免開盤瞬間插針、微幅探頭的假訊號。</p>
    </div>
    <div class="card muted">
    提醒：型態辨識為程式自動判定，盤中價有延遲；進場前請自行確認量價，並參考站內回測報告
    了解此策略的歷史勝率與限制。詳見<a href="/public/disclaimer">免責聲明</a>。
    </div>"""
    return _public_shell("選股邏輯說明", body)

@router.get("/public/disclaimer", response_class=HTMLResponse)
def public_disclaimer_page():
    body = """
    <h1>⚠️ 免責聲明</h1>
    <div class="card">
    <p>本站所有數據、型態訊號、AI 解讀與回測結果，<b>僅供參考，不構成任何投資建議</b>。</p>
    <p>歷史數據與回測績效不代表未來表現；型態辨識與盤中警示為程式自動判定，可能有誤判、
    延遲或資料來源異常，盤中報價尤其可能落後實際成交數秒至數十秒。</p>
    <p>回測結果未計入手續費、證交稅、滑價等交易成本，實際報酬會低於顯示數字；
    AI 解讀由語言模型自動生成，可能包含錯誤或過時資訊。</p>
    <p>任何買賣決策及其後果，請自行判斷並自負風險，本站作者不負 any 法律或財務責任。
    如需投資建議，請洽專業金融顧問。</p>
    </div>"""
    return _public_shell("免責聲明", body)


@router.get("/public/overview.js")
def public_overview_js():
    """舊版獨立公開頁的渲染腳本已停用（公開頁改為共用站內前端 app.js）。
    保留路由回傳空腳本，避免舊書籤/快取的頁面 404。"""
    return Response(content="// 已改為共用站內前端，見 /public/overview\n",
                    media_type="application/javascript")

@router.get("/public/overview", response_class=HTMLResponse)
def public_overview_page():
    """公開總覽：直接服務站內同一份 index.html，注入 data-public 旗標。

    如此 public 與站內共用同一套 HTML/CSS/JS 與設計系統，改一次兩邊同步——
    不再像舊版那樣維護第二套渲染邏輯（那正是 CLAUDE.md 記取的雙實作漂移教訓）。
    app.js 讀到旗標後：API 走 /public/api、隱藏非總覽導覽、且不觸發任何寫入操作。
    """
    with open(os.path.join(WEB_DIR, "index.html"), encoding="utf-8") as f:
        html = f.read()
    # index.html 用相對路徑引資產（站內在 / 底下正常）；本頁在 /public/overview，
    # 相對路徑會被解析成 /public/styles.css → 404。改絕對路徑（不用 <base href="/">：
    # 那會讓滿頁的 href="#" 錨點指向 "/"，點個股連結就跳離本頁）。
    html = (html.replace('href="styles.css"', 'href="/styles.css"')
                .replace('src="app.js"', 'src="/app.js"'))
    return HTMLResponse(html.replace("<body>", '<body data-public="1">', 1))

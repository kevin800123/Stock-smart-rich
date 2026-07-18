"""籌碼分析引擎：當日訊號榜、跨週比較、產業彙整。

訊號核心：大戶增比越高、人數降比越負（散戶減越多）→ 分數越高。
"""


def _num(v):
    return v if isinstance(v, (int, float)) and v is not None else 0.0


def _score(r: dict) -> float:
    # 大戶增比越高、人數降比越負（散戶減越多）得分越高
    return _num(r.get("big_holder_ratio")) - _num(r.get("holder_drop_ratio"))


def _flags(r: dict) -> dict:
    return {
        "w55_bull": _num(r.get("w55")) >= 1,
        "rev_growth": _num(r.get("rev_yoy")) > 0,
        "inst_buy": _num(r.get("trust_3d")) > 0 or _num(r.get("foreign_3d")) > 0,
    }


def daily_signals(rows: list[dict], top_n: int = 30) -> list[dict]:
    scored = []
    for r in rows:
        item = dict(r)
        item["score"] = round(_score(r), 4)
        item["flags"] = _flags(r)
        scored.append(item)
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_n]


def filtered_picks(rows: list[dict]) -> list[dict]:
    """選股篩選：W55=1（技術翻多）＋大戶增比>0＋營收年增>0＋推估EPS>0，再依蘭值由高到低排序。"""
    out = []
    for r in rows:
        if _num(r.get("w55")) < 1:
            continue
        if _num(r.get("big_holder_ratio")) <= 0:
            continue
        if _num(r.get("rev_yoy")) <= 0:
            continue
        if _num(r.get("est_profit")) <= 0:
            continue
        out.append(dict(r))
    out.sort(key=lambda r: (r["lan_value"] if r.get("lan_value") is not None else float("-inf")), reverse=True)
    return out


def subindustry_counts(rows: list[dict]) -> list[dict]:
    """統計（已篩選個股）每個細產業的檔數，由多到少排序。"""
    groups: dict[str, int] = {}
    for r in rows:
        key = r.get("sub_industry") or "未分類"
        groups[key] = groups.get(key, 0) + 1
    out = [{"sub_industry": k, "count": v} for k, v in groups.items()]
    out.sort(key=lambda x: x["count"], reverse=True)
    return out


# CSV 產業欄 → 官方類股名 的別名（少數命名差異；其餘去前綴後即相同）
_SECTOR_ALIAS = {"化工": "化學", "航運業": "航運", "金融": "金融保險",
                 "文化創意": "其他", "農業科技業": "其他"}


def industry_to_sector(industry: str | None) -> str | None:
    """CSV 產業欄（如「上市半導體」/「上櫃IC」）→ 官方類股名（「半導體」）。"""
    if not industry:
        return None
    name = industry
    for p in ("上市", "上櫃"):
        if name.startswith(p):
            name = name[len(p):]
            break
    return _SECTOR_ALIAS.get(name, name)


def margin_maintenance(lots_by_code: dict, closes: dict, margin_value_yi) -> float | None:
    """大盤整體融資維持率(%)≒ Σ(個股融資餘額張×1000×收盤) ÷ 融資金額 ×100。

    lots_by_code＝{代號: 融資餘額(張)}、closes＝{代號: 收盤}、margin_value_yi＝融資金額(億)。
    只加總兩邊都有的代號；缺報價的融資部位不在分子（比實際略低，屬保守估）。
    """
    if not margin_value_yi or margin_value_yi <= 0:
        return None
    value = sum(lots * 1000 * closes[code]
                for code, lots in lots_by_code.items() if code in closes and lots)
    if value <= 0:
        return None
    return round(value / (margin_value_yi * 1e8) * 100, 1)


DEFAULT_TRADE_FEE = 0.585  # 來回費用%＝買賣手續費 0.1425%×2 ＋ 賣出證交稅 0.3%


def trade_stats(trades: list[dict], closes: dict | None = None,
                taiex_by_date: dict | None = None) -> dict:
    """交易帳本統計（純函數）。trades＝db.list_trades 列；closes＝{代號: 最新收盤}
    供未平倉估值；taiex_by_date＝{日期: 加權收盤} 供同期大盤對照。

    每筆皆為「淨值」：報酬%＝毛報酬% − 來回費用%（fee_pct，NULL 用預設 0.585）；
    未平倉以最新收盤估、同樣先扣費用（出場終究要付，避免高估）。
    同期大盤取「≤ 該日的最近一個交易日」加權值；未平倉的出場參考日＝最新一個交易日。
    統計只算已平倉：期望值＝勝率×平均賺%＋(1−勝率)×平均賠%（每筆交易的期望報酬）。
    """
    closes = closes or {}
    tx_dates = sorted((taiex_by_date or {}).keys())

    def _taiex_at(ds):
        """≤ ds 的最近交易日加權值（非交易日進出場也對得到基準）。"""
        prior = [d for d in tx_dates if d <= ds]
        return taiex_by_date[prior[-1]] if prior else None

    enriched, wins, losses, alphas, realized, unrealized = [], [], [], [], 0, 0
    for t in trades:
        fee = t.get("fee_pct") if t.get("fee_pct") is not None else DEFAULT_TRADE_FEE
        closed = t.get("exit_price") is not None
        mark = t["exit_price"] if closed else closes.get(t["code"])
        e = dict(t, status="closed" if closed else "open",
                 mark=None if closed else mark,
                 net_pct=None, pnl=None, mkt_pct=None, alpha=None)
        if mark is not None and t.get("entry_price"):
            cost = t["entry_price"] * t["shares"]
            net_pct = (mark - t["entry_price"]) / t["entry_price"] * 100 - fee
            pnl = (mark - t["entry_price"]) * t["shares"] - cost * fee / 100
            e["net_pct"], e["pnl"] = round(net_pct, 2), round(pnl)
            m0 = _taiex_at(t["entry_date"]) if t.get("entry_date") else None
            m1 = _taiex_at(t["exit_date"]) if closed else (
                taiex_by_date[tx_dates[-1]] if tx_dates else None)
            if m0 and m1:
                mkt = (m1 - m0) / m0 * 100
                e["mkt_pct"], e["alpha"] = round(mkt, 2), round(net_pct - mkt, 2)
            if closed:
                realized += pnl
                (wins if net_pct > 0 else losses).append(net_pct)
                if e["alpha"] is not None:
                    alphas.append(net_pct - (m1 - m0) / m0 * 100)
            else:
                unrealized += pnl
        enriched.append(e)
    n = len(wins) + len(losses)
    win_rate = len(wins) / n * 100 if n else None
    avg_win = sum(wins) / len(wins) if wins else None
    avg_loss = sum(losses) / len(losses) if losses else None
    expectancy = ((win_rate / 100) * (avg_win or 0) + (1 - win_rate / 100) * (avg_loss or 0)) \
        if n else None
    return {"trades": enriched, "stats": {
        "closed_n": n, "open_n": sum(1 for e in enriched if e["status"] == "open"),
        "win_rate": round(win_rate, 1) if win_rate is not None else None,
        "avg_win": round(avg_win, 2) if avg_win is not None else None,
        "avg_loss": round(avg_loss, 2) if avg_loss is not None else None,
        "payoff": round(avg_win / abs(avg_loss), 2) if (avg_win and avg_loss) else None,
        "expectancy": round(expectancy, 2) if expectancy is not None else None,
        "realized_pnl": round(realized), "open_pnl": round(unrealized),
        "avg_alpha": round(sum(alphas) / len(alphas), 2) if alphas else None,
    }}


def picks_by_sector(picks: list[dict], sector_chg: dict) -> list[dict]:
    """把選股清單依官方類股分組，附該類股當日漲跌%，依漲跌%由強到弱排序。

    sector_chg：{官方類股名: 當日漲跌%}。回傳 [{sector, chg_pct, count, stocks:[...]}]。
    """
    groups: dict[str, list[dict]] = {}
    for p in picks:
        sec = industry_to_sector(p.get("industry"))
        if sec:
            groups.setdefault(sec, []).append(p)
    out = [{"sector": s, "chg_pct": sector_chg.get(s), "count": len(st), "stocks": st}
           for s, st in groups.items()]
    out.sort(key=lambda g: (g["chg_pct"] is None, -(g["chg_pct"] or 0)))
    return out


def pick_weekly_pair(dates: list[str]) -> tuple[str, str | None]:
    """跨週比較的日期配對：本期＝最新快照；上期＝最新一筆「ISO 週早於本期」的快照
    （即上週或更早的最後一份 CSV）。集保週資料一週一更，日對日比較的集保Δ沒有意義。
    全部同週（尚無上週資料）退回前一筆至少能比；單筆回 (d, None)。dates 需遞增排序。"""
    from datetime import date as _date
    if not dates:
        return ("", None)
    this = dates[-1]
    if len(dates) < 2:
        return (this, None)
    this_week = _date.fromisoformat(this).isocalendar()[:2]
    for d in reversed(dates[:-1]):
        if _date.fromisoformat(d).isocalendar()[:2] < this_week:
            return (this, d)
    return (this, dates[-2])


def weekly_comparison(this_rows: list[dict], last_rows: list[dict]) -> dict:
    """比較本週最新 vs 上週最新快照，標記每檔 新進榜/加速/持平/退榜 與集保大戶持股 Δ。"""
    last = {r["code"]: r for r in last_rows}
    this = {r["code"]: r for r in this_rows}
    stocks = []
    for code, r in this.items():
        prev = last.get(code)
        custody_delta = (
            round(_num(r.get("custody")) - _num(prev.get("custody")), 4) if prev else None
        )
        if not prev:
            status = "新進榜"
        elif _num(r.get("big_holder_ratio")) > _num(prev.get("big_holder_ratio")):
            status = "加速"
        else:
            status = "持平"
        stocks.append({**r, "custody_delta": custody_delta, "status": status})
    for code, prev in last.items():
        if code not in this:
            stocks.append({**prev, "custody_delta": None, "status": "退榜"})
    return {"stocks": stocks}


def industry_aggregate(rows: list[dict]) -> list[dict]:
    """依產業分組，算平均訊號分數並由高至低排名。"""
    groups: dict[str, list[float]] = {}
    for r in rows:
        key = r.get("industry") or "未分類"
        groups.setdefault(key, []).append(_score(r))
    out = [
        {"industry": k, "count": len(v), "avg_score": round(sum(v) / len(v), 4)}
        for k, v in groups.items()
    ]
    out.sort(key=lambda x: x["avg_score"], reverse=True)
    return out

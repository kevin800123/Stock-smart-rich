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

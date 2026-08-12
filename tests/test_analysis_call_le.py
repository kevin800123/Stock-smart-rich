"""推估季EPS（XQ 的 Call_LE，CSV 欄位「推估獲利」est_profit）：反推自使用者提供的 XS 原始碼。

原始 XS：
  VALUE111=SUMmation(GetField("月營收","M"),3)*100;                       //季營收
  IF 毛利率(Q)>毛利率(Q)[1] and 近3月營收>近3月營收[3] THEN
      VALUE112=ROUND(AVERage(毛利率(Q),2),2)                              // 改善中→近2季均
  ELSE VALUE112=MINList(毛利率(Q),毛利率(Q)[1]);                          // 否則→近2季最低（保守）
  VALUE113=highest(營業費用(Q),4);  VALUE114=highest(所得稅費用(Q),4);      // 近4季最高（保守）
  VALUE115=(VALUE111*(VALUE112/100)-VALUE113-VALUE114)*1000000;
  VALUE116=發行張數(D)*1000;  VALUE117=VALUE115/VALUE116;                 // 推估季EPS

單位換算（GetField 本身的單位不是官方公告單位，用真實數字反推驗證，非 XQ 官方文件）：
「月營收」GetField 是「億元」——VALUE111 的 *100 把「億元」轉「百萬元」；「營業費用」
「所得稅費用」GetField 直接是「百萬元」；最終 *1,000,000 把「百萬元」轉「元」。

驗證：用台積電 2026Q1 真實數字（官方季報，mopsfin 完整報表）代入，算出的推估 EPS≈20.91，
與官方公布的真實單季 EPS 22.08 相差約 5%——這個模型本來就是簡化近似（Revenue×毛利率－
費用－稅金 ≈ 稅後淨利，跳過營業外收支等因子），加上這裡沒有真的「近4季最高」費用/稅金
（用單季頂替），5% 落在合理範圍內，不是公式錯誤。
"""
from stocks_power_rich import analysis


def test_estimate_quarterly_eps_synthetic_hand_computed():
    """乾淨整數，逐步手算鎖公式機制。近3月營收 10(億)+10+10=30(億)，[3] 前一季 8+8+8=24(億)
    → 本季>上季 True；毛利率本季65>上季60 → 條件皆真 → 用近2季均 62.5%。
    季營收(百萬)=30*100=3000；毛利=3000*0.625=1875；近4季最高費用200、稅金100；
    淨利估=(1875-200-100)*1,000,000=1,575,000,000；股數1,000,000,000 → EPS=1.575。
    """
    out = analysis.estimate_quarterly_eps(
        monthly_revenue=[10, 10, 10, 8, 8, 8],   # 最新在前：本季3月+前季3月
        gross_margin=[65, 60],                    # 最新在前：本季、上季
        opex=[200, 150, 180, 190],
        tax=[100, 80, 90, 95],
        shares=1_000_000_000,
    )
    assert out == 1.575


def test_estimate_quarterly_eps_uses_min_when_not_improving():
    """毛利率沒有同時「改善中」（本季65<上季70）→ 改用近2季最低（保守），不用均值。"""
    out = analysis.estimate_quarterly_eps(
        monthly_revenue=[10, 10, 10, 8, 8, 8],
        gross_margin=[65, 70],   # 本季 < 上季 → 條件不成立
        opex=[200, 150, 180, 190],
        tax=[100, 80, 90, 95],
        shares=1_000_000_000,
    )
    # 毛利率改用 min(65,70)=65；季營收=3000；毛利=3000*0.65=1950
    # 淨利估=(1950-200-100)*1e6=1,650,000,000 → EPS=1.65
    assert out == 1.65


def test_estimate_quarterly_eps_real_tsmc_2026q1_sanity_check():
    """台積電 2026Q1 真實數字代入：季營收 11,341.0344(億)、毛利率 66.25%（單季代 2 季）、
    近4季最高費用/稅金用該季實際值頂替（非真正 4 季最高，只驗證單位換算量級），
    股數用本站既有股本×1e7 近似法（capital 2593.24 億元）。結果應落在官方真實 EPS 22.08
    的合理範圍內（模型簡化＋非真正 4 季最高，容許 ~10% 誤差）。
    """
    q1_revenue_billion = 1134103440 / 100000 / 3  # Q1 累計營收(仟元)→億元→平均每月
    out = analysis.estimate_quarterly_eps(
        monthly_revenue=[q1_revenue_billion] * 6,  # 假設近 6 個月月均營收持平（僅驗證單位）
        gross_margin=[66.25, 66.25],
        opex=[94005.657] * 4,      # 官方營業費用(仟元)/1000 → 百萬元
        tax=[114998.383] * 4,      # 官方所得稅費用(仟元)/1000 → 百萬元
        shares=2593.24 * 1e7,      # 股本(億元) × 1e7（既有 estimate_price_range 同款近似）
    )
    assert out is not None
    assert 18.0 <= out <= 24.0   # 官方真實 EPS 22.08，容許模型簡化的合理誤差帶


def test_estimate_quarterly_eps_insufficient_data_returns_none():
    assert analysis.estimate_quarterly_eps(
        monthly_revenue=[10, 10], gross_margin=[65, 60],
        opex=[200, 150, 180, 190], tax=[100, 80, 90, 95], shares=1000,
    ) is None  # 月營收不足 6 個月
    assert analysis.estimate_quarterly_eps(
        monthly_revenue=[10] * 6, gross_margin=[65],
        opex=[200, 150, 180, 190], tax=[100, 80, 90, 95], shares=1000,
    ) is None  # 毛利率不足 2 季
    assert analysis.estimate_quarterly_eps(
        monthly_revenue=[10] * 6, gross_margin=[65, 60],
        opex=[200, 150, 180], tax=[100, 80, 90, 95], shares=1000,
    ) is None  # 營業費用不足 4 季


def test_estimate_quarterly_eps_zero_shares_returns_none():
    assert analysis.estimate_quarterly_eps(
        monthly_revenue=[10] * 6, gross_margin=[65, 60],
        opex=[200, 150, 180, 190], tax=[100, 80, 90, 95], shares=0,
    ) is None


def test_estimate_quarterly_eps_none_in_series_returns_none():
    """任一必要欄位裡有 None（缺值）就不硬湊，回 None（同 lan_score 的守衛風格）。"""
    assert analysis.estimate_quarterly_eps(
        monthly_revenue=[10, 10, None, 8, 8, 8], gross_margin=[65, 60],
        opex=[200, 150, 180, 190], tax=[100, 80, 90, 95], shares=1000,
    ) is None

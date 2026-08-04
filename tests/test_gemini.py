from fastapi.testclient import TestClient

from stocks_power_rich import gemini
from stocks_power_rich.main import create_app
from stocks_power_rich.db import get_connection, init_db, upsert_market_daily


def test_market_summary_is_cached(tmp_path, monkeypatch):
    monkeypatch.setenv("SPR_DB_PATH", str(tmp_path / "t.sqlite"))
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    c = get_connection(str(tmp_path / "t.sqlite"))
    init_db(c)
    upsert_market_daily(c, {"date": "2026-06-17", "taiex": 23000.0, "updated_at": "2026-06-17T15:00:00"})

    calls = {"n": 0}

    def fake_summarize(market_row, api_key):
        calls["n"] += 1
        return {"enabled": True, "text": "盤勢偏多"}

    monkeypatch.setattr(gemini, "summarize_market", fake_summarize)
    client = TestClient(create_app())
    first = client.get("/api/market/summary").json()
    second = client.get("/api/market/summary").json()
    assert first["text"] == "盤勢偏多" and second["text"] == "盤勢偏多"
    assert calls["n"] == 1  # 第二次走快取，不再呼叫 Gemini


def test_degrades_without_key():
    out = gemini.summarize_market({"taiex": 23000}, api_key="")
    assert out["enabled"] is False
    assert "未啟用" in out["text"]


def test_news_summary_prompt_requires_structured_sourced_brief(monkeypatch):
    captured = {}

    def fake_run(prompt, api_key, **kw):
        captured["prompt"] = prompt
        return {"enabled": True, "text": "ok"}

    monkeypatch.setattr(gemini, "_run", fake_run)
    out = gemini.summarize_news(
        {
            "slot": "afternoon",
            "report_date": "2026-08-03",
            "snapshot": {"taiex": 24000},
            "markets": {"tw": [{"title": "台股標題", "source": "Example"}]},
        },
        api_key="k",
    )

    assert out["text"] == "ok"
    assert "2026-08-03 每日財經重點速覽" in captured["prompt"]
    assert "🇹🇼 台股｜6 則精選" in captured["prompt"]
    assert "🇺🇸 美股｜6 則精選" in captured["prompt"] and "🇯🇵 日股｜6 則精選" in captured["prompt"]
    assert "每個市場都要恰好 6 則新聞" in captured["prompt"]
    assert "株探翻譯" in captured["prompt"] and "日股新聞必須先翻譯" in captured["prompt"]
    assert "🧾 **事件**" in captured["prompt"] and "🔢 **關鍵數據**" in captured["prompt"]
    assert "不得補造" in captured["prompt"] and "非投資建議" in captured["prompt"]


def test_uses_model_when_key(monkeypatch):
    class FakeResp:
        text = "盤勢偏多"

    class FakeModels:
        def generate_content(self, model, contents, config=None):
            return FakeResp()

    class FakeClient:
        def __init__(self, api_key):
            self.models = FakeModels()

    monkeypatch.setattr(gemini, "genai_client", lambda key: FakeClient(key))
    out = gemini.summarize_market({"taiex": 23000}, api_key="k")
    assert out["enabled"] is True
    assert out["text"] == "盤勢偏多"


def test_news_prompts_never_carry_the_long_google_news_urls(monkeypatch):
    """網址是純浪費：模型不需要（延伸閱讀是 Python 端從同一份 markets 組的），
    但 Google 新聞的轉址網址每則 250~350 字元、60 則、兩支 prompt 各帶一次，
    實測占輸入 ~55%。月額度是有限的，不該花在這上面。"""
    from stocks_power_rich import gemini

    long_url = "https://news.google.com/rss/articles/CBMi" + "A" * 250
    markets = {m: [{"title": f"{m} 標題", "url": long_url, "source": "經濟日報"}]
               for m in ("tw", "us", "jp")}
    payload = {"slot": "afternoon", "report_date": "2026-08-04",
               "snapshot": {"加權指數": 43386.0}, "markets": markets}

    seen = []
    # 一定要用 monkeypatch：直接指派 gemini._run 會殘留到後續測試，
    # 讓 test_degrades_without_key 這種「單獨跑會過、整批跑就掛」的假失敗出現。
    monkeypatch.setattr(gemini, "_run",
                        lambda prompt, api_key, **kw: seen.append(prompt) or {"enabled": True, "text": "x"})
    gemini.summarize_news(payload, "k")

    assert len(seen) == 1
    for prompt in seen:
        assert long_url not in prompt
        assert "CBMi" not in prompt
        assert "tw 標題" in prompt          # 標題仍要餵進去
        assert "經濟日報" in prompt          # 來源保留（模型會提到株探/中央社）

    # 不可就地改壞呼叫端的 markets——build_reading_links 還要用網址組延伸閱讀
    assert markets["tw"][0]["url"] == long_url


def test_slim_markets_is_pure_and_handles_empty():
    from stocks_power_rich.gemini import slim_markets

    assert slim_markets({}) == {}
    assert slim_markets({"tw": []}) == {"tw": []}
    src = {"tw": [{"title": "t", "url": "u", "source": "s"}]}
    assert slim_markets(src) == {"tw": [{"title": "t", "source": "s"}]}
    assert "url" in src["tw"][0]


def test_thinking_is_off_for_mechanical_news_calls_and_on_for_market_judgement(monkeypatch):
    """gemini-2.5-flash 預設開啟動態思考，而思考 token 以「輸出」計價（牌價約為
    輸入的 8 倍），往往才是帳單大頭。新聞摘要是機械性工作（讀標題→照格式吐條列
    →翻譯），一天跑 4 次，關掉思考；summarize_market 要判斷背離／誘多這類跨指標
    因果、且一天只跑一次，保留思考。"""
    from stocks_power_rich import gemini

    seen = {}
    monkeypatch.setattr(gemini, "_run", lambda prompt, api_key, thinking=True:
                        seen.__setitem__(len(seen), thinking) or {"enabled": True, "text": "x"})

    payload = {"slot": "afternoon", "report_date": "2026-08-04",
               "snapshot": {}, "markets": {"tw": [{"title": "t", "source": "s"}]}}
    gemini.summarize_news(payload, "k")
    gemini.summarize_market({}, "k")

    assert seen[0] is False, "summarize_news 應關閉思考"
    assert seen[1] is True, "summarize_market 是判讀型，保留思考"


def test_thinking_config_uses_minimal_level_not_the_rejected_zero_budget():
    """Gemini 3.x 對 thinking_budget=0 直接回 400（實測 3.6-flash／flash-latest／
    3.5-flash-lite 皆然），必須用 thinking_level='minimal'。"""
    from stocks_power_rich.gemini import _thinking_config

    assert _thinking_config(True) is None          # None = 用模型預設（開啟思考）
    cfg = _thinking_config(False)
    # SDK 會把字串收斂成 ThinkingLevel enum（值是大寫 'MINIMAL'），比對用 .value
    assert cfg.thinking_config.thinking_level.value.lower() == "minimal"
    assert cfg.thinking_config.thinking_budget is None


def test_model_is_pinned_to_an_explicit_version_not_a_latest_alias():
    """釘版本壞掉是 404、看得見、好修；別名會某天無聲換模型讓輸出漂移。"""
    from stocks_power_rich import gemini

    assert "latest" not in gemini.MODEL
    assert gemini.MODEL != "gemini-2.5-flash", "2.5-flash 已不開放新專案，會 404"


def test_usage_logging_never_breaks_the_summary(capsys):
    """記帳失敗不能影響摘要本身；成功時要印出一行可回推額度的用量。"""
    from stocks_power_rich.gemini import _log_usage

    class U:
        prompt_token_count, candidates_token_count = 6710, 2800
        thoughts_token_count, total_token_count = 0, 9510

    class R:
        usage_metadata = U()

    _log_usage(R(), False)
    out = capsys.readouterr().out
    assert "in=6710" in out and "out=2800" in out and "thinking=off" in out

    class Broken:
        @property
        def usage_metadata(self):
            raise RuntimeError("no metadata")

    _log_usage(Broken(), True)      # 不可拋出


def test_friendly_error_never_dumps_the_raw_quota_json_to_the_page():
    """SDK 的 429 例外是一整包巢狀 JSON（quotaMetric／violations／retryDelay／兩條連結），
    原樣放進 text 會在網頁上佔掉半個畫面，而且對使用者沒有可行動資訊。"""
    from stocks_power_rich.gemini import friendly_error

    raw = ("429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your "
           "current quota... Quota exceeded for metric: generativelanguage.googleapis.com/"
           "generate_content_free_tier_requests, limit: 20, model: gemini-3.5-flash', "
           "'status': 'RESOURCE_EXHAUSTED', 'details': [{'@type': 'type.googleapis.com/"
           "google.rpc.QuotaFailure', 'violations': [{'quotaId': "
           "'GenerateRequestsPerDayPerProjectPerModel-FreeTier'}]}]}}")
    msg = friendly_error(Exception(raw))

    assert len(msg) < 60
    for noise in ("quotaMetric", "violations", "@type", "googleapis", "retryDelay", "{"):
        assert noise not in msg, noise
    assert "今日免費額度" in msg and "明日" in msg      # 說明何時會好
    assert "盤面數字不受影響" in msg                    # 說明還有什麼可用


def test_friendly_error_distinguishes_the_failure_kinds():
    from stocks_power_rich.gemini import friendly_error

    # 每分鐘限流（非每日額度）→ 說「稍後自動重試」而不是「明日恢復」
    rpm = friendly_error(Exception("429 RESOURCE_EXHAUSTED quota per minute"))
    assert "稍後" in rpm and "明日" not in rpm

    assert "模型" in friendly_error(Exception("404 NOT_FOUND. model not available"))
    assert "金鑰" in friendly_error(Exception("403 PERMISSION_DENIED"))
    assert "忙碌" in friendly_error(Exception("503 UNAVAILABLE"))
    # 未知錯誤：只取第一行且截斷，不整包倒出來
    long = friendly_error(Exception("Weird failure\n" + "x" * 500))
    assert len(long) < 140 and "\n" not in long


def test_run_logs_the_full_error_but_returns_only_the_short_one(monkeypatch, capsys):
    """診斷資訊不能消失——完整例外要進 stdout（Zeabur 收得到）。"""
    from stocks_power_rich import gemini

    def boom(api_key):
        raise RuntimeError("429 RESOURCE_EXHAUSTED free_tier PerDay limit: 20")

    monkeypatch.setattr(gemini, "genai_client", boom)
    out = gemini._run("p", "k")
    assert out["enabled"] is False
    assert "今日免費額度" in out["text"]
    logged = capsys.readouterr().out
    assert "RESOURCE_EXHAUSTED" in logged and "PerDay" in logged

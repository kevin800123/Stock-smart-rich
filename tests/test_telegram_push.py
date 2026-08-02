from stocks_power_rich import telegram_push as tp


def test_escape_mdv2_covers_every_special_character():
    raw = "_*[]()~`>#+-=|{}.!\\"
    out = tp.escape_mdv2(raw)
    for ch in "_*[]()~`>#+-=|{}.!":
        assert f"\\{ch}" in out
    assert "\\\\" in out


def test_escape_mdv2_empty_and_none():
    assert tp.escape_mdv2("") == ""
    assert tp.escape_mdv2(None) == ""


def test_utf16_len_counts_surrogate_pairs_for_emoji():
    # 📈 (U+1F4C8) 落在輔助平面，UTF-16 用代理對表示＝2 個 unit；純 ASCII 則 1:1
    assert tp.utf16_len("abc") == 3
    assert tp.utf16_len("📈") == 2
    assert tp.utf16_len("📈台股") == 4  # 2 + 1 + 1


def test_split_message_returns_single_chunk_when_under_limit():
    text = "第一行\n第二行\n第三行"
    assert tp.split_message(text) == [text]


def test_split_message_returns_empty_list_for_empty_text():
    assert tp.split_message("") == []


def test_split_message_splits_on_newlines_and_numbers_chunks():
    # 每行 UTF-16 長度 100，budget = 4096-50 = 4046，一個 chunk 約塞 40 行
    line = "字" * 100
    text = "\n".join([line] * 60)
    chunks = tp.split_message(text)
    assert len(chunks) > 1
    assert chunks[0].endswith(f"(1/{len(chunks)})")
    assert chunks[-1].endswith(f"({len(chunks)}/{len(chunks)})")
    # 沒有任何一行被硬切斷（原始長行仍完整出現在某個 chunk 裡，扣掉頁碼後綴）
    for c in chunks:
        body = c.rsplit("\n(", 1)[0]
        assert all(len(seg) == 0 or seg == line for seg in body.split("\n"))


def test_split_message_hard_cuts_a_single_line_longer_than_budget():
    huge = "字" * 5000
    chunks = tp.split_message(huge)
    assert len(chunks) > 1
    for c in chunks:
        assert tp.utf16_len(c) <= tp.MAX_MESSAGE_LENGTH


def test_send_message_without_token_degrades():
    r = tp.send_message("", "123", "hi")
    assert r["ok"] is False and "TELEGRAM_BOT_TOKEN" in r["error"]


def test_send_message_without_chat_id_degrades():
    r = tp.send_message("tok", "", "hi")
    assert r["ok"] is False and "TELEGRAM_CHAT_ID" in r["error"]


def test_send_message_empty_text_degrades():
    r = tp.send_message("tok", "123", "")
    assert r["ok"] is False


def test_send_message_success(monkeypatch):
    calls = []

    class FakeResp:
        status_code = 200
        text = "ok"

    def fake_post(url, timeout=None, json=None):
        calls.append((url, json))
        return FakeResp()

    monkeypatch.setattr(tp.httpx, "post", fake_post)
    r = tp.send_message("tok", "123", "hello")
    assert r["ok"] is True
    assert calls[0][1]["parse_mode"] == "MarkdownV2"
    assert calls[0][1]["chat_id"] == "123"


def test_send_message_falls_back_to_plain_text_on_markdown_parse_failure(monkeypatch):
    """實測風險：跳脫沒做乾淨會讓 Telegram 整則退件——這裡驗證退純文字重送的路徑。"""
    calls = []

    class BadMarkdown:
        status_code = 400
        text = "Bad Request: can't parse entities"

    class OkPlain:
        status_code = 200
        text = "ok"

    def fake_post(url, timeout=None, json=None):
        calls.append(json)
        return BadMarkdown() if json.get("parse_mode") else OkPlain()

    monkeypatch.setattr(tp.httpx, "post", fake_post)
    r = tp.send_message("tok", "123", "壞*符號")
    assert r["ok"] is True
    assert len(calls) == 2
    assert "parse_mode" not in calls[1]


def test_send_message_network_error_returns_error_not_exception(monkeypatch):
    def boom(*a, **k):
        raise ConnectionError("timeout")
    monkeypatch.setattr(tp.httpx, "post", boom)
    r = tp.send_message("tok", "123", "hi")
    assert r["ok"] is False and "timeout" in r["error"]

"""Telegram Bot API 推播：每日財經新聞速覽（07:00／17:00／21:00）。

訊息組裝為純函數（單元測試）；網路呼叫為 thin wrapper，無 token 時安全降級——
同 line_push.py 的分工。MarkdownV2 需要呼叫端自行跳脫特殊字元，這裡集中處理，
不假手任何 runtime 幫忙轉換（那正是每日財經專案 HANDOFF.md 記載最容易踩的坑）。
"""
import re

import httpx

API_BASE = "https://api.telegram.org/bot{token}/sendMessage"
# Telegram 硬上限：一則訊息 4096（以 UTF-16 code unit 計，不是 Python 字元數，
# emoji 等非 BMP 字元算 2）。留 50 給多頁訊息可能附加的頁碼提示。
MAX_MESSAGE_LENGTH = 4096
_SPLIT_RESERVE = 50

# MarkdownV2 規定這些字元在一般文字裡都要跳脫，否則 Telegram 直接退件（整則不送）。
_MDV2_ESCAPE_RE = re.compile(r"([_*\[\]()~`>#+\-=|{}.!\\])")


def escape_mdv2(text: str) -> str:
    """跳脫 MarkdownV2 特殊字元。呼叫端要先組好 *粗體* 這類語法，再對其餘純文字
    片段呼叫這支——不能對整段已含語法的文字呼叫，否則語法符號也會被跳脫掉。"""
    return _MDV2_ESCAPE_RE.sub(r"\\\1", text or "")


def mdv2_link(label: str, url: str) -> str:
    """MarkdownV2 行內連結：可見文字只有 label，長網址藏在後面。

    Google 新聞的 `CBMi…` 轉址網址實測 174～354 字元且是 protobuf 編碼、**解不出原始
    短網址**，所以「縮短連結」唯一可行的做法就是行內連結——讓讀者看到的是「🇹🇼 中央社」
    這種短標籤。代價是網址仍計入 4096 上限，因此只放少數幾條。
    label 要完整跳脫；url 依 Telegram 規定只需處理 `)` 與 `\\`（過度跳脫會把網址弄壞）。
    """
    safe_url = (url or "").replace("\\", "\\\\").replace(")", "\\)")
    return f"[{escape_mdv2(label)}]({safe_url})"


def utf16_len(text: str) -> int:
    """Telegram 用 UTF-16 code unit 計長度，不是 Python 的 len()。

    多數中文字在 BMP 內、UTF-16 一個字算一個 unit，但 emoji（如 📈🌟💥）多半落在
    輔助平面，UTF-16 要用代理對（surrogate pair）表示，等於 2 個 unit。一則含
    30 個 emoji 的財經速覽，len() 和實際 Telegram 計數可以差 30，貼著上限送會低估。
    """
    return sum(2 if ord(c) > 0xFFFF else 1 for c in (text or ""))


def split_message(text: str, limit: int = MAX_MESSAGE_LENGTH) -> list:
    """依 UTF-16 長度切成多則，優先在換行處切，不切壞任何一行。

    財經速覽目前實測約 1200～1900 UTF-16 units（遠低於 4096），這支多半用不到，
    但留著是因為訊息長度會隨新聞則數與 AI 輸出長短波動，沒有硬上限就是隱性風險。
    """
    if utf16_len(text) <= limit:
        return [text] if text else []
    budget = limit - _SPLIT_RESERVE
    lines = text.split("\n")
    chunks, cur, cur_len = [], [], 0
    for line in lines:
        line_len = utf16_len(line) + 1   # +1 給换行本身
        if cur and cur_len + line_len > budget:
            chunks.append("\n".join(cur))
            cur, cur_len = [], 0
        # 單行本身就超長（極端情況）：硬切，不留給下一輪繼續累加
        while utf16_len(line) > budget:
            cut = len(line)
            while cut > 0 and utf16_len(line[:cut]) > budget:
                cut -= 1
            chunks.append(line[:cut])
            line = line[cut:]
        cur.append(line)
        cur_len += utf16_len(line) + 1
    if cur:
        chunks.append("\n".join(cur))
    if len(chunks) > 1:
        chunks = [f"{c}\n({i}/{len(chunks)})" for i, c in enumerate(chunks, 1)]
    return chunks


def send_message(token: str, chat_id: str, text: str) -> dict:
    """送一則（必要時自動分段）。無 token／chat_id 安全降級，不拋例外。

    MarkdownV2 解析失敗時退純文字重送一次——同 Hermes adapter 的雙軌策略，避免
    一個沒跳脫乾淨的符號讓整則訊息完全送不出去。

    回傳值帶 `parse_mode_used`（MarkdownV2／plain）。這個欄位存在的理由是實戰教訓：
    退純文字這條路是**無聲**的——訊息照樣送達、看起來正常，只是粗體與行內連結全部
    失效。先前每一則推播都含 `(8306)`、`3.5%` 這類未跳脫字元，等於長期都在走退路而
    沒人發現。回報實際走哪條，才能在 /api/news/test 一眼看出跳脫有沒有做對。
    """
    if not token:
        return {"ok": False, "error": "未設定 TELEGRAM_BOT_TOKEN"}
    if not chat_id:
        return {"ok": False, "error": "未設定 TELEGRAM_CHAT_ID"}
    if not text:
        return {"ok": False, "error": "空訊息"}
    url = API_BASE.format(token=token)
    last = {"ok": False, "error": "未送出"}
    used = set()
    for chunk in split_message(text):
        try:
            r = httpx.post(url, timeout=15,
                           json={"chat_id": chat_id, "text": chunk,
                                "parse_mode": "MarkdownV2", "disable_web_page_preview": True})
            if r.status_code != 200:
                # MarkdownV2 解析失敗（跳脫沒做乾淨）→ 退純文字重送一次
                r2 = httpx.post(url, timeout=15,
                                json={"chat_id": chat_id, "text": chunk,
                                     "disable_web_page_preview": True})
                used.add("plain")
                last = {"ok": r2.status_code == 200, "status": r2.status_code}
                if r2.status_code != 200:
                    last["error"] = r2.text[:200]
                    last["markdown_error"] = r.text[:200]
            else:
                used.add("MarkdownV2")
                last = {"ok": True, "status": 200}
        except Exception as e:  # noqa: BLE001 — 推播失敗不影響主流程
            return {"ok": False, "error": str(e)}
    last["parse_mode_used"] = "plain" if "plain" in used else "MarkdownV2"
    return last

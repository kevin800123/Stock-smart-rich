"""LINE webhook：使用者傳訊息給官方帳號 → 以 reply 回覆當日內容。

為什麼存在：broadcast/push 按「收訊人數」計入每月免費額度（免費方案 200 則），
而 **reply 完全不計額度**。把主動查詢走這條路，額度就永遠用不完。

安全：本路徑必須免帳密（LINE 伺服器無法帶 HTTP Basic Auth，見 main.py 白名單），
所以 **channel secret 簽章是唯一把關**——未設定 LINE_CHANNEL_SECRET 時整個 webhook 關閉（503）。
無論指令認不認得，只要簽章過就回 200；回非 2xx 會被 LINE 判定 webhook 失效並停用。
"""
from fastapi import APIRouter, Request, Response

from .. import line_push
from ..config import load_config
from .deps import conn
from .helpers import _compose_daily_text, _weekly_text, _rank_text

router = APIRouter()


def _text_for(c, cmd: str) -> str:
    """指令 → 回覆內容。組裝失敗一律降級成可讀訊息，不讓例外冒到 webhook。"""
    try:
        if cmd in ("brief", "full"):
            # force=True：使用者自己問的就該回，即使今天還沒更新（推播才需要 staleness 保護）
            txt, err = _compose_daily_text(c, full=(cmd == "full"), force=True)
            return txt or f"目前無法組裝訊息（{(err or {}).get('error')}）"
        if cmd == "weekly":
            return _weekly_text(c)
        if cmd == "rank":
            return _rank_text(c)
    except Exception as e:  # noqa: BLE001
        return f"查詢失敗：{e}"
    return line_push.HELP_TEXT


@router.post("/line/webhook")
async def line_webhook(request: Request):
    cfg = load_config()
    if not cfg.line_secret:
        return Response(status_code=503)
    body = await request.body()
    if not line_push.verify_signature(cfg.line_secret, body,
                                      request.headers.get("X-Line-Signature", "")):
        return Response(status_code=403)
    try:
        import json
        events = line_push.parse_webhook_events(json.loads(body or b"{}"))
    except ValueError:
        events = []          # 壞 JSON：簽章既然過了就當空事件，仍回 200
    c = conn()
    for ev in events:
        cmd = line_push.route_command(ev["text"])
        txt = line_push.HELP_TEXT if cmd in (None, "help") else _text_for(c, cmd)
        line_push.reply_text(cfg.line_token, ev["reply_token"], txt)
    return {"ok": True, "handled": len(events)}

"""操盤手註冊表：每位操盤手 = 一個模組，實作統一契約，供 `/api/traders*` 端點與前端「操盤手」頁使用。

要新增一位操盤手（例如「莎拉王」）：
  1. 在本套件下建 `<id>.py`，提供：
       META = {"id","name","emoji","tagline","desc"}   # 純字典
       def analyze(conn) -> {"date","sections":[...],"disclaimer"}
     其中 sections 為通用區塊清單，每塊帶 `type`，前端據此泛型渲染：
       {"type":"checklist","title","items":[{name,status,value,note}]}   # status: bull|bear|warn|neutral|na
       {"type":"table","title","note?","columns":[{key,label,kind}],"rows":[...],"empty"}
                                                # kind: stock|num|num1|num2|lan|text
       {"type":"routine","title","groups":[{label,items:[...]}]}
       {"type":"note","title","text"}
  2. 於下方 `_MODULES` 追加該模組。
  3.（選配）在 `.claude/skills/<id>/SKILL.md` 放質化方法論，供 Claude 深度分析。
前端與端點皆不需改動——加人只要新增一個模組。
"""
from . import ss

_MODULES = [ss]   # 顯示順序即此清單順序
REGISTRY = {m.META["id"]: m for m in _MODULES}


def list_traders() -> list[dict]:
    """操盤手清單（供前端畫人物選單）。"""
    return [dict(m.META) for m in _MODULES]


def get_trader(tid: str):
    """取得操盤手模組；不存在回 None。"""
    return REGISTRY.get(tid)

"""LLM 映射助手（**提案制**）—— 只出候选，不生效、不写数据资产。

定位（跨线交接单 §7.5 + 零号铁律）：
    - 代码负责"切片"（`.grp/.rcp` 解析已由数据线 `datasets_menu.py` 完成），
      大文件**绝不整份喂 LLM**（一份 .grp ≈ 15 万 token）。
    - LLM 只做**语义映射提案**：把"未映射的机台列名"对到规范键候选，给置信度与理由。
    - **LLM 不得输出任何数值**、不得改契约、不得写 core；提案落
      `server/kb/adapters/proposed/<tool>.json`，由人在面板里逐条采纳，
      采纳后走既有流程升级契约（改 `datasets_menu.MFC_PARAM/MENU_PARAM` + `schema_v0.1.md` §13.2）。
    - 列名对不上（如 MFC4 到底接什么气）属**设备研究**，不是靠 LLM 猜 ——
      这类提案只作线索，必须标注 `needs_human=True`。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .form_contract import param_keys

PROPOSED_DIR = Path(__file__).resolve().parent / "adapters" / "proposed"
MAX_COLS = 40          # 单次最多提案多少列（控制 token 与幻觉面）


def _safe_tool(tool: str) -> str:
    """机台名只能当文件名的一部分（防路径穿越）。"""
    return re.sub(r"[^\w.-]", "_", (tool or "unknown").strip())[:40] or "unknown"


def _system_prompt() -> str:
    return (
        "你是半导体设备数据接入助手。任务：把**机台导出里的未知列名**映射到给定的**规范键**。\n"
        "硬规则（必须遵守）：\n"
        "1. 只输出 JSON，不要任何解释性前后文。\n"
        "2. **不得输出任何数值、单位换算或猜测的数据值** —— 你只做名字到名字的映射。\n"
        "3. 只允许从给定的规范键列表里选；没有合适候选就写 null。\n"
        "4. 涉及**物理通道/气路归属**（MFC4 接什么气、哪路阀）这类必须靠设备研究的，"
        "一律 needs_human=true，confidence 不超过 0.4。\n"
        "5. 输出格式：{\"mappings\":[{\"column\":\"...\",\"suggest\":\"规范键或null\","
        "\"confidence\":0.0-1.0,\"reason\":\"简短理由\",\"needs_human\":true|false}]}"
    )


def propose_mappings(tool: str, columns: list[str], sample_values: dict | None = None) -> dict:
    """未映射列 → 规范键候选。返回提案 dict（同时落盘到 proposed/）。"""
    from agent import llm

    cols = [c for c in (columns or []) if c and c.strip()][:MAX_COLS]
    if not cols:
        return {"tool": tool, "mappings": [], "note": "没有未映射列，无需提案"}
    keys = param_keys()
    known = sorted(k for k in keys if keys[k].get("from") != "(结构键)")
    lines = []
    for c in cols:
        v = (sample_values or {}).get(c)
        lines.append(f"- {c}" + (f"（样例值: {v}）" if v not in (None, "") else ""))
    user = (
        f"机台: {tool}\n"
        f"规范键候选（只能从这里选）: {', '.join(known)}\n\n"
        f"未映射的机台列名:\n" + "\n".join(lines)
    )
    raw = llm.chat(_system_prompt(), [{"role": "user", "content": user}], temperature=0.0)

    data = _parse_json(raw)
    mappings = []
    for m in (data.get("mappings") or []):
        s = m.get("suggest")
        mappings.append({
            "column": str(m.get("column") or ""),
            "suggest": s if s in known else None,          # **白名单强校验**：不在候选里就作废
            "confidence": float(m.get("confidence") or 0),
            "reason": str(m.get("reason") or "")[:200],
            "needs_human": bool(m.get("needs_human")) or s not in known,
            "raw_suggest": s if s not in known and s else None,   # 模型越界时留痕，便于人看
        })
    res = {
        "tool": tool, "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "model": _model_name(), "columns": cols, "mappings": mappings,
        "status": "proposed",                    # 提案：**不生效**
        "applied": False,
        "how_to_apply": ("采纳后需改 datasets_menu.MFC_PARAM/MENU_PARAM 并同步 "
                         "schema_v0.1.md §13.2，再跑 menu_regression.py —— 由数据线复核后升格"),
    }
    _save(tool, res)
    return res


def _parse_json(raw: str) -> dict:
    """LLM 返回里抠 JSON（它常裹 ```json 围栏或加前后话）。"""
    if not raw:
        return {}
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


def _model_name() -> str:
    try:
        from agent import llm
        return llm._cfg()[2]                      # noqa: SLF001
    except Exception:                             # noqa: BLE001
        return ""


def _save(tool: str, payload: dict) -> Path:
    """**唯一允许 LLM 链路写的位置**（提案区）。"""
    PROPOSED_DIR.mkdir(parents=True, exist_ok=True)
    p = PROPOSED_DIR / f"{_safe_tool(tool)}.json"
    hist = []
    if p.exists():
        try:
            hist = json.loads(p.read_text(encoding="utf-8")).get("history", [])
        except Exception:                         # noqa: BLE001
            hist = []
    payload = dict(payload)
    payload["history"] = (hist + [{"at": payload.get("generated_at"),
                                   "columns": payload.get("columns"),
                                   "model": payload.get("model")}])[-10:]
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def load_proposal(tool: str) -> dict | None:
    p = PROPOSED_DIR / f"{_safe_tool(tool)}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:                             # noqa: BLE001
        return None


def list_proposals() -> list[dict]:
    if not PROPOSED_DIR.exists():
        return []
    out = []
    for p in sorted(PROPOSED_DIR.glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            out.append({"tool": d.get("tool", p.stem), "file": str(p),
                        "generated_at": d.get("generated_at"),
                        "columns": len(d.get("columns") or []),
                        "mappings": len([m for m in (d.get("mappings") or []) if m.get("suggest")]),
                        "needs_human": len([m for m in (d.get("mappings") or []) if m.get("needs_human")]),
                        "applied": bool(d.get("applied"))})
        except Exception:                         # noqa: BLE001
            continue
    return out


def verify_mapping(proposed: dict) -> dict:
    """自证：把提案映射套到真实 dump 上，看能不能把"未映射列"清零。

    只做**字符串映射**检查（不碰数值），所以可以安全地在采纳前反复跑。
    """
    from . import menu_checker as mc
    from .menu_reader import parser
    out = {"checked": 0, "still_unmapped": [], "now_covered": []}
    for m in proposed.get("mappings") or []:
        col, sug = m.get("column"), m.get("suggest")
        out["checked"] += 1
        (out["now_covered"] if sug else out["still_unmapped"]).append(col)
    return out

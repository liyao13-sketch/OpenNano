"""RAG 检索 + 可信度加权提示构建(组织记忆喂 LLM)。

v1 用词法检索(标题/材料/标签/工艺类型/结果字段的 token 重叠),后续换 ChromaDB 向量。
"""
from __future__ import annotations

import re

from kb.store import KBStore

# 工艺关键词 → 命中优先的 process_type
_TYPE_HINTS = {
    "刻蚀": ["RIE_Cl", "RIE_F", "DRIE_Bosch", "ICP"],
    "ta": ["RIE_Cl"], "al": ["RIE_Cl"], "nb": ["RIE_Cl"], "cr": ["RIE_Cl"],
    "mo": ["RIE_Cl"],
    "si": ["RIE_F", "DRIE_Bosch"], "sio2": ["RIE_F"], "sin": ["RIE_F"],
    "sio₂": ["RIE_F"], "硅": ["RIE_F", "DRIE_Bosch"],
}


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9\u4e00-\u9fff]+", (text or "").lower()))


def retrieve(kb: KBStore, query: str, top_k: int = 5) -> list[dict]:
    """词法检索知识条目,可信度高的优先。"""
    entries = kb.list(limit=400)   # 全量(实验室规模很小)
    q_tokens = _tokens(query)
    ql = query.lower()

    # 工艺类型提示
    hints: set[str] = set()
    for key, types in _TYPE_HINTS.items():
        if key in ql:
            hints.update(types)

    scored = []
    for e in entries:
        score = 0.0
        hay = _tokens(" ".join([
            e.get("title", ""), str(e.get("material", {}).get("material", "")),
            " ".join(e.get("tags", [])), e.get("process_type", "")]))
        score += 1.5 * len(q_tokens & hay)
        # 材料名精确命中
        mat = (e.get("material") or {}).get("material", "").lower()
        if mat and mat in ql:
            score += 3.0
        # 工艺类型提示
        if e.get("process_type") in hints:
            score += 2.0
        # 可信度加权
        score += e.get("reliability_score", 1) * 0.3
        if score > 0:
            scored.append((score, e))
    scored.sort(key=lambda x: -x[0])
    return [e for _, e in scored[:top_k]]


SYSTEM_PROMPT = """你是 OpenNano 工艺助手,服务微纳加工实验室的工程师。回答规则:
1. 只依据下方「知识库检索结果」回答;每条条目标注了 [可信度/5] 与来源。
2. 可信度含义(与数据域 core 对齐,严禁升级):
   4 = 仪器实测且记录已核实(可放心引用) · 3 = 部分核实 · **2 = 记录未核实(仅存档数值,引用时必须说明"未核实")** ·
   1 = 存疑或通用参考。**绝不可把 2 当作实测引用**。
2b. 标注 [副本] 的条目是原始数值副本,其权威源是 `18_工艺数据资产/03_实验数据/core/`(CSV 权威);
    涉及具体数值时说明核实状态与来源,不要替记录下结论。
3. 检索结果里没有的信息,明确说"知识库里没有相关记录",绝不编造参数。
4. 回答风格:简洁、中文、先讲机理/原则、再给参数;不下死结论,留开放点。"""


def build_system(query: str, entries: list[dict]) -> str:
    if not entries:
        ctx = "（未检索到相关条目）"
    else:
        ctx = "\n".join(
            f"- [{e['reliability_score']}/5] {e['title']} | {e['process_type']} | "
            f"参数={e.get('parameters')} | 结果={e.get('results')} | 来源={e['source']}"
            for e in entries)
    return SYSTEM_PROMPT + "\n\n知识库检索结果:\n" + ctx


def sources(entries: list[dict]) -> list[dict]:
    return [{"title": e["title"], "process_type": e["process_type"],
             "reliability_score": e["reliability_score"], "source": e["source"]}
            for e in entries]

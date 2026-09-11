"""Agent 编排器:工具调用循环(意图 → 工具 → 结果 → 综合)。

对齐 OpenNano §十二.3/§十三:
  - 先把用户问题喂 RAG 取知识上下文,再让 LLM 决定是否调工具、调哪个。
  - 工具结果回传后由 LLM 综合成最终回答(带可信度分级与来源)。
  - 写操作(record_experiment / generate_gds)立即生效并在轨迹回显,可审计。
"""
from __future__ import annotations

import json

from . import llm, rag
from .tools import Context, describe_tools, execute_tool, tool_schemas

MAX_TOOL_STEPS = 5   # 最多工具调用轮次,防死循环

_TOOL_GUIDE = """\n\n可用工具(需要时调用,不要编造工具结果):
""" + describe_tools()


def run(kb, lib, message: str, history: list[dict], top_k: int = 5) -> dict:
    """执行一轮对话(可能含多次工具调用)。返回 {answer, sources, tool_calls, llm_available}。"""
    entries = rag.retrieve(kb, message, top_k=top_k)
    system = rag.build_system(message, entries) + _TOOL_GUIDE
    ctx = Context(kb=kb, lib=lib)

    messages = [{"role": h.get("role", "user"), "content": h.get("content", "")}
                for h in (history or [])[-8:]]
    messages.append({"role": "user", "content": message})

    trace: list[dict] = []
    canvas_ops: list[dict] = []
    for _ in range(MAX_TOOL_STEPS):
        resp = llm.chat_with_tools(system, messages, tool_schemas())
        tcs = resp.get("tool_calls")
        if not tcs:
            return {"answer": resp.get("content") or "",
                    "sources": rag.sources(entries), "canvas_ops": canvas_ops,
                    "tool_calls": trace, "llm_available": llm.is_available()}

        messages.append({"role": "assistant", "content": resp.get("content") or "",
                         "tool_calls": tcs})
        for tc in tcs:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            result = execute_tool(name, args, ctx)
            if isinstance(result, dict) and isinstance(result.get("op"), dict):
                canvas_ops.append(result["op"])      # 画布操作指令,交前端执行
            trace.append({"name": name, "args": args,
                          "ok": "error" not in result, "result": result})
            messages.append({"role": "tool",
                             "tool_call_id": tc.get("id") or f"call_{name}",
                             "content": json.dumps(result, ensure_ascii=False)})

    return {"answer": "（已达最大工具调用轮次，请换一种问法或分步提问。）",
            "sources": rag.sources(entries), "canvas_ops": canvas_ops,
            "tool_calls": trace, "llm_available": llm.is_available()}

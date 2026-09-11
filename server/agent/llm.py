"""LLM 客户端(OpenAI 兼容,可切换 DeepSeek/GLM/OpenAI)+ 无 key 时的 mock 模式。

环境变量:
  OPENNANO_LLM_API_KEY   必填(无则 mock 模式)
  OPENNANO_LLM_BASE_URL  默认 https://api.deepseek.com/v1
  OPENNANO_LLM_MODEL     默认 deepseek-chat
  (GLM: base_url=https://open.bigmodel.cn/api/paas/v4/ , model=glm-4.6 等)
"""
from __future__ import annotations

import os

import httpx

DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"


def _load_dotenv():
    """读取 server/.env(若存在),不覆盖已设环境变量。"""
    from pathlib import Path
    path = Path(__file__).resolve().parent.parent / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()


def _cfg() -> tuple[str | None, str, str]:
    key = (os.environ.get("OPENNANO_LLM_API_KEY")
           or os.environ.get("DEEPSEEK_API_KEY")
           or os.environ.get("GLM_API_KEY"))
    base = os.environ.get("OPENNANO_LLM_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    model = os.environ.get("OPENNANO_LLM_MODEL", DEFAULT_MODEL)
    return key, base, model


def is_available() -> bool:
    return _cfg()[0] is not None


def chat(system: str, messages: list[dict], temperature: float = 0.3) -> str:
    """system 已含知识库上下文;messages = [{'role','content'}]。"""
    key, base, model = _cfg()
    if not key:
        # mock:回显知识库检索结果,便于无 key 测试 RAG 管线
        return ("（mock 模式 · 未配置 LLM key）\n\n"
                "以下为知识库检索命中(注入给 LLM 的上下文):\n\n" + system)

    payload = {"model": model, "temperature": temperature,
               "messages": [{"role": "system", "content": system}, *messages]}
    r = httpx.post(f"{base}/chat/completions",
                   headers={"Authorization": f"Bearer {key}"},
                   json=payload, timeout=90)
    r.raise_for_status()
    data = r.json()
    return data["choices"][0]["message"]["content"]


def chat_with_tools(system: str, messages: list[dict], tools: list[dict],
                    temperature: float = 0.3) -> dict:
    """OpenAI 兼容 function calling 单轮调用。

    返回 {"content": str|None, "tool_calls": list|None}。
    tool_calls 元素已归一化为 {"id","type","function":{"name","arguments"(JSON 字符串)}}。
    """
    key, base, model = _cfg()
    if not key:
        # mock:无 key 时不做工具调用,回显知识库上下文
        return {"content": "（mock 模式 · 未配置 LLM key，无法工具调用）\n\n" + system,
                "tool_calls": None}

    payload = {"model": model, "temperature": temperature,
               "messages": [{"role": "system", "content": system}, *messages],
               "tools": tools, "tool_choice": "auto"}
    r = httpx.post(f"{base}/chat/completions",
                   headers={"Authorization": f"Bearer {key}"},
                   json=payload, timeout=120)
    r.raise_for_status()
    msg = r.json()["choices"][0]["message"]
    tcs = msg.get("tool_calls")
    if tcs:
        for t in tcs:
            fn = t.setdefault("function", {})
            if not fn.get("arguments"):
                fn["arguments"] = "{}"
    return {"content": msg.get("content"), "tool_calls": tcs}

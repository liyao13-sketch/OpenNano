"""C2 补网：2026-09-16 审计点名的「写数据的模块零回归网」里最要紧的几条。

审计结论：`agent/tools.py`（LLM 工具执行层，能写 KB / 写画布）、`/api/compute`（公式+影响规则引擎）、
`/api/doe`（DOE 矩阵）此前只有**网外手工脚本** `server/test_api.py` 冒烟 —— 不进回归网就等于没有守卫。
本文件把它们的**接口形状与安全边界**钉进 tests/（不重复单测已覆盖的算法细节）。
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi", reason="接口用例需要 fastapi")
from fastapi.testclient import TestClient            # noqa: E402


@pytest.fixture(scope="module")
def client():
    from main import app
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------- agent 画布工具
def test_add_module_ref_每次唯一():
    """P1 修复的回归锁：ref 原来是 `hash(sub+name)%10000`（确定性）⇒ 同 subtype 同 name
    两次调用**必得同一个 ref**，随后 connect 按 ref 挂错节点且无声。"""
    from agent.tools import _canvas_add_module

    refs = [_canvas_add_module({"subtype": "etch"}, None)["op"]["ref"] for _ in range(20)]
    assert len(set(refs)) == 20, f"ref 撞了：{refs}"


def test_add_module_缺_subtype_要报错():
    from agent.tools import _canvas_add_module
    r = _canvas_add_module({}, None)
    assert "error" in r and not r.get("op")


# ---------------------------------------------------------------- 公式/规则引擎
def test_compute_公式与承接值(client):
    r = client.post("/api/compute", json={
        "params": {"x": 2.0},
        "handed": {"y": 3.0},
        "formulas": {"z": "x + y * 2"},
    })
    assert r.status_code == 200
    d = r.json()
    assert (d.get("key_values") or {}).get("z") == 8.0, d


def test_compute_坏公式不崩服务(client):
    r = client.post("/api/compute", json={"params": {}, "formulas": {"z": "1 +"}})
    assert r.status_code in (200, 400, 422), r.status_code      # 关键：不是 500
    if r.status_code == 200:
        assert r.json().get("z") in (None, "") or "error" in str(r.json()).lower()


# ---------------------------------------------------------------- 影响规则
def test_rules_读取与回存(client):
    before = client.get("/api/rules")
    assert before.status_code == 200
    assert "rules" in before.json()
    rules = before.json()["rules"]
    r = client.post("/api/rules", json={"rules": rules})        # 原样回存
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------- DOE
def test_doe_全因子(client):
    r = client.post("/api/doe", json={
        "variables": [{"param": "power", "min": 50, "max": 100, "step": 50},
                      {"param": "pressure", "min": 1, "max": 2, "step": 1}],
        "design_type": "full",
    })
    assert r.status_code == 200
    d = r.json()
    assert d.get("runs") == 4, d
    assert len(d.get("matrix") or []) == 4, d


def test_doe_超大矩阵要拒收不是撑爆内存(client):
    """审计 P2：`step` 任意小 ⇒ `full = product(...)` 直接吃爆内存。
    这里要的是**明确拒收**（4xx），而不是把服务器拖死。"""
    r = client.post("/api/doe", json={
        "variables": [{"param": "a", "min": 0, "max": 1000, "step": 0.001},
                      {"param": "b", "min": 0, "max": 1000, "step": 0.001},
                      {"param": "c", "min": 0, "max": 1000, "step": 0.001}],
        "design_type": "full"})
    assert r.status_code in (400, 413, 422), (
        f"超大 DOE 没有被拒收（{r.status_code}）—— 会撑爆内存")

"""后端接口冒烟（`main.py` 的批次/续做/菜单/契约端点）。

⚠️ 这里只钉**接口形状与安全边界**，不重复单测已覆盖的算法。
   用 `TestClient` 直连 app（不启服务、不走网络）。
"""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi", reason="接口用例需要 fastapi")
from fastapi.testclient import TestClient            # noqa: E402

from batch_fixtures import BATCH, modules, run_rows, batch_rows, sample_rows   # noqa: E402


@pytest.fixture(scope="module")
def client():
    from main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def core(tmp_path, monkeypatch):
    """把 core 指向合成夹具 —— 接口要能像真环境一样**从 core 回读** sample/parent/性质。"""
    from conftest import seed_core
    from kb import append_pack as ap
    d = seed_core(tmp_path / "core", runs=run_rows(), batches=batch_rows(),
                  samples=sample_rows())
    monkeypatch.setenv("OPENNANO_CORE_DIR", str(d))
    monkeypatch.setattr(ap, "CORE_DIR", d)
    return d


# ------------------------------------------------------------------ 批次视图
def test_批次列表与_run_链(client):
    r = client.post("/api/batch/list", json={"modules": modules()})
    assert r.status_code == 200
    bs = r.json()["batches"]
    assert [b["batch_id"] for b in bs] == [BATCH]
    assert bs[0]["chain_nodes"] == bs[0]["runs"] == len(modules())

    r = client.post("/api/batch/runs", json={"modules": modules(), "batch_id": BATCH})
    assert r.status_code == 200
    d = r.json()
    assert {"batch_id", "runs", "count", "edges", "roots", "parallels", "natures",
            "nature_needs_human", "sample_tree"} <= set(d)
    assert d["count"] == len(modules())


def test_样品树读不到时_报错但接口不崩(client):
    """core 不可用（CI/别的机器）⇒ 样品树字段给 error，**其余字段照常返回**。"""
    r = client.post("/api/batch/runs", json={"modules": modules(), "batch_id": BATCH})
    assert r.status_code == 200
    tree = r.json()["sample_tree"]
    assert "tree" in tree or "error" in tree


# ------------------------------------------------------------------ 续做
def test_续做_自动编号且不改工程文件(client):
    """`persist=false`（默认）时**不许**落盘 —— 点一下"续做"不能偷偷改工程。"""
    payload = {"batch_id": BATCH, "stage": "ICP", "modules": modules()}
    r = client.post("/api/run/continue", json=payload)
    assert r.status_code == 200
    d = r.json()
    assert d["run"]["run_id"] == f"{BATCH}-ICP-0005"      # 序号 = 最大 + 1
    assert d["saved"] is False
    assert d["project"]["modules"][-1]["core_run_id"] == f"{BATCH}-ICP-0005"


def test_续做_无父时不硬连线(client, core):
    """抽到的父 run 不在画布上 ⇒ **不许**伪造边（宁可无连线，也不连错）。"""
    mods = [m for m in modules() if m["core_run_id"] != f"{BATCH}-ICP-0004"]
    payload = {"batch_id": BATCH, "stage": "ICP", "modules": mods,
               "parent_run_id": f"{BATCH}-NO-SUCH-0001"}
    d = client.post("/api/run/continue", json=payload).json()
    assert d["edge"] is None and d["project"]["edges"] == []


def test_续做_分支安全_sample_决定父(client, core):
    """★ 回归网抓出的真 bug：UI 会把"界面上选中的 run"和"手填样号"**一起**发过来，
    若显式父优先 ⇒ 给 DIE15-01 续做会挂到别的分支上。**sample 必须赢**。"""
    payload = {"batch_id": BATCH, "stage": "ICP", "modules": modules(),
               "sample_id": f"{BATCH}-01-DIE15-01",
               "parent_run_id": f"{BATCH}-ICP-0004"}       # 故意给一个同 stage 但别样品的父
    d = client.post("/api/run/continue", json=payload).json()
    assert d["run"]["parent_run_id"] == f"{BATCH}-ICP-0003"
    assert d["run"]["sample_id"] == f"{BATCH}-01-DIE15-01"
    assert d["module"]["core_parent_run_id"] == f"{BATCH}-ICP-0003"
    assert d["edge"] == {"src": f"m-{BATCH}-ICP-0003", "dst": d["module"]["id"]}


def test_续做_显式父优先并接线(client):
    payload = {"batch_id": BATCH, "stage": "DRIE", "modules": modules(),
               "parent_run_id": f"{BATCH}-ICP-0001",
               "edges": [{"src": "a", "dst": "b"}]}
    d = client.post("/api/run/continue", json=payload).json()
    assert d["run"]["parent_run_id"] == f"{BATCH}-ICP-0001"
    assert d["edge"] == {"src": f"m-{BATCH}-ICP-0001", "dst": d["module"]["id"]}
    assert d["project"]["edges"][-1] == d["edge"]


def test_续做_灌参失败要给清楚的错(client):
    """菜单目录不存在 ⇒ 必须报错（不能静默给空 steps，那会让人以为"这配方就是空的"）。"""
    payload = {"batch_id": BATCH, "stage": "DRIE", "modules": modules(),
               "menu_group": 4, "menu_dir": "/no/such/menu/dir"}
    r = client.post("/api/run/continue", json=payload)
    assert r.status_code >= 400
    assert "menu" in r.text.lower() or "菜单" in r.text


# ------------------------------------------------------------------ 契约 / 菜单元信息
def test_表单契约端点形状(client, contract_source):
    r = client.get("/api/form/contract")
    if r.status_code == 503:
        # 没装载数据资产（CI/别人机器）⇒ **如实 503 + 说清缺什么**，不是 500，也不是空表
        assert "契约真源不可达" in r.json()["detail"]
        assert "OPENNANO" in r.json()["detail"]
        return
    assert r.status_code == 200
    d = r.json()
    assert {"status", "verification", "method", "quantities", "param_keys"} <= set(d)
    assert "设备遥测" in d["method"]           # 机台 log 与口述必须能区分来源


def test_契约真源缺失时_读契约给_503_且说清怎么修(client, monkeypatch):
    """契约真源没装载 ⇒ 503 + 可操作提示（守的这条：不许 500、不许静默空表）。"""
    from kb import form_contract as fc
    from kb.menu_reader import MenuParserUnavailable

    def _boom():
        raise MenuParserUnavailable("模拟未装载数据资产：找不到 datasets_menu.py")
    monkeypatch.setattr(fc, "parser", _boom)
    r = client.get("/api/form/contract")
    assert r.status_code == 503
    assert "契约真源不可达" in r.json()["detail"]


def test_菜单元信息端点(client):
    r = client.get("/api/menu/zones")
    assert r.status_code == 200
    d = r.json()
    assert d["scope_max"] == 49 and d["pair_tol_min"] >= 1
    assert any(z["range"] == "G50+" and z["keep"] is False for z in d["zones"])

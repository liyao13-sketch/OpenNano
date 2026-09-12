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


def test_调试线端点_有视图给数据_没有也给原因(client, monkeypatch, tmp_path):
    """`/api/batch/tune_line`：数据线视图在 ⇒ 按 tune_id 分组；不在 ⇒ available=false + 原因（不 500）。"""
    import sqlite3
    from kb import batch_runs as br
    # ① 无视图：给一个空 core
    core = tmp_path / "core"
    core.mkdir(exist_ok=True)
    (core / "runs.csv").write_text("run_id,batch_id\n", encoding="utf-8")
    monkeypatch.setenv("OPENNANO_CORE_DIR", str(core))
    for n in dir(br):
        if n.endswith("_CACHE"):
            setattr(br, n, None)
    r = client.post("/api/batch/tune_line", json={"modules": [], "batch_id": BATCH})
    assert r.status_code == 200
    d = r.json()
    assert d["available"] is False and "reason" in d
    # ② 有视图：造一个
    db = core / "process.db"
    con = sqlite3.connect(db)
    con.execute('CREATE VIEW v_tune_line AS SELECT "T1" AS tune_id, "1" AS tune_step,'
                ' "X-ICP-0001" AS run_id, "2026-09-01" AS date, "ICP" AS stage,'
                ' "t" AS tool, "s" AS sample_id, NULL AS t_set_s, NULL AS t_dwell_s,'
                ' 750 AS source_w, 150 AS bias_w, NULL AS bias_w_actual,'
                ' NULL AS chf3_sccm, NULL AS ar_sccm, NULL AS o2_sccm, NULL AS cf4_sccm,'
                ' NULL AS sf6_sccm, NULL AS cd_delta_nm, NULL AS depth_nm, NULL AS er_nm_min,'
                ' NULL AS selectivity, NULL AS film_thickness_nm, NULL AS stress_mpa,'
                ' NULL AS refractive_index')
    con.close()
    r2 = client.post("/api/batch/tune_line", json={"modules": [], "batch_id": ""})
    d2 = r2.json()
    assert d2["available"] is True and d2["series"][0]["tune_id"] == "T1"


def test_工程里的边带来源标记与箭头样式(client, monkeypatch, tmp_path):
    """画布边的"两类线"必须落盘可分辨（`_link`），否则刷新后实线/虚线又会混成一样。"""
    from kb import append_pack as ap
    from conftest import seed_core
    from batch_fixtures import BATCH, ROOT, batch_rows, sample_rows
    d = seed_core(tmp_path / "core", batches=batch_rows(), samples=sample_rows(), runs=[
        {"run_id": f"{BATCH}-LDW-0001", "batch_id": BATCH, "sample_id": ROOT,
         "stage": "LDW", "stage_seq": "2", "parent_run_id": ""},
        {"run_id": f"{BATCH}-ICP-0001", "batch_id": BATCH, "sample_id": f"{BATCH}-01-DIE4",
         "stage": "ICP", "stage_seq": "3", "parent_run_id": ""},
        {"run_id": f"{BATCH}-ICP-0002", "batch_id": BATCH, "sample_id": f"{BATCH}-01-DIE4",
         "stage": "ICP", "stage_seq": "3", "parent_run_id": f"{BATCH}-ICP-0001"},
    ])
    monkeypatch.setattr(ap, "CORE_DIR", d)
    monkeypatch.setenv("OPENNANO_CORE_DIR", str(d))
    p = ap.core_to_project(BATCH)
    assert p["edges"] and all("_link" in e for e in p["edges"])
    kinds = {e["_link"] for e in p["edges"]}
    assert kinds <= {"recorded", "inferred"}


def test_续做产出的模块形状完整_不许缺_key_values(client, core):
    """★ 回归：点 DRIE（续做出来的 DRIE-0002）整屏变白。

    根因：续做时把 `key_values`/`sim_result` 整个删掉 ⇒ 前端面板 `m.key_values[k]` 抛错。
    判据：新模块的**结构性字段必须存在**（可以为空），形状与包/core 来的模块一致。
    """
    payload = {"batch_id": BATCH, "stage": "DRIE", "modules": modules()}
    d = client.post("/api/run/continue", json=payload).json()
    m = d["module"]
    for k in ("key_values", "params", "param_defs", "param_inputs",
              "param_outputs", "formulas", "material", "annotations"):
        assert k in m, f"续做模块缺字段 {k}"
    assert m["key_values"] == {} and m["sim_result"] is None    # 不继承结果，但字段在
    assert isinstance(m["param_inputs"], list) and isinstance(m["formulas"], dict)


def test_一键整理布局端点(client):
    """`/api/layout/arrange`：只改 x/y，边与标注原样；乱的画布整完要能过体检。"""
    from kb.layout_audit import audit
    mods = [dict(m) for m in modules()]
    for m in mods:                                  # 全叠在原点
        m["x"] = m["y"] = 0
    edges = [{"src": mods[0]["id"], "dst": mods[1]["id"]}]
    r = client.post("/api/layout/arrange", json={"modules": mods, "edges": edges})
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True and d["summary"]["cols"] >= 1
    assert len(d["modules"]) == len(mods)
    assert audit({"modules": d["modules"], "edges": edges}, comment_lines=3)["ok"] is True
    assert "不动" in d["note"] or "只改" in d["note"]

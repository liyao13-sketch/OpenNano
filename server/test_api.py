"""后端冒烟测试(TestClient,不启服务)。

⚠️ 这是**手动冒烟脚本**，不在 `tests/` 回归网里（`pytest.ini` 的 testpaths 只收 tests/）。
   它跑的是 /api/compute /api/doe /api/kb 这些回归网还没覆盖的接口，所以留着有用——
   但**必须先把用户数据目录指到临时目录**：
   2026-09-13 实测过不指的后果 —— 它把 `t.json` 存进了 `~/.opennano/projects/`，
   而界面上「最近修改的工程」正是被载入的那个 ⇒ **owner正在看的 AR50-T1 画布被顶掉**。
   现在这两个环境变量（`OPENNANO_PROJECTS_DIR` / `OPENNANO_DB`）在 import main **之前**设好。
   跑法：`server/.venv/bin/python test_api.py`
"""
import os
import sys
import tempfile

_tmp = tempfile.mkdtemp(prefix="opennano-smoke-")
os.environ["OPENNANO_PROJECTS_DIR"] = os.path.join(_tmp, "projects")   # 别碰真工程目录
os.environ["OPENNANO_DB"] = os.path.join(_tmp, "opennano.db")          # 别碰真知识库

sys.path.insert(0, ".")

from fastapi.testclient import TestClient
from main import app

c = TestClient(app)


def test_health_catalog():
    r = c.get("/api/health")
    assert r.json()["ok"]
    r = c.get("/api/catalog")
    d = r.json()
    total = len(d["processes"]) + len(d["metrology"])
    # 2026-09-13 修陈旧断言：目录已长到 89 工艺 + 16 表征 = 105，模块目录 25
    # （原来写死 104/24 ⇒ 这脚本没进 tests/ 回归网，所以没人发现它一直在骗人）
    assert total >= 100, total
    assert len(d["module_catalog"]) >= 25
    print(f"✓ catalog: {len(d['processes'])} 工艺 + {len(d['metrology'])} 表征 = {total}")


def test_library_and_module():
    r = c.get("/api/library")
    assert r.json()["categories"]
    r = c.post("/api/modules/new", json={"subtype": "etch"})
    m = r.json()
    assert m["param_inputs"] == ["胶CD", "胶SWA", "膜厚"]
    assert "source_power" in m["param_defs"] or m["param_defs"]
    print(f"✓ module/new: etch 默认设备接口 in={m['param_inputs']} out={m['param_outputs']}")


def test_compute():
    r = c.post("/api/compute", json={
        "params": {"bias_nm": 100},
        "handed": {"胶CD": 500},
        "key_values": {},
        "formulas": {"硅CD": "胶CD - 2 * bias_nm"},
    })
    assert r.json()["key_values"]["硅CD"] == 300
    print("✓ compute: 胶CD 500 - 2×100 = 硅CD 300")


def test_doe():
    r = c.post("/api/doe", json={
        "variables": [{"param": "power", "min": 100, "max": 300, "step": 100}],
        "design_type": "full"})
    assert r.json()["runs"] == 3
    print("✓ doe: 3 水平 → 3 runs")


def test_project_roundtrip():
    m = c.post("/api/modules/new", json={"subtype": "etch"}).json()
    c.post("/api/project/save", json={
        "name": "t", "modules": [m], "edges": []})
    d = c.get("/api/project").json()
    assert d["name"] == "t" and len(d["modules"]) == 1
    print("✓ project: save/load 往返")


def test_kb():
    """KB 接口：①`/api/kb/ingest` 按数据域协议 §11 **已停用**（KB 不存原始数值副本）
    ②检索/统计在**有内容时**才断言细节 —— 之前这里断言"≥30 条、第一条 4 分"，
       其实是**读着owner的真知识库**在自欺：一旦把 DB 指向临时库（正确做法）就立刻露馅。
    """
    d = c.post("/api/kb/ingest").json()
    assert d["ok"] is False, d                       # 停用即正确行为
    assert "core" in d["message"]                    # 并且明说"原始数据走数据线 core"
    total = d["total"]
    assert isinstance(total, int) and total >= 0

    stats = c.get("/api/kb/stats").json()
    assert stats["total"] == total

    if total:                                        # 空库（隔离环境）就只验形状，不验内容
        r = c.get("/api/kb", params={"process_type": "RIE_Cl", "limit": 5})
        entries = r.json()
        assert all(1 <= e["reliability_score"] <= 5 for e in entries)
        assert all(e["reliability_score"] >= 5
                   for e in c.get("/api/kb", params={"min_reliability": 5}).json())
        print(f"✓ kb: 库内 {total} 条，过滤/可信度检索正常")
    else:
        print("✓ kb: ingest 已按协议停用；空库（隔离环境）只验接口形状")


if __name__ == "__main__":
    test_health_catalog()
    test_library_and_module()
    test_compute()
    test_doe()
    test_project_roundtrip()
    test_kb()
    print("\n✅ 后端全部通过")

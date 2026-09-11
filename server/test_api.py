"""后端冒烟测试(TestClient,不启服务)。"""
import sys
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
    assert total == 104, total
    assert len(d["module_catalog"]) == 24
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
    r = c.post("/api/kb/ingest")
    d = r.json()
    assert d["total"] >= 30
    r = c.get("/api/kb", params={"process_type": "RIE_Cl", "limit": 5})
    entries = r.json()
    assert entries and entries[0]["reliability_score"] == 4
    r = c.get("/api/kb", params={"min_reliability": 5})
    assert all(e["reliability_score"] >= 5 for e in r.json())
    r = c.get("/api/kb/stats")
    assert r.json()["total"] >= 30
    print(f"✓ kb: 录入 {d['total']} 条, 过滤/可信度检索正常")


if __name__ == "__main__":
    test_health_catalog()
    test_library_and_module()
    test_compute()
    test_doe()
    test_project_roundtrip()
    test_kb()
    print("\n✅ 后端全部通过")

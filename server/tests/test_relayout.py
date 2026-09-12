"""按工艺序重排画布（`kb/relayout.py`）—— 回归网。

守的是"**显示层**和**数据层**分开"这条线：重排只改坐标与连线，不改任何测量/归属；
而"还没入库的计划 run"必须保住它原有的父边（曾经被重排/修复一并抹掉）。
"""
from __future__ import annotations

import json

import pytest

from batch_fixtures import BATCH, ROOT, batch_rows, sample_rows
from conftest import seed_core


def _core(core_dir, planned_run_in_core=False):
    runs = [
        {"run_id": f"{BATCH}-PECVD-0001", "batch_id": BATCH, "sample_id": ROOT,
         "stage": "PECVD", "stage_seq": "1", "date": "2026-09-01"},
        {"run_id": f"{BATCH}-LDW-0001", "batch_id": BATCH, "sample_id": ROOT,
         "stage": "LDW", "stage_seq": "2", "date": "2026-09-02", "parent_run_id": ""},
        {"run_id": f"{BATCH}-ICP-0001", "batch_id": BATCH, "sample_id": ROOT,
         "stage": "ICP", "stage_seq": "3", "date": "2026-09-03", "parent_run_id": ""},
        {"run_id": f"{BATCH}-ICP-0002", "batch_id": BATCH, "sample_id": ROOT,
         "stage": "ICP", "stage_seq": "3", "date": "2026-09-03", "parent_run_id": ""},
        {"run_id": f"{BATCH}-ASH-0001", "batch_id": BATCH, "sample_id": ROOT,
         "stage": "ASH", "stage_seq": "4", "date": "2026-09-04",
         "parent_run_id": f"{BATCH}-ICP-0002"},
    ]
    if planned_run_in_core:
        runs.append({"run_id": f"{BATCH}-DRIE-0001", "batch_id": BATCH, "sample_id": ROOT,
                     "stage": "DRIE", "stage_seq": "5", "date": "2026-09-05",
                     "parent_run_id": f"{BATCH}-ASH-0001"})
    return seed_core(core_dir, batches=batch_rows(), samples=sample_rows(), runs=runs)


@pytest.fixture
def env(tmp_path, monkeypatch):
    from kb import append_pack as ap
    import kb.relayout as rl
    d = _core(tmp_path / "core")
    monkeypatch.setattr(ap, "CORE_DIR", d)
    monkeypatch.setenv("OPENNANO_CORE_DIR", str(d))
    monkeypatch.setattr(rl, "PROJECTS_DIR", tmp_path / "projects")
    (tmp_path / "projects").mkdir(exist_ok=True)
    return tmp_path


def _write_project(path, planned_parent=None, x=9999, y=9999):
    mods = [{"id": f"md_{s}", "core_run_id": f"{BATCH}-{s}-0001",
             "core_measurements": [{"quantity": "cd_top_nm", "value": "512"}],
             "comment": "留着我"}
            for s in ("PECVD", "LDW", "ASH")]
    mods.insert(2, {"id": "md_ICP1", "core_run_id": f"{BATCH}-ICP-0001"})
    mods.append({"id": "md_ICP2", "core_run_id": f"{BATCH}-ICP-0002"})
    if planned_parent:
        mods.append({"id": "md_plan", "core_run_id": f"{BATCH}-DRIE-0001",
                     "core_parent_run_id": planned_parent, "x": x, "y": y})
    path.write_text(json.dumps({"name": "重排回归", "modules": mods, "edges": []},
                               ensure_ascii=False), encoding="utf-8")
    return path


def test_重排_按工序分列且连线分类(env):
    from kb.relayout import relayout_project
    p = _write_project(env / "proj.json")
    r = relayout_project(p, BATCH, write=True)
    j = json.loads(p.read_text(encoding="utf-8"))
    byid = {m["id"]: m for m in j["modules"]}
    cols = {m["core_run_id"]: m["x"] for m in j["modules"]}
    assert cols[f"{BATCH}-PECVD-0001"] < cols[f"{BATCH}-LDW-0001"] < cols[f"{BATCH}-ICP-0001"]
    assert cols[f"{BATCH}-ICP-0001"] < cols[f"{BATCH}-ASH-0001"]
    assert r["recorded"] == 1 and r["inferred"] == 3          # ASH→ICP2 记录；其余按工艺序推断
    kinds = {(byid[e["src"]]["core_run_id"], byid[e["dst"]]["core_run_id"]): e.get("_link")
             for e in j["edges"]}
    assert kinds[(f"{BATCH}-ICP-0002", f"{BATCH}-ASH-0001")] == "recorded"
    # 测量/备注**一个字都不能动**
    assert byid["md_PECVD"]["core_measurements"] == [{"quantity": "cd_top_nm", "value": "512"}]
    assert byid["md_PECVD"]["comment"] == "留着我"


def test_重排_未入库的计划_run_父边不许丢(env):
    """★ DRIE-0002 那种"计划中、还没入库"的 run：core 里查不到它 ⇒ 它的父边必须保住。"""
    from kb.relayout import relayout_project
    p = _write_project(env / "proj2.json", planned_parent=f"{BATCH}-ASH-0001")
    r = relayout_project(p, BATCH, write=True)
    j = json.loads(p.read_text(encoding="utf-8"))
    byid = {m["id"]: m for m in j["modules"]}
    pairs = {(byid[e["src"]]["core_run_id"], byid[e["dst"]]["core_run_id"])
             for e in j["edges"]}
    assert (f"{BATCH}-ASH-0001", f"{BATCH}-DRIE-0001") in pairs
    assert r["planned_only"] == 1
    plan = next(m for m in j["modules"] if m["core_run_id"] == f"{BATCH}-DRIE-0001")
    assert plan["core_parent_run_id"] == f"{BATCH}-ASH-0001"
    assert plan["x"] > byid["md_ASH"]["x"]                     # 摆在父的右边


def test_重排_干跑不落盘(env):
    from kb.relayout import relayout_project
    p = _write_project(env / "proj3.json")
    before = p.read_text(encoding="utf-8")
    relayout_project(p, BATCH)
    assert p.read_text(encoding="utf-8") == before


def test_修复器不许清掉未入库_run_的父(env):
    """回归：`repair_edges` 曾按 core 全量覆盖 ⇒ 把 DRIE-0002 的父清空（实际踩到）。"""
    from kb.repair_edges import repair_project
    p = _write_project(env / "proj4.json", planned_parent=f"{BATCH}-ASH-0001")
    repair_project(p, BATCH, write=True)
    plan = next(m for m in json.loads(p.read_text(encoding="utf-8"))["modules"]
                if m["core_run_id"] == f"{BATCH}-DRIE-0001")
    assert plan["core_parent_run_id"] == f"{BATCH}-ASH-0001"

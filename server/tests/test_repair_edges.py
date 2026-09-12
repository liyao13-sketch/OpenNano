"""工程文件连线修复（`kb/repair_edges.py`）—— 回归网。

背景：`expack` 的兜底曾把**真实的空 parent**（并存试验）编成直线，而这些假边会被
**持久化进工程文件**（`~/.opennano/projects/*.json`）⇒ 刷新后照旧显示。
所以除了修合成逻辑，还需要一个"按 core 重算工程连线"的入口 —— 本用例守它：
**只按 core 的真实 parent 重算**、逐条报差异、落盘前自动备份、不碰 core。
"""
from __future__ import annotations

import json

import pytest

from batch_fixtures import BATCH, ROOT, batch_rows, sample_rows
from conftest import seed_core


def _core(core_dir):
    """AR50-T1 形状：LDW 与 3 条 ICP 的 parent 都是空（并存试验）。"""
    return seed_core(core_dir, batches=batch_rows(), samples=sample_rows(), runs=[
        {"run_id": f"{BATCH}-PECVD-0001", "batch_id": BATCH, "sample_id": ROOT,
         "stage": "PECVD", "stage_seq": "1"},
        {"run_id": f"{BATCH}-LDW-0001", "batch_id": BATCH, "sample_id": ROOT,
         "stage": "LDW", "stage_seq": "2", "parent_run_id": ""},
        {"run_id": f"{BATCH}-ICP-0001", "batch_id": BATCH, "sample_id": ROOT,
         "stage": "ICP", "stage_seq": "3", "parent_run_id": ""},
        {"run_id": f"{BATCH}-ICP-0002", "batch_id": BATCH, "sample_id": ROOT,
         "stage": "ICP", "stage_seq": "3", "parent_run_id": f"{BATCH}-ICP-0001"},
    ])


def _project(path):
    """一个"被编过假边"的工程：PECVD→LDW→ICP1→ICP2 串成一条直线。"""
    ids = {n: f"md_{n}" for n in ("pecvd", "ldw", "icp1", "icp2")}
    mods = [
        {"id": ids["pecvd"], "core_run_id": f"{BATCH}-PECVD-0001"},
        {"id": ids["ldw"], "core_run_id": f"{BATCH}-LDW-0001"},
        {"id": ids["icp1"], "core_run_id": f"{BATCH}-ICP-0001"},
        {"id": ids["icp2"], "core_run_id": f"{BATCH}-ICP-0002"},
    ]
    edges = [{"src": ids["pecvd"], "dst": ids["ldw"]}, {"src": ids["ldw"], "dst": ids["icp1"]},
             {"src": ids["icp1"], "dst": ids["icp2"]}]
    path.write_text(json.dumps({"name": "回归工程", "modules": mods, "edges": edges},
                               ensure_ascii=False), encoding="utf-8")
    return path, ids


@pytest.fixture
def env(tmp_path, monkeypatch):
    from kb import append_pack as ap
    import kb.repair_edges as re_
    d = _core(tmp_path / "core")
    monkeypatch.setattr(ap, "CORE_DIR", d)
    monkeypatch.setenv("OPENNANO_CORE_DIR", str(d))
    monkeypatch.setattr(re_, "PROJECTS_DIR", tmp_path / "projects")
    (tmp_path / "projects").mkdir(exist_ok=True)
    return tmp_path


def test_干跑只报不动盘(env):
    from kb.repair_edges import repair_project
    p, _ = _project(env / "proj.json")
    before = p.read_text(encoding="utf-8")
    r = repair_project(p, BATCH)
    assert r["ok"] and r["changed"] is True
    assert r["edges_before"] == 3 and r["edges_after"] == 1
    assert [(e["src"], e["dst"]) for e in r["dropped"]] == [
        (f"{BATCH}-PECVD-0001", f"{BATCH}-LDW-0001"),
        (f"{BATCH}-LDW-0001", f"{BATCH}-ICP-0001")]      # core 里这两条 parent 为空
    assert p.read_text(encoding="utf-8") == before       # 干跑**不写盘**
    assert not list(env.glob("*.bak-*"))


def test_落盘要备份且同步模块字段(env):
    from kb.repair_edges import repair_project
    p, ids = _project(env / "proj.json")
    r = repair_project(p, BATCH, write=True)
    assert r["written"] is True and r.get("backup")
    assert json.loads(p.read_text(encoding="utf-8"))["edges"] == [
        {"src": ids["icp1"], "dst": ids["icp2"]}]
    bak = json.loads(open(r["backup"], encoding="utf-8").read())
    assert len(bak["edges"]) == 3                        # 备份是**改前**的样子
    # 模块上的 core_parent_run_id 与 core 对齐（空就是空）
    mods = {m["core_run_id"]: m.get("core_parent_run_id") for m in
            json.loads(p.read_text(encoding="utf-8"))["modules"]}
    assert mods[f"{BATCH}-LDW-0001"] == "" and mods[f"{BATCH}-ICP-0001"] == ""
    assert mods[f"{BATCH}-ICP-0002"] == f"{BATCH}-ICP-0001"


def test_已经干净的工程不报改动(env):
    from kb.repair_edges import repair_project
    p, ids = _project(env / "proj.json")
    repair_project(p, BATCH, write=True)
    r2 = repair_project(p, BATCH)
    assert r2["changed"] is False and r2["dropped"] == [] and r2["added"] == []
    r3 = repair_project(p, BATCH, write=True)            # 再写一次不该产生备份
    assert "backup" not in r3


def test_父在工程之外的_不连线也不报假边(env):
    """跨包续做：父 run 不在本工程里 ⇒ 不连线（也不该被算成"掉了"）。"""
    from kb.repair_edges import repair_project
    p = env / "proj2.json"
    mods = [{"id": "m1", "core_run_id": f"{BATCH}-ICP-0002"}]     # 它的父 ICP-0001 不在
    p.write_text(json.dumps({"modules": mods, "edges": []}, ensure_ascii=False), encoding="utf-8")
    r = repair_project(p, BATCH)
    assert r["ok"] and r["changed"] is False and r["added"] == []
    assert r["ambiguous_parents"] == [f"{BATCH}-ICP-0002"]


def test_认不出批次要如实报错(env):
    from kb.repair_edges import repair_project
    p = env / "proj3.json"
    p.write_text(json.dumps({"modules": [{"id": "m1"}], "edges": []}, ensure_ascii=False),
                 encoding="utf-8")
    assert repair_project(p, "")["ok"] is False
    assert repair_project(env / "nope.json", BATCH)["ok"] is False


def test_命令行干跑默认不落盘(env, monkeypatch, capsys):
    import kb.repair_edges as re_
    # 工程名 = 批次号（命令行的定位约定：PROJECTS_DIR/<batch>.json）
    p, _ = _project(env / "projects" / f"{BATCH}.json")
    monkeypatch.setattr("sys.argv", ["repair_edges", BATCH])
    assert re_.main() == 0
    out = capsys.readouterr().out
    assert "有假边" in out and "干跑结束" in out
    assert "去掉" in out
    assert len(json.loads(p.read_text(encoding="utf-8"))["edges"]) == 3   # 没写

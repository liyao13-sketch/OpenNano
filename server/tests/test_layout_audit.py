"""画布布局体检 + 一键整理（`kb/layout_audit.py` / `kb/arrange.py`）—— 回归网。

背景（2026-09-13 owner："run2 和 run3 两个方块会叠在一起"）：
    备注块以前**不限高**，长备注把节点撑高 ⇒ 压到下一格；肉眼看不全 ⇒ 必须机器查。
"""
from __future__ import annotations

import pytest

from batch_fixtures import BATCH, ROOT, batch_rows, sample_rows
from conftest import seed_core


def _proj_ar50(core_dir):
    from kb import append_pack as ap
    d = seed_core(core_dir, batches=batch_rows(), samples=sample_rows(), runs=[
        {"run_id": f"{BATCH}-LDW-0001", "batch_id": BATCH, "sample_id": ROOT,
         "stage": "LDW", "stage_seq": "2", "parent_run_id": ""},
        {"run_id": f"{BATCH}-ICP-0001", "batch_id": BATCH, "sample_id": f"{BATCH}-01-DIE4",
         "stage": "ICP", "stage_seq": "3", "parent_run_id": ""},
        {"run_id": f"{BATCH}-ICP-0002", "batch_id": BATCH, "sample_id": f"{BATCH}-01-DIE4",
         "stage": "ICP", "stage_seq": "3", "parent_run_id": ""},
        {"run_id": f"{BATCH}-ICP-0003", "batch_id": BATCH, "sample_id": f"{BATCH}-01-DIE4",
         "stage": "ICP", "stage_seq": "3", "parent_run_id": f"{BATCH}-ICP-0002"},
        {"run_id": f"{BATCH}-ASH-0001", "batch_id": BATCH, "sample_id": f"{BATCH}-01-DIE4",
         "stage": "ASH", "stage_seq": "4", "parent_run_id": f"{BATCH}-ICP-0003"},
    ])
    return d, ap.core_to_project(BATCH)


@pytest.fixture
def proj(tmp_path, monkeypatch):
    from kb import append_pack as ap
    d, p = _proj_ar50(tmp_path / "core")
    monkeypatch.setattr(ap, "CORE_DIR", d)
    monkeypatch.setenv("OPENNANO_CORE_DIR", str(d))
    return p


def test_体检_合成出来的画布应当干净(proj):
    from kb.layout_audit import audit
    res = audit(proj)
    assert res["ok"] is True, res["issues"]
    assert res["modules"] >= 4 and res["edges"] >= 3


def test_体检_能抓出重叠(proj):
    """把两个节点叠到同一坐标 —— 必须被抓住（这是"方块叠在一起"的判据）。"""
    from kb.layout_audit import audit
    m0, m1 = proj["modules"][1], proj["modules"][2]
    m1["x"], m1["y"] = m0["x"], m0["y"]
    res = audit(proj)
    assert res["ok"] is False
    assert any(i["kind"] == "overlap" for i in res["issues"])


def test_体检_按前端限高建模(proj):
    """★ 备注不限高 ⇒ 长备注把节点撑高压到下一格（owner报的现象）。

    前端已**限高 3 行**；体检器必须按同一规则建模，否则会报出不存在的情况。
    """
    from kb.layout_audit import audit
    for m in proj["modules"]:
        m["comment"] = "x" * 200
    r = audit(proj, comment_lines=99)
    assert r["geometry"]["comment_lines_used"] == 3 and r["geometry"]["comment_clamped"] is True
    assert r["ok"] is True                     # 限高之后，行距 200 足够


def test_体检_能抓出间距不足(proj):
    """把两个节点排到只差 100px ⇒ 必须报重叠（不论备注开关）。"""
    from kb.layout_audit import audit
    m0, m1 = proj["modules"][1], proj["modules"][2]
    m1["x"], m1["y"] = m0["x"], m0["y"] + 100
    assert audit(proj)["ok"] is False


def test_体检_能抓出向上回折与悬空边(proj):
    from kb.layout_audit import audit
    by = {m.get("core_run_id"): m for m in proj["modules"]}
    lw = by[f"{BATCH}-LDW-0001"]
    ash = by[f"{BATCH}-ASH-0001"]
    lw["y"] = ash["y"] + 400                    # 人为把上游放到下面 ⇒ 边必然向上
    res = audit(proj)
    assert any(i["kind"] == "upward_edge" for i in res["issues"])
    proj["edges"].append({"src": "not-exist", "dst": ash["id"]})
    res2 = audit(proj)
    assert any(i["kind"] == "dangling_edge" for i in res2["issues"])


def test_一键整理_把乱掉的画布整干净(proj):
    """★ 把所有节点叠到原点 → 整理后**体检必须通过**（含"备注 6 行"的最坏情况）。"""
    from kb.arrange import arrange_project
    from kb.layout_audit import audit
    for m in proj["modules"]:
        m["x"], m["y"] = 0, 0
        m["comment"] = "y" * 120
    assert audit(proj, comment_lines=6)["ok"] is False
    r = arrange_project(proj)
    assert r["ok"] is True
    assert audit(proj, comment_lines=6)["ok"] is True, audit(proj, comment_lines=6)["issues"]
    # 只动坐标：边与标注不许被改
    assert len(proj["edges"]) >= 3


def test_一键整理_不同批次分道(proj):
    """两个 batch 混在一张画布时不许互相错插（各占一条泳道）。"""
    from kb.arrange import arrange_project
    from kb.layout_audit import audit
    extra = {**proj["modules"][1], "id": "md_other", "core_run_id": "OTHER-T9-ICP-0001",
             "core_batch_id": "OTHER-T9", "core_stage_seq": 3, "x": 0, "y": 0}
    proj["modules"].append(extra)
    arrange_project(proj)
    ys = {m["core_run_id"]: m["y"] for m in proj["modules"]}
    assert ys["OTHER-T9-ICP-0001"] != ys.get(f"{BATCH}-ICP-0001")
    assert audit(proj)["ok"] is True

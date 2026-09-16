"""进程内缓存按**源文件指纹**自动失效 —— A4 修复的回归网（2026-09-16）。

背景（红证）：`kb/batch_runs.py` 的三个缓存（stage_seq / run_facts / parent）原来是
**读一次、永不失效** —— 服务做成 launchd 常驻后，数据线 `build_core` 落了新数据，
接口还在喂旧值（实测：盘上改了 runs.csv，第二次读出来的还是旧的）。
`kb/menu_reader.py` 的 `_GRP_SLOTS_CACHE` 同病（同刻重导菜单会喂旧槽位）。
现在每次调用对源文件 stat 一次，指纹变了就重建。
"""
from __future__ import annotations

import csv

import pytest

from conftest import seed_core


def _runs_csv(core_dir, rows):
    """只重写 runs.csv（内容长度故意不同，避免 mtime 同刻 + 同尺寸的边界）。"""
    header = ["run_id", "batch_id", "stage", "stage_seq", "tool_id", "parent_run_id",
              "sample_id", "run_nature", "date", "status", "recipe_id"]
    p = core_dir / "runs.csv"
    with p.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in header})


def test_stage_seq_map_盘上改了要重建(tmp_path, monkeypatch):
    core = seed_core(tmp_path / "core")
    monkeypatch.setenv("OPENNANO_CORE_DIR", str(core))
    from kb import batch_runs as br

    _runs_csv(core, [{"run_id": "B1-RIE-0001", "batch_id": "B1", "stage": "RIE",
                      "stage_seq": "3"}])
    assert br.stage_seq_map()[("B1", "RIE")] == 3

    _runs_csv(core, [{"run_id": "B1-RIE-0001", "batch_id": "B1", "stage": "RIE",
                      "stage_seq": "7"},
                     {"run_id": "B2-ICP-0001", "batch_id": "B2", "stage": "ICP",
                      "stage_seq": "1", "note": "长一点，让文件尺寸一定变"}])
    m = br.stage_seq_map()
    assert m[("B1", "RIE")] == 7, "runs.csv 改了还喂旧值 = A4 复发"
    assert m[("B2", "ICP")] == 1


def test_run_facts_盘上改了要重建(tmp_path, monkeypatch):
    core = seed_core(tmp_path / "core")
    monkeypatch.setenv("OPENNANO_CORE_DIR", str(core))
    from kb import batch_runs as br

    _runs_csv(core, [{"run_id": "B1-RIE-0001", "batch_id": "B1", "stage": "RIE",
                      "tool_id": "RIE10NR"}])
    assert br._core_run_facts()["B1-RIE-0001"]["tool_id"] == "RIE10NR"

    _runs_csv(core, [{"run_id": "B1-RIE-0001", "batch_id": "B1", "stage": "RIE",
                      "tool_id": "RIE200NL", "status": "done 改长一些改变尺寸"}])
    assert br._core_run_facts()["B1-RIE-0001"]["tool_id"] == "RIE200NL"


def test_parent_map_core_部分盘上改了要重建(tmp_path, monkeypatch):
    core = seed_core(tmp_path / "core")
    monkeypatch.setenv("OPENNANO_CORE_DIR", str(core))
    monkeypatch.setenv("OPENNANO_PACKS_ROOT", "")          # 不扫实验包，只测 core 部分
    from kb import batch_runs as br

    _runs_csv(core, [{"run_id": "B1-RIE-0002", "batch_id": "B1", "stage": "RIE",
                      "parent_run_id": "B1-RIE-0001"}])
    assert br._parent_map_from_packs()["B1-RIE-0002"] == "B1-RIE-0001"

    _runs_csv(core, [{"run_id": "B1-RIE-0002", "batch_id": "B1", "stage": "RIE",
                      "parent_run_id": "B1-PECVD-0009 改长改变尺寸"}])
    assert br._parent_map_from_packs()["B1-RIE-0002"].startswith("B1-PECVD-0009")


def test_parent_map_包扫描内容变了要重建(tmp_path, monkeypatch):
    core = seed_core(tmp_path / "core")
    packs = tmp_path / "packs"
    packs.mkdir()
    monkeypatch.setenv("OPENNANO_CORE_DIR", str(core))
    monkeypatch.setenv("OPENNANO_PACKS_ROOT", str(packs))
    from kb import batch_runs as br

    (packs / "runs.csv").write_text(
        "run_id,parent_run_id\nX-RIE-0001,\n", encoding="utf-8")
    assert br._parent_map_from_packs().get("X-RIE-0001") == ""

    (packs / "runs.csv").write_text(
        "run_id,parent_run_id,note\nX-RIE-0001,X-PECVD-0003,补上父边（顺便改变尺寸）\n",
        encoding="utf-8")
    assert br._parent_map_from_packs()["X-RIE-0001"] == "X-PECVD-0003"


def test_grp_slots_菜单变了要重建(tmp_path, menu_export):
    """`_GRP_SLOTS_CACHE`：同一目录里 `.grp` 变了，槽位集合必须重算（同刻重导菜单场景）。"""
    import shutil
    from kb import menu_reader as mr

    d = tmp_path / "menu"
    d.mkdir()
    grps = sorted(menu_export.rglob("*.grp"))
    if not grps:
        pytest.skip("菜单导出里没有 .grp")
    shutil.copy(grps[0], d / grps[0].name)
    first = mr._grp_slots(d)

    # 模拟同刻重导：同名文件内容变化（这里直接删掉它 ⇒ 槽位集合必须变空）
    (d / grps[0].name).unlink()
    second = mr._grp_slots(d)
    assert second == set() and second != first or not first, (
        "菜单文件变了还喂旧槽位 = A4 同病复发")

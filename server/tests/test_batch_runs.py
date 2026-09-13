"""批次 / 续做 / 样品树 —— 回归网（`kb/batch_runs.py`）。"""
from __future__ import annotations

import pytest

from conftest import seed_core
from batch_fixtures import BATCH, ROOT, batch_rows, modules, run_rows, sample_rows


@pytest.fixture
def core(tmp_path, monkeypatch):
    """合成 core + 把 append_pack.CORE_DIR 指过去（batch_runs 走 OPENNANO_CORE_DIR）。"""
    from kb import append_pack as ap
    d = seed_core(tmp_path / "core", runs=run_rows(), batches=batch_rows(),
                  samples=sample_rows())
    monkeypatch.setattr(ap, "CORE_DIR", d)
    monkeypatch.setenv("OPENNANO_CORE_DIR", str(d))
    return d


# ------------------------------------------------------------------ 纯逻辑
def test_parse_run_id_从右切_stage_可含横线():
    from kb.batch_runs import parse_run_id
    assert parse_run_id("AR50-T1-DRIE-0002") == {"batch": "AR50-T1", "stage": "DRIE", "seq": 2}
    assert parse_run_id("TEST-T1-ICP-0001")["batch"] == "TEST-T1"
    # 非法输入一律 None（**绝不猜**）
    for bad in ("", None, "AR50-T1-DRIE", "AR50-T1-DRIE-XX", "DRIE-0002"):
        assert parse_run_id(bad) is None, bad


def test_stage_seq_回落顺序_核心缺失才用习惯表():
    """① core 权威 ② 画布 ③ 习惯表 ④ 表外续编 —— 四级顺序不能乱。"""
    from kb.batch_runs import STAGE_ORDER, _stage_seq
    assert _stage_seq([], "X", "DRIE") == STAGE_ORDER.index("DRIE") + 1
    mods = [{"core_run_id": "X-NEWSTAGE-0001", "core_stage_seq": 42}]
    assert _stage_seq(mods, "X", "NEWSTAGE") == 42          # 表外：沿用画布给的号
    assert _stage_seq([{"core_run_id": "X-FOO-0001"}], "X", "FOO") == len(STAGE_ORDER) + 1


# ------------------------------------------------------------------ core 回读
def test_runs_of_batch_从_core_回读_parent_sample_nature(core):
    """⚠️ 教训：语义标注常**只在 core 侧**（模块没带 ⇒ 必须回读，否则批次视图丢整条链）。"""
    from kb.batch_runs import runs_of_batch
    rows = runs_of_batch(modules(), BATCH)
    assert [r["run_id"] for r in rows][0] == f"{BATCH}-PECVD-0001"
    by_id = {r["run_id"]: r for r in rows}
    ldw = by_id[f"{BATCH}-LDW-0001"]
    assert ldw["parent_run_id"] == f"{BATCH}-PECVD-0001"      # 模块没带 ⇒ 从 core 读
    assert ldw["sample_id"] == ROOT
    assert by_id[f"{BATCH}-ICP-0003"]["run_nature"] == "trial"
    assert by_id[f"{BATCH}-ICP-0004"]["run_nature"] == ""      # core 也没标 ⇒ 留空，不猜
    # 排序：按 (stage_seq, stage, seq)
    seqs = [(r["stage_seq"], r["stage"], r["seq"]) for r in rows]
    assert seqs == sorted(seqs)
    assert {r["stage_seq"] for r in rows if r["stage"] == "ICP"} == {3}   # 同 stage 同号


def test_chain_of_可见链自洽且悬空父单列(core):
    from kb.batch_runs import chain_of
    c = chain_of(modules(), BATCH)
    assert c["count"] == len(run_rows())
    # 3 条有父（LDW→PECVD、ICP1/2→LDW、DRIE1→ICP1、DRIE2→DRIE1）里，数一数
    assert c["edges"] == sum(1 for r in run_rows() if r.get("parent_run_id"))
    assert c["dangling_parents"] == []                       # 全部父都在画布里
    assert len(c["roots"]) == c["count"] - c["edges"]


def test_chain_of_悬空父不算错但要报(core, monkeypatch):
    """跨包续做时父在画布外 ⇒ 单列 `dangling_parents`，**不当错误**。"""
    from kb import batch_runs as br
    mods = [m for m in modules() if m["core_run_id"] != f"{BATCH}-PECVD-0001"]
    c = br.chain_of(mods, BATCH)
    assert f"{BATCH}-LDW-0001" in c["dangling_parents"]


# ------------------------------------------------------------------ 续做（分支安全）
def test_续做序号_取最大加一_不按条数(core):
    """删过 run 也不许撞号 ⇒ 必须用 max(seq)+1。"""
    from kb.batch_runs import next_run
    n = next_run(modules(), BATCH, "ICP")
    assert n["seq"] == 5 and n["run_id"] == f"{BATCH}-ICP-0005"
    assert n["is_continuation"] is True
    assert n["stage_seq"] == 3                              # 沿用 core 权威值


def test_续做取父_指定sample只看自己_绝不误挂到别的分支(core):
    """并发分支的核心保障：给 DIE15-01 续做 ⇒ parent 只能是它自己那条 ICP-0003。"""
    from kb.batch_runs import next_run
    n = next_run(modules(), BATCH, "ICP", sample_id=f"{BATCH}-01-DIE15-01")
    assert n["parent_run_id"] == f"{BATCH}-ICP-0003"
    assert n["same_sample_runs"] == [f"{BATCH}-ICP-0003"]
    # 没做过的新样品 ⇒ 空 parent（合法语义），**绝不退回别人的 run**
    fresh = next_run(modules(), BATCH, "ICP", sample_id=f"{BATCH}-01-DIE4-X9")
    assert fresh["parent_run_id"] == ""
    assert fresh["run_id"] == f"{BATCH}-ICP-0005"


def test_续做取父_不指定sample时保持线性语义(core):
    from kb.batch_runs import next_run
    n = next_run(modules(), BATCH, "DRIE")
    assert n["parent_run_id"] == f"{BATCH}-DRIE-0002"       # 该 stage 最后一条
    assert n["seq"] == 3
    assert n["sample_id"] == f"{BATCH}-01-DIE15-01"          # 继承上一条的 sample


def test_续做显式父优先(core):
    from kb.batch_runs import next_run
    n = next_run(modules(), BATCH, "DRIE", parent_run_id=f"{BATCH}-ICP-0002")
    assert n["parent_run_id"] == f"{BATCH}-ICP-0002"


def test_new_stage_首条_无父且_stage_seq_可提示(core):
    from kb.batch_runs import next_run
    n = next_run(modules(), BATCH, "RIE", stage_hint=6)
    assert n["seq"] == 1 and n["parent_run_id"] == ""
    assert n["is_continuation"] is False and n["stage_seq"] == 6


# ------------------------------------------------------------------ 并行分支
def test_parallels_识别同上游并发并告警缺_die_层(core):
    from kb.batch_runs import parallels
    ps = parallels(modules(), BATCH)
    icp = {p["parent_run_id"]: p for p in ps if p["stage"] == "ICP"}
    # ① 同一上游（LDW）下的两条并发 —— 这是"分支"的正身
    p = icp[f"{BATCH}-LDW-0001"]
    assert p["count"] == 2 and p["runs"] == [f"{BATCH}-ICP-0001", f"{BATCH}-ICP-0002"]
    assert p["distinct_samples"] == 2
    assert p["hint"] == ""                                   # 有 die 区分 ⇒ 不告警
    assert p["kind"].startswith("concurrent under one upstream")
    # ② 无共同上游的两条并发（ICP-0003 有样品、ICP-0004 没标）
    orphan = icp[""]
    assert orphan["count"] == 2
    assert orphan["distinct_samples"] == 1                    # 只有 0003 有 sample
    assert "share sample" in orphan["hint"]
    assert orphan["kind"].startswith("same-stage concurrency with no common upstream")


def test_parallels_全都没标_sample_不许说成同一个样品():
    """⚠️ 曾把"都没标 sample"误报成"sample 是同一个（样品组）"⇒ 提示会把人带偏。

    （不建 core ⇒ 没有任何可回读的 sample，正是"包与 core 都没标"的场景）
    """
    from kb.batch_runs import parallels
    mods = [{"core_run_id": f"{BATCH}-ICP-0001", "core_parent_run_id": "P"},
            {"core_run_id": f"{BATCH}-ICP-0002", "core_parent_run_id": "P"}]
    p = parallels(mods, BATCH)[0]
    assert p["distinct_samples"] == 0 and p["samples"] == []
    assert "none of these runs has a sample" in p["hint"] and "one wafer ran several times" in p["hint"]


def test_parallels_同一样品组要提示需人工标性质():
    """同 stage 共享同一个 sample ⇒ 可能是**样品组**（组内每颗各做一次）⇒ 工具不猜。"""
    from kb.batch_runs import parallels
    batch = BATCH
    mods = [{"core_run_id": f"{batch}-ICP-0001", "core_parent_run_id": "P", "core_sample_id": "G"},
            {"core_run_id": f"{batch}-ICP-0002", "core_parent_run_id": "P", "core_sample_id": "G"}]
    p = parallels(mods, batch)[0]
    assert p["distinct_samples"] == 1 and "share sample" in p["hint"]


# ------------------------------------------------------------------ 性质判定
def test_classify_四类性质_证据优先_人工覆盖最先(core):
    from kb.batch_runs import classify
    got = {c["run_id"]: c for c in classify(modules(), BATCH)}
    assert got[f"{BATCH}-LDW-0001"]["nature"] == "chain"          # 有父 ⇒ 链（最硬）
    assert got[f"{BATCH}-ICP-0003"]["nature"] == "trial"          # core 标 trial
    assert got[f"{BATCH}-PECVD-0001"]["nature"] == "batch_level"  # 整片 + 本批已有 die
    # core 侧 run_nature 是权威标注
    assert got[f"{BATCH}-ICP-0003"]["why"] == "人工标注（域知识优先）"


def test_classify_画布覆盖优先于_core(core):
    from kb.batch_runs import classify
    mods = modules()
    for m in mods:
        if m["core_run_id"] == f"{BATCH}-DRIE-0002":
            m["core_run_nature"] = "trial"
    got = {c["run_id"]: c for c in classify(mods, BATCH)}
    assert got[f"{BATCH}-DRIE-0002"]["nature"] == "trial"
    assert got[f"{BATCH}-DRIE-0002"]["overridden"] is True


def test_needs_human_只列真的判不了的(core):
    """无父 + 无 sample + 同 stage 多条 ⇒ 列出来给人标（season? 独立试验?）——工具不猜。"""
    from kb.batch_runs import needs_human_nature
    assert needs_human_nature(modules(), BATCH) == [f"{BATCH}-ICP-0004"]


# ------------------------------------------------------------------ 样品树
def test_sample_tree_整片到组到组内_悬空父单列(core):
    from kb.append_pack import sample_tree
    t = sample_tree(BATCH)
    assert t["count"] == 5
    roots = [n["sample_id"] for n in t["tree"]]
    assert roots == [ROOT]
    root = t["tree"][0]
    assert sorted(root["children"]) == [f"{BATCH}-01-DIE15", f"{BATCH}-01-DIE4"]
    die15 = t["nodes"][f"{BATCH}-01-DIE15"]
    assert die15["children"] == [f"{BATCH}-01-DIE15-01"]
    assert die15["child_count"] == 1
    assert t["orphan_parent"] == [f"{BATCH}-01-DIE99"]          # 悬空 parent 不静默丢
    assert "count inside the group" in t["note"]                              # DIE4/DIE15 的数字不是位号


def test_sample_tree_样品性质标签(core):
    from kb.append_pack import _sample_nature
    # 界面 2026-09-13 定全英文 ⇒ 这些是**界面标签**，跟界面语言走（`run_nature` 的键不变）
    assert _sample_nature([{"run_nature": "trial"}]) == "trial wafer"
    assert _sample_nature([{"run_nature": "chain"}]) == "chained sample"
    assert _sample_nature([{"run_nature": "batch_level"}]) == "batch level (whole/multi-die)"
    assert _sample_nature([]) == "no runs"
    assert _sample_nature([{}]) == "untagged (parent inferred)"


# ------------------------------------------------------------------ 批次概览
def test_batches_of_概览(core):
    from kb.batch_runs import batches_of
    bs = batches_of(modules())
    assert len(bs) == 1
    b = bs[0]
    assert b["batch_id"] == BATCH and b["runs"] == len(run_rows())
    assert b["chain"].startswith("PECVD → LDW")

"""expack `stage_seq` 语义（2026-09-16 审计 **P0** 的回归锁）。

红证（修前，子代理实测）：`extract_rows` 用 **run_id 尾数**当 `stage_seq` 的兜底 ⇒
多工序工程里**每个工序的首个 run 都写成 1**（实测 PECVD/MA6/ICP/DRIE 全 1）；
更糟的是它用 `setdefault` **写回模块**，而追加包的第一优先级就是读模块上的 `core_stage_seq`
⇒ 错值一路传下去，最后原样落 core（数据线 `build_core` 不重算 stage_seq）。

正解：`stage_seq` ＝ **工序序号**（core 语义），来源必须是
`kb.batch_runs._stage_seq`（① core 已入库值 → ② 画布已有值 → ③ 习惯序表）。
"""
from __future__ import annotations

from conftest import seed_core


#: 画布模板名 → stage 的真映射（`resolve_stage` 先看 `equipment_name`；
#: ⚠️ 它**不看** `core_stage` —— 见测试末尾的"顺带发现"）
TMPL = {"PECVD": "PECVD", "MA6": "UV Exposure", "ICP": "ICP Etch"}


def _mod(mid, stage, rid, **kw):
    m = {"id": mid, "name": stage, "subtype": "etch", "core_run_id": rid,
         "core_stage": stage, "core_sample_id": "S1",
         "equipment_name": TMPL.get(stage, stage)}
    m.update(kw)
    return m


def test_新节点_stage_seq_取工序序号_不是_run_尾数(tmp_path, monkeypatch):
    """合成 core 里 PECVD=1 / MA6=5 / ICP=6 ⇒ 工程内三条**首 run** 必须分别是 1/5/6，不是 1/1/1。"""
    core = seed_core(tmp_path / "core", runs=[
        {"run_id": "E-T1-PECVD-0001", "batch_id": "E-T1", "stage": "PECVD", "stage_seq": "1"},
        {"run_id": "E-T1-MA6-0001", "batch_id": "E-T1", "stage": "MA6", "stage_seq": "5"},
        {"run_id": "E-T1-ICP-0001", "batch_id": "E-T1", "stage": "ICP", "stage_seq": "6"},
    ])
    monkeypatch.setenv("OPENNANO_CORE_DIR", str(core))
    from kb import batch_runs as br
    br._STAGE_SEQ_CACHE = None
    br._STAGE_SEQ_CACHE_SIG = None

    from kb import expack
    proj = {"name": "E-T1", "edges": [], "modules": [
        _mod("m1", "PECVD", "E-T1-PECVD-0001"),
        _mod("m2", "MA6", "E-T1-MA6-0001"),
        _mod("m3", "ICP", "E-T1-ICP-0001"),
    ]}
    rows, _, _, _, _ = expack.extract_rows(proj, lib=None)
    got = {r[0]: r[4] for r in rows}
    assert got == {"E-T1-PECVD-0001": 1, "E-T1-MA6-0001": 5, "E-T1-ICP-0001": 6}, got


def test_stage_seq_会写回模块_且值正确(tmp_path, monkeypatch):
    """错值被写回模块是"传播链"的根 ⇒ 写回可以，但必须是**正确的工序序号**。"""
    core = seed_core(tmp_path / "core", runs=[
        {"run_id": "E-T2-ICP-0001", "batch_id": "E-T2", "stage": "ICP", "stage_seq": "6"},
    ])
    monkeypatch.setenv("OPENNANO_CORE_DIR", str(core))
    from kb import batch_runs as br
    br._STAGE_SEQ_CACHE = None
    br._STAGE_SEQ_CACHE_SIG = None

    from kb import expack
    mods = [_mod("m1", "ICP", "E-T2-ICP-0002")]
    expack.extract_rows({"name": "E-T2", "edges": [], "modules": mods}, lib=None)
    assert mods[0]["core_stage_seq"] == 6, mods[0].get("core_stage_seq")


def test_表外工序_不与已有工序撞号(tmp_path, monkeypatch):
    """习惯序表里没有的 stage：`_stage_seq` 会续编（不与已用序号重叠）。"""
    core = seed_core(tmp_path / "core", runs=[
        {"run_id": "E-T3-PECVD-0001", "batch_id": "E-T3", "stage": "PECVD", "stage_seq": "1"},
    ])
    monkeypatch.setenv("OPENNANO_CORE_DIR", str(core))
    monkeypatch.setenv("OPENNANO_PACKS_ROOT", "")
    from kb import batch_runs as br
    br._STAGE_SEQ_CACHE = None
    br._STAGE_SEQ_CACHE_SIG = None

    got = br._stage_seq([_mod("m1", "CUSTOM_X", "E-T3-CUSTOM_X-0001")], "E-T3", "CUSTOM_X")
    assert got and got > 1, got

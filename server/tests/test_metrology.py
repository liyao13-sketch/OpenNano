"""表征/检测节点（metrology）语义 —— 回归网（2026-09-13，owner拍板 B+）。

背景（owner发现「metrology 游离于体系之外」）与三条查实的机制：
  1. 画布：检测模块没有工序列号 ⇒ `col = max(seq-1, 0) = 0` ⇒ 排到**第 0 列**（最左、
     在被测 run 的**左边**，看着像最早的一步，还会造出"向左"的边）；
  2. 导出：`resolve_stage` 给它造一条 run，而**父是空**（老兜底"按导出顺序接上一条"是猜测）；
  3. core 现状：检测 run 确实存在（AR50-T2 的 ELLIP/SEM，note＝"上机前建行；结果现场填"），
     且 `stage_seq` **排在流程序列里**、`parent_run_id` ＝**被测的那条 run**。

B+ 定案（**保留 run**，与已发布契约 §32 和 core 既有 3 条一致；不建 run 的 A 方案会与记录冲突）：
  · 父 = **画布连线**（契约 §37「parent_run_id / 时序 = 连线」）—— 检测节点靠这条说出"我在测谁"；
  · 列 = **被测 run 右侧一列**（stage_seq 从父推导），不再落第 0 列；
  · **不给 `run{N}` 徽标**（"本工序第几次"对检测无意义）；
  · `.card` / CSV 一致：`build_expack` 必须把 `lib` 传给 `extract_rows`（否则经设备模板
    才认得出的节点会"卡上有、CSV 里没有"）。
⚠️ 本文件**不测**测量值挂谁（挂检测 run 还是被测 run）—— 那是数据域口径，已发【跨线】单；
   工具侧一律不擅自改（零号铁律：不推断、不替记录编归属）。
"""
from __future__ import annotations

import csv
import io
import zipfile

from kb.expack import (_bad_edges, _layout_modules, build_expack, build_process_card,
                       extract_rows, is_metrology_stage, resolve_stage, stage_run_index,
                       unmapped_modules)
from kb.canvas_geom import COL_PITCH

BATCH = "MT-T1"
SEM_TMPL = "扫描电镜（SEM）"
ELLIP_TMPL = "椭偏仪"


def _mod(mid, eq, **kw):
    m = {"id": mid, "equipment_name": eq, "params": {}, "param_outputs": []}
    m.update(kw)
    return m


# ---------------------------------------------------------------- 映射（不发明 stage）

def test_resolve_stage_covers_the_four_core_metrology_stages():
    """core 词表里的表征 stage 只有 SEM/ELLIP/STRESS/PROFILE —— 这四个必须认得出。"""
    assert resolve_stage(_mod("a", SEM_TMPL)) == "SEM"
    assert resolve_stage(_mod("b", ELLIP_TMPL)) == "ELLIP"
    assert resolve_stage(_mod("c", "应力仪")) == "STRESS"
    assert resolve_stage(_mod("d", "台阶仪")) == "PROFILE"
    for s in ("SEM", "ELLIP", "PROFILE", "STRESS"):
        assert is_metrology_stage(s) and not is_metrology_stage(s.lower().upper() + "X")


def test_unmapped_instrument_is_loud_not_silent():
    """TEM/XRD 这些**不硬塞**成 SEM —— 不许编 stage；但必须**出声**（进 unmapped 清单）。"""
    proj = {"name": BATCH, "modules": [_mod("a", "透射电镜（TEM）", subtype="tem")], "edges": []}
    assert resolve_stage(proj["modules"][0]) == ""          # 不编
    un = unmapped_modules(proj)
    assert len(un) == 1 and un[0]["name"]                    # 但卡片/清单里点得出名


# ---------------------------------------------------------------- 父 = 连线（契约 §37）

def test_metrology_parent_comes_from_the_link_not_from_export_order():
    """**决定性用例**：SEM 挂在第一道工序上，而导出顺序里它紧挨着第二条 ——
    父必须取连线那一端，不能取"上一条"（老兜底是猜测）。"""
    proj = {"name": BATCH, "edges": [{"src": "m1", "dst": "m3", "_link": "recorded"}],
            "modules": [_mod("m1", "PECVD"), _mod("m2", "ICP Etch"), _mod("m3", SEM_TMPL)]}
    runs, _, _, _, _ = extract_rows(proj)
    by = {r[0]: r for r in runs}
    assert set(by) == {f"{BATCH}-PECVD-0001", f"{BATCH}-ICP-0001", f"{BATCH}-SEM-0001"}
    parent = by[f"{BATCH}-SEM-0001"][13]
    assert parent == f"{BATCH}-PECVD-0001"                   # 连线那一端
    assert parent != f"{BATCH}-ICP-0001"                     # 而不是"导出顺序的上一条"


def test_core_recorded_empty_parent_is_never_filled_from_the_canvas():
    """在 core 里、parent 记为空 ⇒ **空就是空**：画布上有边也不许回填（零号铁律）。"""
    proj = {"name": BATCH, "edges": [{"src": "m1", "dst": "m3", "_link": "recorded"}],
            "modules": [_mod("m1", "PECVD"),
                        _mod("m3", SEM_TMPL, core_run_id=f"{BATCH}-SEM-0001")]}
    runs, _, _, _, _ = extract_rows(proj)
    row = next(r for r in runs if r[0] == f"{BATCH}-SEM-0001")
    assert row[13] == ""


def test_metrology_run_still_gets_created():
    """B+ **保留** run（不是 A 的"不建 run"）：core 里本来就有这样 3 条仪器 session。"""
    proj = {"name": BATCH, "edges": [{"src": "m1", "dst": "m3"}],
            "modules": [_mod("m1", "PECVD"), _mod("m3", SEM_TMPL)]}
    runs, _, _, _, _ = extract_rows(proj)
    sem = next(r for r in runs if r[0].endswith("-SEM-0001"))
    assert sem[3] == "SEM" and sem[13] == f"{BATCH}-PECVD-0001"


# ---------------------------------------------------------------- run{N} 不给检测节点

def test_stage_run_index_skips_metrology():
    runs = [{"run_id": f"{BATCH}-ICP-{i:04d}", "batch_id": BATCH, "stage": "ICP",
             "stage_seq": "3"} for i in (1, 2)]
    runs.append({"run_id": f"{BATCH}-SEM-0001", "batch_id": BATCH, "stage": "SEM",
                 "stage_seq": "4"})
    idx = stage_run_index(runs)
    assert idx[f"{BATCH}-ICP-0001"] == 1 and idx[f"{BATCH}-ICP-0002"] == 2
    assert f"{BATCH}-SEM-0001" not in idx                    # 检测节点不给"第几次"


# ---------------------------------------------------------------- 画布：不落第 0 列

def _layout_case():
    mods = [_mod("m1", "PECVD"), _mod("m2", "ICP Etch"), _mod("m3", SEM_TMPL)]
    runs = [{"run_id": f"{BATCH}-PECVD-0001", "batch_id": BATCH, "stage_seq": 1,
             "run_nature": "", "parent_run_id": ""},
            {"run_id": f"{BATCH}-ICP-0001", "batch_id": BATCH, "stage_seq": 2,
             "run_nature": "", "parent_run_id": f"{BATCH}-PECVD-0001"},
            # ⚠️ 计划节点（还没入库）：stage_seq 缺 ⇒ 老代码把它按 0 排到第 0 列
            {"run_id": f"{BATCH}-SEM-0001", "batch_id": BATCH, "stage_seq": 0,
             "run_nature": "", "parent_run_id": f"{BATCH}-PECVD-0001"}]
    edges = [{"src": "m1", "dst": "m2", "_link": "recorded"},
             {"src": "m1", "dst": "m3", "_link": "recorded"}]
    return runs, mods, edges


def test_metrology_sits_one_column_right_of_what_it_measures():
    runs, mods, edges = _layout_case()
    _layout_modules(runs, mods, edges)
    x = {m["id"]: m["x"] for m in mods}
    assert x["m3"] == x["m1"] + COL_PITCH          # 被测 run 右侧一列（不是第 0 列）
    assert x["m3"] > x["m1"]
    assert _bad_edges(runs, mods, edges) == []     # 修前这里会给出"向左的边"


def test_metrology_never_steals_the_spine():
    """主线必须还是工艺链：检测节点不许把真正的接棒者挤下去。"""
    runs, mods, edges = _layout_case()
    _layout_modules(runs, mods, edges)
    pos = {m["id"]: (m["x"], m["y"]) for m in mods}
    assert pos["m2"][1] == pos["m1"][1]            # 工艺子节点继承父行 ⇒ 主链一条直线
    assert pos["m3"][1] > pos["m1"][1]             # 检测节点挂到下一行
    assert pos["m3"][0] == pos["m2"][0]            # 同在被测 run 右侧那一列


# ---------------------------------------------------------------- 卡 / CSV 一致

class _Lib:
    """最小 lib 桩：够 `resolve_stage` 的三级回退用（机台 → 设备模板 → 工艺大类）。"""

    def __init__(self):
        self.data = {"machines": [{"id": "mc1", "name": "自研刻蚀机-1", "equipment_id": "e1"}],
                     "equipment": {"etch": [{"id": "e1", "name": "ICP Etch"}]}}

    def machines(self):
        return list(self.data["machines"])


def _zip_runs(data: bytes) -> list[dict]:
    z = zipfile.ZipFile(io.BytesIO(data))
    name = next(n for n in z.namelist() if n.endswith("runs.csv"))
    return list(csv.DictReader(z.read(name).decode("utf-8").splitlines()))


def test_build_expack_actually_uses_lib_so_card_and_csv_agree():
    """只靠设备模板才认得出的节点：**卡上有，CSV 也必须有**。

    修前 `build_expack` 收了 `lib` 却没传给 `extract_rows` ⇒ 这类节点从 CSV 里静默消失，
    而流程卡（另一个函数，传了 lib）里还在 —— 正好打破"卡与 CSV 逐字一致"的承诺。
    """
    proj = {"name": BATCH, "edges": [],
            "modules": [_mod("m1", "自研刻蚀机（内部编号）", machine_name="自研刻蚀机-1")]}
    assert resolve_stage(proj["modules"][0]) == ""            # 不给 lib ⇒ 认不出（会丢）
    data, _ = build_expack(proj, lib=_Lib())
    runs = _zip_runs(data)
    assert [r["run_id"] for r in runs] == [f"{BATCH}-ICP-0001"]
    assert runs[0]["stage"] == "ICP"
    card = build_process_card({"name": BATCH, "edges": [],
                               "modules": [_mod("m1", "自研刻蚀机（内部编号）",
                                                machine_name="自研刻蚀机-1")]}, lib=_Lib())
    assert f"{BATCH}-ICP-0001" in card                        # 卡与 CSV 同一套 id


# ---------------------------------------------------------------- 画布体检：游离要报出来

def test_layout_audit_flags_a_metrology_node_with_no_input():
    """检测节点没有入边 ⇒ 体检器要报「说不出在测谁」（owner原话：游离于体系之外）。"""
    from kb.layout_audit import audit
    proj = {"name": BATCH, "edges": [],
            "modules": [_mod("m1", SEM_TMPL, x=400, y=80)]}
    got = {i["kind"] for i in audit(proj)["issues"]}
    assert "metrology_no_input" in got

    # 连上被测 run 之后就不该再报（同一条规则的两面）
    proj2 = {"name": BATCH, "edges": [{"src": "m0", "dst": "m1", "_link": "recorded"}],
             "modules": [_mod("m0", "PECVD", x=140, y=80), _mod("m1", SEM_TMPL, x=402, y=80)]}
    got2 = {i["kind"] for i in audit(proj2)["issues"]}
    assert "metrology_no_input" not in got2


def test_process_card_says_what_the_metrology_node_measures():
    """流程卡要能读出"测的是谁"；没连线的检测节点要在卡上**显式告警**。"""
    linked = {"name": BATCH, "edges": [{"src": "m1", "dst": "m3", "_link": "recorded"}],
              "modules": [_mod("m1", "PECVD"), _mod("m3", SEM_TMPL)]}
    card = build_process_card(linked)
    assert f"检测对象**：`{BATCH}-PECVD-0001`" in card

    loose = {"name": BATCH, "edges": [], "modules": [_mod("m3", SEM_TMPL)]}
    assert "说不出测谁" in build_process_card(loose)

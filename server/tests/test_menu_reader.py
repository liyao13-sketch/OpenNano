"""设备菜单直读 / 批量体检（`kb/menu_reader.py` + `kb/menu_checker.py`）—— 回归网。

⚠️ 这类用例用**真菜单导出**（只读），本机没有就跳过 —— CI 上跑不到是**已知且可接受**的：
   它校验的是"控件口径"（`group N = [2(chuck), N(etch), 4(dechuck)]`、
   机台槽位号不许丢、G50+ 必须剔），这些口径在合成数据上测不出问题。
"""
from __future__ import annotations

import pytest


def _grp(menu_export):
    grps = sorted(menu_export.rglob("*.grp"))
    assert grps, f"{menu_export} 里没有 .grp"
    return grps[0]


# ------------------------------------------------------------------ 基本读取
def test_load_menu_配对时间戳差要告警(menu_export):
    from kb.menu_reader import PAIR_TOL_MIN, load_menu
    m = load_menu(menu_export)
    assert m["recipes"] and m["groups"], "两份导出都得解出东西（.grp=recipe 库 / .rcp=group 库）"
    assert m["scope_max"] == 49
    assert m["pair_delta_min"] is not None
    # 本机这份是**不同刻**导出的 ⇒ 必须明确告警（名字↔槽位配对不可靠）
    if m["pair_delta_min"] > PAIR_TOL_MIN:
        assert m["pair_warning"] and "不可靠" in m["pair_warning"]
    assert m["pair_delta_min"] <= 24 * 60                      # 单位是分钟，不是秒/天


def test_槽位分区与越界剔除(menu_export):
    from kb.menu_reader import SCOPE_MAX, load_menu
    m = load_menu(menu_export)
    zones = {z["range"]: z for z in m["zones"]}
    assert "G50+" in zones and zones["G50+"]["keep"] is False
    assert "他人菜单" in zones["G50+"]["use"]                   # G50+ 一律不分析/不入库
    for r in m["recipes"] + m["groups"]:
        assert r["slot"] <= SCOPE_MAX
    assert isinstance(m["skipped_out_of_scope"], int)


def test_recipe_超出范围直接拒绝(menu_export):
    from kb.menu_reader import SCOPE_MAX, recipe_slot
    with pytest.raises(ValueError):
        recipe_slot(SCOPE_MAX + 1, menu_export)


def test_step_列数就是原始表头(menu_export):
    """`step_columns` 给的是**原始 47 列**（体检要判"哪列没被映射"，只看规范键是看不出的）。"""
    from kb.menu_reader import step_columns
    cols = step_columns(_grp(menu_export))
    assert len(cols) >= 40 and cols[0].startswith("Step type")
    assert all(c.strip() == c and c for c in cols)


def test_解析器模块是动态加载的共享真源(menu_export):
    """菜单解析**只有一份实现**（数据线 `datasets_menu.py`）—— 工具绝不另写一套。"""
    from kb.menu_reader import parser
    dm = parser()
    for fn in ("parse_grp", "parse_rcp", "executed_steps", "map_params", "zone_of",
               "recipe_by_slot", "menu_duration", "role_from_params", "skipped_slots"):
        assert callable(getattr(dm, fn)), fn


# ------------------------------------------------------------------ group 灌参口径
def test_group_是_2_N_4_三段(menu_export):
    from kb.menu_reader import group_steps
    slot = 4
    g = group_steps(slot, menu_export)
    assert g["group_seq"] == [2, slot, 4]                       # chuck / etch / dechuck
    assert set(g["segments"]) == {"chuck", "etch", "dechuck"}
    assert g["segments"]["chuck"]["slot"] == 2
    assert g["segments"]["dechuck"]["slot"] == 4
    assert g["segments"]["etch"]["slot"] == slot
    assert g["total_steps"] == sum(v["executed"] for v in g["segments"].values())


def test_只记实际执行步_定义数可以更多(menu_export):
    """⚠️ 数据线裁定：**step 只记实际执行步**（未启用 loop 的槽不进 run），
    而配方定义仍保 30 槽。所以 `executed <= defined`，且两者都要报出来。"""
    from kb.menu_reader import group_steps
    g = group_steps(4, menu_export)
    for phase, v in g["segments"].items():
        assert 0 <= v["executed"] <= v["defined"], f"{phase}: {v}"
    assert g["defined_total"] == sum(v["defined"] for v in g["segments"].values())
    assert "只含**实际执行**步" in g["note"]


def test_step_order_全局递增而机台槽位号保留(menu_export):
    """`step_order` 全局 1..N（duration/steps 表要单调）；
    **机台槽位号**（组内局部编号）不能丢 —— 工艺侧"步号=机台槽位号"的口径靠它。"""
    from kb.menu_reader import group_steps
    g = group_steps(4, menu_export)
    assert [s["step_order"] for s in g["steps"]] == list(range(1, g["total_steps"] + 1))
    for s in g["steps"]:
        assert isinstance(s["machine_step"], int) and 1 <= s["machine_step"] <= 30
        assert s["step_name"] == f"{s['param_json']['phase']}-{s['machine_step']:02d}"
        assert s["param_json"]["machine_step"] == s["machine_step"]
        assert isinstance(s["duration_s"], (int, float))


def test_每个可用_group_都能灌出参(menu_export):
    """回归网的"地毯式"检查：所有**真正有配方**的 group 槽都要能走完 group_steps 而不炸；
    `.rcp` 列了但 `.grp` 没配方的（实测 G10）必须给**清楚的报错**，不许抛 SystemExit。"""
    from kb.menu_reader import _grp_slots, group_steps, load_menu
    slots = [g["slot"] for g in load_menu(menu_export)["groups"]]
    assert slots, "本机导出里没有 group"
    have = _grp_slots(menu_export)
    bad = []
    for slot in slots:
        if slot not in have:
            with pytest.raises(ValueError) as ei:
                group_steps(slot, menu_export)
            assert "没有 recipe" in str(ei.value) and "重导" in str(ei.value)
            bad.append(slot)
            continue
        g = group_steps(slot, menu_export)
        assert g["total_steps"] >= 1, f"G{slot} 灌不出步"
        assert g["group_seq"] == [2, slot, 4]
    assert len(slots) - len(bad) >= 15, f"可用 group 太少：{slots}（缺配方 {bad}）"


def test_recipe_roles_不改调顺序(menu_export):
    """role 序列要**照菜单原样**（Bosch 交替就是交替，不许"顺手调顺"）。"""
    from kb.menu_reader import group_steps, recipe_roles
    roles = recipe_roles(4, menu_export)
    assert roles == [s["role"] for s in group_steps(4, menu_export)["steps"]]


# ------------------------------------------------------------------ 批量体检
def test_体检报告回答五个问题(menu_export):
    from kb.menu_checker import check_dump
    r = check_dump(menu_export)
    assert set(r) >= {"ok", "dump", "pairing", "counts", "step_columns", "recipes",
                      "groups", "drift", "warnings"}
    assert r["counts"]["recipes"] > 0 and r["counts"]["groups"] > 0
    # G50+ 是**他人菜单**：必须剔掉且报数（实测本机那份有 4 个：G051–G054）
    assert r["counts"]["out_of_scope"] >= 0
    if r["counts"]["out_of_scope"]:
        assert any("G50+" in w for w in r["warnings"])
    assert r["step_columns"]["total"] >= 40
    assert "§13.4" in r["step_columns"]["rule_handled_note"]
    # §13.4 的时间列是**故意不映射**的 ⇒ 不许出现在"漏映射"里
    assert not (set(r["step_columns"]["unmapped"]) & set(r["step_columns"]["rule_handled"]))
    assert all("Process time" not in c for c in r["step_columns"]["unmapped"])
    assert r["ok"] == (not r["warnings"])


def test_体检报告要对齐配对告警(menu_export):
    from kb.menu_checker import check_dump
    r = check_dump(menu_export)
    if r["pairing"]["delta_min"] and r["pairing"]["delta_min"] > 5:
        assert r["pairing"]["ok"] is False
        assert any("不可靠" in w for w in r["warnings"])


def test_只有一次导出时不许报跨_dump_漂移(menu_export):
    """漂移是"与上一次比"—— 只有一份时**必须啥都不报**（曾把自身当上一次比）。"""
    from kb.menu_checker import check_dump, discover_dumps
    dumps = discover_dumps(menu_export.parent)
    if len(dumps) > 1:
        pytest.skip(f"本机同机台有 {len(dumps)} 次导出，此断言只在单份时成立")
    r = check_dump(menu_export)
    assert r["drift"]["prev"] is None
    assert r["drift"]["changed"] == []


def test_report_text_是人读报告(menu_export):
    """`report_text` 收的是 **`check_tree`**（批量）的结果，不是 `check_dump`（单份）——
    两者形状不同，混用会 KeyError（回归网抓过这个错配）。"""
    from kb.menu_checker import check_tree, report_text
    res = check_tree(menu_export.parent)
    assert res["summary"]["dumps"] >= 1
    assert res["summary"]["ok"] + res["summary"]["with_warnings"] == res["summary"]["dumps"]
    assert res["summary"]["verdict"]
    txt = report_text(res)
    assert "批量体检" in txt and "结论" in txt
    assert str(menu_export.name) in txt


def test_discover_dumps_扫得到导出目录(menu_export):
    from kb.menu_checker import discover_dumps
    got = discover_dumps(menu_export.parent)
    assert got and any(d["dir"] == str(menu_export) for d in got)
    assert all(d["files"] for d in got)

"""左栏方块的族色 **必须** 等于画布方块的族色（`engine/module_factory.py`）—— 回归网。

为什么值得钉住：owner 2026-09-13 说「左栏方块按族上色」。而"按族"能成立的前提是
**两边算出同一个 family**。此前左栏是写死的两组色（工艺=靛紫 / 检测=紫罗兰），
画布却按 15 个工艺族上色 ⇒ 同一个 etch 在左栏一个色、在画布另一个色。
根因是"同一件事在两处各写一份规则"——本用例把这条不变量变成机器断言：

    module_catalog() 里每一项的 family  ==  build_module(该项 subtype) 的 family

后端给对了，前端只剩"用这个 family 查色表"一件事（前端侧由 `工具/UI截图.cjs` 实测方块色）。
"""
from __future__ import annotations

import pytest


def test_每个方块都带族_且与模块工厂同源():
    """纯逻辑：目录项必须带 family/family_label；无库时按类别名回落到同一判法。"""
    from engine.module_factory import module_catalog, _family_of
    from engine.process_catalog import CATEGORY_LABELS, CATEGORIES, METROLOGY, METRO_FAMILY

    items = module_catalog(None)
    assert len(items) == len(CATEGORIES) + len(METROLOGY) == 25
    for it in items:
        assert it["family"], it
        assert it["family_label"], it
        # 目录里的族 = 该 subtype 造出来的模块会拿到的族（同一函数算，不是抄一份）
        assert it["family"] == _family_of(it["subtype"], None), it

    proc = {i["subtype"]: i for i in items if i["group"] == "PROCESS"}
    assert set(proc) == set(CATEGORIES)
    for cat in CATEGORIES:
        assert proc[cat]["name"] == CATEGORY_LABELS[cat]

    metro = {i["subtype"]: i for i in items if i["group"] == "METROLOGY"}
    assert set(metro) == {m[0] for m in METROLOGY}
    for sub, fam in METRO_FAMILY.items():
        if sub in metro:
            assert metro[sub]["family"] == fam, (sub, metro[sub]["family"], fam)


def test_有库时_目录族等于真实建出的模块族():
    """接真库（`main.LIB`）：目录项与 `build_module` 必须给同一个 family。

    ⚠️ process 类的族要**默认设备名**参与判（同属 graphic，Spin Coating 是 resist、
    UV Exposure 是 expose）——所以这条只有接上真库才测得到，无库时上一条用例已覆盖回落路径。
    """
    pytest.importorskip("fastapi")                    # main 需要 fastapi（与其它接口用例同规矩）
    from main import LIB                              # noqa: E402
    from engine.module_factory import module_catalog, build_module

    items = module_catalog(LIB)
    for it in items:
        m = build_module(it["subtype"], LIB)
        assert m["family"] == it["family"], f"{it['subtype']}: 目录 {it['family']} vs 模块 {m['family']}"
        assert m["family_label"] == it["family_label"], it["subtype"]


def test_图谱族色表_覆盖目录里的每一个族():
    """前端色表（`FAMILY_COLOR`）必须认得出后端可能给出的**每一个**族。

    `FAMILY_COLOR` 在 TS 里，这里用同一份清单做**清单级**校验：族名对不上就会静默落到
    兜底灰色（`ProcessNode` 里 `|| KIND_COLOR[kind] || '#6b7280'`），颜色对不上很难被发现。
    """
    from pathlib import Path
    from engine.module_factory import module_catalog
    from engine.process_catalog import FAMILY_LABELS

    ts = (Path(__file__).resolve().parents[2] / "web/src/App.tsx").read_text(encoding="utf-8")
    block = ts.split("const FAMILY_COLOR", 1)[1].split("}", 1)[0]
    known = set()
    for line in block.splitlines():
        for tok in line.replace(":", " ").replace(",", " ").split():
            if tok.isidentifier():
                known.add(tok)
    missing = {i["family"] for i in module_catalog(None)} - known
    assert not missing, f"前端 FAMILY_COLOR 缺这些族：{sorted(missing)}"
    # 后端标签表也认得出（左栏 tooltip 用 family_label）
    assert {i["family"] for i in module_catalog(None)} <= set(FAMILY_LABELS)

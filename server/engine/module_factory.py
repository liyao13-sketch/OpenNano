"""模块工厂:按工艺大类构造模块 dict(加载默认设备的参数模板+参数接口)。

对应桌面版 scene._apply_equipment_defaults + make_module,去 Qt 化、输出纯 JSON dict。
"""
from __future__ import annotations

from .library import LibraryStore
from .param_defs import defaults_for
from .process_catalog import (CATEGORIES, CATEGORY_LABELS, METROLOGY,
                              family_for, family_label)
from .schema import new_id


def _family_of(subtype: str, library: LibraryStore | None) -> str:
    """这个 subtype 造出来的模块会落到哪个**工艺族**。

    左栏方块的色 = 画布上那个方块的色 ⇒ 两边必须同源（owner 2026-09-13：左栏方块按族上色）。
    process 类要让**默认设备名**参与判族（同属 graphic，`Spin Coating` 是 resist、
    `UV Exposure` 是 expose）；查不到默认设备就退回类别名。
    """
    from .process_catalog import METRO_FAMILY
    if subtype in CATEGORIES:
        eq_name = ""
        if library is not None:
            eid = library.default_equipment_id(subtype)
            eq = library.get_equipment(eid) if eid else None
            eq_name = (eq or {}).get("name", "") or ""
        return family_for(eq_name or CATEGORY_LABELS[subtype], subtype)
    return METRO_FAMILY.get(subtype, "metro")


def module_catalog(library: LibraryStore | None = None) -> list[dict]:
    """模块目录(左栏):PROCESS(9 大类) + METROLOGY(16 项)。

    每项带上 `family/family_label` ⇒ 前端左栏方块直接用**画布同一套族色**
    （此前是"9 个工艺全一个靛紫、16 个检测全一个紫罗兰"，看不出谁是谁）。
    """
    items = []
    for cat in CATEGORIES:
        fam = _family_of(cat, library)
        items.append({"group": "PROCESS", "kind": "process", "subtype": cat,
                      "name": CATEGORY_LABELS[cat], "desc": CATEGORY_LABELS[cat],
                      "family": fam, "family_label": family_label(fam)})
    for sub, name, desc in METROLOGY:
        fam = _family_of(sub, library)
        items.append({"group": "METROLOGY", "kind": "inspect", "subtype": sub,
                      "name": name, "desc": desc,
                      "family": fam, "family_label": family_label(fam)})
    return items


def build_module(subtype: str, library: LibraryStore | None, name: str | None = None,
                 x: float = 0, y: float = 0) -> dict:
    """构造模块 dict。process 类自动加载默认设备的参数模板与参数接口。"""
    if subtype in CATEGORIES:
        kind = "process"
        name = name or CATEGORY_LABELS[subtype]
        param_defs = defaults_for(subtype)
        equipment_id = ""
        equipment_name = ""
        inputs: list = []
        outputs: list = []
        formulas: dict = {}
        if library is not None:
            eid = library.default_equipment_id(subtype)
            if eid:
                eq = library.get_equipment(eid)
                if eq and eq.get("params"):
                    param_defs = {k: dict(v) for k, v in eq["params"].items()}
                    equipment_id = eid
                    equipment_name = eq.get("name", "")
                    inputs = list(eq.get("inputs", []))
                    outputs = list(eq.get("outputs", []))
                    formulas = dict(eq.get("formulas", {}))
        fam = _family_of(subtype, library)
        return {
            "id": new_id("md"), "kind": kind, "subtype": subtype, "name": name,
            "x": x, "y": y,
            "params": {}, "param_meta": {}, "param_defs": param_defs,
            "equipment_id": equipment_id, "equipment_name": equipment_name,
            "family": fam, "family_label": family_label(fam),
            "param_inputs": inputs, "param_outputs": outputs, "formulas": formulas,
            "material": {}, "key_values": {},
            "doe": None, "annotations": [], "sim_result": None,
        }
    # metrology / 其它:表征节点按其测量能力输出接口参数,参与下游传递
    from .process_catalog import METROLOGY_OUTPUTS
    mfam = _family_of(subtype, library)
    meta = next((m for m in METROLOGY if m[0] == subtype), None)
    nm = name or (meta[1] if meta else subtype)
    return {
        "id": new_id("md"), "kind": "inspect", "subtype": subtype, "name": nm,
        "x": x, "y": y,
        "params": {}, "param_meta": {}, "param_defs": {},
        "equipment_id": "", "equipment_name": nm,
        "family": mfam, "family_label": family_label(mfam),
        "param_inputs": [], "param_outputs": list(METROLOGY_OUTPUTS.get(subtype, [])),
        "formulas": {},
        "material": {}, "key_values": {},
        "doe": None, "annotations": [], "sim_result": None,
    }

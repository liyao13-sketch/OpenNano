"""模块工厂:按工艺大类构造模块 dict(加载默认设备的参数模板+参数接口)。

对应桌面版 scene._apply_equipment_defaults + make_module,去 Qt 化、输出纯 JSON dict。
"""
from __future__ import annotations

from .library import LibraryStore
from .param_defs import defaults_for
from .process_catalog import (CATEGORIES, CATEGORY_LABELS, METROLOGY,
                              family_for, family_label)
from .schema import new_id


def module_catalog() -> list[dict]:
    """模块目录(左栏):PROCESS(蓝,9 大类) + METROLOGY(紫,15 项)。"""
    items = []
    for cat in CATEGORIES:
        items.append({"group": "PROCESS", "kind": "process", "subtype": cat,
                      "name": CATEGORY_LABELS[cat], "desc": CATEGORY_LABELS[cat]})
    for sub, name, desc in METROLOGY:
        items.append({"group": "METROLOGY", "kind": "inspect", "subtype": sub,
                      "name": name, "desc": desc})
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
        fam = family_for(equipment_name or CATEGORY_LABELS[subtype], subtype)
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
    from .process_catalog import METROLOGY_OUTPUTS, METRO_FAMILY
    mfam = METRO_FAMILY.get(subtype, "metro")
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

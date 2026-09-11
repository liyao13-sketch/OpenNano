"""选择性导出:把勾选的模块数据按语义归类到 Excel 不同 sheet。

V1 完整落地"参数"sheet;DOE/标注/仿真结果 sheet 提供表头模板,有数据则填充。
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from .container import Project
from .schema import ANNOTATION_TYPES

# 语义标签 → sheet 名
SHEET_PARAMS = "参数"
SHEET_MODULES = "模块"
SHEET_EDGES = "连线"
SHEET_DOE = "DOE实验"
SHEET_ANNOT = "标注"
SHEET_SIM = "仿真结果"

_HEAD = Font(bold=True)


def _write_header(ws, headers: list[str]) -> None:
    for c, h in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=c, value=h)
        cell.font = _HEAD


def _autosize(ws, ncols: int, widths: list[int]) -> None:
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def export_project(project: Project, selected_ids: Iterable[str],
                   out_path: str | Path) -> Path:
    """把 project 中被勾选的模块(selected_ids)导出到 out_path 的 xlsx。

    若 selected_ids 为空则导出全部模块。返回写出的文件路径。
    """
    sel = list(selected_ids) or list(project.modules.keys())
    mods = [project.modules[i] for i in sel if i in project.modules]

    wb = Workbook()

    # ---- 模块 sheet ----
    ws = wb.active
    ws.title = SHEET_MODULES
    _write_header(ws, ["模块ID", "名称", "大类", "子类型", "X", "Y"])
    for m in mods:
        ws.append([m.id, m.name, m.kind, m.subtype, m.x, m.y])
    _autosize(ws, 6, [22, 18, 12, 14, 8, 8])

    # ---- 参数 sheet ----
    ws = wb.create_sheet(SHEET_PARAMS)
    _write_header(ws, ["模块名", "子类型", "参数名", "值", "单位"])
    for m in mods:
        for key, val in m.params.items():
            unit = m.param_meta.get(key, {}).get("unit", "")
            ws.append([m.name, m.subtype, key, val, unit])
    _autosize(ws, 5, [18, 14, 16, 12, 8])

    # ---- 连线 sheet ----
    ws = wb.create_sheet(SHEET_EDGES)
    _write_header(ws, ["上游模块", "上游口", "下游模块", "下游口"])
    for e in project.edges:
        if e.src_module in project.modules and e.dst_module in project.modules:
            src = project.modules[e.src_module].name
            dst = project.modules[e.dst_module].name
            ws.append([src, e.src_port, dst, e.dst_port])
    _autosize(ws, 4, [18, 14, 18, 14])

    # ---- DOE sheet ----
    ws = wb.create_sheet(SHEET_DOE)
    _write_header(ws, ["模块名", "设计类型", "变量", "min", "max", "step", "实验号", "值"])
    for m in mods:
        doe = m.doe
        if not doe:
            continue
        for var in doe.get("variables", []):
            for rno, row in enumerate(doe.get("matrix", []), start=1):
                ws.append([m.name, doe.get("design_type", ""),
                           var.get("param", ""), var.get("min", ""),
                           var.get("max", ""), var.get("step", ""), rno, ""])
    _autosize(ws, 8, [18, 14, 16, 10, 10, 10, 8, 12])

    # ---- 标注 sheet ----
    ws = wb.create_sheet(SHEET_ANNOT)
    _write_header(ws, ["模块名", "标注类型", "测量值", "单位", "图像ID"])
    for m in mods:
        for a in m.annotations:
            label = dict(ANNOTATION_TYPES).get(a.get("semantic_type", ""), a.get("semantic_type", ""))
            ws.append([m.name, label, a.get("value", ""), a.get("unit", ""), a.get("image_id", "")])
    _autosize(ws, 5, [18, 16, 12, 8, 18])

    # ---- 仿真结果 sheet ----
    ws = wb.create_sheet(SHEET_SIM)
    _write_header(ws, ["模块名", "状态", "cd_top_nm", "cd_bottom_nm", "swa_deg", "depth_nm"])
    for m in mods:
        r = m.sim_result or {}
        metrics = r.get("metrics", {})
        ws.append([m.name, r.get("status", ""),
                   metrics.get("cd_top_nm", ""), metrics.get("cd_bottom_nm", ""),
                   metrics.get("swa_deg", ""), metrics.get("depth_nm", "")])
    _autosize(ws, 6, [18, 12, 12, 14, 10, 10])

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)
    return out

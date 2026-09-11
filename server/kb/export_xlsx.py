"""数据导出:知识条目 → xlsx,列名/列序严格对齐实验室现有表格。

四个 sheet:
  data      —— 与 03_实验数据/<dir>/data.csv 同列序(元信息 + 结果),一行一个 run
  steps     —— 与 steps.csv 同列序(test_id/step_order/step_name/气体_sccm/power_w/pressure_pa/time_s/note)
  中文对照   —— 中文表头(名称+单位),便于人读/粘进自己的表
  项目画布   —— 可选:当前画布模块/参数/连线
可反复导出→编辑→再导入(ingest 幂等),字段不丢。
"""
from __future__ import annotations

import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .result_fields import field_meta

# —— 与 data.csv 完全一致的列序 ——
DATA_COLUMNS = [
    "test_id", "test_label", "date", "operator", "mask_batch", "material",
    "substrate", "film_thickness_nm", "dep_method", "dep_time_min", "wafer_size",
    "resist_type", "resist_thickness_nm", "resist_swa_deg", "pattern_type",
    "mask_cd_nm", "pitch_nm",
    "er_nm_min", "depth_center_nm", "depth_top_nm", "depth_bottom_nm",
    "depth_left_nm", "depth_right_nm",
    "dist_top_mm", "dist_bottom_mm", "dist_left_mm", "dist_right_mm",
    "ler_nm", "lwr_nm", "sidewall_angle_deg", "selectivity", "final_cd_nm",
    "cd_loss_nm", "sem_path", "notes_path", "note",
]
# —— 与 steps.csv 一致的列序(气体列按各工艺类型实际列集,做到逐列对齐) ——
STEP_GAS_ORDER: dict[str, list[str]] = {
    "RIE_Cl":     ["bcl3", "cl2", "o2", "ar", "n2", "chf3"],   # 同 cl_rie/steps.csv
    "RIE_F":      ["chf3", "cf4", "sf6", "o2", "n2", "ar"],   # 同 f_rie/steps.csv
    "DRIE_Bosch": ["sf6", "c4f8", "o2"],
}
STEP_GAS_DEFAULT = ["bcl3", "cl2", "chf3", "cf4", "sf6", "o2", "ar", "n2", "hbr"]
STEP_BASE = ["test_id", "step_order", "step_name"]
STEP_MID = ["power_w", "pressure_pa", "time_s"]


def step_columns(entries: list[dict]) -> tuple[list[str], list[str]]:
    """返回 (列名, 气体键)。单一工艺类型用其固有列集(与对应 steps.csv 逐列一致);
    混合导出用并集;步骤里出现的其它键(pass_gas_C4F8 等)追加在 power/pressure/time 之后。"""
    pts = {e.get("process_type") for e in entries}
    if len(pts) == 1:
        gases = STEP_GAS_ORDER.get(next(iter(pts)), STEP_GAS_DEFAULT)
    else:
        gases = STEP_GAS_DEFAULT
    known = set(gases) | set(STEP_MID) | {"step_name", "note"}
    other = sorted({k for e in entries
                    for st in ((e.get("parameters") or {}).get("steps") or [])
                    for k in st if k not in known})
    cols = (STEP_BASE + [f"{g}_sccm" for g in gases] + STEP_MID + other + ["note"])
    return cols, gases

_HDR_FILL = PatternFill("solid", fgColor="F2F2F2")


def test_id_of(entry: dict) -> str:
    """从 source 提取 test_id:
    'cl_rie/steps+data.csv · Cl-Ta-019' → 'Cl-Ta-019'
    '.../DOE_Ta_BBD_执行表.xlsx::Run1'   → 'Run1'
    """
    src = entry.get("source") or ""
    if "::" in src:
        return src.split("::")[-1].strip()
    if "·" in src:
        return src.split("·")[-1].strip()
    return src


def _header_row(ws, cols: list[str]) -> None:
    ws.append(cols)
    for i, _ in enumerate(cols, start=1):
        c = ws.cell(row=1, column=i)
        c.font = Font(bold=True, size=10)
        c.fill = _HDR_FILL
        c.alignment = Alignment(horizontal="center")
        ws.column_dimensions[get_column_letter(i)].width = max(10, min(18, len(cols[i - 1]) + 4))
    ws.freeze_panes = "A2"


def build_workbook(entries: list[dict], project: dict | None = None) -> bytes:
    wb = Workbook()

    # ---- sheet 1: data(一行一个 run,列序对齐 data.csv) ----
    ws = wb.active
    ws.title = "data"
    extra_result_keys = sorted({k for e in entries for k in (e.get("results") or {})
                                if k not in DATA_COLUMNS})
    cols = DATA_COLUMNS + extra_result_keys          # 新增结果列追加在末尾,不丢
    _header_row(ws, cols)
    for e in entries:
        mat = e.get("material") or {}
        res = e.get("results") or {}
        row = []
        for c in cols:
            if c == "test_id":
                row.append(test_id_of(e))
            elif c in res:
                row.append(res[c])
            else:
                row.append(mat.get(c, ""))
        ws.append(row)

    # ---- sheet 2: steps(列序对齐对应工艺的 steps.csv) ----
    step_cols, gases = step_columns(entries)
    ws2 = wb.create_sheet("steps")
    _header_row(ws2, step_cols)
    for e in entries:
        tid = test_id_of(e)
        steps = (e.get("parameters") or {}).get("steps") or []
        for i, st in enumerate(steps, start=1):
            row = {"test_id": tid, "step_order": i,
                   "step_name": st.get("step_name", "")}
            for g in gases:
                row[f"{g}_sccm"] = st.get(g, "")
            for k in ("power_w", "pressure_pa", "time_s"):
                row[k] = st.get(k, "")
            row["note"] = st.get("note", "")
            for k in step_cols:                      # 其它键(DRIE 三步骤等)直接取值
                if k not in row:
                    row[k] = st.get(k, "")
            ws2.append([row[c] for c in step_cols])

    # ---- sheet 3: 中文对照(便于人读) ----
    ws3 = wb.create_sheet("中文对照")
    zh_cols = ["test_id", "工艺类型", "材料", "来源", "可信度"] + \
              [f"{field_meta(k)['label']}{('(' + field_meta(k)['unit'] + ')') if field_meta(k)['unit'] else ''}"
               for k in sorted({k for e in entries for k in (e.get('results') or {})})]
    _header_row(ws3, zh_cols)
    for e in entries:
        res = e.get("results") or {}
        ws3.append([test_id_of(e), e.get("process_type", ""),
                    (e.get("material") or {}).get("material", ""),
                    e.get("source", ""), e.get("reliability_score", "")]
                   + [res.get(k, "") for k in sorted({k for ee in entries
                                                      for k in (ee.get('results') or {})})])

    # ---- sheet 4: 项目画布(可选) ----
    if project:
        ws4 = wb.create_sheet("项目画布")
        _header_row(ws4, ["类型", "名称/字段", "值"])
        ws4.append(["项目", "项目名", project.get("name", "")])
        for m in project.get("modules", []):
            ws4.append(["模块", m.get("name", ""), m.get("equipment_name") or m.get("subtype", "")])
            for k, v in (m.get("params") or {}).items():
                ws4.append(["参数", f"{m.get('name','')} · {k}", v])
            for k, v in (m.get("key_values") or {}).items():
                ws4.append(["输出", f"{m.get('name','')} · {k}", v])
            for k, v in (m.get("material") or {}).items():
                ws4.append(["材料", f"{m.get('name','')} · {k}", v])
        for e in project.get("edges", []):
            ws4.append(["连线", e.get("src", ""), e.get("dst", "")])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()

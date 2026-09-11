"""KB 录入:读实验 CSV(只读)→ 自动生成知识条目(幂等)。

数据源(只读红线,仅读不写):
  个人空间/18_工艺数据资产/03_实验数据/cl_rie/{steps,data}.csv  → RIE_Cl (RIE200NL)
  个人空间/18_工艺数据资产/03_实验数据/f_rie/{steps,data}.csv   → RIE_F  (RIE10NR)

映射规范见 OpenNano/项目文档/知识条目Schema与录入规范_v0.1_20260909.md §四。
"""
from __future__ import annotations

import csv
from pathlib import Path

from .store import KBStore

from opennano_config import DATA_ROOT  # 可在 .env 覆盖(发布用)

# 目录 → (process_type, 设备型号)
SOURCES = {
    "cl_rie": ("RIE_Cl", "RIE200NL"),
    "f_rie": ("RIE_F", "RIE10NR"),
}

MATERIAL_FIELDS = ["material", "substrate", "film_thickness_nm", "resist_type",
                   "resist_thickness_nm", "pattern_type", "mask_cd_nm", "pitch_nm",
                   "mask_batch"]
RESULT_FIELDS = ["er_nm_min", "depth_center_nm", "depth_top_nm", "depth_bottom_nm",
                 "depth_left_nm", "depth_right_nm", "sidewall_angle_deg",
                 "selectivity", "final_cd_nm", "cd_loss_nm", "ler_nm", "lwr_nm"]
STEP_META = ["power_w", "pressure_pa", "time_s"]

# data.csv 的元信息列(非结果):全部原样保留,保证导出能还原成同一张表
CSV_META_FIELDS = ["test_label", "date", "operator", "mask_batch", "material",
                   "substrate", "film_thickness_nm", "dep_method", "dep_time_min",
                   "wafer_size", "resist_type", "resist_thickness_nm",
                   "resist_swa_deg", "pattern_type", "mask_cd_nm", "pitch_nm",
                   "dist_top_mm", "dist_bottom_mm", "dist_left_mm", "dist_right_mm",
                   "sem_path", "notes_path", "note"]



# ---------- 与数据域 core 的边界(协议_数据域_工艺数据库.md §11) ----------
# KB 不得存原始数值副本;此处仅为存量副本打标 + 按 core verification 派生可信度(止血期)。
from opennano_config import CORE_DIR  # noqa: E402


def core_verification_index() -> dict[str, dict]:
    """读 core/measurements.csv → {run_id: {已核实:n, 未核实:n, 存疑:n}}。"""
    p = CORE_DIR / "measurements.csv"
    if not p.exists():
        return {}
    out: dict[str, dict] = {}
    with open(p, newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            rid = (row.get("run_id") or "").strip()
            if not rid:
                continue
            v = (row.get("verification") or "").strip() or "未核实"
            d = out.setdefault(rid, {"已核实": 0, "未核实": 0, "存疑": 0})
            d[v if v in d else "未核实"] += 1
    return out


def derive_reliability(vcount: dict | None) -> tuple[int, str]:
    """按 core verification 派生可信度(已核实→4 / 部分→3 / 未核实→2 / 存疑→1)。

    协议铁律:不替记录升级可信度;无 core 记录时按"未核实"处理(2),绝不给 4。
    """
    if not vcount:
        return 2, "无 core 核实记录(按未核实处理)"
    if vcount.get("存疑"):
        return 1, f"core 含存疑 {vcount['存疑']} 条"
    ok, un = vcount.get("已核实", 0), vcount.get("未核实", 0)
    if ok and ok >= un:
        return 4, f"core 已核实 {ok} / 未核实 {un}"
    if ok:
        return 3, f"core 部分核实 {ok} / 未核实 {un}"
    return 2, f"core 全部未核实({un} 条)"


def mark_raw_copy(entry: dict, run_id: str, vcount: dict | None) -> dict:
    """给"原始数值副本"条目打标 + 派生可信度。"""
    rel, basis = derive_reliability(vcount)
    entry["reliability_score"] = rel
    entry.setdefault("constraints", []).extend([
        {"type": "raw_copy", "value": "数值副本;权威源=core/measurements.csv"},
        {"type": "core_run", "value": run_id},
        {"type": "core_verification", "value": basis},
    ])
    entry.setdefault("tags", []).append("副本")
    return entry


def _f(v):
    """转 float;空/非法返回 None。"""
    if v is None or str(v).strip() == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _read_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:  # utf-8-sig 去 BOM
        return list(csv.DictReader(f))


def load_steps(path: Path) -> dict[str, list[dict]]:
    """steps.csv → {test_id: [step dict 按序]}。气体列去 _sccm 后缀。"""
    out: dict[str, list[dict]] = {}
    for row in _read_csv(path):
        tid = (row.get("test_id") or "").strip()
        if not tid:
            continue
        step: dict = {"step_name": row.get("step_name", "")}
        for k, v in row.items():
            if k and k.endswith("_sccm"):
                val = _f(v)
                if val:
                    step[k[:-5]] = val          # bcl3_sccm → bcl3
        for k in STEP_META:
            val = _f(row.get(k))
            if val is not None:
                step[k] = val
        if row.get("note"):
            step["note"] = row["note"]
        out.setdefault(tid, []).append(step)
    for tid in out:
        out[tid].sort(key=lambda s: len(s))  # 尽量按原序(无 order 列时)
    return out


def load_data(path: Path) -> dict[str, dict]:
    """data.csv → {test_id: row}。"""
    out: dict[str, dict] = {}
    for row in _read_csv(path):
        tid = (row.get("test_id") or "").strip()
        if tid:
            out[tid] = row
    return out


def build_entries(dir_path: Path, process_type: str, equipment_model: str) -> list[dict]:
    steps = load_steps(dir_path / "steps.csv")
    data = load_data(dir_path / "data.csv")
    entries = []
    for tid in sorted(set(steps) | set(data)):
        material = {}
        results = {}
        row = data.get(tid, {})
        # 元信息列全保留(导出可还原同一张表);数值化的转 float,其余留字符串
        for k in CSV_META_FIELDS:
            v = row.get(k)
            if v in (None, ""):
                continue
            fv = _f(v)
            material[k] = fv if fv is not None else v
        # 结果列:优先白名单,外加表里其余数值列(自动收录,不丢列)
        for k, v in row.items():
            if not k or k in CSV_META_FIELDS or k == "test_id":
                continue
            fv = _f(v)
            if fv is not None:
                results[k] = fv
        mat_name = material.get("material", "")
        note = (row.get("note") or "").strip()
        entries.append(mark_raw_copy({
            "process_type": process_type,
            "title": f"{mat_name} 刻蚀 {tid}".strip(),
            "equipment": {"model": equipment_model},
            "material": material,
            "parameters": {"steps": steps.get(tid, [])},
            "results": results,
            "source": f"{dir_path.name}/steps+data.csv · {tid}",
            "reliability_score": 4,
            "constraints": [],
            "tags": [t for t in [mat_name, process_type, row.get("test_label", "")]
                     if t],
            **({"_note": note} if note else {}),
        }, tid, VIDX.get(tid)))
    return entries


def _with_machine(entry: dict, lib=None) -> dict:
    """把 equipment.model 追溯成机台 id(若库里有匹配机台)。"""
    if lib is None:
        return entry
    try:
        model = (entry.get("equipment") or {}).get("model", "")
        m = lib.machine_by_model(model)
        if m:
            entry["equipment"] = {**entry.get("equipment", {}),
                                  "machine_id": m["id"], "machine": m["name"]}
    except Exception:  # noqa: BLE001  # 机台解析失败不影响入库
        pass
    return entry


def ingest_all(kb: KBStore, root: Path = DATA_ROOT, lib=None) -> dict:
    """扫描 SOURCES 目录,幂等录入。返回 {added, updated, total}。lib 用于机台追溯。"""
    load_core_index()          # 可信度按 core verification 派生
    added = updated = 0
    for dirname, (pt, model) in SOURCES.items():
        d = root / dirname
        if not d.exists():
            continue
        for e in build_entries(d, pt, model):
            note = e.pop("_note", "")
            obj, created = kb.upsert(_with_machine(e, lib))
            if created:
                added += 1
            else:
                updated += 1
    # Excel 执行表(结果列填了才算数;空结果行跳过)
    xlsx = ingest_xlsx_dir(kb, lib=lib)
    return {"added": added + xlsx["added"], "updated": updated + xlsx["updated"],
            "xlsx": xlsx, "total": kb.stats()["total"]}


# ---------- Excel 执行表录入(Ta/Si/曝光 DOE,openpyxl 只读) ----------

# 执行表所在目录(只读红线:仅读不写)
from opennano_config import DOE_DIR as XLSX_DIR  # noqa: E402

# core 核实索引(惰性载入;core 未上线/缺文件时为空 dict)
VIDX: dict[str, dict] = {}


def load_core_index() -> dict[str, dict]:
    global VIDX
    VIDX = core_verification_index()
    return VIDX

# 文件名关键词 → (process_type, 设备型号, 步结构: 列→步参数映射)
# 列名匹配执行表表头: Cl2/BCl3/Ar/Power/BT/ME_time_s/ME_pressure_*/结果列
_XLSX_SOURCES = {
    "Ta": ("RIE_Cl", "RIE200NL", "cl"),
    "Si": ("RIE_F", "RIE10NR", "f"),
}

_XLSX_RESULTS = ["速率_nm_min", "选择比", "SWA_grating_", "SWA_square_d",
                 "Δd_target_nm", "Δd_mask_nm", "er_nm_min", "selectivity",
                 "sidewall_angle_deg", "depth_center_nm", "SWA_deg"]


def _f2(v):
    try:
        f = float(v)
        return f
    except (TypeError, ValueError):
        return None


# 结果列名归一:已知量映射到标准英文键(供建模/工具用),未知列保留原名(不丢数据)
RESULT_ALIASES = {
    "速率_nm_min": "er_nm_min", "速率": "er_nm_min", "刻蚀速率": "er_nm_min",
    "选择比": "selectivity", "SWA_grating_": "sidewall_angle_deg",
    "SWA_square_d": "swa_square_deg", "SWA_deg": "sidewall_angle_deg",
    "Δd_target_nm": "depth_target_nm", "Δd_mask_nm": "mask_loss_nm",
    "Δd_nm": "depth_nm", "粗糙度": "roughness_nm", "粗糙度_nm": "roughness_nm",
    "scallop": "scallop", "scallop_nm": "scallop", "扇贝": "scallop",
    "均匀性": "uniformity_pct", "均匀性_%": "uniformity_pct",
    "LER_nm": "ler_nm", "LWR_nm": "lwr_nm", "CD_loss_nm": "cd_loss_nm",
    "final_CD_nm": "final_cd_nm", "CD_top_nm": "cd_top_nm",
    "CD_bot_nm": "cd_bot_nm", "残胶": "resist_residue",
    "掩膜剩余_nm": "mask_remain_nm", "resist_remain_nm": "mask_remain_nm",
}

# 参数/标识列(前缀匹配;其余数值列一律当"结果"自动收录)
PARAM_COL_PREFIXES = (
    "Run", "序号", "BT", "Cl2", "BCl3", "Ar", "CF4", "CF₄", "CHF3", "CHF₃",
    "SF6", "SF₆", "C4F8", "C₄F₈", "O2", "O₂", "N2", "N₂", "HBr",
    "Power", "power", "Source", "Bias", "ME_time", "Time_s", "时间",
    "ME_pressure", "Pressure", "压力", "cycles", "Cycles", "循环",
    "temp", "Temp", "温度", "pass_", "brk_", "etch_", "dose", "Dose",
    "pitch", "Pitch", "线宽", "周期", "note", "Note", "备注", "step",
)


def _norm_result_key(header: str) -> str:
    """表头 → 结果键:已知量用标准英文键,未知列保留原表头(清洗空白/单位括号)。"""
    h = header.strip()
    if h in RESULT_ALIASES:
        return RESULT_ALIASES[h]
    for k, v in RESULT_ALIASES.items():          # 前缀匹配(表头常带 _nm/_deg 尾巴)
        if k and h.startswith(k):
            return v
    return (h.replace("(", "").replace(")", "").replace("/", "_per_")
            .replace("%", "pct").replace(" ", "_").strip("_"))


def xlsx_columns(path: Path) -> dict:
    """预览:识别哪些列是参数、哪些是结果(不写库)。"""
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb[wb.sheetnames[0]]
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    if not rows:
        return {"params": [], "results": [], "rows": 0, "filled_rows": 0}
    header = [str(h).strip() if h is not None else "" for h in rows[0]]
    params = [h for h in header if h and h.startswith(PARAM_COL_PREFIXES)]
    results = [h for h in header if h and not h.startswith(PARAM_COL_PREFIXES)]
    body = rows[1:]
    filled = 0
    idx = [i for i, h in enumerate(header)
           if h and not h.startswith(PARAM_COL_PREFIXES)]
    for r in body:
        if any(_f2(r[i] if i < len(r) else None) is not None for i in idx):
            filled += 1
    return {"params": params, "results": results, "result_keys": {h: _norm_result_key(h) for h in results},
            "rows": len(body), "filled_rows": filled,
            "sheet": ws.title, "sheets": None}


def ingest_xlsx(path: Path, process_type: str, equipment_model: str,
                material: str | None = None,
                source_name: str | None = None) -> list[dict]:
    """单张执行表 → 知识条目列表(只收结果列有值的 run;幂等键=source)。

    结果列 = 除参数列外的**所有数值列**(自动收录,表越详细收录越全),
    已知量归一为标准键(er_nm_min/scallop/roughness_nm…),未知列保留原表头。
    material: 显式指定材料(上传的任意表无法从文件名判断);
    source_name: 溯源名(默认用"目录/文件名",上传时用原文件名)。
    """
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb[wb.sheetnames[0]]
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    header = [str(h).strip() if h is not None else "" for h in rows[0]]

    def col(row, name_prefix: str):
        for i, h in enumerate(header):
            if h.startswith(name_prefix):
                return row[i] if i < len(row) else None
        return None

    # 结果列 = 非参数列
    result_cols = [(i, h) for i, h in enumerate(header)
                   if h and not h.startswith(PARAM_COL_PREFIXES)]

    entries = []
    for r in rows[1:]:
        run_no = col(r, "Run")
        if run_no is None or str(run_no).strip() in ("", "Run"):
            continue
        # 结果列(至少一个有值才收录——数据在收的空行跳过)
        results = {}
        for i, rc in result_cols:
            v = _f2(r[i] if i < len(r) else None)
            if v is not None:
                results[_norm_result_key(rc)] = v
        if not results:
            continue
        # 配方 → 两步结构(BT + ME),与 cl_rie steps.csv 同构
        bt_note = col(r, "BT")
        steps = []
        bt = {"step_name": "BT", "power_w": 80.0, "pressure_pa": 1.0, "time_s": 20.0}
        if bt_note:
            bt["note"] = str(bt_note)
        steps.append(bt)
        me = {"step_name": "ME"}
        for colname, key in [("Cl2", "cl2"), ("BCl3", "bcl3"), ("Ar", "ar"),
                             ("CF4", "cf4"), ("CHF3", "chf3")]:
            v = _f2(col(r, colname))
            if v is not None:
                me[key] = v
        for colname, key in [("Power", "power_w"), ("Power_W", "power_w"),
                             ("ME_time_s", "time_s"), ("Time_s", "time_s"),
                             ("ME_pressure_", "pressure_pa"), ("Pressure_pa", "pressure_pa")]:
            v = _f2(col(r, colname))
            if v is not None and key not in me:
                me[key] = v
        steps.append(me)
        run_id = f"Run{int(_f2(run_no))}" if _f2(run_no) else str(run_no).strip()
        mat = material if material is not None else (
            "Ta" if "Ta" in path.name else ("Si" if "Si" in path.name else ""))
        src = (f"{source_name}::{run_id}" if source_name
               else f"{path.parent.name}/{path.name}::{run_id}")
        entries.append(mark_raw_copy({
            "process_type": process_type,
            "title": f"{mat} DOE {path.stem} {run_id}".strip(),
            "equipment": {"model": equipment_model},
            "material": {"material": mat},
            "parameters": {"steps": steps},
            "results": results,
            "source": src,
            "reliability_score": 4,
            "constraints": [],
            "tags": [t for t in [mat, process_type, "DOE"] if t],
        }, run_id, VIDX.get(run_id)))
    wb.close()
    return entries


def ingest_xlsx_dir(kb: KBStore, directory: Path | None = None, lib=None) -> dict:
    """扫描执行表目录,幂等录入所有已填结果的 run。"""
    d = directory or XLSX_DIR
    added = updated = skipped_files = 0
    if not d.exists():
        return {"added": 0, "updated": 0, "files": 0, "skipped_files": 0}
    files = 0
    for p in sorted(d.glob("*.xlsx")):
        if "~$" in p.name:
            continue
        key = next((k for k in _XLSX_SOURCES if k in p.name), None)
        if not key:
            skipped_files += 1
            continue
        pt, model, _ = _XLSX_SOURCES[key]
        files += 1
        try:
            for e in ingest_xlsx(p, pt, model):
                _, created = kb.upsert(_with_machine(e, lib))
                if created:
                    added += 1
                else:
                    updated += 1
        except Exception:  # noqa: BLE001  # 单文件坏不阻塞
            skipped_files += 1
    return {"added": added, "updated": updated, "files": files,
            "skipped_files": skipped_files}


if __name__ == "__main__":
    kb = KBStore()
    print(ingest_all(kb))
    print(kb.stats())

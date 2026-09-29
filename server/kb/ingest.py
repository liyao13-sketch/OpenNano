"""KB 录入:读实验 CSV(只读)→ 自动生成知识条目(幂等)。

数据源(只读红线,仅读不写):
  个人空间/32_工艺数据资产/03_实验数据/<源目录>/{steps,data}.csv  → process_type + 机台号
  ⚠️ **源目录 ↔ (process_type, 机台号) 的对应表是本地数据**（`~/.opennano/kb_sources.json`，
     可用 `OPENNANO_KB_SOURCES` 覆盖）—— 公开仓库里不写任何机台型号（工单 B2-残C 的 C3/C6）。
     表缺失 ⇒ 机台号留空并**出声**（知识条目的 `equipment` 会少一个溯源字段，但不编造）。

映射规范见 个人空间/33_工艺资料/契约/归档/知识条目Schema与录入规范_v0.1_20260909.md §四。
"""
from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path

from .store import KBStore

from opennano_config import DATA_ROOT  # 可在 .env 覆盖(发布用)

#: 本地映射表（**不进公开仓库**）：`{"cl_rie": ["RIE_Cl", "<机台号>"], ...}`
KB_SOURCES_PATH = Path(os.environ.get("OPENNANO_KB_SOURCES")
                       or (Path.home() / ".opennano" / "kb_sources.json"))


def _load_local_sources() -> dict:
    """读本地「源目录 → (process_type, 机台号)」表；**没有就出声并返回空**（不编造机台号）。"""
    p = Path(os.environ.get("OPENNANO_KB_SOURCES") or KB_SOURCES_PATH)
    if not p.exists():
        print(f"[KB ingester] ⚠️ 未提供本地源目录映射表：{p} ⇒ 条目的 `equipment`（机台号）"
              f"会**留空**；要带上溯源请写一份（格式见 kb/ingest.py 顶部）。", file=sys.stderr)
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        print(f"[KB ingester] ⚠️ 本地源目录映射表读不动：{p}（{type(e).__name__}: {e}）"
              f" ⇒ 本次不写机台号（**不静默改用别的表**）。", file=sys.stderr)
        return {}
    out: dict[str, tuple[str, str]] = {}
    for k, v in (raw or {}).items():
        if isinstance(v, (list, tuple)) and len(v) >= 2:
            out[str(k)] = (str(v[0]), str(v[1] or ""))
    return out


# 目录 → (process_type, 机台号) —— **值来自本地表**，代码里零机台型号
SOURCES: dict[str, tuple[str, str]] = _load_local_sources()

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

# 文件名关键词 → (process_type, 机台号, 步结构: 列→步参数映射)
# 列名匹配执行表表头；**机台号取自本地表**（`_load_local_sources()`，键＝同一源目录名）
_XLSX_SOURCES = {
    "Ta": ("RIE_Cl", (SOURCES.get("cl_rie") or ("", ""))[1], "cl"),
    "Si": ("RIE_F", (SOURCES.get("f_rie") or ("", ""))[1], "f"),
}

#: 执行表结果列的**参考列序**（目前未被引用；保留作列序备忘）。
#: ⚠️ 里面的名字也必须走 `_norm_result_key` 后落 §三 —— 原来含 legacy 名 `sidewall_angle_deg`
#:    与计划列 `Δd_target_nm`，已按 2026-09-28 工单清理（判据会连同本表一起查）。
_XLSX_RESULTS = ["速率_nm_min", "选择比", "SWA_grating_", "SWA_square_d",
                 "Δd_mask_nm", "er_nm_min", "selectivity",
                 "SWA_deg", "depth_center_nm"]


def _f2(v):
    try:
        f = float(v)
        return f
    except (TypeError, ValueError):
        return None


# 结果列名归一:已知量映射到标准英文键(供建模/工具用),未知列保留原名(不丢数据)
RESULT_ALIASES = {
    "速率_nm_min": "er_nm_min", "速率": "er_nm_min", "刻蚀速率": "er_nm_min",
    "选择比": "selectivity", "SWA_grating_": "swa_deg",
    "SWA_square_d": "swa_deg", "SWA_deg": "swa_deg",
    "Δd_nm": "depth_nm", "Δd_mask_nm": "mask_consumed_nm",
    "粗糙度": "roughness_nm", "粗糙度_nm": "roughness_nm",
    "scallop_pitch_nm": "scallop_pitch_nm",
    "scallop_nm": "scallop_nm", "scallop": "scallop_nm", "扇贝": "scallop_nm",
    "均匀性": "nu_pct", "均匀性_%": "nu_pct",
    "LER_nm": "ler_nm", "LWR_nm": "lwr_nm", "CD_loss_nm": "cd_loss_nm",
    "final_CD_nm": "final_cd_nm", "CD_top_nm": "cd_top_nm",
    "CD_bot_nm": "cd_bottom_nm", "CD_mid_nm": "cd_mid_nm",
    "掩膜剩余_nm": "mask_remaining_nm", "resist_remain_nm": "mask_remaining_nm",
    "掩膜消耗_nm": "mask_consumed_nm", "掩膜剩余": "mask_remaining_nm",
    "刻蚀深度_nm": "depth_nm", "过刻_nm": "overetch_nm",
    "侧壁角": "swa_deg", "侧壁开口角": "sidewall_open_deg",
    "粗糙度_Ra_nm": "roughness_nm", "线边缘粗糙度_nm": "ler_nm", "线宽粗糙度_nm": "lwr_nm",
}
# ⚠️ **本表每个「值」都必须是 `schema §三` 的受控量名**（别名只许**照词表**，不许自造）——
#    机器判据见 `unregistered_alias_targets()` 与 `tests/test_ingest_aliases.py`
#    （2026-09-28 工单 `20260928-助手线-to-工具线-01`：数据线用**真执行表列头**核出 5 处，本侧复核实为 **9 处**）。
# ⚠️ `Δd_target_nm`（**计划**列）与 `残胶`（**现象**）**刻意不在本表**：前者不是实测（映射到任何实测量名都违
#    「不把计划当实测」），后者属现象受控词表（`OBS-*`）而非量测 ⇒ 见 `NON_QUANTITY_HEADERS`。

# 参数/标识列(前缀匹配;其余数值列一律当"结果"自动收录)
PARAM_COL_PREFIXES = (
    "Run", "序号", "BT", "Cl2", "BCl3", "Ar", "CF4", "CF₄", "CHF3", "CHF₃",
    "SF6", "SF₆", "C4F8", "C₄F₈", "O2", "O₂", "N2", "N₂", "HBr",
    "Power", "power", "Source", "Bias", "ME_time", "Time_s", "时间",
    "ME_pressure", "Pressure", "压力", "cycles", "Cycles", "循环",
    "temp", "Temp", "温度", "pass_", "brk_", "etch_", "dose", "Dose",
    "pitch", "Pitch", "线宽", "周期", "note", "Note", "备注", "step",
)


#: **不是量测**的表头 ⇒ 不进 `results`（也就不会变成未登记量名）。
#: 三类：① 现象（残胶…属 core `obs_type` 受控词表）② 可信度标注（`可靠性*` ⇒ core §四 `verification`）
#: ③ DOE 设计元数据（`点类型`/`图形`）。⚠️ 这不是"丢数据"：它们本就不属于 `measurements`，
#: 应走 observations / verification / 参数列；本 ingester 目前没有这两个出口 ⇒ **跳过并出声**（见 ingest_xlsx）。
NON_QUANTITY_HEADERS = (
    "残胶", "黑硅", "草状", "侧掏",          # 现象（obs_type）
    "可靠性", "可靠性标注", "可靠性评分",     # 可信度（verification）
    "点类型", "图形",                        # DOE 设计元数据
    "显影质量",                              # 质量判断（非量测）
    "Δd_target_nm", "目标深度_nm",           # **计划/目标**列（实测在别的列；映射到实测量名＝把计划当实测）
)


def _norm_result_key(header: str) -> str:
    """表头 → 结果键:已知量用标准英文键,未知列保留原表头(清洗空白/单位括号)。"""
    h = header.strip()
    if h in NON_QUANTITY_HEADERS or any(h.startswith(k) for k in NON_QUANTITY_HEADERS):
        return ""                                # 非量测 ⇒ 调用方跳过（不是量测就不该进 measurements）
    if h in RESULT_ALIASES:
        return RESULT_ALIASES[h]
    # 前缀匹配(表头常带 _nm/_deg 尾巴)：**长键优先** ——
    # ⚠️ 2026-09-28 实测踩到：按插入序匹配时 `scallop_pitch_nm` 会被更短的 `scallop` 抢先
    #    映成 `scallop_nm`（明明 §三 里就有 `scallop_pitch_nm`）⇒ 错量名。长键优先即修。
    for k in sorted(RESULT_ALIASES, key=len, reverse=True):
        if k and h.startswith(k):
            return RESULT_ALIASES[k]
    return (h.replace("(", "").replace(")", "").replace("/", "_per_")
            .replace("%", "pct").replace(" ", "_").strip("_"))


def alias_targets(aliases: dict | None = None) -> set[str]:
    """别名表的**全部目标量名**（判据用）。"""
    return set((aliases if aliases is not None else RESULT_ALIASES).values())


def unregistered_alias_targets(registered, aliases: dict | None = None) -> list[str]:
    """**判据本体**：别名表里**不在受控量名表内**的目标（空 ＝ 全部已登记）。

    口径（2026-09-28 工单）：别名只许**照词表**，不许自造量名 —— 否则草稿会带未登记量名，
    落 core 前必须人工再映一次（那正是要堵的口子）。
    """
    return sorted(alias_targets(aliases) - set(registered or ()))


def unregistered_reachable_names(registered, aliases: dict | None = None,
                                 extra_names=()) -> list[str]:
    """**更强的判据**：把「可能当结果列出现」的名字（别名键 ＋ `_XLSX_RESULTS`）过一遍归一，
    归一后仍不在受控量名表内的列出来。

    为什么需要它：只查"别名表的值"漏掉**没进别名表**的列名 —— 它们会**原样**（清洗后）落进草稿，
    同样带未登记量名（真表实测：`CD_grat_top_nm` / `占空比` / `Linewidth_nm` …）。
    ⚠️ 归一为空串 ＝ 判为**非量测**（现象/可信度/设计元数据）⇒ 不算违规（它们本就不该进 measurements）。
    """
    reg = set(registered or ())
    names = set((aliases if aliases is not None else RESULT_ALIASES)) | set(_XLSX_RESULTS) | set(extra_names)
    bad = []
    for n in names:
        key = _norm_result_key(n)
        if key and key not in reg:
            bad.append((n, key))
    return [f"{n} → {k}" for n, k in sorted(bad)]


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
    keys = {h: _norm_result_key(h) for h in results}
    return {"params": params, "results": results,
            "result_keys": {h: k for h, k in keys.items() if k},
            #: 非量测列（现象/可信度/设计元数据）——**不会**进 measurements，单列出来以免看着像"丢数据"
            "non_quantity": [h for h, k in keys.items() if not k],
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
    skipped_non_quantity: set = set()
    for r in rows[1:]:
        run_no = col(r, "Run")
        if run_no is None or str(run_no).strip() in ("", "Run"):
            continue
        # 结果列(至少一个有值才收录——数据在收的空行跳过)
        results = {}
        for i, rc in result_cols:
            v = _f2(r[i] if i < len(r) else None)
            if v is None:
                continue
            key = _norm_result_key(rc)
            if not key:
                # 非量测列（现象/可信度/设计元数据）⇒ **不进 measurements**；但要**出声**记账
                skipped_non_quantity.add(rc)
                continue
            results[key] = v
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
    if skipped_non_quantity:
        # ⚠️ 出声（不静默）：这些列**不是**量测，本 ingester 没有 observations/verification 出口
        #    ⇒ 明确告知它们没进 `results`，别让人以为"丢数据"。
        print(f"[KB ingester] {path.name}：跳过 {len(skipped_non_quantity)} 个**非量测**列"
              f"（现象/可信度/设计元数据，不进 measurements）：{sorted(skipped_non_quantity)}",
              file=sys.stderr)
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

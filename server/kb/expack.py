"""实验数据包（Experiment Package）v0.1 · 数据管理体系 ⇄ 画布流程 的双向桥梁。

包 = 一个文件夹（名为 batch_id，遵守「日期是字段不是名字」），内容与数据域 core
逐列一致，另带 manifest.json（包元信息）与 flow.json（画布项目，工具导出时携带）：

    {batch_id}/
      manifest.json       # {format, version, batch_id, created_at, source, purpose, operator…}
      flow.json           # 画布项目(节点/连线/参数/机台);手工采集包可无 → 由 runs 合成
      batches.csv         # core 列,1 行
      runs.csv            # core 列(一次上机一行)
      steps.csv           # core 列(步骤参数进 param_json)
      measurements.csv    # core 列(工具导出=待填模板; 实测包=已填数值)
      observations.csv    # core 列(obs_type 来自现象受控词表)
      artifacts/          # 证据图(SEM 等)
      gds/                # 版图(可选)

方向：
  导出  画布流程 → 包(待填模板) → 现场实验/sem-profiler 标注 → 《数据》会话 build_core 落库
  导入  包(含实测) → 画布流程(节点=run、参数=steps、key_values=measurements、备注=observations)
"""
from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from datetime import datetime
from pathlib import Path

from . import core_source as core
from .result_fields import field_meta

# ---- stage ⇄ 画布模板 ----
STAGE_TO_TEMPLATE: dict[str, tuple[str, str]] = {
    "PECVD": ("deposition", "PECVD"), "EVAP": ("deposition", "E-beam Evap"),
    "SPUT": ("deposition", "Magnetron Sputter"),
    "LDW": ("graphic", "Laser Direct Write"), "EBL": ("graphic", "E-beam Litho"),
    "MA6": ("graphic", "UV Exposure"),
    "ICP": ("etch", "ICP Etch"), "RIE": ("etch", "RIE"),
    "DRIE": ("etch", "DRIE (Bosch)"), "ASH": ("etch", "Plasma Strip"),
    "LIFTOFF": ("wet", "Lift-off"), "DICE": ("packaging", "Dicing"),
    "SEM": ("sem", "扫描电镜（SEM）"), "ELLIP": ("ellip", "椭偏仪"),
    "STRESS": ("stress", "应力仪"), "PROFILE": ("profilo", "台阶仪"),
}
TEMPLATE_TO_STAGE = {tmpl: st for st, (_sub, tmpl) in STAGE_TO_TEMPLATE.items()}

# 工艺大类 → 缺省 stage(模板名匹配不到时)
CATEGORY_TO_STAGE = {"etch": "RIE", "deposition": "PECVD", "graphic": "EBL",
                     "wet": "LIFTOFF", "packaging": "DICE",
                     # 表征类(2026-09-12 补:此前 SEM/椭偏等节点会被静默丢弃)
                     "sem": "SEM", "metro_form": "SEM", "cd_sem": "SEM",
                     "ellip": "ELLIP", "profilo": "PROFILE", "stress": "STRESS"}

# ---- core 量名词 ⇄ 画布接口参数 ----
QUANTITY_TO_PARAM = {
    "depth_center_nm": "刻蚀深度", "depth_nm": "刻蚀深度", "depth_target_nm": "刻蚀深度",
    "final_cd_nm": "硅CD", "selectivity": "选择比", "swa_deg": "侧壁角_光栅",
    "sidewall_angle_deg": "侧壁角_光栅", "film_thickness_nm": "膜厚",
    "stress_mpa": "应力", "mask_cd_nm": "胶CD", "scallop_nm": "scallop",
    "roughness_nm": "粗糙度", "nu_pct": "均匀性", "thickness_range_nm": "深度均匀性",
}
PARAM_TO_QUANTITY = {v: k for k, v in QUANTITY_TO_PARAM.items()}
PARAM_TO_QUANTITY.update({"硅CD": "final_cd_nm", "刻蚀深度": "depth_center_nm",
                          "选择比": "selectivity", "膜厚": "film_thickness_nm"})

# Bosch 三步骤前缀(导出 steps 时拆步)
_STEP_PREFIXES = ("pass_", "brk_", "etch_", "bt_", "me_", "stage")

# 气体令牌(参数键常只写气体名,如 etch_sf6 ↔ 设备模板 etch_gas_SF6)
GAS_TOKENS = ("SF6", "CF4", "C4F8", "CHF3", "O2", "Ar", "N2", "Cl2", "BCl3",
              "CH4", "HBr", "H2", "He", "NF3", "XeF2")


def _sanitize_batch(name: str) -> str:
    s = re.sub(r"[^\w\u4e00-\u9fff-]+", "-", (name or "EXP").strip()).strip("-")
    return s or "EXP"


def _csv_bytes(header: list[str], rows: list[list]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue().encode("utf-8")


def _read_csv(p: Path) -> list[dict]:
    if not p.exists():
        return []
    with open(p, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


# ============================================================
# 导出：画布流程 → 实验数据包(zip)
# ============================================================

def resolve_stage(m: dict, lib=None) -> str:
    """画布模块 → core stage。三级回退：模板名 → 设备模板(经机台) → 工艺大类。

    回退存在的理由：画布的 `equipment_name` 是**画布模板名**，而 TEMPLATE_TO_STAGE
    只收了 16 个；SEM/椭偏/台阶等表征设备常对不上，过去会**静默丢节点**。
    """
    stage = TEMPLATE_TO_STAGE.get(m.get("equipment_name") or "")
    if stage:
        return stage
    eq_name, cat = "", ""
    if lib:
        for mc in lib.machines():
            if mc.get("name") and mc["name"] == m.get("machine_name"):
                eq_name = mc.get("equipment_id") or ""
                break
        for c, eqs in (lib.data.get("equipment") or {}).items():
            for t in eqs:
                if eq_name and t.get("id") == eq_name:
                    cat = c
                    eq_name = t.get("name") or ""
                    break
            if cat:
                break
        if eq_name and eq_name in TEMPLATE_TO_STAGE:
            return TEMPLATE_TO_STAGE[eq_name]
        # 直接拿 equipment_name 撞设备模板名
        if m.get("equipment_name") in TEMPLATE_TO_STAGE:
            return TEMPLATE_TO_STAGE[m["equipment_name"]]
    return CATEGORY_TO_STAGE.get(cat or m.get("subtype") or "", "")


def unmapped_modules(project: dict, lib=None) -> list[dict]:
    """列出无法映射为工步的节点(用于**显式告警**,不再静默丢)。"""
    out = []
    for m in project.get("modules", []):
        if not resolve_stage(m, lib):
            out.append({"name": m.get("name") or "(未命名)",
                        "equipment_name": m.get("equipment_name") or "",
                        "subtype": m.get("subtype") or "",
                        "reason": "设备名/大类不在 stage 映射表内"})
    return out


def extract_rows(project: dict, purpose: str = "", operator: str = "",
                 lib=None) -> tuple[list, list, list, dict, str]:
    """画布项目 → (run_rows, step_rows, meas_rows, stage_counter, batch)。

    抽成独立函数的原因：流程卡(md)与三个 CSV **必须共用同一套 run/step/meas id**，
    否则两处各算一遍必然漂移(卡上写的 run_id 在 runs.csv 里找不到)。
    """
    batch = _sanitize_batch(project.get("name", "EXP"))
    modules = project.get("modules", [])
    now = datetime.now().strftime("%Y-%m-%d")
    run_rows, step_rows, meas_rows = [], [], []
    stage_counter: dict[str, int] = {}
    for m in modules:
        stage = resolve_stage(m, lib)
        if not stage:
            continue
        # ⚠️ 已有 core_run_id 的模块**一律沿用**（续做时工具已算好序号）；
        #    只有全新节点才按 stage 计数分配。否则重导出会把 DRIE-0002 重编号回 0001。
        if not m.get("core_run_id"):
            stage_counter[stage] = stage_counter.get(stage, 0) + 1
            rid = f"{batch}-{stage}-{stage_counter[stage]:04d}"
            m["core_run_id"] = rid
        rid = m["core_run_id"]
        parsed = rid.rsplit("-", 2)
        seq_in_stage = int(parsed[2]) if len(parsed) == 3 and parsed[2].isdigit() else \
            stage_counter.get(stage, 1)
        m.setdefault("core_batch_id", batch)
        m.setdefault("core_stage", stage)
        m.setdefault("core_stage_seq", seq_in_stage)
        tool_id = m.get("machine_name") or ""
        # parent：工具算好的优先，否则取同 batch 上一个 run（导出顺序即执行顺序）
        parent = m.get("core_parent_run_id") or (run_rows[-1][0] if run_rows else "")
        m["core_parent_run_id"] = parent
        run_rows.append([rid, batch, m.get("core_sample_id") or "", stage,
                         m.get("core_stage_seq", seq_in_stage), now,
                         "", "", m.get("equipment_name") or stage, tool_id,
                         m.get("core_recipe_id") or "", operator or "", purpose or "",
                         parent, "", "", "planned",
                         m.get("comment") or ""])
        menu_steps = m.get("core_menu_steps") or []      # 菜单直读灌入的步**优先**（含机台槽位号）
        if menu_steps:
            for s in menu_steps:
                pj = dict(s.get("param_json") or {})
                # 只有菜单步有机台槽位号（非菜单步的 param_json 里没有这个键）
                mslot = pj.pop("machine_step", "") or s.get("machine_step", "")
                pj.pop("phase", None)
                step_rows.append([f"{rid}.S{s['step_order']:02d}", rid, s["step_order"], mslot,
                                  s.get("step_name", ""), s.get("role", ""),
                                  round(float(s.get("duration_s") or 0), 3) or "",
                                  (pj.get("apc1_press") or ""), "Pa",
                                  json.dumps(pj, ensure_ascii=False), ""])
            # 菜单步已覆盖，跳过 params 生成
        for si, (sname, pv) in enumerate([] if menu_steps else group_params(m.get("params") or {}).items(), start=1):
            dur = next((vv for kk, vv in pv.items()
                        if kk.endswith(("time_s", "duration_s"))), "")
            press = next((vv for kk, vv in pv.items() if "pressure" in kk), "")
            pj = {k: v for k, v in pv.items()
                  if not k.endswith(("time_s", "duration_s")) and "pressure" not in k}
            # 非菜单步**没有**机台槽位号 ⇒ 该列留空（step_name 仍记组名）
            step_rows.append([f"{rid}.S{si:02d}", rid, si, "", sname, "",
                              dur, press, "", json.dumps(pj, ensure_ascii=False), ""])
        for out in (m.get("param_outputs") or []):
            q = PARAM_TO_QUANTITY.get(out, out)
            meta = field_meta(q)
            n = len([r for r in meas_rows if r[0].startswith(rid)]) + 1
            meas_rows.append([f"{rid}.M{n:02d}", rid, "", q, "", meta.get("unit", ""),
                              "", "", "", "", "", "", "", ""])
    return run_rows, step_rows, meas_rows, stage_counter, batch


def group_params(params: dict) -> dict[str, dict]:
    """按已知步骤前缀把参数拆成 {步骤名: {参数: 值}}(与 steps.csv 同一口径)。"""
    groups: dict[str, dict] = {}
    for k, v in (params or {}).items():
        pre = next((p for p in _STEP_PREFIXES if k.startswith(p)), None)
        if pre:
            groups.setdefault(pre[:-1] if pre.endswith("_") else pre, {})[k] = v
        else:
            groups.setdefault("main", {})[k] = v
    return groups


def _lib_templates(lib) -> list[tuple[str, dict]]:
    out = []
    for _cat, eqs in ((lib.data.get("equipment") or {}) if lib else {}).items():
        for t in eqs:
            out.append((t.get("name", ""), t.get("params") or {}))
    return out


def param_meta(key: str, lib, equipment_name: str = "") -> dict:
    """参数键 → {label, unit}。

    画布参数键与设备模板键常不同名（`etch_sf6` vs `etch_gas_SF6`、`rf_power` vs
    `etch_bias_power`），故依次尝试：精确 → 去步骤前缀 → 气体令牌 → 尽力匹配。
    """
    if not key:
        return {"label": key, "unit": "", "known": False}
    kl = key.lower()
    parts = key.split("_", 1)
    core = parts[1].lower() if len(parts) == 2 and parts[0] + "_" in _STEP_PREFIXES else kl
    variants = [kl]
    if core != kl:
        variants.append(core)
    if kl.endswith("_sccm"):
        variants.append(kl[:-5])
    if core.endswith("_sccm"):
        variants.append(core[:-5])
    gas = next((g.lower() for g in GAS_TOKENS if g.lower() == core.split("_")[0]), None)

    templates = _lib_templates(lib)
    ordered = [p for n, p in templates if n == equipment_name] + \
              [p for n, p in templates if n != equipment_name]
    for tpl in ordered:
        for tkey, tdef in tpl.items():
            if not isinstance(tdef, dict):
                continue
            tl = tkey.lower()
            hit = False
            if tl in variants:
                hit = True
            elif gas and re.search(rf"(^|_){re.escape(gas)}($|_)", tl):
                hit = True          # 同一种气体的流量参数
            if hit:
                return {"label": tdef.get("label") or key, "unit": tdef.get("unit") or "",
                        "known": True}
    return {"label": key, "unit": "", "known": False}


def _fmt_num(v) -> str:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return str(v)


def _unpack_step(s) -> tuple:
    """steps 表一行 → 规范 11 元组 (step_id, run_id, step_order, machine_step,
    step_name, role, duration_s, pressure, pressure_unit, param_json, note)。

    兼容 v0.1.2 的 10 列（无 `machine_step`）—— 老包/老调用不会崩。
    """
    if len(s) >= 11:
        return tuple(s[:11])
    sid, rid, order, sname, role, dur, press, pu, pj, note = (list(s) + [""] * 10)[:10]
    return (sid, rid, order, "", sname, role, dur, press, pu, pj, note)


def build_process_card(project: dict, purpose: str = "", operator: str = "",
                       lib=None) -> str:
    """画布项目 → 人读「实验流程卡」Markdown(上机对照/交接用)。

    与 build_expack 共用 extract_rows ⇒ run_id / step_id / meas_id 与 CSV 逐字一致。
    内容口径：**计划 + 待填占位**(不掺实测值,实测以 core CSV 为准)。
    """
    run_rows, step_rows, meas_rows, stage_counter, batch = extract_rows(
        project, purpose, operator, lib)
    modules = [m for m in project.get("modules", []) if m.get("core_run_id")]
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    L: list[str] = []
    L.append(f"# {batch} · 实验流程卡")
    L.append("")
    L.append("> 由 **OpenNano 画布**导出（一次性快照）。本卡为**人读版**；"
             "数据以同包 core CSV 为准（CSV 权威）。")
    L.append("")
    L.append("| 项 | 值 |")
    L.append("|---|---|")
    L.append(f"| 批次 | `{batch}` |")
    L.append(f"| 实验目的 | {purpose or '—'} |")
    L.append(f"| 操作人 | {operator or '—'} |")
    L.append(f"| 导出时间 | {now} |")
    L.append(f"| 工步数 / 计划测量 | {len(run_rows)} / {len(meas_rows)} |")
    L.append(f"| 项目 | {project.get('name') or batch} |")
    L.append("")

    # 设备链（含未映射节点显式告警，不静默丢）
    chain = " → ".join(
        (f"**{m.get('name') or m.get('equipment_name')}**"
         f"（{m.get('equipment_name') or '—'}"
         + (f" @ {m.get('machine_name')}" if m.get("machine_name") else "") + "）")
        for m in modules)
    L.append("## 设备链（按序执行）")
    L.append("")
    L.append(chain or "（画布上没有可映射为工步的节点）")
    L.append("")
    unmapped = unmapped_modules(project, lib)
    if unmapped:
        L.append(f"> ⚠️ **有 {len(unmapped)} 个节点未能映射为工步，未进入本卡与 CSV**——"
                 "请补 stage 映射或改设备名后重新导出：")
        L.append("")
        for u in unmapped:
            L.append(f"> - `{u['name']}`（equipment={u['equipment_name'] or '—'} / "
                     f"subtype={u['subtype'] or '—'}）：{u['reason']}")
        L.append("")

    # 逐工步
    L.append("## 步骤明细")
    L.append("")
    step_by_run: dict[str, list] = {}
    for s in step_rows:
        step_by_run.setdefault(s[1], []).append(s)
    meas_by_run: dict[str, list] = {}
    for r in meas_rows:
        meas_by_run.setdefault(r[1], []).append(r)
    for i, m in enumerate(modules, start=1):
        rid = m["core_run_id"]
        L.append(f"### {i}. `{rid}` · {m.get('name') or ''}")
        L.append("")
        L.append(f"- 设备：{m.get('equipment_name') or '—'}"
                 + (f" ｜ 机台：{m.get('machine_name')}" if m.get("machine_name") else ""))
        if m.get("note"):
            L.append(f"- 备注：{m['note']}")
        L.append("")
        steps = step_by_run.get(rid, [])
        if steps:
            def _all_params(s):
                """一条 step 行的全部参数(含 CSV 独立列 duration/pressure),返回 (步序, [(键,值,单位)])。"""
                (sid, _rid, order, mslot, sname, _role, dur, press, _pu,
                 pj, _note) = _unpack_step(s)
                triples = [(k, v, "") for k, v in json.loads(pj or "{}").items()]
                if dur not in ("", None):
                    triples.append(("duration_s", dur, "s"))
                if press not in ("", None):
                    triples.append(("pressure", press, _pu or "Pa"))
                return order, triples, mslot

            eq_name = m.get("equipment_name") or ""
            labels_known = any(
                param_meta(k, lib, eq_name).get("known")
                for s in steps for _o, triples, _m in [_all_params(s)] for k, _v, _u in triples)
            L.append("| 步 | 机台槽 | 参数 | 值 | 单位 |" if labels_known
                     else "| 步 | 机台槽 | 参数 | 值 |")
            L.append("|---|---|---|---|---|" if labels_known else "|---|---|---|---|")
            for s in steps:
                order, triples, mslot = _all_params(s)
                first = True
                for k, v, unit in triples:
                    meta = param_meta(k, lib, eq_name)
                    if not meta.get("known") and "_" in k:      # 设备名对不上模板时剥前缀再试
                        meta2 = param_meta(k.split("_", 1)[1], lib, eq_name)
                        if meta2.get("known"):
                            meta = meta2
                    label = meta["label"] if meta.get("known") else f"`{k}`"
                    cells = [f"S{order:02d}" if first else "", (f"{mslot}" if first else ""),
                             label, _fmt_num(v)]
                    if labels_known:
                        cells.append(unit or meta["unit"])
                    L.append("| " + " | ".join(cells) + " |")
                    first = False
                if not triples:
                    L.append(f"| S{order:02d} | （无参数） | " + (" | " if labels_known else "") + "|")
            L.append("")
        ms = meas_by_run.get(rid, [])
        if ms:
            L.append("**待填测量**（填回 `measurements.csv`）：")
            L.append("")
            L.append("| meas_id | 量名词 | 单位 | 值 | 备注 |")
            L.append("|---|---|---|---|---|")
            for r in ms:
                L.append(f"| `{r[0]}` | {r[3]} | {r[5]} | ☐ | |")
            L.append("")

    # 通用规范与记录位
    L.append("## 上机前检查 / 记录")
    L.append("")
    L.append("- ☐ 样品编号与数量核对：")
    L.append("- ☐ 腔体状态确认（上次工艺、清洗/dummy 是否已做）：")
    L.append("- ☐ 参数与 `runs.csv`/`steps.csv` 核对一致：")
    L.append("- ☐ 现象记录（填入 `observations.csv`：obs_type 取自现象受控词表）：")
    L.append("- ☐ SEM/测量图放 `artifacts/`，版图放 `gds/`：")
    L.append("")

    L.append("## 本包文件说明")
    L.append("")
    L.append("| 文件 | 用途 |")
    L.append("|---|---|")
    L.append("| `流程_%s.md` | **本卡**：人读流程快照 |" % batch)
    L.append("| `manifest.json` | 包元信息（批次/目的/操作人/统计） |")
    L.append("| `flow.json` | 画布原始 JSON（可导回画布） |")
    L.append("| `batches.csv` · `runs.csv` · `steps.csv` | core 列：批次 / 工步 / 步骤参数 |")
    L.append("| `measurements.csv` | **待填**实测值（量名词与单位已给） |")
    L.append("| `observations.csv` | **待填**现象（受控词表取值） |")
    L.append("| `artifacts/` · `gds/` | 证据图 / 版图 |")
    L.append("")
    L.append("---")
    L.append("")
    L.append("*权限与改动口径：本卡由画布生成、**只读参考**；要改流程请改画布并重新导出"
             "（卡不会回写画布）。现场只允许在 `measurements.csv`/`observations.csv` 填数，"
             "**不要改本卡与 steps.csv 的参数**——core CSV 才是权威源。*")
    return "\n".join(L) + "\n"


def build_expack(project: dict, purpose: str = "", operator: str = "",
                 lib=None) -> tuple[bytes, str]:
    """画布项目 → (zip 字节, 文件夹名=BatchID)。生成待填模板(measurements 留空)。

    包内除 core 列 CSV 外还含 **`流程_<批次>.md`**(人读流程卡,见 build_process_card)。
    """
    run_rows, step_rows, meas_rows, stage_counter, batch = extract_rows(
        project, purpose, operator)
    now = datetime.now().strftime("%Y-%m-%d")

    files: dict[str, bytes] = {
        "manifest.json": json.dumps({
            "format": "opennano-expack", "version": "0.1", "batch_id": batch,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "source": "canvas", "project": project.get("name", ""),
            "purpose": purpose, "operator": operator,
            "runs": len(run_rows), "planned_measurements": len(meas_rows),
            "unmapped_nodes": unmapped_modules(project, lib),   # 非空 = 有节点没进包,须处理
        }, ensure_ascii=False, indent=2).encode(),
        "flow.json": json.dumps(project, ensure_ascii=False, indent=2).encode(),
        "batches.csv": _csv_bytes(
            ["batch_id", "series", "title", "owner", "purpose", "wafer_size",
             "substrate_json", "planned_stages", "started_on", "status", "note"],
            [[batch, "", project.get("name", batch), "", purpose, "", "",
              "·".join(stage_counter), now, "planned", "由 OpenNano 画布导出"]]),
        "runs.csv": _csv_bytes(
            ["run_id", "batch_id", "sample_id", "stage", "stage_seq", "date",
             "t_start", "t_end", "tool", "tool_id", "recipe_id", "operator",
             "purpose", "parent_run_id", "env_temp_c", "env_rh_pct", "status", "note"],
            run_rows),
        "steps.csv": _csv_bytes(
            ["step_id", "run_id", "step_order", "machine_step", "step_name", "role",
             "duration_s", "pressure", "pressure_unit", "param_json", "note"], step_rows),
        "measurements.csv": _csv_bytes(
            ["meas_id", "run_id", "sample_id", "quantity", "value", "unit",
             "method", "loc", "n", "uncertainty", "source_artifact_id",
             "measured_by", "verification", "note"], meas_rows),
        "observations.csv": _csv_bytes(
            ["obs_id", "run_id", "sample_id", "obs_type", "severity",
             "description", "judgement", "action", "artifact_id",
             "recorded_by", "date"], []),
        # 人读流程卡(与上面 CSV 共用同一套 id;不掺实测值)
        f"流程_{batch}.md": build_process_card(
            project, purpose=purpose, operator=operator, lib=lib).encode(),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(f"{batch}/{name}", data)
        for d in ("artifacts", "gds"):
            z.writestr(f"{batch}/{d}/.gitkeep", "")
    return buf.getvalue(), batch


# ============================================================
# 导入：实验数据包 → 画布项目
# ============================================================

def _match_machine(tool_id: str, machines: list[dict]) -> dict | None:
    if not tool_id:
        return None
    t = tool_id.strip().lower()
    tok = {x for x in re.split(r"[^a-z0-9\u4e00-\u9fff]+", t) if x}
    best, score = None, 0
    for m in machines:
        cand = " ".join(str(m.get(k) or "") for k in
                        ("tool_id", "name", "model", "serial")).lower()
        if t == cand.strip() or t in cand:
            return m
        ctok = {x for x in re.split(r"[^a-z0-9\u4e00-\u9fff]+", cand) if x}
        s = len(tok & ctok)
        if s > score:
            best, score = m, s
    return best


def parse_expack(path: Path, lib) -> dict:
    """包路径(文件夹或 zip) → 画布项目 dict {name, modules, edges}。

    有 flow.json → 用它(保布局/连线),并把 measurements/observations 叠加到对应节点;
    无 flow.json(手工采集包) → 由 runs/steps 合成节点,按时序连线。
    """
    import tempfile
    if path.suffix.lower() == ".zip":
        tmp = Path(tempfile.mkdtemp(prefix="expack_"))
        with zipfile.ZipFile(path) as z:
            z.extractall(tmp)
        root = next((d for d in tmp.iterdir() if d.is_dir()), tmp)
    else:
        root = path
    manifest = {}
    mf = root / "manifest.json"
    if mf.exists():
        manifest = json.loads(mf.read_text(encoding="utf-8"))
    batch = manifest.get("batch_id") or root.name

    runs = _read_csv(root / "runs.csv")
    steps = _read_csv(root / "steps.csv")
    meas = _read_csv(root / "measurements.csv")
    obs = _read_csv(root / "observations.csv")

    def _steps_of(rid: str) -> list[dict]:
        return [s for s in steps if s.get("run_id") == rid]

    def _meas_of(rid: str) -> dict:
        out = {}
        for m in meas:
            if m.get("run_id") != rid or m.get("value") in ("", None):
                continue
            try:
                v = float(m["value"])
            except (TypeError, ValueError):
                continue
            q = m.get("quantity") or ""
            out[QUANTITY_TO_PARAM.get(q, q)] = v
        return out

    def _obs_of(rid: str) -> str:
        lines = []
        for o in obs:
            if o.get("run_id") != rid:
                continue
            lines.append(f"【{o.get('obs_type','现象')}·{o.get('severity','')}】"
                         f"{o.get('description','')}"
                         + (f"｜判断:{o.get('judgement')}" if o.get("judgement") else "")
                         + (f"｜对策:{o.get('action')}" if o.get("action") else ""))
        return "\n".join(lines)

    from engine.module_factory import build_module
    machines = lib.machines() if lib else []

    def _equipment_id(tmpl_name: str) -> str:
        if not lib:
            return ""
        for cat, eqs in (lib.data.get("equipment") or {}).items():
            for e in eqs:
                if e.get("name") == tmpl_name:
                    return e.get("id", "")
        return ""

    def _module_from_run(r: dict, idx: int) -> dict:
        stage = r.get("stage") or ""
        sub, tmpl = STAGE_TO_TEMPLATE.get(stage, (None, None))
        m = build_module(sub or "assist", lib, name=r.get("tool") or stage) if sub \
            else build_module(stage, lib)
        if sub and tmpl:
            eid = _equipment_id(tmpl)
            if eid:
                m["equipment_id"], m["equipment_name"] = eid, tmpl
        mc = _match_machine(r.get("tool_id") or "", machines)
        if mc:
            m["machine_id"], m["machine_name"] = mc["id"], mc.get("name", "")
        params = {}
        for st in _steps_of(r.get("run_id", "")):
            try:
                pj = json.loads(st.get("param_json") or "{}")
            except Exception:  # noqa: BLE001
                pj = {}
            pre = (st.get("step_name") or "").strip().lower().replace(" ", "_")
            for k, v in pj.items():
                params[f"{pre}_{k}" if pre and not k.startswith(pre) else k] = v
            for k in ("duration_s", "pressure"):
                if st.get(k):
                    params[f"{pre}_{k}" if pre else k] = st[k]
        m["params"] = params
        kv = _meas_of(r.get("run_id", ""))
        m["key_values"] = kv
        m["run_state"] = "ok" if kv else "idle"
        m["core_run_id"] = r.get("run_id", "")
        m["core_batch_id"] = r.get("batch_id", batch)
        # 把样品/die 与配方带进画布（往返不丢；老包该列为空 ⇒ 留空，不推断）
        if r.get("sample_id"):
            m["core_sample_id"] = r["sample_id"]
        if r.get("recipe_id"):
            m["core_recipe_id"] = r["recipe_id"]
        if r.get("stage_seq"):
            m["core_stage_seq"] = r["stage_seq"]
        if r.get("date"):
            m["core_date"] = r["date"]
        if r.get("parent_run_id"):
            m["core_parent_run_id"] = r["parent_run_id"]
        oc = _obs_of(r.get("run_id", ""))
        if oc:
            m["comment"] = oc
        m["x"], m["y"] = 120 + (idx % 2) * 40, 60 + idx * 130
        return m

    # ① flow.json 存在 → 保布局,叠加实测/现象
    fj = root / "flow.json"
    if fj.exists():
        proj = json.loads(fj.read_text(encoding="utf-8"))
        for m in proj.get("modules", []):
            rid = m.get("core_run_id") or ""
            if not rid:
                continue
            kv = _meas_of(rid)
            if kv:
                m["key_values"] = {**(m.get("key_values") or {}), **kv}
                m["run_state"] = "ok"
            oc = _obs_of(rid)
            if oc:
                m["comment"] = ((m.get("comment") + "\n") if m.get("comment") else "") + oc
        return proj

    # ② 无 flow.json → 由 runs 合成
    runs_sorted = sorted(runs, key=lambda r: (r.get("date") or "",
                                              int(r.get("stage_seq") or 0),
                                              r.get("run_id") or ""))
    modules = [_module_from_run(r, i) for i, r in enumerate(runs_sorted)]
    id_by_run = {r.get("run_id"): m["id"] for r, m in zip(runs_sorted, modules)}
    edges = []
    for i, r in enumerate(runs_sorted):
        src = id_by_run.get(r.get("parent_run_id") or "")
        if not src and i > 0:
            src = modules[i - 1]["id"]
        dst = id_by_run.get(r.get("run_id", ""))
        if src and dst and src != dst:
            e = {"src": src, "dst": dst}
            if e not in edges:
                edges.append(e)
    return {"name": batch, "modules": modules, "edges": edges}


# ============================================================
# core 全量导出(xlsx)：9 表 + 量名词 + 录入模板
# ============================================================

def core_workbook(project: dict | None = None) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    wb = Workbook()
    hdr_fill = PatternFill("solid", fgColor="F2F2F2")
    tables = ["batches", "samples", "runs", "recipes", "steps",
              "measurements", "observations", "artifacts", "eq_state"]
    first = True
    for t in tables:
        rows = core._rows(t)
        if not rows:
            continue
        ws = wb.active if first else wb.create_sheet(t)
        if first:
            ws.title = t
            first = False
        cols = list(rows[0].keys())
        ws.append(cols)
        for i in range(len(cols)):
            c = ws.cell(row=1, column=i + 1)
            c.font = Font(bold=True, size=10)
            c.fill = hdr_fill
        for r in rows:
            ws.append([r.get(c, "") for c in cols])
        ws.freeze_panes = "A2"
    ws = wb.create_sheet("量名词")
    ws.append(["quantity", "unit", "n"])
    for q in core.quantities():
        ws.append([q["quantity"], q["unit"], q["n"]])
    if project:
        ws = wb.create_sheet("项目画布")
        ws.append(["类型", "名称/字段", "值"])
        ws.append(["项目", "项目名", project.get("name", "")])
        for m in project.get("modules", []):
            ws.append(["模块", m.get("name", ""), m.get("equipment_name") or m.get("subtype", "")])
            for k, v in (m.get("params") or {}).items():
                ws.append(["参数", f"{m.get('name','')} · {k}", v])
            for k, v in (m.get("key_values") or {}).items():
                ws.append(["输出", f"{m.get('name','')} · {k}", v])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()

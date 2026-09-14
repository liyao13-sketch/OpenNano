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
import shutil
import stat
import zipfile
from datetime import datetime
from pathlib import Path

from . import core_source as core
from .core_vocab import TOOL_ID_SENTINEL, resolve_tool
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
    # 2026-09-14 补 12（数据线协议 §15.4）：模板名照抄 engine/process_catalog.py 的 METROLOGY 表，
    # **不在这里另造名字**（名字的唯一真相在那边，改了要对齐）
    "TEM": ("tem", "透射电镜（TEM）"), "AFM": ("afm", "原子力显微镜（AFM）"),
    "OM": ("om", "光学显微镜"), "FLUOR": ("fluor", "荧光检测"),
    "XRD": ("xrd", "X射线衍射（XRD）"), "XPS": ("xps", "X射线光电子能谱（XPS）"),
    "AES": ("aes", "俄歇能谱（AES）"), "SIMS": ("sims", "二次离子质谱（SIMS）"),
    "FOURPP": ("fourpp", "四探针"), "HALL": ("hall", "霍尔测试（Hall）"),
    "CV": ("cv", "电容-电压（C-V）"), "IR": ("ir", "红外热成像"),
}
TEMPLATE_TO_STAGE = {tmpl: st for st, (_sub, tmpl) in STAGE_TO_TEMPLATE.items()}

#: 全部 stage 代号（= core_schema.STAGES 的 28 个；跨线逐字判据钉住）——
#: 用途只有一个：**拦住"把 stage 名当机台号"写进 `tool_id`**（数据线机台闸 ② 类错误）。
STAGE_CODES = frozenset(STAGE_TO_TEMPLATE)

# 工艺大类 → 缺省 stage(模板名匹配不到时)
CATEGORY_TO_STAGE = {"etch": "RIE", "deposition": "PECVD", "graphic": "EBL",
                     "wet": "LIFTOFF", "packaging": "DICE",
                     # 表征类(2026-09-12 补:此前 SEM/椭偏等节点会被静默丢弃)
                     "sem": "SEM", "metro_form": "SEM", "cd_sem": "SEM",
                     "ellip": "ELLIP", "profilo": "PROFILE", "stress": "STRESS",
                     # 2026-09-14 补 12（协议 §15.4：16 种表征器械 1:1 都有代号，不再有节点进不了包）
                     "tem": "TEM", "afm": "AFM", "om": "OM", "fluor": "FLUOR",
                     "xrd": "XRD", "xps": "XPS", "aes": "AES", "sims": "SIMS",
                     "fourpp": "FOURPP", "hall": "HALL", "cv": "CV", "ir": "IR"}

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


#: 表征类 stage —— 画布上"检测"的身份判据只有这一处（契约 §三 第四层 + 数据线协议 §15.4）。
#: 2026-09-14 由 4 个扩到 **16 个**（一台仪器一个代号，与画布 `process_catalog.METROLOGY` 的 16 种 1:1）：
#: 形貌 TEM/AFM/OM/FLUOR · 成分结构 XRD/XPS/AES/SIMS · 光学厚度 ELLIP/PROFILE ·
#: 力学 STRESS · 电学 FOURPP/HALL/CV · 热学 IR。
#: ⚠️ `FOURPP` 不是 `4PP`：run_id 的 stage 段保持纯字母，不给下游解析留特例（数据线 §15.4 的选择）。
#: ⚠️ 词表两侧必须**同批落地**：本文件改了映射，数据线那边 `core_schema.STAGES` / `schema §4` / 协议 §4
#:    也要同批加这 12 个（否则 `build_core` 的写前硬闸会拒收 —— 那是**可见失败**，不是静默污染）。
METROLOGY_STAGES = ("SEM", "ELLIP", "PROFILE", "STRESS",
                    "TEM", "AFM", "OM", "FLUOR",
                    "XRD", "XPS", "AES", "SIMS",
                    "FOURPP", "HALL", "CV", "IR")


def is_metrology_stage(stage: str) -> bool:
    return (stage or "").upper() in METROLOGY_STAGES


def stage_from_run_id(rid: str) -> str:
    """从 run_id 里取 stage 段（`AR50-T2-SEM-0001` → `SEM`）。

    为什么不直接读 `runs[*]["stage"]`：**计划节点**（还没入库的新节点）在 `relayout` 里是
    由模块**合成**出来的 run dict，只有 `run_id/stage_seq/parent_run_id`、**没有 `stage` 字段** ——
    按字段判就会漏掉正好要修的那一类节点（2026-09-13 metrology B+ 踩过）。
    """
    parts = (rid or "").rsplit("-", 2)
    return parts[-2] if len(parts) == 3 else ""


def is_metrology(m: dict, lib=None) -> bool:
    """该模块是不是**检测节点**（表征设备）。检测节点有三条特殊待遇（见各调用处）：
    父＝被测的那条 run、排在被测 run 右侧一列、**不给 `run{N}` 徽标**（"本工序第几次"对检测无意义）。"""
    return is_metrology_stage(resolve_stage(m, lib))


def _link_parents(project: dict, rid_by_mid: dict) -> dict:
    """画布连线 → `{下游模块id: 上游 run_id}`（契约 §37：parent_run_id / 时序 = 连线）。

    只认"上游模块**有 run id**"的边（没映射成工步的节点给不出 run，不参与）；
    同一个下游有多条入边时**记录边优先**（`_link != inferred`），并列取先出现的那条 ——
    不推断：只把用户画的那条线翻译成 core 的列。
    """
    out: dict[str, str] = {}
    rank: dict[str, int] = {}
    for e in (project.get("edges") or []):
        src = rid_by_mid.get(e.get("src") or "")
        dst = e.get("dst") or ""
        if not src or not dst:
            continue
        score = 0 if (e.get("_link") == "inferred") else 1
        if dst not in out or score > rank.get(dst, -1):
            out[dst], rank[dst] = src, score
    return out


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
    machines = lib.machines() if lib else []       # 机台口径解析用（画布选的机台 → core tool_id）
    now = datetime.now().strftime("%Y-%m-%d")
    run_rows, step_rows, meas_rows = [], [], []
    stage_counter: dict[str, int] = {}

    # ── ⓪ 预扫描：**先给所有模块定下 run id**，才能按连线算出"谁是父" ──
    #    为什么必须先行：父要走**连线**（契约 §37「parent_run_id / 时序 = 连线」），
    #    而连线另一端的 run id 得先存在。原地一趟循环时后面的节点还没有 id，只能退化成
    #    "按导出顺序接上一条"——那是个猜测，对并存试验/检测节点都会编错归属。
    #    ⚠️ 计数语义与原来**逐字一致**（按模块顺序、按 stage 各自计数），只是提前算。
    was_in_core: dict[int, bool] = {}          # 以**对象 id** 为键：判断"这节点原本在不在 core"
    rid_by_mid: dict[str, str] = {}
    for m in modules:
        stage = resolve_stage(m, lib)
        if not stage:
            continue
        was_in_core[id(m)] = bool(m.get("core_run_id"))
        # ⚠️ 已有 core_run_id 的模块**一律沿用**（续做时工具已算好序号）；
        #    只有全新节点才按 stage 计数分配。否则重导出会把 DRIE-0002 重编号回 0001。
        if not m.get("core_run_id"):
            stage_counter[stage] = stage_counter.get(stage, 0) + 1
            m["core_run_id"] = f"{batch}-{stage}-{stage_counter[stage]:04d}"
        rid_by_mid[m.get("id") or ""] = m["core_run_id"]
    link_parent = _link_parents(project, rid_by_mid)

    for m in modules:
        stage = resolve_stage(m, lib)
        if not stage:
            continue
        rid = m["core_run_id"]
        parsed = rid.rsplit("-", 2)
        seq_in_stage = int(parsed[2]) if len(parsed) == 3 and parsed[2].isdigit() else \
            stage_counter.get(stage, 1)
        m.setdefault("core_batch_id", batch)
        m.setdefault("core_stage", stage)
        m.setdefault("core_stage_seq", seq_in_stage)
        # 机台口径：**只从这里出**（2026-09-14）。过去的 `tool_id = m.get("machine_name") or ""` 写的是
        # 应用库的**显示名**（`DRIE-Bosch` / `PECVD` / `ICP-鲁汶`）—— 其中 `PECVD` 正好是 stage 名
        # （撞数据线机台闸 ②），其余看着合法却是**错的机台号**（`RIE-400iPB` / `ICP-PishowA` 才是真值），
        # 会静默入库把归属记错。解析顺序与理由见 `kb/core_vocab.resolve_tool`。
        tool_id, tool_name = resolve_tool(m, machines, STAGE_CODES)
        # parent：**core 的语义优先，空就是空**。
        # ⚠️ 原实现是 `m.get("core_parent_run_id") or run_rows[-1][0]`（"导出顺序即执行顺序"）——
        #    这在"一个 batch 一次导出"的旧假设下勉强成立，但对**并存试验**（如 AR50-T1 的 6 条
        #    ICP，core 里 parent 为空）会编出一条假直线，且会被持久化 ⇒ 界面上看着像
        #    "同一片刻了 8 次"（2026-09-13 owner实测）。呼应零号铁律：**不推断、不替记录编归属**。
        # ⚠️ 2026-09-13（metrology B+）再补一层：**从来不在 core 里的新节点**，父取**画布连线**
        #    （契约 §37「parent_run_id / 时序 = 连线」）—— 这才是检测节点"说得出我在测谁"的来源，
        #    也是把老的"按导出顺序接上一条"（一个猜测）换成**用户自己画的归属**。
        #    ⚠️ 在 core 里、只是 parent 为空的节点**绝不**因此被补父：那条空是记录本身。
        if not m.get("core_parent_run_id") and not was_in_core.get(id(m)):
            cand = link_parent.get(m.get("id") or "")
            if cand:
                m["core_parent_run_id"] = cand
        if not m.get("core_parent_run_id") and not was_in_core.get(id(m)):
            m["core_parent_run_id"] = run_rows[-1][0] if run_rows else ""
        parent = m.get("core_parent_run_id") or ""
        run_rows.append([rid, batch, m.get("core_sample_id") or "", stage,
                         m.get("core_stage_seq", seq_in_stage), now,
                         "", "", tool_name, tool_id,
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
        # ── 测量行挂在**哪条 run** 上 ──────────────────────────────────────────────
        # 数据线协议 §15.1（2026-09-14 裁定）：`measurement.run_id` ＝「这个数是在哪次工艺之后
        # 测出来的」，**不是**「用哪台仪器测的」；并明写 **检测 run 上不许挂 measurement**。
        # ⇒ 检测节点（球）上的量名词，模板行要挂到**它测的那条 run**（父）上；
        #    meas_id 也用被测 run 的前缀（`{被测run}.Mnn`），与 core 现状 108/108 同构。
        # ⚠️ 这正是我此前挂起、等口径的那一处：老行为把测量行挂在检测 run 自己身上（错口径）。
        host_rid = rid
        if is_metrology_stage(stage):
            _p = (m.get("core_parent_run_id") or "").strip()
            if _p and _p != rid:
                host_rid = _p
        host_m = m if host_rid == rid else next(
            (x for x in modules if (x.get("core_run_id") or "") == host_rid), m)
        # 面板填的测量值（表单）→ 合并进 measurements：同 quantity 填值，未覆盖的追加行
        form_meas = [r for r in (m.get("core_measurements") or [])
                     if str(r.get("value", "")).strip() != ""]     # 空=未测，不当 0
        used_ids: set = set()
        for out in (m.get("param_outputs") or []):
            q = PARAM_TO_QUANTITY.get(out, out)
            meta = field_meta(q)
            hit = next((r for r in form_meas
                        if r.get("quantity") == q and id(r) not in used_ids), None)
            if hit:
                used_ids.add(id(hit))
            elif form_meas:
                # 面板已经填过值 ⇒ **不再产出空模板行**（与追加包口径一致：空=未测，不写行）
                continue
            # 行挂在 host_rid（检测节点 ⇒ 被测 run），meas_id 前缀也跟着走
            n = len([r for r in meas_rows if r[0].startswith(host_rid)]) + 1
            meas_rows.append([(hit or {}).get("meas_id") or f"{host_rid}.M{n:02d}", host_rid,
                              (hit or {}).get("sample_id") or host_m.get("core_sample_id")
                              or m.get("core_sample_id") or "", q,
                              str(hit.get("value", "")).strip() if hit else "",
                              (hit or {}).get("unit") or meta.get("unit", ""),
                              (hit or {}).get("method", ""), (hit or {}).get("loc", ""),
                              (hit or {}).get("n", ""), (hit or {}).get("uncertainty", ""),
                              (hit or {}).get("source_artifact_id", ""),
                              (hit or {}).get("measured_by") or operator or "",
                              (hit or {}).get("verification") or "未核实",
                              (hit or {}).get("note", "")])
        for r in form_meas:                            # 不在接口输出里的量名也照记
            if id(r) in used_ids:
                continue
            n = len([x for x in meas_rows if x[0].startswith(rid)]) + 1
            meas_rows.append([r.get("meas_id") or f"{rid}.M{n:02d}", rid,
                              r.get("sample_id") or m.get("core_sample_id") or "",
                              r.get("quantity", ""), str(r.get("value", "")).strip(),
                              r.get("unit", ""), r.get("method", ""), r.get("loc", ""),
                              r.get("n", ""), r.get("uncertainty", ""),
                              r.get("source_artifact_id", ""),
                              r.get("measured_by") or operator or "",
                              r.get("verification") or "未核实", r.get("note", "")])
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


def _form_observations(project: dict, operator: str, now: str) -> list[list]:
    """面板填的**现象** → observations 行（obs_type 表外跳过；id 照抄或用 {run}.O{nn}）。"""
    from . import form_contract as fc
    vocab = {o["obs_type"] for o in fc.observations()}
    out = []
    for m in project.get("modules") or []:
        rid = m.get("core_run_id") or ""
        for i, o in enumerate((m.get("core_observations") or []), start=1):
            ot = (o.get("obs_type") or "").strip()
            if not ot or (vocab and ot not in vocab):
                continue
            out.append([o.get("obs_id") or f"{rid}.O{i:02d}", rid,
                        o.get("sample_id") or m.get("core_sample_id") or "", ot,
                        o.get("severity", ""), o.get("description", ""),
                        o.get("judgement", ""), o.get("action", ""),
                        o.get("artifact_id", ""), o.get("recorded_by") or operator or "",
                        o.get("date") or now])
    return out


def _form_eq_state(project: dict) -> list[list]:
    """面板填的**环境一行** → eq_state.csv 行（§十一 口径；超量程留空并在 note 标注）。"""
    from . import form_contract as fc
    rows = project.get("core_eq_state") or []
    if isinstance(rows, dict):
        rows = [rows]
    out = []
    for r in rows:
        norm, warns = fc.check_eq_state(r)
        if not norm.get("date"):
            continue
        sid = r.get("state_id") or f"EQ-{norm['date'].replace('-', '')}-{norm.get('tool', '(环境)')}"
        out.append([sid, norm["date"], norm.get("tool", "(环境)"),
                    norm.get("env_temp_c", ""), norm.get("env_rh_pct", ""),
                    norm.get("chamber_bg_pa", ""), norm.get("chiller_temp_c", ""),
                    norm.get("chamber_temp_c", ""), norm.get("he_flow", ""),
                    norm.get("clean_done", ""),
                    (norm.get("note", "") + ("；⚠️ " + "；".join(warns) if warns else ""))])
    return out


def build_process_card(project: dict, purpose: str = "", operator: str = "",
                       lib=None) -> str:
    """画布项目 → 人读「实验流程卡」Markdown(上机对照/交接用)。

    与 build_expack 共用 extract_rows ⇒ run_id / step_id / meas_id 与 CSV 逐字一致。
    内容口径：**计划 + 待填占位**(不掺实测值,实测以 core CSV 为准)。
    """
    run_rows, step_rows, meas_rows, stage_counter, batch = extract_rows(
        project, purpose, operator, lib)
    modules = [m for m in project.get("modules", []) if m.get("core_run_id")]
    machines = lib.machines() if lib else []
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
        # 机台口径（2026-09-14）：卡上断言的是**落 core 的那个机台号**。画布机台名与应用库显示名
        # 是两套字面量，过去卡上只有前者 ⇒ 人看不出这条 run 会被记到哪台机器名下。
        _tid, _tname = resolve_tool(m, machines, STAGE_CODES)
        L.append(f"- 机台口径（core）：`{_tid}` · {_tname}")
        # 检测节点：**写清测的是哪条 run**（B+ 口径：检测 ＝ 对上游 run 的一次测量）。
        # 这一行是"游离于体系之外"的正面回答 —— 卡上不再是一个孤零零的 SEM，而是"测的是谁"。
        if is_metrology_stage(stage_from_run_id(rid) or m.get("core_stage") or ""):
            par = m.get("core_parent_run_id") or ""
            L.append("- **检测对象**：" + (f"`{par}`" if par
                                        else "⚠️ 未连到被测 run（说不出测谁）"))
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
    # ⚠️ `lib` 必须传下去：`resolve_stage` 的三级回退里有两级要用它（经机台的设备模板）。
    #    漏传过一次（函数收了 `lib=LIB` 却没用）⇒ 只靠回退才认得出的节点会**卡上有、CSV 里没有**，
    #    正好打破"卡与 CSV 逐字一致"的承诺，且 `unmapped_modules(project, lib)` 用 lib 查得出、
    #    于是**连告警都不会出**（2026-09-13 查出）。
    run_rows, step_rows, meas_rows, stage_counter, batch = extract_rows(
        project, purpose, operator, lib)
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
             "recorded_by", "date"], _form_observations(project, operator, now)),
        # 环境一行（面板填的；没有就不写这个文件）
        **({} if not _form_eq_state(project) else {
            "eq_state.csv": _csv_bytes(
                ["state_id", "date", "tool", "env_temp_c", "env_rh_pct",
                 "chamber_bg_pa", "chiller_temp_c", "chamber_temp_c", "he_flow",
                 "clean_done", "note"], _form_eq_state(project))}),
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


class ExpackError(ValueError):
    """实验包不可用（含 zip 安全校验不通过）—— API 层应转 400，不要漏成 500。"""


#: 解包硬上限（zip 炸弹/资源耗尽的第一道闸；测试里会 monkeypatch 成小值来验证判据）
MAX_ZIP_MEMBERS = 4096
MAX_ZIP_BYTES = 512 * 1024 * 1024        # 解压后总字节上限
_COPY_CHUNK = 256 * 1024


def _safe_extract_zip(z: zipfile.ZipFile, dest: Path) -> None:
    """把 zip 解到 `dest`，**逐条校验**后再落盘（不再用 `extractall`）。

    为什么不能直接 `extractall`（2026-09-13 审计发现）：
      · **Zip Slip**：成员名可以是 `../../x` 或绝对路径 ⇒ 写出 `dest` 之外，覆盖任意文件；
      · **符号链接**：成员可以是 symlink ⇒ 后续写入被重定向到别处；
      · **zip 炸弹**：压缩比可以极大，`extractall` 会一直写满磁盘（本函数按**声明大小**累计设上限，
        并边写边计数，声明值不可信时也能在超限时中止）。
    只读用途（导入实验包）**不需要**任何越界能力，所以一律拒绝而不是"尽量兼容"。
    """
    dest = Path(dest)
    base = dest.resolve()
    total, count = 0, 0
    for info in z.infolist():
        name = info.filename or ""
        if not name:
            continue
        if name.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", name):
            raise ExpackError(f"包内成员是绝对路径，已拒绝：{name}")
        parts = Path(name.replace("\\", "/")).parts
        if any(p == ".." for p in parts):
            raise ExpackError(f"包内成员试图跳出解包目录（zip slip），已拒绝：{name}")
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise ExpackError(f"包内含符号链接成员，已拒绝：{name}")
        count += 1
        if count > MAX_ZIP_MEMBERS:
            raise ExpackError(f"包内成员数超过上限 {MAX_ZIP_MEMBERS}")
        target = dest.joinpath(*parts)
        if info.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        total += int(info.file_size or 0)
        if total > MAX_ZIP_BYTES:
            raise ExpackError(f"解压后总体积超过上限 {MAX_ZIP_BYTES} 字节（疑似 zip 炸弹）")
        target.parent.mkdir(parents=True, exist_ok=True)
        if not str(target.parent.resolve()).startswith(str(base)):
            raise ExpackError(f"成员落点越出解包目录，已拒绝：{name}")
        with z.open(info) as src, open(target, "wb") as dst:
            shutil.copyfileobj(src, dst, _COPY_CHUNK)
            if dst.tell() > MAX_ZIP_BYTES:
                raise ExpackError("单个成员超过体积上限（疑似 zip 炸弹）")


def _unpack_expack(path: Path) -> tuple[Path, Path | None]:
    """包路径 → (包根目录, 需要清理的临时目录或 None)。zip 走**安全解包**。"""
    import tempfile
    if path.suffix.lower() != ".zip":
        return path, None
    tmp = Path(tempfile.mkdtemp(prefix="expack_"))
    try:
        with zipfile.ZipFile(path) as z:
            _safe_extract_zip(z, tmp)
    except ExpackError:
        shutil.rmtree(tmp, ignore_errors=True)      # 校验失败也要把半截目录清掉
        raise
    except zipfile.BadZipFile as e:
        shutil.rmtree(tmp, ignore_errors=True)
        raise ExpackError(f"不是有效的 zip 包：{e}") from e
    root = next((d for d in tmp.iterdir() if d.is_dir()), tmp)
    return root, tmp


def parse_expack(path: Path, lib) -> dict:
    """包路径(文件夹或 zip) → 画布项目 dict {name, modules, edges}。

    有 flow.json → 用它(保布局/连线),并把 measurements/observations 叠加到对应节点;
    无 flow.json(手工采集包) → 由 runs/steps 合成节点,按时序连线。
    """
    root, _tmpdir = _unpack_expack(path)
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
            # 前缀取**清洗后的步名**：剥掉机台槽位后缀「·槽N」「·slotN」并清标点，
            # 否则会生成 `chuck-si·槽1_hv_press_exp` 这类脏键（core 里的参数键是干净的）
            pre = re.sub(r"[·•]\s*(槽|slot)\s*\d+\s*$", "", (st.get("step_name") or "").strip(),
                         flags=re.I)
            pre = re.sub(r"[^0-9a-z]+", "_", pre.lower()).strip("_")
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
        # 机台口径也往返（2026-09-14）：画布的 `machine_name` 是**应用库显示名**，
        # 与 core 的 `tool_id`/`tool` 是两套字面量 —— 不把 core 原值带回来，再导出就只能
        # 拿显示名去顶（`DRIE-Bosch` 顶掉 `RIE-400iPB`，静默把机台归属记错）。
        if (r.get("tool_id") or "").strip():
            m["core_tool_id"] = r["tool_id"].strip()
        if (r.get("tool") or "").strip():
            m["core_tool"] = r["tool"].strip()
        if r.get("stage_seq"):
            m["core_stage_seq"] = r["stage_seq"]
        if r.get("date"):
            m["core_date"] = r["date"]
        if r.get("parent_run_id"):
            m["core_parent_run_id"] = r["parent_run_id"]
        if (r.get("run_nature") or "").strip():
            # season/trial/batch_level 一并带上 —— 画布据此把 season 节点默认收起（owner 2026-09-12 裁断）
            m["run_nature"] = r["run_nature"].strip()
        # 参数调试线归属（v0.1.6）：画布据此显示 run1/run2…（ICP-XXXX 的序号不连续，读数不直观）
        if (r.get("tune_id") or "").strip():
            m["tune_id"] = r["tune_id"].strip()
        if (r.get("tune_step") or "").strip():
            m["tune_step"] = int(r["tune_step"]) if r["tune_step"].strip().isdigit() \
                else r["tune_step"].strip()
        oc = _obs_of(r.get("run_id", ""))
        if oc:
            m["comment"] = oc
        return m                        # 坐标由 `_layout_modules` 统一按工艺列排

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
    _sri = stage_run_index(runs_sorted)                # 本工序第几次（run1/run2/…）
    for m, r in zip(modules, runs_sorted):
        if _sri.get(r.get("run_id")):
            m["stage_run_index"] = _sri[r["run_id"]]
    edges = _edges_from_runs(runs_sorted, id_by_run, [m["id"] for m in modules])
    # 检测＝"挂在被测 run 上的标记"（第 1 期：只显示）—— 把归属算好挂在**被测模块**上，
    # 前端据此渲染球；检测模块本身仍在 modules 里（导出照旧），只是不再当节点画。
    _markers = metro_markers(runs_sorted, id_by_run)
    for _m in modules:
        _ms = _markers.get(_m.get("id") or "")
        if _ms:
            _m["metro_markers"] = _ms
    _layout_modules(runs_sorted, modules, edges)       # 列=工序，主链一行、分支挂下
    # 解包目录**用完即清**：原来每次导入 zip 都在系统临时目录漏一个 `expack_*`（长期只增不减）
    if _tmpdir is not None:
        shutil.rmtree(_tmpdir, ignore_errors=True)
    return {"name": batch, "modules": modules, "edges": edges}


#: 画布几何**唯一来源**在 `kb/canvas_geom.py`（前端尺寸 / 后端布局 / 体检器三处对齐）。
#: 这里只把常用名字引进来，别在本文件里再写死尺寸。
from .canvas_geom import GAP as LAYOUT_GAP, COL_PITCH as LAYOUT_COL   # noqa: E402
from .canvas_geom import X0 as LAYOUT_X0, Y0 as LAYOUT_Y0, node_height as _node_h  # noqa: E402
from .canvas_geom import NODE_W  # noqa: E402
from .canvas_geom import STAGGER  # noqa: E402


def stage_run_index(runs: list[dict]) -> dict[str, int]:
    """每条 run 在**本批次本工序**里是第几次（**不含 season**）—— 画布上显示的 `run1/run2/…`。

    为什么要有它（owner 2026-09-13）：`ICP-0008` 明明是 ICP 的第 **5** 次刻蚀
    （前四次是 0002/0003/0005/0006），界面却只显示 core 的号 `0008` ⇒ **序号不连续时读不出"第几次"**，
    还会让人以为是"第 8 次"甚至"T 字形那根竖是 4 条"这类误判。
    规则：按 `(stage_seq, run_id)` 升序（core 的号本身就是顺序），**跳过 season**，从 1 数起。

    ⚠️ 2026-09-13（metrology B+）：**检测 run 也不给号**。`run{N}` 问的是"本工序第几次"，
    而一个检测节点独占自己的工序列（EM/椭偏各一列）⇒ 它永远是 run1，"第几次检测"没有意义，
    反而会让人以为"检测也参与工艺次数"。检测节点的身份由**它连到谁**表达（父＝被测 run）。
    """
    out: dict[str, int] = {}
    seen: dict[tuple[str, str], int] = {}
    for r in sorted(runs, key=lambda x: (str(x.get("stage_seq") or 0), str(x.get("run_id") or ""))):
        if (r.get("run_nature") or "").strip() == "season":
            continue
        if is_metrology_stage(str(r.get("stage") or "")):
            continue
        key = (str(r.get("batch_id") or ""), str(r.get("stage_seq") or ""))
        seen[key] = seen.get(key, 0) + 1
        rid = (r.get("run_id") or "").strip()
        if rid:
            out[rid] = seen[key]
    return out


def _layout_modules(runs_sorted: list[dict], modules: list[dict],
                    edges: list[dict] | None = None) -> None:
    """**机器自校验的布局**：先试"并列分支收成 2 列子格"（更紧凑）；
    若因此出现了**向上/向左**的边（父在右下、子在左上 ⇒ 读起来像倒流），
    就退回"顺着往下摞"的竖排。判据不靠感觉，靠 `_bad_edge()` 实测。"""
    _layout_once(runs_sorted, modules, edges, pack=True)
    if _bad_edges(runs_sorted, modules, edges):
        _layout_once(runs_sorted, modules, edges, pack=False)


def _bad_edges(runs_sorted: list[dict], modules: list[dict],
               edges: list[dict] | None) -> list[str]:
    """列出"倒流"的边：目标在源的**左边**，或同列内**上方**。"""
    rid_of = [(r.get("run_id") or "").strip() for r in runs_sorted]
    mid = {m.get("id"): m for m in modules}
    rid_by_mid = {m.get("id"): rid for m, rid in zip(modules, rid_of)}
    out = []
    for e in (edges or []):
        a, b = mid.get(e.get("src")), mid.get(e.get("dst"))
        if not a or not b:
            continue
        ax, ay, bx, by = (float(a.get("x") or 0), float(a.get("y") or 0),
                          float(b.get("x") or 0), float(b.get("y") or 0))
        if bx < ax - 1 or (abs(bx - ax) < 1 and by < ay - 1):
            out.append(f"{rid_by_mid.get(e.get('src'))} → {rid_by_mid.get(e.get('dst'))}")
    return out


def _layout_once(runs_sorted: list[dict], modules: list[dict],
                 edges: list[dict] | None = None, pack: bool = True) -> None:
    """按**工艺列**摆放节点（就地改 `x`/`y`）。**主链一条直线，分支挂下面。**

    - **x** = 工序列（`stage_seq`）⇒ 左到右就是工艺顺序；
    - **y** = 主线固定第 1 行（y=80）：**链一路向右，不再上下跳**；并存的分支/独立试验往下排。

    为什么这么改（2026-09-13 owner："从 DWL 到 ICP etch 的连线仍然混乱"）：
      旧规则让"有记录父"的节点在各列内顺排 ⇒ AR50-T1 里真正接棒的 `ICP-0008` 被排到第 3 行，
      而 ASH/DRIE 在第 1 行 ⇒ 画面成了"DWL 扇出 5 条 + 一条从底部斜着往上接 ASH"，看着就乱。
      **判据换成"谁接着往下走"**：每个父节点挑一个**子树最深的子节点当脊柱**（= 继续流向后续工序的那条），
      它**继承父的行号**；其余子节点是分支，依次往下挂。于是主链永远是一条直线、分支像扇子展开 ——
      既看得出流程，也看得出"哪几条是并存的"。
    season（热机）**单独最后摆**（本就不入流程，`relayout` 还会把它们挪到独立区）。
    """
    rid_of = [(r.get("run_id") or "").strip() for r in runs_sorted]
    seq_of = {rid: int(r.get("stage_seq") or 0) for rid, r in zip(rid_of, runs_sorted)}
    nat_of = {rid: (r.get("run_nature") or "").strip() for rid, r in zip(rid_of, runs_sorted)}
    mid_of = {rid: m["id"] for m, rid in zip(modules, rid_of) if rid}
    rid_by_mid = {mid: rid for rid, mid in mid_of.items()}

    # 有效父：记录边优先，其次推断边（同 `_edges_from_runs` 的产物）
    parent: dict[str, str] = {}
    inferred: dict[str, bool] = {}
    for e in (edges or []):
        rid, src = rid_by_mid.get(e.get("dst")), rid_by_mid.get(e.get("src"))
        if not rid or not src or rid == src:
            continue
        is_inf = e.get("_link") == "inferred"
        if rid not in parent or (inferred.get(rid) and not is_inf):
            parent[rid], inferred[rid] = src, is_inf
    children: dict[str, list[str]] = {}
    for rid in rid_of:
        p = parent.get(rid)
        if p:
            children.setdefault(p, []).append(rid)

    def _is_metro_rid(rid: str) -> bool:
        """按 run_id 的 stage 段判检测节点（计划节点没有 `stage` 字段，见 `stage_from_run_id`）。"""
        return is_metrology_stage(stage_from_run_id(rid))

    # ── 检测**不再占工序列**（2026-09-14 owner拍板：检测＝连线上的球，不是工序）──
    #    病根（旧观感）：检测被当成一道工序 ⇒ 一步后接三个测试就只能串成链（语义错）
    #    或扇出再并回（可读性差）。现在检测从列布局里摘出去，主链直接连下一个工序。
    #    ⚠️ 两个连带问题必须一起解，否则会留下"空列"或"检测跑到 0 列"：
    #      ① 列号要按**可见工序**重排连续（PECVD=1/MA6=3/RIE=4 中间那格是 ELLIP 的，
    #         它一走就空 -> 直接按"第几个可见工序"算列），
    #      ② 检测自己的坐标最后单独给（贴在**被测 run 的出边中点**），不参与格子分配。
    _metro_of = {rid: _is_metro_rid(rid) for rid in rid_of}
    flow_rids = [rid for rid in rid_of if not _metro_of.get(rid)]
    _seqs = sorted({seq_of.get(r, 0) for r in flow_rids if seq_of.get(r)})
    _col_of_seq = {sq: i for i, sq in enumerate(_seqs)}

    def _disp_col(rid: str) -> int:
        """显示列＝该工序在**可见工序序列**里的序号（检测不占列 ⇒ 不留空档）。

        ⚠️ 名字别叫 `_col`：本函数体下面有 `for _col, _rids in per_col.items()` 的循环变量，
        同名会**遮蔽**这个函数（第一次就踩了 `TypeError: 'int' object is not callable`）。
        """
        sq = seq_of.get(rid, 0)
        if sq in _col_of_seq:
            return _col_of_seq[sq]
        p = parent.get(rid)
        if p and seq_of.get(p) in _col_of_seq:
            return _col_of_seq[seq_of[p]]
        return 0

    # ── 检测节点（metrology B+）：**列从父推导**，绝不落进第 0 列 ──
    #    病根：检测模块没有工序列号（计划节点 `stage_seq=0`）⇒ `col = max(seq-1, 0) = 0`
    #    ⇒ 排在 x=140 的最左列、**在被测 run 的左边**（2026-09-13 owner：「游离于体系之外」，
    #    实测 SEM 落在与 PECVD 同一列像是最早的一步，还会造出向左的边）。
    #    规则：stage_seq 为空、但父有工序列号 ⇒ 取 `父列 + 1`（＝被测 run **右侧一列**，
    #    与 core 里 AR50-T2 的写法一致：PECVD=1 → ELLIP=2、RIE=4 → SEM=5）。
    for rid in rid_of:
        if seq_of.get(rid):
            continue
        p = parent.get(rid)
        if p and seq_of.get(p):
            seq_of[rid] = seq_of[p] + 1

    memo: dict[str, int] = {}

    def reach(rid: str, seen: frozenset = frozenset()) -> int:
        """该节点子树能走到的最远工序号 ⇒ 用来挑脊柱（继续往下走的那条）。"""
        if rid in memo:
            return memo[rid]
        if rid in seen:
            return seq_of.get(rid, 0)
        best = seq_of.get(rid, 0)
        for c in children.get(rid, []):
            best = max(best, reach(c, seen | {rid}))
        memo[rid] = best
        return best

    rows: dict[str, int] = {}
    used: dict[int, set[int]] = {}

    def take(col: int, want: int) -> int:
        got, busy = max(want, 0), used.setdefault(col, set())
        while got in busy:
            got += 1
        busy.add(got)
        return got

    def place(rid: str, want: int) -> None:
        if rid in rows:
            return
        col = _disp_col(rid)
        rows[rid] = take(col, want)
        kids = children.get(rid, [])
        if not kids:
            return
        # 并列时取后者（run 序靠后=真正的接棒）；**但检测节点不许抢脊柱** ——
        # 它的工序列号是"父+1"推出来的，reach 可能跟真正的接棒者打平；真让它当了脊柱，
        # 主线会被一条检测节点带跑，工艺链被挤到下一行（2026-09-13 metrology B+）。
        spine = max(kids, key=lambda k: (reach(k), not _is_metro_rid(k), kids.index(k)))
        place(spine, rows[rid])                                      # 脊柱继承行号 ⇒ 主线直线
        nxt = rows[rid] + 1
        for k in kids:
            if k != spine:
                place(k, nxt)
                nxt = rows[k] + 1

    batch_of = {}
    for rid, r in zip(rid_of, runs_sorted):
        b = (r.get("batch_id") or "").strip()
        if not b and rid.count("-") >= 2:
            b = rid.rsplit("-", 2)[0]            # 回退：从 run_id 推 batch（仅用于分道）
        batch_of[rid] = b
    lanes: dict[str, int] = {}
    for rid in rid_of:                           # 批次分道：同批一条"泳道"，批次之间不互相错插
        b = batch_of.get(rid) or ""
        if b not in lanes:
            lanes[b] = len(lanes)
    main_rids = [r for r in flow_rids if nat_of.get(r) != "season"]
    # 按批次分组排：每条泳道内部各自从第 0 行起排（跨批次的边本来就不存在）
    by_batch: dict[str, list[str]] = {}
    for rid in main_rids:
        by_batch.setdefault(batch_of.get(rid) or "", []).append(rid)
    for b, rids in by_batch.items():
        lane_base = 0
        for rid in rids:
            if not parent.get(rid):
                place(rid, lane_base)
                lane_base = max(lane_base, rows[rid] + 1)
        for rid in rids:                         # 兜底：父不在本图里（跨包续做）
            if rid not in rows:
                place(rid, lane_base)
    # 多批次时给后面的泳道整体下移，避免与上一批的行号撞车
    if len([b for b in by_batch if b]) > 1:
        lane_shift = {b: i * (max(rows.values(), default=0) + 2) for i, b in enumerate(by_batch)}
        for rid in main_rids:
            rows[rid] = rows.get(rid, 0) + lane_shift.get(batch_of.get(rid) or "", 0)
    seasons = [r for r in flow_rids if nat_of.get(r) == "season"]
    # season：全部排到主流程下方（成列但不参与主线行号）
    below = max(rows.values(), default=0) + 1
    for i, rid in enumerate(seasons):
        col = _disp_col(rid)
        rows[rid] = take(col, below + i)

    # ── 并列分支**块状排布**（owner 2026-09-13："四个 ICP 能不能做成 2×2，从 5 行变 3 行，
    #    而且要有竖直错位表示先后"）──
    # 同一工序列里，除"接着往下走的那条"（脊柱）以外的并列分支：
    #   1–2 条 → 照旧顺着往下摞；
    #   **≥3 条** → 收成 **2 列子格**（每行 2 个），右列整体下错 `STAGGER`：
    #      读序仍是"从上到下、从左到右"（1 左上 → 2 右上偏下 → 3 左下 → 4 右下偏下）。
    # ⚠️ 2026-09-13 owner：「为什么 Plasma Strip 距上一个 ICP 的**横向距离比别处大**？」
    #    原因＝当时让"多出来的那一列把**后面的工序列整体右移一格**" ⇒ ICP→ASH 变成 2 格（524px），
    #    而那一格在主链那一行**是空的**，看着就像莫名多了一段空白。
    #    改法：**后面的工序列不右移** —— 溢出子列去**共用下一列的 x**（不同行就不冲突：
    #    ASH 在 row 0，ICP 的分支在 row 1–2）。真撞上 (列,行) 同一个格子时才往右让一格。
    #    收益：主链每段横向间距都等于一个列距（262），整图窄 262px，T 字形观感消失。
    sub_of: dict[str, int] = {}
    per_col: dict[int, list[str]] = {}
    for rid in flow_rids:
        per_col.setdefault(_disp_col(rid), []).append(rid)
    for _col, _rids in per_col.items():
        _ordered = sorted(_rids, key=lambda r: (rows.get(r, 0), r))
        _branches = _ordered[1:]                     # 第 0 条 = 脊柱（继续往下走的那条）
        if pack and len(_branches) >= 3:
            _base = rows.get(_branches[0], 1)
            for _i, _rid in enumerate(_branches):
                rows[_rid] = _base + _i // 2          # 每行 2 个 ⇒ 行数减半（2×2/2×3 阶梯）
    # 落格子：**列 = 工序列 + 溢出子列**，但后面的工序列**不因别人溢出而右移**；
    # 只有当 (列, 行) 这个格子真被占了，才把这条挤到再右一格（最多让 6 格，兜底）
    cell: dict[tuple[int, int], str] = {}
    sub_of.clear()
    # ① **脊柱先钉**：每列行号最小的那条坐自己那列（主链的列位置谁也不许挤掉）
    spine_of_col: dict[int, str] = {}
    for _c, _rids in per_col.items():
        spine_of_col[_c] = sorted(_rids, key=lambda r: (rows.get(r, 0), r))[0]
    for _c, _rid in spine_of_col.items():
        cell[(_c, rows.get(_rid, 0))] = _rid
    # ② 并列分支：先试自己那列，格子被占（2×2 里同一行的另一半）才**借下一列的 x**
    for _rid in sorted([r for r in flow_rids if r not in set(spine_of_col.values())],
                       key=lambda r: (rows.get(r, 0), r)):
        _c2 = _disp_col(_rid)
        _k = rows.get(_rid, 0)
        _i = 0
        while (_c2 + _i, _k) in cell and _i < 7:
            _i += 1
        cell[(_c2 + _i, _k)] = _rid
        sub_of[_rid] = _i

    # ── 落点：**横纵间距等宽 + 行高自适应** ──
    # 横向：列距 = 节点宽 + GAP（等距）；
    # 纵向：第 k 行的行距 = 该行**最高节点**的高度 + GAP ⇒ 有备注的行自动变高、没备注的保持紧凑，
    #       且因为按"备注限高 3 行"的最坏情况算，用户开关「显示备注」都不会压到下一格。
    row_h: dict[int, int] = {}
    for m, rid in zip(modules, rid_of):
        k = rows.get(rid, 0)
        # ⚠️ **不要**把 STAGGER 加进行高：让它探进下方 72px 的缝里（还剩 28px 余量），
        #    这样每条子列自身的相邻间距仍是 72，视觉节奏不被撑开（2026-09-13 实测）
        row_h[k] = max(row_h.get(k, 0), _node_h(m))
    row_y: dict[int, float] = {}
    y = float(LAYOUT_Y0)
    for k in sorted(row_h):
        row_y[k] = y
        y += row_h[k] + LAYOUT_GAP
    for m, rid in zip(modules, rid_of):
        if _metro_of.get(rid):
            continue                                   # 检测：下面单独定位（贴在出边中点）
        col = _disp_col(rid)
        m["x"] = float(LAYOUT_X0) + (col + sub_of.get(rid, 0)) * LAYOUT_COL
        m["y"] = row_y.get(rows.get(rid, 0), float(LAYOUT_Y0)) + (STAGGER if sub_of.get(rid) else 0)

    # ── 检测模块的坐标：**被测 run 的出边中点**（没有后继工序时贴它右侧）──
    #    它们不参与上面的格子分配，所以这里必须自己给坐标（否则会停在 0,0）；
    #    前端第 1 期按"标记"渲染，坐标只在"切回节点显示"时才看得见。
    resid_rows = {(r.get("run_id") or "").strip(): r for r in runs_sorted}
    for m, rid in zip(modules, rid_of):
        if not _metro_of.get(rid):
            continue
        anchor_rid = ""
        cur = (resid_rows.get(rid, {}).get("parent_run_id") or "").strip()
        for _ in range(32):
            if not cur:
                break
            row = resid_rows.get(cur)
            if row is None or not _is_metro_rid(cur):
                anchor_rid = cur
                break
            cur = (row.get("parent_run_id") or "").strip()
        a_mid = mid_of.get(anchor_rid)
        a_mod = next((x for x in modules if x.get("id") == a_mid), None) if a_mid else None
        if a_mod is not None:
            m["x"] = float(a_mod.get("x") or LAYOUT_X0) + NODE_W + LAYOUT_GAP / 2
            m["y"] = float(a_mod.get("y") or LAYOUT_Y0)
        else:
            m["x"], m["y"] = float(LAYOUT_X0), float(LAYOUT_Y0)


#: 边的来源（**显示层要能区分**，否则"推断"会被当成"记录"）
LINK_RECORDED = "recorded"      # core 的 `parent_run_id` 明确写的
LINK_INFERRED = "inferred"      # 按工艺顺序（batches.planned_stages / stage_seq）补的**显示**边


def metro_markers(runs_sorted: list[dict], id_by_run: dict) -> dict[str, list[dict]]:
    """`{被测 run 的模块 id: [检测条目, …]}` —— 画布上那些"球"的数据来源（第 1 期：只显示）。

    口径（2026-09-14 owner拍板形态）：
      · 检测**不是工序**，是"对某个状态的一次观察" ⇒ 不再占工序列、不再生成扇出/并回；
      · 每个检测**锚在它测的那条 run 上**（父是检测时继续上溯，见 `_metro_anchor`）；
      · 同一个被测 run 上的多个检测**合成一个球**（1 个整圆 / 2 个两半 / 3–4 等分 / ≥5 计数环）——
        所以这里返回的是**列表**，由前端按数量决定画法。
    ⚠️ 只影响显示：检测 run 行照旧存在（仪器 session＝provenance），导出/入库一个字节不改。
    """
    row_of = {(r.get("run_id") or "").strip(): r for r in runs_sorted}

    def _is_metro(run: dict) -> bool:
        return is_metrology_stage(str(run.get("stage") or "") or
                                  stage_from_run_id(str(run.get("run_id") or "")))

    def _anchor(rid: str) -> str:
        seen_rid: set[str] = set()
        cur = rid
        for _ in range(32):
            if not cur or cur in seen_rid:
                return ""
            seen_rid.add(cur)
            row = row_of.get(cur)
            if row is None:
                return ""
            if not _is_metro(row):
                return cur
            cur = (row.get("parent_run_id") or "").strip()
        return ""

    out: dict[str, list[dict]] = {}
    for r in runs_sorted:
        rid = (r.get("run_id") or "").strip()
        if not _is_metro(r):
            continue
        anchor_rid = _anchor((r.get("parent_run_id") or "").strip())
        anchor_mid = id_by_run.get(anchor_rid)
        if not anchor_mid:
            continue                                  # 锚不到被测 run ⇒ 不画球（宁可少画，不编归属）
        _stage = str(r.get("stage") or stage_from_run_id(rid))
        _sub = (STAGE_TO_TEMPLATE.get(_stage) or ("", ""))[0]
        try:                                       # 族色**唯一真相**在后端（engine.METRO_FAMILY）
            from engine.process_catalog import METRO_FAMILY as _MF
            _fam = _MF.get(_sub, "metro")
        except Exception:                          # noqa: BLE001 —— 取不到就退中性色，不影响正确性
            _fam = "metro"
        out.setdefault(anchor_mid, []).append({
            "run_id": rid,
            "stage": _stage,
            "family": _fam,
            "module_id": id_by_run.get(rid) or "",
            "sample_id": (r.get("sample_id") or "").strip(),
            "date": (r.get("date") or "").strip(),
            "tool_id": (r.get("tool_id") or "").strip(),
            "stage_seq": r.get("stage_seq") or "",
        })
    for k in out:                                     # 顺序稳定（run_id 升序）⇒ 颜色/分段不抖动
        out[k].sort(key=lambda x: x["run_id"])
    return out


def _edges_from_runs(runs_sorted: list[dict], id_by_run: dict, module_ids: list[str]) -> list[dict]:
    """由 runs 合成画布连线。**分两类，绝不混淆**：

    · `recorded`（实线）：core 的 `parent_run_id` 明确写的 —— **空就是空，不编**。
      这是被真实数据打回来的规则（2026-09-13 owner："刷新后还是 DWL 后面跟着 8 个连续的 ICP"）：
      AR50-T1 有 **6 条并存的 ICP 试验**（parent 空），"空 parent 就接上一条"会把它们连成直线，
      界面上看着像"同一片刻了 8 次"，而真相是"8 颗 die 各做一次"。
    · `inferred`（虚线）：run 没写 parent 时，按**工艺顺序**补一条显示用连线
      （上游 = 最近的上一个 stage 里、在它之前的那条 run）。它**只影响画布观感**，
      不进 core、不改 `core_parent_run_id`、批次视图也不拿它当父。

    同 stage 的多条 run 之间**永不连线**（那是并存，不是串行）。
    历史/手工节点（连规范 run_id 都没有）才按导出顺序接上一条。

    **`season`（热机）不入流程**（owner 2026-09-12 裁断：录入但不画，留给设备状态监测）：
      · season run 本身**不得**成为任何边的端点 —— 它是设备调机，不加工任何已登记样品；
      · 若某 run 的**记录父**恰是 season（如 AR50-T1 的 ICP-0008，core 里 parent=ICP-0007=season），
        说明那条记录是"参数沿用"被误记成了样品流 ⇒ **不在画布上画它**，
        改按工艺顺序补一条**推断边**（上游 = 最近的非 season 的上一工序 run）。
    """
    edges: list[dict] = []
    seen: set[tuple[str, str]] = set()
    row_of = {(r.get("run_id") or "").strip(): r for r in runs_sorted}

    def _add(src: str, dst: str, link: str) -> None:
        if not src or not dst or src == dst or (src, dst) in seen:
            return
        seen.add((src, dst))
        edges.append({"src": src, "dst": dst, "_link": link})

    def _is_season(run: dict) -> bool:
        return (run.get("run_nature") or "").strip() == "season"

    def _is_metro(run: dict) -> bool:
        return is_metrology_stage(str(run.get("stage") or "") or
                                  stage_from_run_id(str(run.get("run_id") or "")))

    def _metro_anchor(rid: str) -> str:
        """沿 `parent_run_id` 上溯、**跳过检测 run**，返回"被测的那条 run"。

        为什么必须跳过：core 里检测 run 是**链中的一环**（AR50-T2：PECVD→ELLIP→MA6 里
        MA6 的父是 ELLIP），而显示上检测不再是节点 ⇒ 连线要**穿过它直连**，
        否则主链会断在检测处、或者又变成"扇出+并回"。
        """
        seen_rid: set[str] = set()
        cur = rid
        for _ in range(32):                       # 深度兜底：脏数据成环也不能死循环
            if not cur or cur in seen_rid:
                return ""
            seen_rid.add(cur)
            row = row_of.get(cur)
            if row is None:
                return ""
            if not _is_metro(row):
                return cur
            cur = (row.get("parent_run_id") or "").strip()
        return ""

    def _nearest_upstream(idx: int, my_seq: int) -> str | None:
        """最近的**非 season、非检测**上一工序 run 的模块 id（推断边的合法上游）。"""
        for cand in reversed(runs_sorted[:idx]):
            if _is_season(cand) or _is_metro(cand):
                continue
            if int(cand.get("stage_seq") or 0) >= my_seq:
                continue
            cid = id_by_run.get((cand.get("run_id") or "").strip())
            if cid:
                return cid
        return None

    prev_legacy: str | None = None      # 上一条"历史/手工"节点（按导出顺序）
    for idx, r in enumerate(runs_sorted):
        rid = (r.get("run_id") or "").strip()
        # 有规范 run_id ⇒ 用它在 id_by_run 里的模块；历史/手工节点（无 run_id）按位置认领
        dst = id_by_run.get(rid) or (module_ids[idx] if not rid and idx < len(module_ids) else None)
        if not dst:
            continue
        if not rid:                                  # 历史/手工：按时序兜底
            if prev_legacy:
                _add(prev_legacy, dst, LINK_RECORDED)
            prev_legacy = dst
            continue
        if _is_season(r):
            continue                                 # season：本身不挂任何边
        if _is_metro(r):
            continue                                 # 检测：不再当节点画边，改为"锚在被测 run 上的标记"
        my_seq = int(r.get("stage_seq") or 0)
        parent_rid = (r.get("parent_run_id") or "").strip()
        parent_row = row_of.get(parent_rid)
        src = id_by_run.get(parent_rid)
        # ⚠️ 父是检测 ⇒ 改成"连到被测的那条 run"（穿过检测直连）
        if parent_row is not None and _is_metro(parent_row):
            anchor = _metro_anchor(parent_rid)
            src = id_by_run.get(anchor) or src
            if src:
                _add(src, dst, LINK_RECORDED)
                continue
            parent_row = None                        # 锚点解不出 ⇒ 退回落推断边
        if src and parent_row is not None and not _is_season(parent_row):
            _add(src, dst, LINK_RECORDED)             # ① core 明确写的（且父不是 season）
            continue
        # ② 没写 parent、或记录父是 season（"参数沿用"误记成样品流）
        #    ⇒ 按工艺顺序补显示边：上游 = 最近的非 season 上一工序
        upstream = _nearest_upstream(idx, my_seq)
        if upstream:
            _add(upstream, dst, LINK_INFERRED)
    return edges


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

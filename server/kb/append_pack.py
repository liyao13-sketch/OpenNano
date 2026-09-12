"""**追加包**（`source=tool-append`）导出 + core 只读回读契约。

为什么需要"追加包"（数据线 2026-09-12 复核给出的事实）：
    `17/AR50-T1` 这类镜像包的 `manifest.source == "core-slice"`，
    `datasets_folder.discover()` **按设计整包跳过**（防自噬）。
    所以"只把新 run 写进镜像包再导出"→ 落库时整包被丢。
    可行路径 = 导出**只含新增行**的包，manifest 标 `source=tool-append`
    （不是 core-slice ⇒ 不会被跳过），老行天然不会被覆盖（既有源优先）。

core 只读回读的五条口径（数据线 2026-09-12 回执 §三，逐条落在本模块里）：
    1. **只读** core（绝不写 core/*.csv 或 process.db）；工具改动一律写回包 CSV。
    2. **空值语义**：`measurements.value` 空 = 未测（**不渲染成 0**）；
       `param_json` 缺键 ≠ 0（`gvv1/gvv2` 是开关量，0 会显式存键，缺键只能理解为"没记"）。
    3. **ID 不重算**：`step_id/meas_id/obs_id/artifact_id` 照抄；
       `artifact_id` 是**内容 sha256 前 8 位**，重算会打断证据挂接。
    4. **字段名照契约**：`quantity` 用 `schema_v0.1.md` §三 受控量名。
    5. **不许"顺手规范化"**：`role`/`param_json`/`note` 原样呈现
       （G031 的 depo→depo→SE→FE(bias)→SE 是真实结构异常＝工艺线索，调顺即毁）。
"""
from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import datetime
from pathlib import Path

from opennano_config import CORE_DIR      # server/ 在 sys.path 上（main 已保证）
from . import form_contract as fc

#: core 九表（列名照 core_schema，只读用）
CORE_TABLES = ("batches", "samples", "runs", "steps", "measurements",
               "observations", "recipes", "artifacts", "eq_state")
#: 追加包只带这几张（batches/samples/recipes 等留空 —— 增量并入不该动主数据）
APPEND_TABLES = ("runs", "steps", "measurements", "observations")


def core_dir() -> Path:
    return Path(CORE_DIR)


def read_core_table(table: str) -> list[dict]:
    """只读读 core/<table>.csv（缺文件返回 []，不报错、不创建）。"""
    p = core_dir() / f"{table}.csv"
    if not p.exists():
        return []
    with p.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def core_run_ids() -> set[str]:
    """core 里已有的 run_id 集合（判定"哪些是新增行"的唯一依据）。"""
    return {r.get("run_id", "").strip() for r in read_core_table("runs") if r.get("run_id")}


def new_runs_of(project: dict) -> list[str]:
    """画布上**尚未入 core** 的 run（保持画布顺序）。"""
    have = core_run_ids()
    out = []
    for m in project.get("modules") or []:
        rid = (m.get("core_run_id") or "").strip()
        if rid and rid not in have:
            out.append(rid)
    return out


def _csv_bytes(header: list[str], rows: list[list]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue().encode()


def core_facts(run_id: str) -> dict:
    """从 core/runs.csv 取该 run 的**真实归属**（只读、零推断）。

    用途：画布模块不带 sample/die 时（包内模块往往没有 `core_sample_id`），
    应当**继承 core 里该 run 的真实 sample_id**，而不是留空；**绝不凭空造 die 号**
    （数据线 2026-09-12：补 die 编号有真实性代价，须owner拍板）。
    """
    for r in read_core_table("runs"):
        if (r.get("run_id") or "").strip() == run_id:
            return {k: (r.get(k) or "") for k in
                    ("sample_id", "stage_seq", "date", "tool_id", "recipe_id",
                     "batch_id", "stage", "env_temp_c", "env_rh_pct", "status")}
    return {}


def _module_by_run(project: dict) -> dict[str, dict]:
    return {m.get("core_run_id"): m for m in (project.get("modules") or []) if m.get("core_run_id")}


def build_append_pack(project: dict, purpose: str = "", operator: str = "",
                      batch: str = "") -> tuple[bytes | None, dict]:
    """画布 → **追加包** zip（只含 core 里还没有的 run）。

    返回 (zip 字节 | None, 摘要)。没有新 run 时返回 (None, {...reason})。
    包内：`manifest.json`(source=tool-append) + runs/steps/measurements/observations.csv（只新行）
          + `包说明.md`（写清"追加包、不要改老行"）。
    """
    new = new_runs_of(project)
    if not new:
        return None, {"ok": False, "new_runs": [],
                      "reason": "该工程没有「尚未入库」的 run（core 里都已有）⇒ 无需导出追加包"}
    batch = batch or (new[0].rsplit("-", 2)[0] if len(new[0].rsplit("-", 2)) == 3 else "APPEND")
    mods = _module_by_run(project)
    now = datetime.now().strftime("%Y-%m-%d")

    # ---- runs：只带新 run；parent/stage_seq 照画布（工具算好的值）
    run_rows, date_src, sample_src = [], {}, {}
    for rid in new:
        m = mods.get(rid) or {}
        parts = rid.rsplit("-", 2)
        stage = parts[1] if len(parts) == 3 else ""
        parent = m.get("core_parent_run_id") or ""
        # ① sample_id：模块上没有时，**继承 core 里该 run（或它的上游）的真实归属**
        sample = (m.get("core_sample_id") or m.get("sample_id") or "").strip()
        if not sample:
            src = m.get("core_run_id") or rid
            sample = (core_facts(src).get("sample_id") or core_facts(parent).get("sample_id") or "")
            if sample:
                sample_src[rid] = f"继承自 core:{src if core_facts(src).get('sample_id') else parent}"
        # ② stage_seq：模块没给就取 core 里同 stage 的权威值
        if not m.get("core_stage_seq"):
            for r in read_core_table("runs"):
                if ((r.get("batch_id") or "").strip() == batch
                        and (r.get("stage") or "").strip() == stage
                        and (r.get("stage_seq") or "").strip()):
                    m["core_stage_seq"] = r["stage_seq"]
                    break
        # ③ date：**用画布上该 run 的计划日期**；缺失才退回今天，并标注来源
        date = (m.get("core_date") or "").strip()
        if date:
            date_src[rid] = "画布计划日期"
        else:
            date = now
            date_src[rid] = "**未设计划日期 ⇒ 退回导出当天，请核对**"
        run_rows.append([rid, m.get("core_batch_id") or batch,
                         sample, stage,
                         m.get("core_stage_seq", ""), date,
                         "", "", m.get("equipment_name") or stage, m.get("machine_name") or "",
                         m.get("core_recipe_id") or "", operator or "",
                         purpose or "", parent,
                         "", "", "planned", m.get("comment") or ""])

    # ---- steps：菜单灌入的步优先；否则用模块 params 生成的组
    step_rows = []
    meas_rows = []
    for rid in new:
        m = mods.get(rid) or {}
        steps = m.get("core_menu_steps") or []
        if steps:
            for s in steps:
                pj = dict(s.get("param_json") or {})
                # 结构键 ⇒ 升成正式列(数据线 2026-09-12 定)。
                # 槽位号在**步对象**上（menu_reader 的 group_steps 提供），非菜单步没有 ⇒ 留空
                mslot = pj.pop("machine_step", "") or s.get("machine_step", "")
                pj.pop("phase", None)                    # phase 属结构：进 step_name，不进参数
                step_rows.append([f"{rid}.S{s['step_order']:02d}", rid, s["step_order"], mslot,
                                  s.get("step_name", ""), s.get("role", ""),
                                  round(float(s.get("duration_s") or 0), 3) or "",
                                  (pj.get("apc1_press") or ""), "Pa",
                                  json.dumps(pj, ensure_ascii=False), ""])
        for r in (m.get("core_measurements") or []):
            if str(r.get("value", "")).strip() == "":
                continue                      # 空 = 未测，**不**写 0
            meas_rows.append([r.get("meas_id") or "", rid, r.get("sample_id", ""),
                              r.get("quantity", ""), r.get("value", ""), r.get("unit", ""),
                              r.get("method", ""), r.get("loc", ""), r.get("n", ""),
                              r.get("uncertainty", ""), r.get("source_artifact_id", ""),
                              r.get("measured_by", ""), r.get("verification") or "未核实",
                              r.get("note", "")])
    # ---- observations：受控词表校验（表外跳过并计数）
    obs_rows, skipped_obs = [], []
    vocab = {o["obs_type"] for o in fc.observations()}
    for rid in new:
        m = mods.get(rid) or {}
        for i, o in enumerate((m.get("core_observations") or []), start=1):
            ot = (o.get("obs_type") or "").strip()
            if vocab and ot not in vocab:
                skipped_obs.append(ot); continue
            obs_rows.append([f"{rid}.O{i:02d}", rid, o.get("sample_id", ""), ot,
                             o.get("severity", ""), o.get("description", ""),
                             o.get("judgement", ""), o.get("action", ""),
                             o.get("artifact_id", ""), o.get("recorded_by", operator or ""),
                             o.get("date") or now])

    manifest = {
        "format": "opennano-expack", "version": "0.1",
        "batch_id": batch, "created_at": datetime.now().isoformat(timespec="seconds"),
        "source": "tool-append",                    # ★ 不是 core-slice ⇒ 不会被跳过
        "pack_type": "append",
        "project": project.get("name", ""), "purpose": purpose, "operator": operator,
        "new_runs": new, "runs": len(new),
        "only_new_rows": True,
        "sample_id_source": sample_src, "date_source": date_src,
        "note": ("追加包：只含尚未入 core 的行；batches/samples/recipes 留空。"
                 "按既有通道增量并入（既有源优先，老行不会被覆盖）。"
                 "**禁止**把它当第二权威去改老行。"),
    }
    files = {
        "manifest.json": json.dumps(manifest, ensure_ascii=False, indent=2).encode(),
        "runs.csv": _csv_bytes(
            ["run_id", "batch_id", "sample_id", "stage", "stage_seq", "date",
             "t_start", "t_end", "tool", "tool_id", "recipe_id", "operator",
             "purpose", "parent_run_id", "env_temp_c", "env_rh_pct", "status", "note"],
            run_rows),
        "steps.csv": _csv_bytes(
            ["step_id", "run_id", "step_order", "machine_step", "step_name", "role",
             "duration_s", "pressure", "pressure_unit", "param_json", "note"], step_rows),
        "measurements.csv": _csv_bytes(
            ["meas_id", "run_id", "sample_id", "quantity", "value", "unit", "method",
             "loc", "n", "uncertainty", "source_artifact_id", "measured_by",
             "verification", "note"], meas_rows),
        "observations.csv": _csv_bytes(
            ["obs_id", "run_id", "sample_id", "obs_type", "severity", "description",
             "judgement", "action", "artifact_id", "recorded_by", "date"], obs_rows),
        "包说明.md": (
            f"# {batch} · 追加包（tool-append）\n\n"
            f"> 只含 **{len(new)} 个新 run**：{', '.join(new)}\n"
            "> 本包 **不含** batches/samples/recipes —— 增量并入不该动主数据。\n\n"
            "## 入库口径\n"
            "1. 走既有通道：`datasets_folder.py --dry-run` → `build_core.py`。\n"
            "2. **既有源优先**：老行不会被本包覆盖（core-slice 镜像包与此无关）。\n"
            "3. 本包 `source=tool-append`（不是 core-slice），因此**不会被 discover() 跳过**。\n"
            "4. 空值语义：`value` 空 = 未测（不要补 0）；`param_json` 缺键 ≠ 0。\n"
            "5. 异常原样保留（role/note 不得「顺手规范化」）。\n"
        ).encode(),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for n, d in files.items():
            z.writestr(f"{batch}_append/{n}", d)
    return buf.getvalue(), {
        "ok": True, "batch_id": batch, "new_runs": new,
        "sample_id_source": sample_src, "date_source": date_src,
        "runs": len(run_rows), "steps": len(step_rows),
        "measurements": len(meas_rows), "observations": len(obs_rows),
        "skipped_obs": skipped_obs,
        "core_ids_seen": len(core_run_ids()),
    }


def load_run_chain(batch: str) -> dict:
    """**只读**从 core 拉一个 batch 的 run 链（供"回灌画布"/续做前参照）。

    口径（数据线 §三）：runs 全取 → parent 串链 + stage_seq 排序；
    steps 的 param_json 原样；measurements **只拉 value 非空行**；
    observations 的 obs_type 必须在受控词表内（表外跳过并报数）；
    **不重算任何 id**；不猜 env（`runs.env_*` 空就留空，不拿 eq_state 顶替）。
    """
    runs = [r for r in read_core_table("runs") if (r.get("batch_id") or "").strip() == batch]
    steps = read_core_table("steps")
    meas = read_core_table("measurements")
    obs = read_core_table("observations")
    vocab = {o["obs_type"] for o in fc.observations()}

    steps_by_run: dict[str, list] = {}
    for s in steps:
        steps_by_run.setdefault(s.get("run_id", ""), []).append({
            "step_id": s.get("step_id", ""), "step_order": s.get("step_order", ""),
            "step_name": s.get("step_name", ""), "role": s.get("role", ""),
            "duration_s": s.get("duration_s", ""), "pressure": s.get("pressure", ""),
            "pressure_unit": s.get("pressure_unit", ""),
            "param_json": json.loads(s.get("param_json") or "{}") if s.get("param_json") else {},
            "note": s.get("note", ""),
        })
    meas_by_run: dict[str, list] = {}
    skipped_blank = 0
    for m in meas:
        if str(m.get("value", "")).strip() == "":       # 空 = 未测，不渲染成 0
            skipped_blank += 1
            continue
        meas_by_run.setdefault(m.get("run_id", ""), []).append(m)
    obs_by_run: dict[str, list] = {}
    skipped_obs: list[str] = []
    for o in obs:
        ot = (o.get("obs_type") or "").strip()
        if vocab and ot not in vocab:
            skipped_obs.append(ot)
            continue
        obs_by_run.setdefault(o.get("run_id", ""), []).append(o)

    def srt(r):
        try:
            return (int(r.get("stage_seq") or 0), r.get("run_id", ""))
        except ValueError:
            return (0, r.get("run_id", ""))

    chain = []
    for r in sorted(runs, key=srt):
        rid = r.get("run_id", "")
        chain.append({
            "run_id": rid, "stage": r.get("stage", ""), "stage_seq": r.get("stage_seq", ""),
            "parent_run_id": r.get("parent_run_id", ""), "status": r.get("status", ""),
            "tool_id": r.get("tool_id", ""), "recipe_id": r.get("recipe_id", ""),
            "date": r.get("date", ""), "env_temp_c": r.get("env_temp_c", ""),
            "env_rh_pct": r.get("env_rh_pct", ""), "note": r.get("note", ""),
            "steps": steps_by_run.get(rid, []),
            "measurements": meas_by_run.get(rid, []),
            "observations": obs_by_run.get(rid, []),
        })
    return {
        "batch_id": batch, "source": "core(只读)", "nodes": len(chain),
        "edges": sum(1 for r in chain if r.get("parent_run_id")),
        "roots": [r["run_id"] for r in chain if not r.get("parent_run_id")],
        "chain": chain,
        "skipped": {"measurements_blank": skipped_blank, "obs_out_of_vocab": sorted(set(skipped_obs))},
        "note": "id 全部照抄、未重算；env_* 空就留空（不拿 eq_state 顶替）；异常原样保留",
    }

# ---------------------------------------------------------------- 样品树（core 只读）
def sample_tree(batch: str) -> dict:
    """**只读**从 core 建该 batch 的样品继承树（`samples.parent_sample_id` · 契约 v0.1.4）。

    用途：批次视图把样品渲染成 **整片 → die 组 → 组内** 的树，run 挂在叶子上。
    - 只用 `parent_sample_id`（纯继承），**不推断"哪一颗"**；
    - 每片带 `children` / `runs`（含 `run_nature`，空=未标）；
    - 悬空 parent（指向不存在的样品）单独列出，不静默丢。
    """
    smp = [s for s in read_core_table("samples")
           if (s.get("batch_id") or "").strip() == batch]
    runs = [r for r in read_core_table("runs")
            if (r.get("batch_id") or "").strip() == batch]
    by_run: dict[str, list[dict]] = {}
    for r in runs:
        by_run.setdefault((r.get("sample_id") or "").strip(), []).append(r)
    nodes: dict[str, dict] = {}
    for s in smp:
        sid = (s.get("sample_id") or "").strip()
        if not sid:
            continue
        my = by_run.get(sid, [])
        nodes[sid] = {
            "sample_id": sid,
            "parent_sample_id": (s.get("parent_sample_id") or "").strip(),
            "position": s.get("position", ""), "role": s.get("role", ""),
            "status": s.get("status", ""), "note": s.get("note", ""),
            "children": [], "runs": [r.get("run_id", "") for r in my],
            "run_natures": sorted({(r.get("run_nature") or "（未标）") for r in my}),
            "nature_label": _sample_nature(my),
        }
    dangling = []
    roots = []
    for sid, n in nodes.items():
        par = n["parent_sample_id"]
        if not par:
            roots.append(sid)
        elif par in nodes:
            nodes[par]["children"].append(sid)
        else:
            dangling.append(sid)
    # 组内颗数（DIE4 = 4 颗）仅作展示备注，**不当作位号**
    for sid, n in nodes.items():
        if n["children"]:
            n["child_count"] = len(n["children"])
    return {
        "batch_id": batch, "source": "core(只读)",
        "tree": [nodes[r] for r in sorted(roots)],
        "nodes": nodes, "count": len(nodes),
        "orphan_parent": dangling,
        "note": ("样品组（如 DIE4/DIE15）的数字是**组内颗数**、不是 die 位号；"
                 "裂片事件的「哪一颗去了哪」未记 ⇒ 工具不推断"),
    }


def _sample_nature(my_runs: list[dict]) -> str:
    """该样品上 run 的总体性质（供 UI 一眼看：试验片 / 批次级 / 链）。"""
    if not my_runs:
        return "无 run"
    ns = {(r.get("run_nature") or "").strip() for r in my_runs}
    ns.discard("")
    if ns == {"trial"}:
        return "试验片"
    if ns == {"chain"}:
        return "链上样品"
    if ns and ns <= {"batch_level"}:
        return "批次级（整片/多片）"
    if ns:
        return "混合（" + "/".join(sorted(ns)) + "）"
    return "未标（按 parent 自推）"


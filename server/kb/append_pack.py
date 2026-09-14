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
from .core_vocab import resolve_tool
from .expack import STAGE_CODES, export_warnings

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
                      batch: str = "", lib=None) -> tuple[bytes | None, dict]:
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
    machines = lib.machines() if lib else []
    now = datetime.now().strftime("%Y-%m-%d")

    # ---- runs：只带新 run；parent/stage_seq 照画布（工具算好的值）
    run_rows, date_src, sample_src, stage_src = [], {}, {}, {}
    for rid in new:
        m = mods.get(rid) or {}
        parts = rid.rsplit("-", 2)
        stage = parts[1] if len(parts) == 3 else ""
        parent = m.get("core_parent_run_id") or ""
        # ① sample_id：模块上没有时，**继承 core 里该 run（或它的上游）的真实归属**
        sample = (m.get("core_sample_id") or m.get("sample_id") or "").strip()
        if sample:
            # **值正确时也要报来源**（否则 provenance={} 与"没报"无法区分 —— 数据线 2026-09-13 指出）
            sample_src[rid] = "模块自带（画布/导入包）"
        else:
            src = m.get("core_run_id") or rid
            inherit_from = src if core_facts(src).get("sample_id") else parent
            sample = (core_facts(src).get("sample_id") or core_facts(parent).get("sample_id") or "")
            sample_src[rid] = (f"继承自 core:{inherit_from}" if sample
                               else "**空缺：画布与 core 都没有该 run 的 sample_id**")
        # ② stage_seq：模块没给就取 core 里同 stage 的权威值；core 里也没这个 stage
        #    ⇒ 按工序序推算（同 stage 保持同号）**并写明来源**（不许留空、也不许静默填）
        if not m.get("core_stage_seq"):
            for r in read_core_table("runs"):
                if ((r.get("batch_id") or "").strip() == batch
                        and (r.get("stage") or "").strip() == stage
                        and (r.get("stage_seq") or "").strip()):
                    m["core_stage_seq"] = r["stage_seq"]
                    stage_src[rid] = f"取自 core 同 stage：{r.get('run_id')}"
                    break
            else:
                from .batch_runs import _stage_seq      # 延迟导入：避免模块级循环依赖
                hint = _stage_seq([x for x in (project.get("modules") or []) if isinstance(x, dict)],
                                  batch, stage)
                m["core_stage_seq"] = hint
                stage_src[rid] = (f"core 里还没有该 batch 的 {stage} ⇒ 按工序序推算 {hint}"
                                  "（**请核对**：stage_seq 各 batch 自定）")
        else:
            stage_src[rid] = "模块自带（画布/导入包）"
        # ③ date：**用画布上该 run 的计划日期**；缺失才退回今天，并标注来源
        date = (m.get("core_date") or "").strip()
        if date:
            date_src[rid] = "画布计划日期"
        else:
            date = now
            date_src[rid] = "**未设计划日期 ⇒ 退回导出当天，请核对**"
        # 机台口径与整包导出**同一处解析**（2026-09-14）：写库内显示名会把机台归属记错。
        tool_id, tool_name, _warn = resolve_tool(m, machines, STAGE_CODES)
        run_rows.append([rid, m.get("core_batch_id") or batch,
                         sample, stage,
                         m.get("core_stage_seq", ""), date,
                         "", "", tool_name, tool_id,
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
        "stage_seq_source": stage_src,
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
        # 机台口径 / 批次号告警（与整包导出同一处判定；空 = 无话说）
        "warnings": export_warnings(project, lib),
        "sample_id_source": sample_src, "date_source": date_src,
        "stage_seq_source": stage_src,
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
        "batch_id": batch, "source": "core (read-only)", "nodes": len(chain),
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
        "batch_id": batch, "source": "core (read-only)",
        "tree": [nodes[r] for r in sorted(roots)],
        "nodes": nodes, "count": len(nodes),
        "orphan_parent": dangling,
        "note": ("The number in a sample group (DIE4/DIE15) is the **count inside the group**, "
                 "not a die position; which die went where was not recorded ⇒ the tool does not infer it"),
    }


def _sample_nature(my_runs: list[dict]) -> str:
    """该样品上 run 的总体性质（**界面标签，英文**；`run_nature` 的键不动）。

    ⚠️ 2026-09-13 界面定全英文 ⇒ 这里返回的说明改成英文；键值仍是 trial/chain/batch_level。
    """
    if not my_runs:
        return "no runs"
    ns = {(r.get("run_nature") or "").strip() for r in my_runs}
    ns.discard("")
    if ns == {"trial"}:
        return "trial wafer"
    if ns == {"chain"}:
        return "chained sample"
    if ns and ns <= {"batch_level"}:
        return "batch level (whole/multi-die)"
    if ns:
        return "mixed (" + "/".join(sorted(ns)) + ")"
    return "untagged (parent inferred)"

# ---------------------------------------------------------------- core → 画布（回灌）
#: core 表 → 包内 CSV 名（只读，用来喂 parse_expack）
_PACK_OF_TABLE = {"runs": "runs.csv", "steps": "steps.csv", "measurements": "measurements.csv",
                  "observations": "observations.csv", "batches": "batches.csv",
                  "samples": "samples.csv", "recipes": "recipes.csv"}


def core_to_project(batch: str, project_name: str = "", lib=None,
                    include_measurements: bool = True) -> dict:
    """**从 core 只读回灌画布**：core → 临时包 → `parse_expack` → 画布项目 dict。

    为什么走"临时包"这一跳：
        `parse_expack` 已经有一整套成熟的 run→模块构造（建模块/匹配机台/灌参数/挂测量/拼备注）。
        从 core 直接另捏一套模块，**必然与"导入实验包"的产物漂移**（形状、键名、状态都可能不同）。
        生成同构的临时包再喂给它 ⇒ 两条路的产物**逐字一致**，且日后 parse_expack 升级自动受益。

    口径（数据线 2026-09-12 五条，逐条落实）：
        只读 core（不写任何数据资产）· `measurements.value` 空**不进画布**（不当 0）·
        `param_json` 原样 · `obs_type` 表外跳过（parse_expack 侧按词表）· **ID 全部照抄**。
    """
    import tempfile
    from pathlib import Path as _P

    rows = [r for r in read_core_table("runs") if (r.get("batch_id") or "").strip() == batch]
    if not rows:
        raise ValueError(f"core 里没有 batch「{batch}」的 run")
    runs_by_id = {(r.get("run_id") or "").strip(): r for r in rows}
    keep = set(runs_by_id)

    def _pick(table: str, key: str) -> list[dict]:
        return [r for r in read_core_table(table) if (r.get(key) or "").strip() in keep]

    tables = {
        "runs": rows,
        "steps": _pick("steps", "run_id"),
        "observations": _pick("observations", "run_id"),
        "batches": [r for r in read_core_table("batches")
                    if (r.get("batch_id") or "").strip() == batch],
        "samples": [r for r in read_core_table("samples")
                    if (r.get("batch_id") or "").strip() == batch],
        "recipes": _pick("recipes", "run_id"),
    }
    meas_all = _pick("measurements", "run_id")
    if include_measurements:                      # 空值=未测 ⇒ 不进画布（不当 0）
        tables["measurements"] = [r for r in meas_all if str(r.get("value", "")).strip() != ""]
    else:
        tables["measurements"] = []
    skipped_blank = len(meas_all) - len(tables["measurements"])

    # 写临时包（内存目录，用完即弃；只含 core 选中行）
    tmp = _P(tempfile.mkdtemp(prefix="core_to_canvas_"))
    pdir = tmp / batch
    pdir.mkdir(parents=True, exist_ok=True)
    for table, fname in _PACK_OF_TABLE.items():
        rs = tables.get(table, [])
        if not rs:
            continue
        hdr = list(rs[0].keys())
        with (pdir / fname).open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=hdr)
            w.writeheader()
            for r in rs:
                w.writerow({k: r.get(k, "") for k in hdr})
    (pdir / "manifest.json").write_text(json.dumps({
        "format": "opennano-expack", "version": "0.1", "batch_id": batch,
        "source": "core-readonly",                # 明示：这是回灌用的只读镜像，不是新数据
        "project": project_name or batch, "runs": len(rows),
        "note": "由 core 只读生成，仅供回灌画布；勿当作数据源",
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    from . import expack
    proj = expack.parse_expack(pdir, lib)
    proj["name"] = project_name or batch
    proj["core_batch_id"] = batch
    proj["_core_to_canvas"] = {
        "source": "core (read-only)", "batch_id": batch, "runs": len(rows),
        "steps": len(tables.get("steps", [])),
        "measurements": len(tables.get("measurements", [])),
        "measurements_blank_skipped": skipped_blank,
        "observations": len(tables.get("observations", [])),
        "samples": len(tables.get("samples", [])),
        "note": "空值测量未进画布（未测 ≠ 0）；ID 全部照抄；不写 core",
    }
    return proj


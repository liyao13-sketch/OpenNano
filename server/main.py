"""OpenNano FastAPI 服务:工艺引擎 + 组织记忆(知识库)。

启动: cd server && .venv/bin/uvicorn main:app --port 8000
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from engine import (CATEGORIES, CATEGORY_LABELS, PROCESSES, METROLOGY,
                    LibraryStore, formula_engine, generate_matrix,
                    module_catalog, build_module)
from engine import rules as rule_engine
from engine.process_catalog import family_for, family_label, FAMILY_LABELS
from kb.store import KBStore
from opennano_config import PROJECTS_DIR    # 用户数据目录的唯一来源（OPENNANO_PROJECTS_DIR 可覆盖）
from kb.ingest import ingest_all
from kb import core_source as core
from kb import expack as expack_engine

app = FastAPI(title="OpenNano Server", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])

# 全局单例(读 ~/.opennano/library.json 与 ~/.opennano/opennano.db)
LIB = LibraryStore()
KB = KBStore()
# （原 PROJECT_PATH = ~/.opennano/project.json 是**死代码**，全仓无引用 ⇒ 2026-09-13 删）


# ---------- 请求/响应模型 ----------
class ComputeReq(BaseModel):
    params: dict[str, float] = {}
    handed: dict[str, float] = {}       # 上游承接参数
    key_values: dict[str, float] = {}
    formulas: dict[str, str] = {}       # {输出参数: 表达式}
    context: dict = {}                  # 规则上下文(surface_film/process 等属性)


class DoeReq(BaseModel):
    variables: list[dict]               # [{param,min,max,step}]
    design_type: str = "full"           # full/partial/bbd/ccd
    center_points: int = 0
    n_runs: int | None = None
    randomize: bool = False
    alpha: float | None = None          # CCD 轴向距离(默认1=面心)


class ModuleReq(BaseModel):
    subtype: str
    name: str | None = None
    x: float = 0
    y: float = 0


class ProjectSaveReq(BaseModel):
    name: str = "Untitled"
    modules: list[dict] = []
    edges: list[dict] = []


class EntryReq(BaseModel):
    process_type: str
    title: str = ""
    equipment: dict = {}
    material: dict = {}
    parameters: dict = {}
    results: dict = {}
    source: str
    reliability_score: int | None = None   # v0.2:不再默认 4;缺省由 source_tier 分档决定(非 core 落 2)
    constraints: list = []
    tags: list = []
    extra_metadata: dict = {}              # v0.2:文献抽取元数据(citation/loc/gap/…)与 source_tier


# ---------- P1: 工艺引擎 ----------
@app.get("/api/catalog")
def api_catalog():
    """104 种工艺目录 + 模块目录(左栏)。"""
    return {
        "categories": [{"key": c, "label": CATEGORY_LABELS[c]} for c in CATEGORIES],
        "processes": [
            {"category": cat, "name": name, "param_key": sub,
             "family": family_for(name, cat), "family_label": family_label(family_for(name, cat)),
             "inputs": inputs, "outputs": outputs, "formulas": formulas}
            for cat, items in PROCESSES.items()
            for name, sub, inputs, outputs, formulas in items
        ],
        "metrology": [{"subtype": s, "name": n, "desc": d} for s, n, d in METROLOGY],
        "families": [{"key": k, "label": v} for k, v in FAMILY_LABELS.items()],
        "module_catalog": module_catalog(LIB),
    }


@app.get("/api/library")
def api_library():
    """设备库 + 参数注册表 + 依赖边 + 默认 + 材料属性 + 影响规则。"""
    d = LIB.data
    return {
        "categories": CATEGORIES,
        "equipment": d.get("equipment", {}),
        "params": d.get("params", {}),
        "param_links": d.get("param_links", []),
        "defaults": d.get("defaults", {}),
        "film_props": d.get("film_props", {}),
        "influence_rules": LIB.influence_rules(),
        "bias_table": rule_engine.bias_table(LIB.influence_rules()),
        "machines": LIB.machines(),
        "param_categories": LIB.param_categories(),
    }


@app.post("/api/modules/new")
def api_module_new(req: ModuleReq):
    """构造一个模块(自动加载默认设备参数模板+接口)。"""
    return build_module(req.subtype, LIB, req.name, req.x, req.y)


@app.post("/api/compute")
def api_compute(req: ComputeReq):
    """公式引擎:先按 formulas 计算输出,再应用全局影响规则(可覆盖)。"""
    mod = SimpleNamespace(params=req.params, key_values=dict(req.key_values),
                          formulas=req.formulas)
    formula_engine.compute_outputs(mod, req.handed)
    env = dict(req.params)
    env.update(req.handed)
    env.update(req.context)
    env.update(mod.key_values)
    allowed = set(req.params) | set(req.key_values) | set(req.formulas)
    rule_engine.compute_outputs(LIB.influence_rules(), env, mod.key_values, allowed)
    return {"key_values": mod.key_values}


@app.post("/api/doe")
def api_doe(req: DoeReq):
    return generate_matrix(req.variables, req.design_type, req.center_points,
                           req.n_runs, req.randomize, 42, req.alpha)


@app.post("/api/export")
def api_export(project: ProjectSaveReq):
    """导出:知识库数据(对齐现有表格式) + 当前画布项目 → 可下载 xlsx。"""
    from kb.export_xlsx import build_workbook
    entries = KB.list(limit=1_000_000)
    data = build_workbook(entries, project.model_dump())
    return _xlsx_response(data, "opennano_export")


class ExportDataReq(BaseModel):
    process_type: str | None = None
    material: str | None = None
    project: dict | None = None          # 可选:当前画布 {name, modules, edges}


class ExportDataReq(BaseModel):
    process_type: str | None = None      # 兼容旧参数(忽略)
    material: str | None = None
    project: dict | None = None


@app.post("/api/export/data")
def api_export_data(req: ExportDataReq):
    """数据导出:core 9 表全量 + 量名词 + 项目画布(权威源,协议 §11)。"""
    data = expack_engine.core_workbook(req.project)
    return _xlsx_response(data, "opennano_core")


# ---------- P0: 批次管理 · 续做 · 表单契约 · 菜单直读 ----------
class BatchListReq(BaseModel):
    """批次列表只需要画布模块。

    ⚠️ 这里曾经复用 `ExpackExportReq`（其必填字段是 `name`），而批次面板发的是
    `project_name` ⇒ **422**，面板的批次列表**整个打不开**（2026-09-13 由回归网查出）。
    入参模型必须与调用方实际发的字段对齐；多出来的字段直接忽略。
    """
    model_config = {"extra": "ignore"}
    project_name: str = ""
    modules: list[dict] = []
    edges: list[dict] = []
    purpose: str = ""
    operator: str = ""


class BatchRunsReq(BaseModel):
    modules: list[dict] = []
    batch_id: str


class RunContinueReq(BaseModel):
    project_name: str = ""
    batch_id: str
    stage: str
    modules: list[dict] = []
    edges: list[dict] = []
    parent_run_id: str = ""          # 空 = 取同 sample 的上一条（取不到才退回该 stage 最后一条）
    sample_id: str = ""              # 样品/die（并发分支下续做必须带，否则可能挂错分支）
    menu_group: int | None = None    # 给了就是用 group N 灌参
    menu_dir: str = ""
    title: str = ""
    date: str = ""
    persist: bool = False


class MenuScanReq(BaseModel):
    dir: str = ""
    tool: str = "RIE-400iPB"


class MenuGroupReq(BaseModel):
    group: int
    dir: str = ""
    tool: str = "RIE-400iPB"


class ProposalReq(BaseModel):
    tool: str = "RIE-400iPB"
    dir: str = ""                 # 给了就自动取其未映射列
    columns: list[str] = []
    samples: dict = {}


class ProposalVerifyReq(BaseModel):
    tool: str = "RIE-400iPB"


class AppendPackReq(BaseModel):
    project_name: str = ""
    modules: list[dict] = []
    edges: list[dict] = []
    purpose: str = ""
    operator: str = ""
    batch: str = ""
    save_dir: str = ""               # 非空 ⇒ 同时把 zip 落盘到该目录（便于交数据线验收）
    core_eq_state: list[dict] = []   # 面板填的"上机环境一行"（批次级）


class RehydrateReq(BaseModel):
    batch_id: str
    project_name: str = ""          # 空 = 用 batch_id
    include_measurements: bool = True
    persist: bool = False


class BatchEventsReq(BaseModel):
    batch_id: str


class ProposeReq(BaseModel):
    batch_id: str
    events: list[dict] = []          # [{kind,at,from_sample_id,to_sample_id,count,note,...}]
    operator: str = ""
    apply: bool = False              # 默认**干跑**；True 才落账（调数据线 propose_apply.py）


class MenuCheckReq(BaseModel):
    dir: str = ""          # 缺省 = <设备菜单>/<tool>
    tool: str = "RIE-400iPB"
    text: bool = False     # True = 附人读报告


@app.get("/api/form/contract")
def api_form_contract():
    """表单用枚举/键表（全部读自 schema 与受控词表，工具侧不另编一份）。"""
    from kb import form_contract as fc
    try:
        return fc.contract()
    except fc.ContractUnavailable as e:
        # 契约真源没装载 ⇒ 明说"怎么修"，而不是 500 或静默空表
        raise HTTPException(
            503, f"契约真源不可达：{e}（检查 OPENNANO_WORKSPACE / OPENNANO_DATA_ROOT）") from e
    except Exception as e:                       # noqa: BLE001
        raise HTTPException(500, f"读契约失败（schema/词表/解析器不可达）：{e}") from e


@app.post("/api/batch/list")
def api_batch_list(req: BatchListReq):
    """画布上的 batch 概览（含节点/连线数）。"""
    from kb import batch_runs as br
    mods = req.modules or []
    return {"batches": [{**b, "chain_nodes": b["runs"]} for b in br.batches_of(mods)]}


@app.post("/api/batch/runs")
def api_batch_runs(req: BatchRunsReq):
    """某 batch 的 run 链（parent 链 + 性质 + 并行分支）**＋样品继承树**（core 只读）。"""
    from kb import batch_runs as br, append_pack as ap
    res = br.chain_of(req.modules or [], req.batch_id)
    try:
        res["sample_tree"] = ap.sample_tree(req.batch_id)      # 契约 v0.1.4：samples.parent_sample_id
    except Exception as e:                                      # noqa: BLE001
        res["sample_tree"] = {"error": f"读样品树失败：{e}"}
    return res


@app.post("/api/run/continue")
def api_run_continue(req: RunContinueReq):
    """**续做**：算 run_id / parent_run_id / stage_seq，可选直接用 group N 灌参。

    - 序号由工具算（该 batch 该 stage 已有最大序号 +1），**禁手输**；stage_seq 沿用已入库值。
    - `menu_group` 给了 ⇒ 调共享解析器灌三段 recipe 的 steps（数据线权威口径）。
    - `persist=true` 时把新节点+连线写回工程文件（画布刷新即见）。
    """
    import copy
    import uuid as _uuid
    from kb import batch_runs as br
    mods = req.modules or []
    nxt = br.next_run(mods, req.batch_id, req.stage, req.parent_run_id or None,
                      sample_id=(req.sample_id or None))
    src = next((m for m in mods if m.get("core_run_id") == nxt["parent_run_id"]), None)

    menu_info = None
    steps: list[dict] = []
    if req.menu_group:
        from kb import menu_reader as mr
        export = req.menu_dir or str(mr.default_menu_dir() / "RIE-400iPB")
        try:
            g = mr.group_steps(int(req.menu_group), export)
        except Exception as e:                     # noqa: BLE001
            # 灌参失败**必须报错**（静默给空 steps 会让人以为"这配方就是空的"）；
            # 目录不存在 / 槽没配方都是用户可修的输入问题 ⇒ 400 而不是 500。
            raise HTTPException(400, f"菜单灌参失败：{e}") from e
        steps = g["steps"]
        menu_info = {"group": g["group"], "group_seq": g["group_seq"],
                     "segments": g["segments"], "total_steps": g["total_steps"],
                     "defined_total": g["defined_total"], "skipped_slots": g["skipped_slots"],
                     "recipe_id": f"RCP-400iPB-G{int(req.menu_group):03d}", "dir": export}

    base = copy.deepcopy(src) if src else {}
    new_mod = {
        **{k: v for k, v in base.items()
           if k not in ("core_run_id", "core_parent_run_id", "core_recipe_id",
                        "key_values", "sim_result", "core_date", "id")},
        # ⚠️ 新 run 的**结果**字段必须"存在但为空"：
        #    以前是把 key_values/sim_result 整个删掉 ⇒ 画布模块缺字段 ⇒
        #    前端面板 `m.key_values[k]` 抛错、**整屏变白**（2026-09-13 owner"点 DRIE 什么都不见了"）。
        #    语义不变（不继承上游结果），但形状完整。
        "key_values": {},
        "sim_result": None,
        # 结构性字段一律给默认值，保证任何来源的模块形状一致（包/core 来的模块也可能缺）
        "params": base.get("params") or {},
        "param_defs": base.get("param_defs") or {},
        "param_inputs": base.get("param_inputs") or [],
        "param_outputs": base.get("param_outputs") or [],
        "formulas": base.get("formulas") or {},
        "material": base.get("material") or {},
        "annotations": base.get("annotations") or [],
        "id": f"md_{_uuid.uuid4().hex[:8]}",
        "name": (req.title or (base.get("name") or "") or f"{req.stage} 续做"),
        "x": float(base.get("x") or 0) + 260,
        "y": float(base.get("y") or 0),
        "core_run_id": nxt["run_id"],
        "core_parent_run_id": nxt["parent_run_id"],
        "core_batch_id": nxt["batch_id"],
        "core_stage": nxt["stage"],
        "core_stage_seq": nxt["stage_seq"],
        "core_sample_id": nxt.get("sample_id") or "",
        "core_date": req.date or "",
        "run_state": "planned",
        "annotations": [],
    }
    if menu_info:
        new_mod["core_recipe_id"] = menu_info["recipe_id"]
        new_mod["core_menu_steps"] = steps          # 灌入的 steps（与 CSV 同口径）

    edge = ({"src": src["id"], "dst": new_mod["id"]} if src else None)
    project = {"name": req.project_name or req.batch_id,
               "modules": mods + [new_mod],
               "edges": (req.edges or []) + ([edge] if edge else [])}
    if req.persist:
        p = _project_path(project["name"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")

    issues: list[str] = []
    if steps:
        from kb import form_contract as fc
        issues = fc.check_steps(steps, strict=True, stage=nxt["stage"])
    return {"run": nxt, "module": new_mod, "edge": edge, "menu": menu_info,
            "project": project, "issues": issues,
            "saved": bool(req.persist),
            "summary": (f"{nxt['run_id']}（parent={nxt['parent_run_id'] or '—'} · "
                        f"stage_seq={nxt['stage_seq']}）"
                        + (f" · 灌入 {len(steps)} 步" if steps else ""))}


@app.get("/api/menu/zones")
def api_menu_zones():
    from kb import menu_reader as mr
    return {"zones": mr.slot_zones(), "scope_max": mr.SCOPE_MAX,
            "default_dir": str(mr.default_menu_dir()), "pair_tol_min": mr.PAIR_TOL_MIN}


@app.post("/api/menu/scan")
def api_menu_scan(req: MenuScanReq):
    """解析一个设备菜单导出目录（.grp/.rcp）→ 预览（不写任何文件）。"""
    from kb import menu_reader as mr
    d = req.dir or str(mr.default_menu_dir() / req.tool)
    try:
        return mr.load_menu(d)
    except (FileNotFoundError, mr.MenuParserUnavailable) as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/menu/group")
def api_menu_group(req: MenuGroupReq):
    """取 group N 的三段步骤（**预览**；实际灌参走 /api/run/continue）。"""
    from kb import menu_reader as mr
    d = req.dir or str(mr.default_menu_dir() / req.tool)
    try:
        return mr.group_steps(int(req.group), d)
    except (FileNotFoundError, mr.MenuParserUnavailable, ValueError) as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/menu/check")
def api_menu_check(req: MenuCheckReq):
    """**批量菜单体检**：扫该机台下所有导出，出可用性报告（只读，不写任何文件）。

    回答：配对可靠吗 / 列认全了吗 / 配方是空壳吗 / 越界了吗 / 与上次 dump 漂移了吗。
    """
    from kb import menu_checker as mc, menu_reader as mr
    root = Path(req.dir).expanduser() if req.dir else (mr.default_menu_dir() / req.tool)
    try:
        res = mc.check_tree(root)
    except Exception as e:                       # noqa: BLE001
        raise HTTPException(400, f"体检失败：{e}") from e
    if req.text:
        res["text"] = mc.report_text(res)
    return res


@app.get("/api/adapter/proposals")
def api_adapter_proposals():
    """列出已落盘的**提案**（在 kb/adapters/proposed/，不生效）。"""
    from kb import adapter_proposal as ap
    return {"dir": str(ap.PROPOSED_DIR), "proposals": ap.list_proposals()}


@app.post("/api/adapter/propose")
def api_adapter_propose(req: ProposalReq):
    """**LLM 映射助手**：未映射列 → 规范键候选（只落提案文件，不生效）。

    - 只把**列名 + 少量样例值**交给 LLM（绝不整份文件）。
    - 输出经白名单强校验（不在规范键表里的建议作废并留痕）。
    - `needs_human=true` 的条目（气路归属/未知语义）必须人工裁决。
    """
    from kb import adapter_proposal as ap, menu_checker as mc, menu_reader as mr
    cols = req.columns
    if not cols and req.dir:
        root = Path(req.dir).expanduser()
        try:
            res = mc.check_dump(root)
            cols = res.get("step_columns", {}).get("unmapped") or []
        except Exception as e:                     # noqa: BLE001
            raise HTTPException(400, f"取未映射列失败：{e}") from e
    if not cols:
        return {"skipped": True, "note": "没有未映射列（列名已全覆盖）"}
    try:
        res = ap.propose_mappings(req.tool, cols, req.samples)
    except Exception as e:                         # noqa: BLE001
        raise HTTPException(500, f"提案失败：{e}") from e
    return res


@app.post("/api/adapter/verify")
def api_adapter_verify(req: ProposalVerifyReq):
    """自证：提案里的映射能否把未映射列清零（只比名字，不碰数值）。"""
    from kb import adapter_proposal as ap
    prop = ap.load_proposal(req.tool)
    if not prop:
        raise HTTPException(404, f"没有 {req.tool} 的提案")
    return ap.verify_mapping(prop)


@app.post("/api/expack/append")
def api_expack_append(req: AppendPackReq):
    """**追加包**（`source=tool-append`）：只含尚未入 core 的 run。

    为什么必须单独有这个出口（数据线 2026-09-12 复核）：
    `AR50-T1` 这类镜像包 `manifest.source == "core-slice"`，`datasets_folder.discover()`
    **按设计整包跳过**（防自噬）⇒ 只往镜像包里加 run 再导出，落库时整包被丢。
    追加包不是 core-slice ⇒ 正常入库，且既有源优先、老行不会被覆盖。
    """
    from fastapi import Response as _R
    from urllib.parse import quote as _q
    from kb import append_pack as ap
    # 新 run 的 note 不该继承上游 run 的长备注（那是上一炉的结论），只留本 run 自己的
    mods = []
    for m in req.modules:
        m = dict(m)
        if m.get("core_parent_run_id") and (m.get("comment") or "").count("【OBS-") >= 2:
            m["comment"] = ""
        mods.append(m)
    proj = {"name": req.project_name, "modules": mods, "edges": req.edges,
            "core_eq_state": req.core_eq_state}
    blob, info = ap.build_append_pack(proj, purpose=req.purpose,
                                      operator=req.operator, batch=req.batch)
    if blob is None:
        return info                                   # 没有新 run：回 JSON 说明，不产空包
    name = f"{info['batch_id']}_append.zip"
    if req.save_dir:
        # 落盘到用户**显式指定**的目录（工具不擅自写数据资产区）
        try:
            d = Path(req.save_dir).expanduser()
            d.mkdir(parents=True, exist_ok=True)
            (d / name).write_bytes(blob)
            info["saved_to"] = str(d / name)
        except OSError as e:
            info["save_error"] = f"落盘失败（仍可下载）：{e}"
    return _R(content=blob, media_type="application/zip",
              headers={"Content-Disposition":
                       f"attachment; filename=append_pack.zip; "
                       f"filename*=UTF-8''{_q(name)}",
                       "X-Append-Info": _q(json.dumps(info, ensure_ascii=False))})


@app.post("/api/expack/append/preview")
def api_expack_append_preview(req: AppendPackReq):
    """预览：哪些 run 会被当成"新增行"打进追加包（只读 core 比主键）。"""
    from kb import append_pack as ap
    proj = {"name": req.project_name, "modules": req.modules, "edges": req.edges}
    new = ap.new_runs_of(proj)
    have = ap.core_run_ids()
    return {"core_runs": len(have), "new_runs": new, "count": len(new),
            "would_skip_in_core_slice": True,
            "note": ("这些 run 不在 core 里 ⇒ 会进追加包；其余 run 已在 core ⇒ 不会重复写。"
                     "追加包 source=tool-append（不是 core-slice）⇒ 不会被 discover() 跳过。")}


@app.post("/api/batch/rehydrate")
def api_batch_rehydrate(req: RehydrateReq):
    """**从 core 只读回灌画布**：core → 临时包 → parse_expack → 画布项目。

    用途：接着做（PECVD→…→run1 已入库，从权威源起步，不依赖那个镜像包）。
    口径：只读 core · 空值测量不进画布（未测≠0）· ID 全照抄 · 不写任何数据资产。
    """
    from kb import append_pack as ap
    try:
        proj = ap.core_to_project(req.batch_id, req.project_name or req.batch_id, lib=LIB,
                                  include_measurements=req.include_measurements)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
    if req.persist:
        p = _project_path(proj["name"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(proj, ensure_ascii=False, indent=2), encoding="utf-8")
        proj["_saved_to"] = str(p)
    return proj


@app.post("/api/batch/events")
def api_batch_events(req: BatchEventsReq):
    """批次事件（裂片/取样分配）**只读**视图 + 计划/实际比数。

    `split`=物理裂片（AR50-T1 只 1 条×49）· `allocate`=取样分配（3 条 4/15/1）；
    实际用量只算 allocate —— 混为一谈会让比数失真（数据线 2026-09-13 纠正）。
    """
    from kb import batch_events as be
    evs = be.read_events(req.batch_id)
    return {"batch_id": req.batch_id, "events": evs, "count": len(evs),
            "plan_vs_actual": be.plan_vs_actual(req.batch_id),
            "consistency": be.consistency_preview(req.batch_id),
            "ledger": str(be.events_path())}


@app.get("/api/machine/defaults")
def api_machine_defaults(stage: str = ""):
    """各机台的**实测默认参数**（只读 core 推出来；按段 chuck/etch/dechuck 分开）。

    用途：画布节点"套用机台实测值"，以及回答"我们这台机器平时到底用什么参数"。
    """
    from kb import machine_defaults as md
    return md.machine_defaults(stage)


@app.post("/api/layout/arrange")
def api_layout_arrange(req: BatchListReq):
    """**一键自动整理画布**：只用画布已有信息重排坐标（不动边、不动标注）。

    给"方块叠在一起"用：布局算法与 `relayout`/回灌**共用同一份**（`expack._layout_modules`），
    所以不会出现"第二套排布规则"。
    """
    from kb.arrange import arrange_project
    proj = {"modules": req.modules or [], "edges": req.edges or []}
    res = arrange_project(proj)
    return {"ok": res.get("ok", False), "modules": proj["modules"],
            "summary": res, "note": "只改了 x/y；边与标注原样未动"}


@app.post("/api/batch/tune_line")
def api_batch_tune_line(req: BatchRunsReq):
    """**参数调试线**（O1 单点优化视图）—— 只读数据线的 `v_tune_line`。

    返回按 `tune_id` 分组的步序列（参数轴 + 响应列），缺的格就是 NULL（不补值）；
    视图/库不存在 ⇒ `available=false` + 怎么修，不给 500。
    """
    from kb import tune_line as tl
    return tl.tune_lines(req.batch_id)


@app.post("/api/batch/propose")
def api_batch_propose(req: ProposeReq):
    """工具产出**提案**（裂片/取样）→ 本地预检 → 交数据线 `propose_apply.py`（默认干跑）。

    **工具不写 core、也不直接写台账**：`apply=false` 时只让对方脚本干跑；
    `apply=true` 才真正落账（由数据线的脚本执行，含幂等与不推断校验）。
    """
    from kb import batch_events as be
    prop = be.build_proposal(req.batch_id, req.events, operator=req.operator)
    chk = be.precheck(prop)
    out = {"proposal": prop, "precheck": chk}
    if chk["ok"] or req.apply:           # 预检不过时默认不惊动对方脚本
        out["run"] = be.run_proposer(prop, apply=req.apply)
    else:
        out["run"] = {"ok": False, "skipped": "本地预检未过 ⇒ 未调用数据线脚本"}
    return out


class ExpackExportReq(BaseModel):
    name: str = "EXP"
    core_eq_state: list[dict] = []      # 面板填的"上机环境一行"（批次级）
    modules: list[dict] = []
    edges: list[dict] = []
    purpose: str = ""
    operator: str = ""


@app.post("/api/expack/export")
def api_expack_export(req: ExpackExportReq):
    """画布流程 → 实验数据包 zip(core 列格式,measurements 为待填模板 + 人读流程卡 md)。"""
    from fastapi import Response as _R
    data, batch = expack_engine.build_expack(
        {"name": req.name, "modules": req.modules, "edges": req.edges,
         "core_eq_state": req.core_eq_state},
        purpose=req.purpose, operator=req.operator, lib=LIB)
    from urllib.parse import quote as _q
    return _R(content=data, media_type="application/zip",
              headers={"Content-Disposition":
                       f"attachment; filename=experiment_package.zip; "
                       f"filename*=UTF-8''{_q(batch + '.zip')}"})


@app.post("/api/expack/card")
def api_expack_card(req: ExpackExportReq):
    """画布流程 → 单独下载「实验流程卡」Markdown(人读,与包内那张同一份)。"""
    from fastapi import Response as _R
    from urllib.parse import quote as _q
    md = expack_engine.build_process_card(
        {"name": req.name, "modules": req.modules, "edges": req.edges},
        purpose=req.purpose, operator=req.operator, lib=LIB)
    return _R(content=md.encode(), media_type="text/markdown; charset=utf-8",
              headers={"Content-Disposition":
                       f"attachment; filename=process_card.md; "
                       f"filename*=UTF-8''{_q('流程_' + req.name + '.md')}"})


class ExpackImportReq(BaseModel):
    path: str


@app.post("/api/expack/import")
def api_expack_import(req: ExpackImportReq):
    """实验数据包(文件夹或 zip) → 画布项目(节点=run,参数=steps,输出=measurements,备注=现象)。"""
    from pathlib import Path as _P
    p_ = _P(req.path.strip()).expanduser()
    if not p_.exists():
        raise HTTPException(404, f"路径不存在: {p_}")
    return expack_engine.parse_expack(p_, LIB)


def _xlsx_response(data: bytes, stem: str):
    """xlsx 字节 → 下载响应(文件名带日期)。"""
    from datetime import datetime
    from fastapi import Response as _R
    fname = f"{stem}_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    return _R(content=data,
              media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
              headers={"Content-Disposition": f'attachment; filename="{fname}"'})


# ---------- P1: 工程(画布) · 多项目 ----------


def _project_path(name: str) -> Path:
    import re
    safe = re.sub(r"[^\w\u4e00-\u9fff.-]", "_", (name or "Untitled").strip()) or "Untitled"
    return PROJECTS_DIR / f"{safe}.json"


@app.get("/api/project")
def api_project_load(name: str | None = None):
    """载入项目;不指定 name 时取最近修改的一个。"""
    if name:
        p = _project_path(name)
        if not p.exists():
            raise HTTPException(404, f"project not found: {name}")
        return json.loads(p.read_text(encoding="utf-8"))
    if not PROJECTS_DIR.exists() or not list(PROJECTS_DIR.glob("*.json")):
        return {"name": "未命名项目", "modules": [], "edges": []}
    latest = max(PROJECTS_DIR.glob("*.json"), key=lambda f: f.stat().st_mtime)
    return json.loads(latest.read_text(encoding="utf-8"))


@app.get("/api/project/list")
def api_project_list():
    from datetime import datetime
    if not PROJECTS_DIR.exists():
        return {"projects": []}
    out = []
    for f in sorted(PROJECTS_DIR.glob("*.json"),
                    key=lambda f: -f.stat().st_mtime):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            out.append({"name": d.get("name", f.stem),
                        "modules": len(d.get("modules", [])),
                        "edges": len(d.get("edges", [])),
                        "saved_at": datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds")})
        except Exception:  # noqa: BLE001
            continue
    return {"projects": out}


@app.post("/api/project/save")
def api_project_save(req: ProjectSaveReq):
    p = _project_path(req.name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(req.model_dump_json(indent=2), encoding="utf-8")
    return {"saved": str(p), "name": req.name, "modules": len(req.modules)}


@app.delete("/api/project/{name}")
def api_project_delete(name: str):
    p = _project_path(name)
    if p.exists():
        p.unlink()
    return {"ok": True}


# ---------- 配置打包:导出/导入(换机/备份/分享) ----------
@app.get("/api/config/export")
def api_config_export():
    """全部配置打包:设备库+参数+影响规则+默认 + 知识库条目。"""
    from datetime import datetime
    return {
        "kind": "opennano-config", "version": 1,
        "exported_at": datetime.now().isoformat(timespec="seconds"),
        "library": LIB.data,
        "kb_entries": KB.list(limit=1_000_000),
    }


class ConfigImportReq(BaseModel):
    library: dict | None = None
    kb_entries: list[dict] | None = None


@app.post("/api/config/import")
def api_config_import(req: ConfigImportReq):
    """导入配置。library 先自动备份原文件再整体替换;KB 条目按 source 幂等合并。"""
    from datetime import datetime
    import shutil
    result = {"library": "skipped", "kb_added": 0, "kb_updated": 0, "backup": None}
    if req.library:
        src = LIB.path
        if src.exists():
            bak = src.with_suffix(f".backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json")
            shutil.copy2(src, bak)
            result["backup"] = str(bak)
        LIB.data.clear()
        LIB.data.update(req.library)
        LIB._save()
        result["library"] = "replaced"
    if req.kb_entries:
        for e in req.kb_entries:
            _, created = KB.upsert(e)
            result["kb_added" if created else "kb_updated"] += 1
    return result


# ---------- P2: 组织记忆 ----------
@app.get("/api/kb")
def api_kb(process_type: str | None = None, material: str | None = None,
           min_reliability: int | None = None, q: str | None = None,
           limit: int = 200):
    return KB.list(process_type=process_type, material=material,
                   min_reliability=min_reliability, q=q, limit=limit)


@app.post("/api/kb")
def api_kb_add(req: EntryReq):
    obj, created = KB.upsert(req.model_dump())
    return {"created": created, "entry": obj.to_dict()}


@app.post("/api/kb/ingest")
def api_kb_ingest():
    """已停用:按数据域协议 §11,KB 不得存原始数值副本(core 才是权威源)。"""
    return {"ok": False, "added": 0, "updated": 0, "total": KB.stats()["total"],
            "message": "原始数据请走数据线 core(18_工艺数据资产/03_实验数据/ingest/build_core.py);"
                       "KB 只存结论与影响规则。查询数据用 /api/core/* 或工具 query_core。",
            "core": core.stats()["counts"]}


class IngestUploadReq(BaseModel):
    filename: str
    content_b64: str                  # xlsx 文件的 base64
    process_type: str = "RIE_Cl"
    material: str = ""
    dry_run: bool = False             # True=只预检(识别列/行数),不写库


@app.post("/api/kb/ingest_upload")
def api_kb_ingest_upload(req: IngestUploadReq):
    """上传 Excel(执行表/数据收集表) → 预检或导入知识库。

    列自动识别:参数列(Cl2/Power/…)进配方,其余数值列全部进结果;
    未知列保留原表头。source=原文件名::Run编号,重复上传按 source 幂等更新。
    """
    import base64
    import tempfile
    from pathlib import Path as _P
    from kb.ingest import ingest_xlsx, xlsx_columns

    try:
        raw = base64.b64decode(req.content_b64)
    except Exception:
        raise HTTPException(422, "content_b64 解码失败")
    tmp_dir = _P(tempfile.mkdtemp(prefix="opennano_up_"))
    tmp = tmp_dir / (req.filename or "upload.xlsx")
    tmp.write_bytes(raw)

    if req.dry_run:
        info = xlsx_columns(tmp)
        info.update({"filename": req.filename, "process_type": req.process_type})
        return {"dry_run": True, **info}

    from kb.ingest import load_core_index
    load_core_index()          # 可信度按 core verification 派生
    entries = ingest_xlsx(tmp, req.process_type, "uploaded",
                          material=req.material or None,
                          source_name=req.filename)
    draft = [{"run_id": f"NEW-{i+1:04d}", "quantity": k, "value": v, "unit": "",
              "method": "", "verification": "未核实", "note": "未落库草稿"}
             for i, e in enumerate(entries) for k, v in (e.get("results") or {}).items()]
    return {"dry_run": False, "filename": req.filename, "added": 0, "updated": 0,
            "entries": len(entries), "total": KB.stats()["total"],
            "result_keys": sorted({k for e in entries for k in e["results"]}),
            "draft_measurements": draft[:50],
            "message": "已停用写入 KB(协议 §11):原始数据应落数据线 core。"
                       "上式 draft_measurements 为按 core measurement 结构解析的草稿,"
                       "请交《数据》会话走 build_core.py 落库。"}


# ---------- 数据域 core（只读·权威源；协议 §11） ----------
@app.get("/api/core/stats")
def api_core_stats():
    return core.stats()


@app.get("/api/core/runs")
def api_core_runs(stage: str | None = None, tool_id: str | None = None,
                  batch_id: str | None = None, limit: int = 200):
    return {"runs": core.runs(stage=stage, tool_id=tool_id, batch_id=batch_id, limit=limit)}


@app.get("/api/core/run/{run_id}")
def api_core_run(run_id: str):
    d = core.run_detail(run_id)
    if "error" in d:
        raise HTTPException(404, d["error"])
    return d


@app.get("/api/core/quantities")
def api_core_quantities():
    return {"quantities": core.quantities(), "tools": core.tools(),
            "stages": sorted(core.STAGES)}


@app.get("/api/core/wide")
def api_core_wide(quantity: str | None = None, stage: str | None = None,
                  tool_id: str | None = None, limit: int = 300):
    return core.runs_wide(quantity=quantity, stage=stage, tool_id=tool_id, limit=limit)


@app.get("/api/kb/stats")
def api_kb_stats():
    return KB.stats()


@app.get("/api/kb/fields")
def api_kb_fields(process_type: str | None = None, material: str | None = None):
    """知识库里出现过的结果字段(含条数/中文名/单位)——供优化目标下拉动态列出。"""
    from collections import Counter
    from kb.result_fields import field_meta
    from kb.param_map import param_for_field
    entries = KB.list(process_type=process_type, material=material, limit=100000)
    c: Counter = Counter()
    for e in entries:
        c.update((e.get("results") or {}).keys())
    return {"total": len(entries),
            "fields": [{"field": k, "count": n, "param": param_for_field(k),
                        **field_meta(k)} for k, n in c.most_common()]}


# ---------- 流程运行引擎(对标 BEAMER 的 Run / Run To) ----------
class FlowRunReq(BaseModel):
    modules: list[dict] = []
    edges: list[dict] = []
    until: str | None = None          # Run To:运行到该模块为止
    context: dict = {}                # 全局兜底上下文(如 surface_film)


def _upstream_film(mid: str, mods: dict, incoming: dict, depth: int = 0) -> str:
    """沿上游找最近的输出膜层(自身是沉积且有材料则用自身)。"""
    if depth > 16:
        return ""
    m = mods.get(mid) or {}
    mat = m.get("material") or {}
    if m.get("family") == "dep" and mat.get("film"):
        return str(mat["film"])
    for s in incoming.get(mid, []):
        f = _upstream_film(s, mods, incoming, depth + 1)
        if f:
            return f
    return ""


@app.post("/api/flow/run")
def api_flow_run(req: FlowRunReq):
    """按拓扑序运行整个流程(或运行到 until 为止)。

    逐节点:承接上游输出 → 设备公式 → 全局影响规则(可带 surface_film/process/machine 上下文);
    跳过 disabled 的节点与 disabled 的连线;返回每节点结果、日志与错误(对应 BEAMER 的
    Run / Run To / Reset 语义)。
    """
    mods = {m.get("id"): m for m in req.modules if m.get("id")}
    incoming: dict[str, list[str]] = {mid: [] for mid in mods}
    outgoing: dict[str, list[str]] = {mid: [] for mid in mods}
    for e in req.edges:
        s_ = e.get("src") or e.get("source")
        d_ = e.get("dst") or e.get("target")
        if s_ in mods and d_ in mods and not e.get("disabled"):
            incoming[d_].append(s_)
            outgoing[s_].append(d_)

    indeg = {mid: len(incoming[mid]) for mid in mods}
    queue = [mid for mid in mods if indeg[mid] == 0]
    order: list[str] = []
    while queue:
        n = queue.pop(0)
        order.append(n)
        for d_ in outgoing[n]:
            indeg[d_] -= 1
            if indeg[d_] == 0:
                queue.append(d_)
    cyclic = [mid for mid in mods if mid not in order]

    rules = LIB.influence_rules()
    results: dict[str, dict] = {}
    log: list[dict] = []
    errors: list[dict] = []

    def run_one(mid: str):
        m = mods[mid]
        name = m.get("name") or mid
        if m.get("disabled"):
            log.append({"module": mid, "name": name, "status": "skipped",
                        "reason": "模块已禁用"})
            return
        params = m.get("params") or {}
        handed: dict = {}
        for s_ in incoming.get(mid, []):
            kv = results.get(s_) or {}
            for k in (m.get("param_inputs") or []):
                if kv.get(k) is not None:
                    handed[k] = kv[k]
        ctx = dict(req.context or {})
        ctx["process"] = m.get("equipment_name") or name
        ctx["machine"] = m.get("machine_name") or ""
        film = _upstream_film(mid, mods, incoming)
        if film:
            ctx["surface_film"] = film
        dummy = SimpleNamespace(params=params,
                                key_values=dict(m.get("key_values") or {}),
                                formulas=m.get("formulas") or {})
        before = dict(dummy.key_values)
        try:
            formula_engine.compute_outputs(dummy, handed)
            env = dict(params); env.update(handed); env.update(ctx); env.update(dummy.key_values)
            allowed = set(m.get("param_outputs") or []) | set(params) | set(before)
            rule_engine.compute_outputs(rules, env, dummy.key_values, allowed)
        except Exception as e:  # noqa: BLE001
            errors.append({"module": mid, "name": name, "error": f"{type(e).__name__}: {e}"})
            log.append({"module": mid, "name": name, "status": "error",
                        "reason": str(e)[:120]})
            return
        results[mid] = dict(dummy.key_values)
        changed = {k: v for k, v in dummy.key_values.items() if before.get(k) != v}
        log.append({"module": mid, "name": name, "status": "ok",
                    "outputs": dict(dummy.key_values),
                    "changed": changed,
                    "incoming_params": handed})

    for mid in order:
        run_one(mid)
        if req.until and mid == req.until:
            log.append({"module": None, "name": "Run To", "status": "stopped",
                        "reason": "已运行到指定模块"})
            break

    return {"ok": not errors, "order": order, "results": results, "log": log,
            "errors": errors, "cyclic": [mods[c].get("name") or c for c in cyclic],
            "ran": len(results), "skipped": len([l for l in log if l["status"] == "skipped"])}


# ---------- O1 单点优化引擎(GPR + BO + 可视化) ----------
from fastapi import Response as FResponse  # noqa: E402

from opt import MODEL_REGISTRY as MODELS  # noqa: E402
from opt import bo as bo_engine  # noqa: E402
from opt import gp as gp_engine  # noqa: E402
from opt import plots as plot_engine  # noqa: E402


class OptFitReq(BaseModel):
    source: str = "core"           # core(权威,默认) | kb(旧结论库路径)
    quantity: str | None = None    # core 量名词(如 depth_center_nm)
    stage: str | None = None       # core stage(如 RIE)
    tool_id: str | None = None     # core tool_id(如 RIE200NL)
    process_type: str = "RIE_Cl"
    material: str | None = None
    target: str = "er_nm_min"
    step: str | None = "ME"        # 用哪一步的参数作特征(None=全部步)
    features: list[str] | None = None


class OptSuggestReq(BaseModel):
    model_id: str
    mode: str = "max"              # max / min / target
    target_value: float | None = None
    bounds: dict[str, list] | None = None   # {特征: [lo,hi]},缺省用训练范围±10%
    n: int = 5


@app.post("/api/opt/fit")
def api_opt_fit(req: OptFitReq):
    """从 KB 取数 → GPR 拟合 → 返回模型摘要(cv_r2 等)。"""
    import uuid
    if req.source == "core":
        q = req.quantity or req.target
        ds = core.dataset(q, stage=req.stage, tool_id=req.tool_id, features=req.features)
    else:
        entries = KB.list(process_type=req.process_type, material=req.material,
                          limit=100000)
        ds = gp_engine.build_dataset(entries, req.target, req.features, req.step)
    if ds["X"] is None or len(ds["y"]) == 0:
        raise HTTPException(404, f"无可用样本(target={req.target}, "
                                 f"process_type={req.process_type})")
    res = gp_engine.fit(ds)
    if not res.get("ok"):
        raise HTTPException(422, res.get("error", "拟合失败"))
    mid = f"opt-{uuid.uuid4().hex[:8]}"
    MODELS[mid] = {"model": res["model"], "X": ds["X"], "y": ds["y"],
                   "features": ds["feature_names"],
                   "target": (req.quantity or req.target), "source": req.source,
                   "meta": {k: v for k, v in res.items() if k != "model"}}
    return {"model_id": mid, "n": res["n"], "features": ds["feature_names"],
            "cv_r2": res["cv_r2"], "train_r2": res["train_r2"],
            "fallback_linear": res["fallback_linear"],
            "skipped": ds["skipped"], "target": req.target}


@app.get("/api/opt/models")
def api_opt_models():
    return {"models": [{"model_id": k, "target": v["target"], "n": len(v["y"]),
                        "cv_r2": v["meta"].get("cv_r2"),
                        "features": v["features"]} for k, v in MODELS.items()]}


@app.post("/api/opt/suggest")
def api_opt_suggest(req: OptSuggestReq):
    """BO(EI)下一轮实验建议。"""
    m = MODELS.get(req.model_id)
    if not m:
        raise HTTPException(404, "model not found(先 /api/opt/fit)")
    if req.mode == "target" and req.target_value is None:
        raise HTTPException(422, "mode=target 需要 target_value")
    X = m["X"]
    bounds = req.bounds or {f: [float(X[:, i].min()) * 0.9, float(X[:, i].max()) * 1.1]
                            for i, f in enumerate(m["features"])}
    bounds = {k: tuple(v) for k, v in bounds.items()}
    out = bo_engine.suggest(m["model"], m["features"], bounds, req.mode,
                            req.target_value, req.n, X_train=m["X"])
    if not out.get("ok"):
        raise HTTPException(422, out.get("error", "建议失败"))
    return out


@app.get("/api/opt/plot")
def api_opt_plot(model_id: str, kind: str = "contour",
                 x: str | None = None, y: str | None = None):
    """响应面等高线 / 主效应图 → PNG。"""
    m = MODELS.get(model_id)
    if not m:
        raise HTTPException(404, "model not found")
    try:
        if kind == "main":
            png = plot_engine.main_effects(m["model"], m["features"], m["X"],
                                           m["target"])
        else:
            png = plot_engine.contour(m["model"], m["features"], m["X"],
                                      m["target"], x, y)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(500, f"绘图失败: {e}")
    return FResponse(content=png, media_type="image/png")


@app.get("/api/health")
def health():
    return {"ok": True, "service": "opennano", "version": "0.1.0"}


# ---------- P3: Agent + RAG ----------
class ChatReq(BaseModel):
    message: str
    history: list[dict] = []   # [{role, content}]


@app.post("/api/agent/chat")
def api_agent_chat(req: ChatReq):
    from agent.orchestrator import run
    return run(KB, LIB, req.message, req.history)


# ---------- 设备应用(切换工艺,返回带 family 的解析结果) ----------
class ApplyEqReq(BaseModel):
    subtype: str
    equipment_id: str  # "" = 内置默认


@app.post("/api/modules/apply_equipment")
def api_apply_equipment(req: ApplyEqReq):
    from engine.process_catalog import family_for, family_label
    cat = req.subtype
    if not req.equipment_id:
        nm = CATEGORY_LABELS.get(cat, cat)
        fam = family_for(nm, cat)
        return {"equipment_id": "", "equipment_name": "", "family": fam,
                "family_label": family_label(fam), "param_defs": defaults_for(cat),
                "param_inputs": [], "param_outputs": [], "formulas": {}}
    eq = LIB.get_equipment(req.equipment_id)
    if not eq:
        raise HTTPException(404, "equipment not found")
    fam = family_for(eq.get("name", ""), cat)
    return {"equipment_id": req.equipment_id, "equipment_name": eq.get("name", ""),
            "family": fam, "family_label": family_label(fam),
            "param_defs": eq.get("params", {}), "param_inputs": eq.get("inputs", []),
            "param_outputs": eq.get("outputs", []), "formulas": eq.get("formulas", {})}


# ---------- 库管理(设备/参数/材料属性/默认) ----------
class EqAddReq(BaseModel):
    category: str
    name: str


class DefaultReq(BaseModel):
    category: str
    equipment_id: str | None = None


class ParamReq(BaseModel):
    name: str
    unit: str = ""
    category: str = ""
    scope: str | None = None          # global / process:<模板id> / machine:<机台id>


class FilmPropsReq(BaseModel):
    props: dict


class ParamLinksReq(BaseModel):
    links: list[dict]


@app.post("/api/library/equipment/add")
def api_eq_add(req: EqAddReq):
    eid = LIB.add_equipment(req.category, req.name, defaults_for(req.category))
    return {"id": eid}


@app.post("/api/library/equipment/remove")
def api_eq_remove(req: ApplyEqReq):
    LIB.remove_equipment(req.equipment_id)
    return {"ok": True}


class EqUpdateReq(BaseModel):
    equipment_id: str
    name: str | None = None
    params: dict | None = None          # 参数模板 {key: {label,unit,default,min,max}}
    inputs: list[str] | None = None
    outputs: list[str] | None = None
    formulas: dict[str, str] | None = None


@app.post("/api/library/equipment/update")
def api_eq_update(req: EqUpdateReq):
    """更新设备定义(参数模板/接口/公式),存为模板供后续节点继承。"""
    if not LIB.get_equipment(req.equipment_id):
        raise HTTPException(404, "equipment not found")
    LIB.update_equipment(req.equipment_id, name=req.name, params=req.params,
                         inputs=req.inputs, outputs=req.outputs,
                         formulas=req.formulas)
    return {"ok": True, "equipment_id": req.equipment_id}


@app.post("/api/library/defaults")
def api_defaults(req: DefaultReq):
    LIB.set_default_equipment(req.category, req.equipment_id)
    return {"ok": True}


@app.post("/api/library/params")
def api_param_add(req: ParamReq):
    LIB.set_param(req.name, req.unit, req.category, req.scope)
    return {"ok": True}


@app.delete("/api/library/params/{name}")
def api_param_remove(name: str):
    LIB.remove_param(name)
    return {"ok": True}


@app.post("/api/library/film_props")
def api_film_props(req: FilmPropsReq):
    LIB.set_film_props(req.props)
    return {"ok": True}


@app.post("/api/library/param_links")
def api_param_links(req: ParamLinksReq):
    LIB.set_param_links(req.links)
    return {"ok": True}


# ---------- 参数语义类别(可自定义) ----------
class CategoryReq(BaseModel):
    name: str


@app.get("/api/library/param_categories")
def api_categories():
    return {"categories": LIB.param_categories()}


@app.post("/api/library/param_categories")
def api_category_add(req: CategoryReq):
    LIB.add_param_category(req.name)
    return {"ok": True, "categories": LIB.param_categories()}


@app.delete("/api/library/param_categories/{name}")
def api_category_remove(name: str):
    LIB.remove_param_category(name)
    return {"ok": True, "categories": LIB.param_categories()}


# ---------- 机台(真实设备实例) ----------
class MachineReq(BaseModel):
    name: str
    model: str = ""
    vendor: str = ""                  # 厂家
    tool_id: str = ""                 # core 机台标识(数据域对齐)
    serial: str = ""                  # 资产编号
    equipment_id: str = ""            # 所属工艺模板
    location: str = ""
    status: str = "active"
    max_sample: str = ""              # 最大样品尺寸
    notes: str = ""


@app.get("/api/machines")
def api_machines():
    return {"machines": LIB.machines()}


@app.post("/api/machines")
def api_machine_add(req: MachineReq):
    mid = LIB.add_machine(**req.model_dump())
    return {"ok": True, "id": mid}


@app.post("/api/machines/{mid}")
def api_machine_update(mid: str, req: MachineReq):
    if not LIB.get_machine(mid):
        raise HTTPException(404, "machine not found")
    LIB.update_machine(mid, **req.model_dump())
    return {"ok": True}


@app.delete("/api/machines/{mid}")
def api_machine_remove(mid: str):
    LIB.remove_machine(mid)
    return {"ok": True}


# ---------- 影响规则(参数/属性 → 参数,定性+定量统一) ----------
class RulesReq(BaseModel):
    rules: list[dict]


class ResolveReq(BaseModel):
    context: dict = {}          # {surface_film, process, ...} 任意属性
    to: str | None = None       # 只看影响某参数的规则(可选)


@app.get("/api/rules")
def api_rules():
    return {"rules": LIB.influence_rules(),
            "bias_table": rule_engine.bias_table(LIB.influence_rules())}


@app.post("/api/rules")
def api_rules_set(req: RulesReq):
    LIB.set_influence_rules(req.rules)
    return {"ok": True, "count": len(req.rules),
            "bias_table": rule_engine.bias_table(req.rules)}


@app.post("/api/rules/resolve")
def api_rules_resolve(req: ResolveReq):
    """给定上下文,返回全部生效规则及定量求值结果(定性规则 value=None)。"""
    env = dict(req.context)
    out = []
    for r in rule_engine.applicable(LIB.influence_rules(), env):
        if req.to and r.get("to") != req.to:
            continue
        out.append({
            "id": r.get("id"), "from": r.get("from"), "to": r.get("to"),
            "when": r.get("when"), "expr": r.get("expr"),
            "value": rule_engine.eval_rule(r, env),
            "sign": r.get("sign"), "mechanism": r.get("mechanism"),
            "scope": r.get("scope"), "source": r.get("source"),
            "reliability_score": r.get("reliability_score", 3),
        })
    return {"resolved": out}


# ---------- P4: 版图(GDS) ----------
class GdsReq(BaseModel):
    pitch_nm: float = 1000
    linewidth_nm: float = 400
    nx: int = 1
    ny: int = 1
    cell_um: float = 100
    label: str = "OpenNano"


@app.post("/api/gds")
def api_gds(req: GdsReq):
    from engine.gds import generate_gds
    return generate_gds(req.model_dump())


@app.post("/api/gds/live")
def api_gds_live(req: GdsReq):
    from engine.klink_draw import live_draw
    return live_draw(req.model_dump())

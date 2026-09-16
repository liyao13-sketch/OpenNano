"""Agent 工具注册表:声明式 schema + Python 实现分离。

设计约束(对齐 DSH「工具=声明(schema)+实现(local)分离」,OpenNano §十二.2):
  - 每个工具 = name / description / parameters(JSON Schema, object-rooted) / func。
  - 新增工具只在本文件注册,编排器(orchestrator)内核不改。
  - 能力边界(OpenNano §十三):L0 问答 / L1 只读白名单 / L2 专用可审计工具;L3 不做。

`confirm=True` 的工具为 L2 写操作(记实验/生成 GDS):立即生效,但结果会在
聊天轨迹中完整回显,可审计;交互式「二次确认」留待后续 UI 层加。
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable

from kb.ingest import DATA_ROOT, load_data, load_steps

from . import rag

# ---------------------------------------------------------------------------
# 上下文:工具运行时注入(由编排器提供,避免模块级全局状态)
# ---------------------------------------------------------------------------


@dataclass
class Context:
    kb: Any = None          # KBStore
    lib: Any = None         # LibraryStore


# ---------------------------------------------------------------------------
# 工具定义
# ---------------------------------------------------------------------------


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict                 # JSON Schema (type=object)
    func: Callable[[dict, Context], Any]
    confirm: bool = False            # L2 写操作标记

    def schema(self) -> dict:
        return {"type": "function",
                "function": {"name": self.name, "description": self.description,
                             "parameters": self.parameters}}


def _compact_entry(e: dict) -> dict:
    """知识条目压缩视图(喂给 LLM,去掉无关字段)。"""
    return {
        "id": e.get("id"), "title": e.get("title"),
        "process_type": e.get("process_type"),
        "material": e.get("material", {}).get("material", ""),
        "parameters": e.get("parameters", {}),
        "results": e.get("results", {}),
        "source": e.get("source"),
        "reliability_score": e.get("reliability_score"),
        "tags": e.get("tags", []),
    }


# ---- 工具实现 ---------------------------------------------------------------

def _search_kb(args: dict, ctx: Context) -> dict:
    """查知识库:词法检索 + 可信度加权,再按过滤条件收窄。"""
    q = str(args.get("q") or "").strip()
    entries = rag.retrieve(ctx.kb, q, top_k=12) if q else ctx.kb.list(limit=200)
    pt = str(args.get("process_type") or "").strip()
    mat = str(args.get("material") or "").strip()
    min_rel = args.get("min_reliability")
    out = []
    for e in entries:
        if pt and e.get("process_type") != pt:
            continue
        if mat and mat.lower() not in (e.get("material") or {}).get("material", "").lower():
            continue
        if min_rel is not None and e.get("reliability_score", 0) < int(min_rel):
            continue
        out.append(_compact_entry(e))
    return {"count": len(out), "entries": out}


def _record_experiment(args: dict, ctx: Context) -> dict:
    """记实验/录入知识条目(带 source 幂等去重 + 可信度)。"""
    required = ["process_type", "source"]
    missing = [k for k in required if not str(args.get(k) or "").strip()]
    if missing:
        return {"error": f"缺少必填字段: {', '.join(missing)}"}
    # v0.2:可信度必须显式给出 —— 曾因缺省 4 让文献/口述条目静默变成"系统实测"
    if args.get("reliability_score") in (None, ""):
        return {"error": "缺少必填字段: reliability_score（可信度必须显式给出："
                         "1 推测/已被推翻 · 2 文献未验证 · 3 多次实测 · 4 实测或权威源互证 · 5 金标准）。"
                         "不要默认为 4。"}
    entry = {
        "process_type": args["process_type"],
        "title": str(args.get("title") or ""),
        "equipment": args.get("equipment") or {},
        "material": args.get("material") or {},
        "parameters": args.get("parameters") or {},
        "results": args.get("results") or {},
        "source": str(args["source"]),
        "reliability_score": int(args["reliability_score"]),
        "constraints": args.get("constraints") or [],
        "tags": args.get("tags") or [],
        "extra_metadata": args.get("extra_metadata") or {},
    }
    obj, created = ctx.kb.upsert(entry)
    return {"created": created, "entry": _compact_entry(obj.to_dict())}


def _generate_doe(args: dict, ctx: Context) -> dict:
    from engine import generate_matrix
    variables = args.get("variables") or []
    if not variables:
        return {"error": "variables 为空,无法生成矩阵"}
    design = str(args.get("design_type") or "full")
    center = int(args.get("center_points") or 0)
    res = generate_matrix(variables, design, center, args.get("n_runs"))
    return {"runs": res["runs"], "design_type": res["design_type"],
            "matrix": res["matrix"], "levels": res.get("levels")}


def _compute_formula(args: dict, ctx: Context) -> dict:
    from engine import formula_engine
    params = args.get("params") or {}
    handed = args.get("handed") or {}
    key_values = args.get("key_values") or {}
    formulas = args.get("formulas") or {}
    mod = SimpleNamespace(params=params, key_values=dict(key_values), formulas=formulas)
    formula_engine.compute_outputs(mod, handed)
    return {"key_values": mod.key_values}


def _list_processes(args: dict, ctx: Context) -> dict:
    from engine.process_catalog import (CATEGORIES, CATEGORY_LABELS, PROCESSES,
                                        METROLOGY)
    q = str(args.get("q") or "").lower()
    cat = str(args.get("category") or "").strip()
    procs = []
    for c, items in PROCESSES.items():
        if cat and c != cat:
            continue
        for name, sub, inputs, outputs, formulas in items:
            if q and q not in name.lower() and q not in CATEGORY_LABELS[c]:
                continue
            procs.append({"category": c, "category_label": CATEGORY_LABELS[c],
                          "name": name, "inputs": inputs, "outputs": outputs})
    metro = [{"subtype": s, "name": n, "desc": d} for s, n, d in METROLOGY
             if not q or q in n.lower() or q in d]
    return {"categories": [{"key": c, "label": CATEGORY_LABELS[c]} for c in CATEGORIES],
            "processes": procs, "metrology": metro}


def _query_core(args: dict, ctx: Context) -> dict:
    """查数据域 core(权威源):run 详情 / 按量名词 / 宽表 / 量词表 / 机台。"""
    from kb import core_source as core
    if not core.available():
        return {"error": "core 未上线(03_实验数据/core)"}
    run_id = str(args.get("run_id") or "").strip()
    if run_id:
        return core.run_detail(run_id)
    q = str(args.get("quantity") or "").strip()
    if str(args.get("list_quantities") or "") in ("1", "true", "True"):
        return {"quantities": core.quantities(), "tools": core.tools()}
    if q:
        # 中文量名/别名 → core 量名词(防止 0 行导致模型反复重试)
        known = {x["quantity"] for x in core.quantities()}
        if q not in known:
            from kb.expack import QUANTITY_TO_PARAM
            # 中文接口参数 → core 量名词(按映射顺序取第一个真实存在的)
            for quant, cn in QUANTITY_TO_PARAM.items():
                if cn == q and quant in known:
                    q = quant
                    break
        wide = core.runs_wide(quantity=q, stage=args.get("stage"), tool_id=args.get("tool_id"),
                              limit=int(args.get("limit", 50)))
        if wide["n"] == 0:
            return {"quantity": q, "n": 0, "rows": [],
                    "hint": "该量名词无数据。可用量名词: "
                            + ", ".join(x["quantity"] for x in core.quantities()[:20])
                            + "。请直接用这些名称作答,不要继续重试。"}
        return {"quantity": q, "n": wide["n"], "rows": wide["rows"]}
    return {"runs": core.runs(stage=args.get("stage"), tool_id=args.get("tool_id"),
                              limit=int(args.get("limit", 50))),
            "note": "可用参数:run_id / quantity / stage / tool_id / list_quantities"}


def _generate_gds(args: dict, ctx: Context) -> dict:
    from engine.gds import generate_gds
    cfg = {
        "pitch_nm": float(args.get("pitch_nm", 1000)),
        "linewidth_nm": float(args.get("linewidth_nm", 400)),
        "nx": int(args.get("nx", 1)),
        "ny": int(args.get("ny", 1)),
        "cell_um": float(args.get("cell_um", 100)),
        "label": str(args.get("label", "OpenNano")),
    }
    return generate_gds(cfg)


# ---- O1 优化工具 ------------------------------------------------------------

def _suggest_center(args: dict, ctx: Context) -> dict:
    """先验中心点:优先从数据域 core 取该 stage/机台的历史参数分布(中位数+范围)。"""
    import statistics
    from kb import core_source as core
    stage = str(args.get("stage") or "").strip() or None
    tool_id = str(args.get("tool_id") or "").strip() or None
    pt = str(args.get("process_type") or "")
    if not stage and pt:
        stage = core.PROCESS_TYPE_TO_STAGE.get(pt)
    if core.available():
        buckets = core.step_params(stage=stage, tool_id=tool_id)
        n_runs = len(core.runs(stage=stage, tool_id=tool_id, limit=10000))
        if not buckets:
            return {"error": f"core 中 stage={stage} tool={tool_id} 无步骤参数可参考"}
        center = {k: round(statistics.median(v), 3) for k, v in sorted(buckets.items())}
        spread = {k: [round(min(v), 3), round(max(v), 3)] for k, v in sorted(buckets.items())}
        return {"source": "core(权威源)", "stage": stage, "tool_id": tool_id,
                "based_on_runs": n_runs, "center": center, "range": spread,
                "note": "中位数为稳健中心(DOE/BO 的先验起点);范围可作因子上下界"}
    # core 未上线 → 退回 KB 结论条目
    buckets2: dict[str, list[float]] = {}
    for e in ctx.kb.list(process_type=pt or "RIE_Cl", limit=200):
        for st in ((e.get("parameters") or {}).get("steps") or []):
            for k, v in st.items():
                if k == "step_name":
                    continue
                try:
                    buckets2.setdefault(k, []).append(float(v))
                except (TypeError, ValueError):
                    continue
    if not buckets2:
        return {"error": "无可用先验(core 未上线且 KB 无配方条目)"}
    return {"source": "kb", "center": {k: round(statistics.median(v), 3) for k, v in sorted(buckets2.items())},
            "range": {k: [min(v), max(v)] for k, v in sorted(buckets2.items())}}


def _fit_model(args: dict, ctx: Context) -> dict:
    """KB → GPR 拟合(与 /api/opt/fit 同一实现)。"""
    from opt import MODEL_REGISTRY, gp as gp_engine
    from kb import core_source as core
    import uuid
    target = str(args.get("target", "depth_center_nm"))
    if core.available():               # 权威源:数据域 core(协议 §11)
        ds = core.dataset(target, stage=args.get("stage"), tool_id=args.get("tool_id"),
                          features=args.get("features"))
    else:
        step = args.get("step") or "ME"
        entries = ctx.kb.list(process_type=str(args.get("process_type", "RIE_Cl")),
                              material=args.get("material") or None, limit=100000)
        ds = gp_engine.build_dataset(entries, target, args.get("features"), step or None)
    if ds["X"] is None or len(ds["y"]) == 0:
        return {"error": f"无可用样本(target={args.get('target')})"}
    res = gp_engine.fit(ds)
    if not res.get("ok"):
        return {"error": res.get("error")}
    mid = f"opt-{uuid.uuid4().hex[:8]}"
    MODEL_REGISTRY[mid] = {"model": res["model"], "X": ds["X"], "y": ds["y"],
                           "features": ds["feature_names"],
                           "target": str(args.get("target", "er_nm_min")),
                           "meta": {k: v for k, v in res.items() if k != "model"}}
    cv = res["cv_r2"]
    return {"model_id": mid, "n": res["n"], "features": ds["feature_names"],
            "cv_r2": round(cv, 3) if cv == cv else None,
            "train_r2": round(res["train_r2"], 3),
            "fallback_linear": res["fallback_linear"]}


def _suggest_next_experiment(args: dict, ctx: Context) -> dict:
    """拟合 + BO(EI) 一步到位:给下一轮参数建议。"""
    from opt import MODEL_REGISTRY, bo as bo_engine
    fit_args: dict = {k: args.get(k) for k in
                      ("process_type", "material", "target", "step", "features")}
    f = _fit_model(fit_args, ctx)
    if "error" in f:
        return f
    mode = str(args.get("mode", "max"))
    if mode not in ("max", "min", "target"):
        mode = "max"
    m = MODEL_REGISTRY[f["model_id"]]
    X = m["X"]
    bounds = {feat: [float(X[:, i].min()) * 0.9, float(X[:, i].max()) * 1.1]
              for i, feat in enumerate(m["features"])}
    out = bo_engine.suggest(m["model"], m["features"], bounds, mode,
                            args.get("target_value"), int(args.get("n", 5)),
                            X_train=X)
    out["model"] = f
    return out


# ---- 注册表 -----------------------------------------------------------------



# ---- 画布操作工具(声明式:返回 op 指令,前端按序执行) -------------------------

def _op(kind: str, **kw) -> dict:
    return {"ok": True, "op": {"type": kind, **kw}, "note": f"画布操作 {kind} 已排队"}


def _canvas_add_module(args: dict, ctx: Context) -> dict:
    """在画布上新增节点(工艺或表征),可带设备/机台/参数。返回临时引用 ref 供连线用。"""
    sub = str(args.get("subtype") or args.get("stage") or "").strip()
    if not sub:
        return {"error": "需要 subtype(如 etch/deposition/graphic)或 stage(如 RIE/ICP/PECVD/SEM)"}
    # ⚠️ ref 必须**每次唯一**（2026-09-16 审计 P1）：原来 `hash(sub+name)%10000` 是确定性哈希，
    #    同 subtype 同 name（LLM 常不给 name ⇒ 两次都是 None）**必得同一个 ref**，
    #    随后 `canvas_connect` 按 ref 找节点就挂到错的那个上，而且无声。
    import uuid
    ref = f"t{uuid.uuid4().hex[:8]}"
    return _op("add_module", ref=ref, subtype=sub, name=args.get("name"),
               equipment_name=args.get("equipment_name"),
               machine_name=args.get("machine_name"),
               params=args.get("params") or {},
               x=args.get("x"), y=args.get("y"))


def _canvas_connect(args: dict, ctx: Context) -> dict:
    if not args.get("src") or not args.get("dst"):
        return {"error": "需要 src 与 dst(节点名或上文 add_module 返回的 ref)"}
    return _op("connect", src=str(args["src"]), dst=str(args["dst"]))


def _canvas_set_params(args: dict, ctx: Context) -> dict:
    if not args.get("node") or not isinstance(args.get("params"), dict):
        return {"error": "需要 node 与 params(对象)"}
    return _op("set_params", node=str(args["node"]), params=args["params"])


def _canvas_delete(args: dict, ctx: Context) -> dict:
    nodes = args.get("nodes") or ([args["node"]] if args.get("node") else [])
    if not nodes:
        return {"error": "需要 node 或 nodes(节点名/ref 列表)"}
    return _op("delete_nodes", nodes=[str(x) for x in nodes])


def _canvas_clear(args: dict, ctx: Context) -> dict:
    return _op("clear")


def _canvas_run(args: dict, ctx: Context) -> dict:
    return _op("run", until=args.get("until"))


CANVAS_TOOL_DEFS = [
    Tool(
        name="canvas_add_module",
        description="在流程画布上新增一个节点(工艺或表征)。工艺用 subtype(etch/deposition/graphic/wet/thermal/doping/bonding/packaging/assist),表征用 stage/subtype(sem/ellip/stress/fourpp…)。可指定设备模板名与机台名与初始参数。返回 ref 供 canvas_connect 引用。",
        parameters={"type": "object", "properties": {
            "subtype": {"type": "string", "description": "工艺大类或表征 subtype,如 etch / deposition / sem"},
            "stage": {"type": "string", "description": "core 阶段名(与 subtype 二选一),如 RIE/ICP/DRIE/PECVD/EBL/SEM"},
            "name": {"type": "string", "description": "节点名(默认按类型)"},
            "equipment_name": {"type": "string", "description": "可选:设备模板名,如 RIE / ICP Etch / DRIE (Bosch)"},
            "machine_name": {"type": "string", "description": "可选:机台名或 tool_id,如 TOOL-A / TOOL-B"},
            "params": {"type": "object", "description": "可选:初始参数"},
        }, "required": ["subtype"]},
        func=_canvas_add_module),
    Tool(
        name="canvas_connect",
        description="把画布上两个节点连起来(源→目标)。src/dst 可用节点名,或 canvas_add_module 刚返回的 ref。",
        parameters={"type": "object", "properties": {
            "src": {"type": "string"}, "dst": {"type": "string"}},
            "required": ["src", "dst"]},
        func=_canvas_connect),
    Tool(
        name="canvas_set_params",
        description="给画布上某个节点设置参数(键值对象)。",
        parameters={"type": "object", "properties": {
            "node": {"type": "string", "description": "节点名或 ref"},
            "params": {"type": "object"}},
            "required": ["node", "params"]},
        func=_canvas_set_params),
    Tool(
        name="canvas_delete",
        description="删除画布上的一个或多个节点(连同其连线)。",
        parameters={"type": "object", "properties": {
            "node": {"type": "string"}, "nodes": {"type": "array", "items": {"type": "string"}}}},
        func=_canvas_delete),
    Tool(
        name="canvas_clear",
        description="清空画布(删除所有节点与连线)。破坏性操作,仅在用户明确要求清空时使用。",
        parameters={"type": "object", "properties": {}},
        func=_canvas_clear, confirm=True),
    Tool(
        name="canvas_run",
        description="运行画布流程(Run);可指定 until 只运行到某节点(Run To)。",
        parameters={"type": "object", "properties": {
            "until": {"type": "string", "description": "可选:节点名/ref,运行到它为止"}}},
        func=_canvas_run),
]

TOOLS: list[Tool] = [
    Tool(
        name="search_kb",
        description="在组织记忆知识库中检索工艺知识条目(配方/实测结果/来源),结果带可信度评分。回答「某材料怎么刻蚀/常用参数」时先调用它。",
        parameters={"type": "object", "properties": {
            "q": {"type": "string", "description": "检索关键词(材料/工艺/现象)"},
            "process_type": {"type": "string", "description": "可选:按工艺类型过滤,如 RIE_Cl/RIE_F/DRIE_Bosch"},
            "material": {"type": "string", "description": "可选:按材料名过滤,如 Ta/Si/SiO2"},
            "min_reliability": {"type": "integer", "description": "可选:最低可信度(1~5)"},
        }, "required": ["q"]},
        func=_search_kb,
    ),
    Tool(
        name="list_processes",
        description="列出工艺目录(104 种工艺大类/名称/输入输出参数)或表征手段。用户问「有哪些刻蚀工艺/能做什么」时调用。",
        parameters={"type": "object", "properties": {
            "category": {"type": "string", "description": "可选:工艺大类,如 etch/deposition/graphic"},
            "q": {"type": "string", "description": "可选:按工艺名关键词过滤"},
        }},
        func=_list_processes,
    ),
    Tool(
        name="generate_doe",
        description="生成 DOE 实验矩阵(全因子/部分因子),给定各参数 min/max/step。用户要「设计实验/做DOE」时调用。",
        parameters={"type": "object", "properties": {
            "variables": {"type": "array", "items": {"type": "object", "properties": {
                "param": {"type": "string"}, "min": {"type": "number"},
                "max": {"type": "number"}, "step": {"type": "number"}},
                "required": ["param", "min", "max"]},
                "description": "要扫描的变量列表"},
            "design_type": {"type": "string", "enum": ["full", "partial"], "description": "全因子 full / 部分因子 partial"},
            "center_points": {"type": "integer", "description": "中心点重复次数,默认 0"},
        }, "required": ["variables"]},
        func=_generate_doe,
    ),
    Tool(
        name="compute_formula",
        description="用安全公式引擎计算输出参数(如 硅CD=胶CD-2*bias)。给定 params/handed/key_values/formulas。",
        parameters={"type": "object", "properties": {
            "params": {"type": "object", "description": "设备参数(变量环境)"},
            "handed": {"type": "object", "description": "上游承接参数"},
            "key_values": {"type": "object", "description": "已设输出初值"},
            "formulas": {"type": "object", "description": "{输出参数: 表达式}"},
        }, "required": ["formulas"]},
        func=_compute_formula,
    ),
    Tool(
        name="query_core",
        description="查实验原始数据(数据域 core,权威源):run 详情(步骤/测量/现象/证据)、按量名词查各 run 的值、量名词表、机台表。用户问「某次实验的数据/某量的历史值/有哪些量可查」时调用。",
        parameters={"type": "object", "properties": {
            "run_id": {"type": "string", "description": "指定 run(如 Cl-Ta-019 / AR50-T1-DRIE-0001),返回该 run 全部明细"},
            "quantity": {"type": "string", "description": "量名词(如 depth_center_nm/final_cd_nm/selectivity/film_thickness_nm)"},
            "stage": {"type": "string", "description": "可选:阶段过滤 RIE/ICP/DRIE/PECVD/EBL/LDW/MA6/SPUT/SEM…"},
            "tool_id": {"type": "string", "description": "可选:机台过滤 TOOL-A/TOOL-B…"},
            "list_quantities": {"type": "boolean", "description": "true=列出全部量名词与机台"},
            "limit": {"type": "integer"},
        }},
        func=_query_core,
    ),
    Tool(
        name="record_experiment",
        description="把一条实验/工艺配方录入知识库(带来源与可信度评分,按 source 幂等去重)。用户要「记下这条实验/保存这个配方」时调用。",
        parameters={"type": "object", "properties": {
            "process_type": {"type": "string", "description": "工艺类型,如 RIE_Cl/RIE_F/PECVD"},
            "title": {"type": "string", "description": "条目标题"},
            "equipment": {"type": "object", "description": "设备信息,如 {\"model\":\"RIE200NL\"}"},
            "material": {"type": "object", "description": "材料信息,如 {\"material\":\"Ta\",\"film_thickness_nm\":200}"},
            "parameters": {"type": "object", "description": "配方参数"},
            "results": {"type": "object", "description": "实测结果"},
            "source": {"type": "string", "description": "来源(实验编号/设备手册/文献),必填,用于去重"},
            "reliability_score": {"type": "integer", "description": "可信度 1~5,**必须显式给出、不要默认 4**:1 推测/已被推翻 · 2 文献未验证 · 3 多次实测 · 4 实测或权威源互证 · 5 金标准"},
            "tags": {"type": "array", "items": {"type": "string"}},
        }, "required": ["process_type", "source", "reliability_score"]},
        func=_record_experiment, confirm=True,
    ),
    Tool(
        name="generate_gds",
        description="生成 GDS 版图文件(周期线条,给定 pitch/linewidth/行列数)。用户要「生成版图」时调用。",
        parameters={"type": "object", "properties": {
            "pitch_nm": {"type": "number", "description": "周期,单位 nm"},
            "linewidth_nm": {"type": "number", "description": "线宽,单位 nm"},
            "nx": {"type": "integer", "description": "X 方向重复数"},
            "ny": {"type": "integer", "description": "Y 方向重复数"},
            "cell_um": {"type": "number", "description": "单元尺寸,单位 um"},
            "label": {"type": "string", "description": "版图标签"},
        }},
        func=_generate_gds, confirm=True,
    ),
    Tool(
        name="suggest_center",
        description="给出 DOE/贝叶斯优化的先验中心点:从数据域 core 取该阶段/机台的历史步骤参数分布(中位数+范围)。用户刚开始一个新工艺、还没定中心点时调用。",
        parameters={"type": "object", "properties": {
            "stage": {"type": "string", "description": "core 阶段,如 RIE/ICP/DRIE/PECVD/EBL"},
            "tool_id": {"type": "string", "description": "可选:机台,如 TOOL-A/TOOL-B"},
            "process_type": {"type": "string", "description": "可选:旧工艺类型(RIE_Cl/RIE_F/DRIE_Bosch),自动折算 stage"},
        }},
        func=_suggest_center,
    ),
    Tool(
        name="fit_model",
        description="用知识库实测数据拟合 GPR 代理模型(参数→目标)。返回模型 id、样本数、交叉验证 R²。用户想'建模/看参数影响'时调用。",
        parameters={"type": "object", "properties": {
            "process_type": {"type": "string", "description": "如 RIE_Cl"},
            "target": {"type": "string", "description": "core 量名词,如 depth_center_nm/final_cd_nm/selectivity/film_thickness_nm"},
            "stage": {"type": "string", "description": "可选:阶段 RIE/ICP/DRIE/PECVD/EBL…"},
            "tool_id": {"type": "string", "description": "可选:机台 TOOL-A/TOOL-B…"},
            "step": {"type": "string", "description": "用哪一步参数作特征,默认 ME;空串=全部步"},
        }, "required": ["target"]},
        func=_fit_model,
    ),
    Tool(
        name="suggest_next_experiment",
        description="贝叶斯优化:拟合模型 + Expected Improvement,给出下一轮最值得做的实验参数(含预测值与理由)。用户问'下一个实验做什么参数'时调用。",
        parameters={"type": "object", "properties": {
            "process_type": {"type": "string", "description": "如 RIE_Cl"},
            "target": {"type": "string", "description": "优化目标(core 量名词),如 depth_center_nm"},
            "mode": {"type": "string", "enum": ["max", "min", "target"], "description": "max 最大化/min 最小化/target 逼近 target_value"},
            "target_value": {"type": "number", "description": "mode=target 时的目标值"},
            "material": {"type": "string"},
            "n": {"type": "integer", "description": "建议点数,默认 5"},
        }, "required": ["target"]},
        func=_suggest_next_experiment,
    ),
]

TOOLS.extend(CANVAS_TOOL_DEFS)

TOOL_MAP: dict[str, Tool] = {t.name: t for t in TOOLS}


def tool_schemas() -> list[dict]:
    return [t.schema() for t in TOOLS]


def execute_tool(name: str, args: dict, ctx: Context) -> dict:
    tool = TOOL_MAP.get(name)
    if not tool:
        return {"error": f"未知工具 {name};可用: {sorted(TOOL_MAP)}"}
    try:
        out = tool.func(args or {}, ctx)
        if isinstance(out, dict):
            return out
        return {"result": out}
    except Exception as e:  # noqa: BLE001  # 工具失败回传 LLM,不中断循环
        return {"error": f"{type(e).__name__}: {e}"}


def describe_tools() -> str:
    return "\n".join(f"- {t.name}: {t.description}" for t in TOOLS)

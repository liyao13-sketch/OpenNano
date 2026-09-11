"""OpenNano 核心数据模型 (V1 先行 schema)。

含核心对象:
    Module            画布节点(工艺/表征/设计/仿真)
    Port              模块的输入/输出口
    Parameter         基础参数
    DOEExperiment     DOE 实验设计(附加在工艺/仿真模块上)
    Annotation        SEM 语义标注
    SimulationResult  仿真结果

设计原则:
- 模块(节点)是数据容器,参数/DOE/标注/结果都挂在 module_id 上。
- 连线(Edge)表达"上游输出口 -> 下游输入口"的数据流,下游可从上游自动取协变量。
- V1 持久化用 JSON 项目文件;其余对象已按将来 SQLite 定 schema,可平移。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from typing import Any

# 模块大类
KIND_PROCESS = "process"      # 曝光/刻蚀/镀膜
KIND_INSPECT = "inspect"      # SEM/AFM 表征
KIND_DESIGN = "design"        # GDS 版图
KIND_SIM = "sim"              # 刻蚀轮廓仿真

# 表征语义标注类型
ANNOTATION_TYPES = [
    ("cd", "线宽 (CD)"),
    ("slot_width", "槽宽"),
    ("pitch", "间距"),
    ("height", "高度"),
    ("swa", "侧壁角"),
    ("area", "面积"),
    ("perimeter", "周长"),
]


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@dataclass
class Port:
    """模块的输入/输出口,连线端点。"""
    name: str
    direction: str          # "in" | "out"
    kind: str = "data"      # 数据种类: data | mask | profile | recipe


@dataclass
class Module:
    """画布节点 = 一个工艺/表征/设计/仿真模块。"""
    id: str
    kind: str               # process | inspect | design | sim
    subtype: str            # 例: ICP蚀刻 / UV曝光 / PECVD / SEM / GDS / EtchSim
    name: str
    x: float = 0.0
    y: float = 0.0
    params: dict[str, Any] = field(default_factory=dict)   # key -> value(基础参数)
    param_meta: dict[str, dict] = field(default_factory=dict)  # key -> {unit, ...}
    param_defs: dict[str, dict] = field(default_factory=dict)  # key -> {label,unit,default,min,max}(可自定义)
    equipment_id: str = ""  # 关联的用户设备 id(空 = 内置默认模板)
    material: dict = field(default_factory=dict)   # {"film","thickness","substrate"} 工艺链联动用
    key_values: dict = field(default_factory=dict)  # 第一类关键参数(贯穿全流程,如 CD/SWA)
    param_inputs: list = field(default_factory=list)   # 承接参数名(接口:来自上游)
    param_outputs: list = field(default_factory=list)  # 影响参数名(接口:传给下游)
    formulas: dict = field(default_factory=dict)       # 输出参数 -> 安全表达式
    inputs: list[Port] = field(default_factory=list)
    outputs: list[Port] = field(default_factory=list)
    doe: dict | None = None          # 见 DOEExperiment 结构(embedded)
    annotations: list[dict] = field(default_factory=list)  # Annotation(embedded)
    sim_result: dict | None = None   # SimulationResult(embedded)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["inputs"] = [asdict(p) for p in self.inputs]
        d["outputs"] = [asdict(p) for p in self.outputs]
        return d


@dataclass
class Edge:
    """连线:上游输出口 -> 下游输入口(数据流/协变量)。"""
    id: str
    src_module: str
    src_port: str
    dst_module: str
    dst_port: str


# ---- 附加对象结构(以 dict 形式挂在模块上,便于序列化) ----

def _empty_doe() -> dict:
    return {
        "variables": [],      # [{param, min, max, step}]
        "design_type": "full",  # full | partial
        "center_points": 0,
        "matrix": [],          # [[value,...]]
        "status": "draft",
    }


def _empty_annotation() -> dict:
    return {
        "id": new_id("ann"),
        "image_id": "",
        "semantic_type": "cd",     # ANNOTATION_TYPES
        "value": 0.0,
        "unit": "nm",
        "geometry": [],            # [[x,y],...]
        "mode": "line",            # line | polygon | points
        "note": "",
    }


def _empty_sim_result() -> dict:
    return {
        "id": new_id("sim"),
        "status": "idle",          # idle | running | done | error
        "profile": [],             # 轮廓点集 [(x_nm, z_nm), ...]
        "metrics": {},             # cd_top_nm, cd_bottom_nm, swa_deg, depth_nm...
        "input_ref": None,         # 关联的上游模块 id(掩膜/工艺)
        "created_at": None,
    }


# ---- 工厂:常用模块类型 ----

def make_module(kind: str, subtype: str, name: str, x: float = 0.0, y: float = 0.0) -> Module:
    """创建带默认输入/输出口与默认参数定义的模块。"""
    from .param_defs import defaults_for
    m = Module(id=new_id("md"), kind=kind, subtype=subtype, name=name, x=x, y=y)
    m.param_defs = defaults_for(subtype)   # 默认参数定义(可自定义)
    if kind == KIND_PROCESS:
        m.inputs = [Port("recipe_in", "in")]
        m.outputs = [Port("recipe_out", "out")]
    elif kind == KIND_INSPECT:
        m.inputs = [Port("sample_in", "in")]
        m.outputs = [Port("data_out", "out")]
    elif kind == KIND_DESIGN:
        m.inputs = [Port("param_in", "in")]
        m.outputs = [Port("gds_out", "out", kind="mask")]
    elif kind == KIND_SIM:
        m.inputs = [Port("profile_in", "in"), Port("mask_in", "in", kind="mask")]
        m.outputs = [Port("sim_out", "out", kind="profile")]
    return m

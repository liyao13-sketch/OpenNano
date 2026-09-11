"""工艺链联动:沿连线传递"膜层堆叠",并按用户规则推导下游参数(如 GDS bias)。

链路语义:
- 每个沉积模块(coating*)把 [film, thickness] 叠到上游堆叠之上,作为本模块输出。
- 下游模块(如曝光/LDW)的"输入堆叠" = 上游模块的输出堆叠;无上游时用自身衬底。
- 联动规则(library.rules):如 top film = SiO₂ → GDS bias = -700 nm。
"""
from __future__ import annotations

from .container import Project
from .schema import Module

BASE_SUBSTRATE = {"film": "Si", "thickness": 0.0}


def upstream_module(project: Project, module: Module) -> Module | None:
    """连线意义上直接位于 module 上游的模块(取第一条入边)。"""
    for e in project.edges:
        if e.dst_module == module.id:
            m = project.get_module(e.src_module)
            if m:
                return m
    return None


def output_stack(project: Project, module: Module, _depth: int = 0) -> list[dict]:
    """该模块输出端的膜层堆叠 = 上游堆叠 + 本模块沉积的膜。"""
    if _depth > 16:
        return [dict(BASE_SUBSTRATE)]
    up = upstream_module(project, module)
    stack = output_stack(project, up, _depth + 1) if up else _own_substrate(module)
    mat = module.material or {}
    if (module.kind == "process" and module.subtype == "deposition"
            and mat.get("film")):
        stack = stack + [{"film": mat["film"],
                          "thickness": float(mat.get("thickness") or 0)}]
    return [dict(l) for l in stack]


def incoming_stack(project: Project, module: Module) -> list[dict]:
    """该模块输入端的膜层堆叠(= 上游输出;无上游 = 自身衬底)。"""
    up = upstream_module(project, module)
    if up:
        return output_stack(project, up)
    return _own_substrate(module)


def _own_substrate(module: Module) -> list[dict]:
    sub = (module.material or {}).get("substrate") or "Si"
    return [{"film": sub, "thickness": 0.0}]


def top_film(stack: list[dict]) -> str:
    return stack[-1]["film"] if stack else ""


def gds_bias_nm(library, stack: list[dict]):
    """按堆叠顶层膜查 etch_bias(材料属性);无则 None。"""
    if library is None:
        return None
    return library.film_bias(top_film(stack))


def handed_params(project: Project, module: Module) -> dict:
    """下游承接到的参数值(上游 outputs ∩ 本模块 param_inputs)。"""
    up = upstream_module(project, module)
    if not up:
        return {}
    outs = up.key_values or {}
    return {k: v for k, v in outs.items() if k in (module.param_inputs or [])}

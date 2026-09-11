"""项目数据容器:管理模块与连线,持久化到 JSON 项目文件。

V1 用 JSON 文件(工程文件 *.opnano.json)承载整个项目数据;
后续可平移为 SQLite 主库 + JSON 快照。容器提供数据树/导出所需的形态。
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from .schema import Edge, Module, new_id


class Project:
    """一个 OpenNano 项目 = 若干模块 + 连线 + 元信息。"""

    def __init__(self, name: str = "未命名项目"):
        self.name = name
        self.modules: dict[str, Module] = {}
        self.edges: list[Edge] = []
        self.meta: dict = {"version": "0.1", "created_at": None}

    # ---- 模块 ----
    def add_module(self, module: Module) -> Module:
        self.modules[module.id] = module
        return module

    def remove_module(self, module_id: str) -> None:
        self.modules.pop(module_id, None)
        # 移除与该模块相关的连线
        self.edges = [e for e in self.edges
                      if e.src_module != module_id and e.dst_module != module_id]

    def get_module(self, module_id: str) -> Module | None:
        return self.modules.get(module_id)

    # ---- 连线 ----
    def connect(self, src_module, src_port, dst_module, dst_port) -> Edge:
        # 去重:同一对(源口->目的口)只保留一条
        for e in self.edges:
            if (e.src_module == src_module and e.src_port == src_port
                    and e.dst_module == dst_module and e.dst_port == dst_port):
                return e
        e = Edge(id=new_id("edge"), src_module=src_module, src_port=src_port,
                 dst_module=dst_module, dst_port=dst_port)
        self.edges.append(e)
        return e

    def get_downstream(self, module_id: str) -> list[Edge]:
        """返回以 module_id 为上游的所有连线(下游协变量)。"""
        return [e for e in self.edges if e.src_module == module_id]

    # ---- 序列化 ----
    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "meta": self.meta,
            "modules": [m.as_dict() for m in self.modules.values()],
            "edges": [{"id": e.id, "src_module": e.src_module, "src_port": e.src_port,
                       "dst_module": e.dst_module, "dst_port": e.dst_port}
                      for e in self.edges],
        }

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
                     encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "Project":
        p = Path(path)
        data = json.loads(p.read_text(encoding="utf-8"))
        proj = cls(name=data.get("name", "未命名项目"))
        proj.meta = data.get("meta", {})
        for md in data.get("modules", []):
            m = Module(
                id=md["id"], kind=md["kind"], subtype=md.get("subtype", ""),
                name=md.get("name", ""), x=md.get("x", 0.0), y=md.get("y", 0.0),
                params=md.get("params", {}), param_meta=md.get("param_meta", {}),
                param_defs=md.get("param_defs", {}),
                equipment_id=md.get("equipment_id", ""),
                material=md.get("material", {}),
                key_values=md.get("key_values", {}),
                param_inputs=md.get("param_inputs", []), param_outputs=md.get("param_outputs", []),
                formulas=md.get("formulas", {}),
                doe=md.get("doe", None), annotations=md.get("annotations", []),
                sim_result=md.get("sim_result", None),
            )
            # 旧工程没有 param_defs 时,用默认定义补齐
            if not m.param_defs:
                from .param_defs import defaults_for
                m.param_defs = defaults_for(m.subtype)
            # 端口用工厂重建(保证结构一致)
            from .schema import make_module
            fresh = make_module(m.kind, m.subtype, m.name)
            m.inputs = fresh.inputs
            m.outputs = fresh.outputs
            proj.add_module(m)
        for ed in data.get("edges", []):
            proj.edges.append(Edge(id=ed["id"], src_module=ed["src_module"],
                                   src_port=ed["src_port"], dst_module=ed["dst_module"],
                                   dst_port=ed["dst_port"]))
        return proj

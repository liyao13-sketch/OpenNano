"""OpenNano 引擎层(纯 Python, 由桌面原型迁移, 无 UI 依赖)。

对外暴露: 工艺目录 / 参数定义 / 设备库 / 公式引擎 / DOE / 导出 / 状态流 / 模块工厂。
"""
from .param_defs import DEFAULT_PARAM_DEFS, defaults_for
from .process_catalog import CATEGORIES, CATEGORY_LABELS, PROCESSES, METROLOGY
from . import engine as formula_engine
from .doe import generate_matrix
from .library import LibraryStore, category_for_subtype
from .schema import Module, make_module

__all__ = [
    "DEFAULT_PARAM_DEFS", "defaults_for",
    "CATEGORIES", "CATEGORY_LABELS", "PROCESSES", "METROLOGY",
    "formula_engine", "generate_matrix", "LibraryStore", "category_for_subtype",
    "Module", "make_module",
]
from .module_factory import module_catalog, build_module

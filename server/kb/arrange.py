"""画布**一键自动整理** —— 把任意状态的工程（含被拖乱/合成重叠的）重排成规范布局。

与 `relayout.py` 的分工：
    · `relayout.py`：按 **core** 重算（顺带回读标注、连边），是"从权威源重建画布"；
    · 本模块：**只用画布自己已有的信息**（模块上的 stage_seq / run_nature / 边的走向）重排坐标，
      不动任何边、不动任何标注 —— 纯几何整理，给"方块叠在一起"一键复位用。

复用 `expack._layout_modules`（**布局算法只有一份**，避免出现第二套规则）。
"""
from __future__ import annotations

from .expack import _layout_modules


def _runs_of(modules: list[dict]) -> list[dict]:
    """把画布模块伪装成 "runs" 喂给布局算法（它只需要 run_id / stage_seq / run_nature）。"""
    out = []
    for m in modules:
        rid = (m.get("core_run_id") or "").strip()
        seq = m.get("core_stage_seq")
        if seq is None and rid.count("-") >= 2:            # 回退：从 run_id 末尾能推工序号吗？不能 ⇒ 0
            seq = 0
        out.append({
            "run_id": rid,
            "batch_id": (m.get("core_batch_id") or
                         (rid.rsplit("-", 2)[0] if rid.count("-") >= 2 else "")),
            "stage_seq": int(seq or 0),
            "run_nature": (m.get("run_nature") or "").strip(),
            "parent_run_id": "",                            # 父关系由 edges 决定（见下）
        })
    return out


def arrange_project(project: dict) -> dict:
    """就地重排 `project["modules"]` 的坐标（**不改边、不改其它字段**），返回摘要。"""
    mods = project.get("modules") or []
    if not mods:
        return {"ok": False, "reason": "没有模块", "modules": mods}
    runs = _runs_of(mods)
    # 布局算法从 edges 推父关系：把边端点 id 映射成 run_id 会更直观，
    # 但 _layout_modules 内部用 mid 映射，故这里直接把 edges 传进去即可。
    _layout_modules(runs, mods, project.get("edges") or [])
    xs = [m["x"] for m in mods]
    ys = [m["y"] for m in mods]
    return {"ok": True, "modules": len(mods),
            "cols": len(set(xs)), "rows": len(set(ys)),
            "bbox": {"x": [min(xs), max(xs)], "y": [min(ys), max(ys)]}}

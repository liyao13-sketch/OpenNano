"""批次 / run 序号 / 续做链 的纯逻辑（无 IO，可单测）。

契约（`schema_v0.1.md` §一 + 数据协议 §4）：
    - run_id = `{batch}-{STAGE}-{NNNN}`，**序号 = 该 batch 该 stage 已有数 + 1**（禁手输）
    - `parent_run_id` = 上游 run（**续做的唯一凭证**，画布连线靠它）
    - `stage_seq` = **同 stage 保持同号**（同 stage 重复上机不递增）
    - 续做时 recipe 可从 group 灌入；steps 只记实际执行步
"""
from __future__ import annotations

import re

RUN_ID_RE = re.compile(r"^(?P<batch>.+)-(?P<stage>[A-Za-z0-9_]+)-(?P<seq>\d{4})$")

#: 回退用的 stage 习惯序 —— **只在本 batch 从未记录过时兜底**。
#: ⚠️ `stage_seq` 是"本 batch 内的工序序号"（协议 §94），各 batch 自定：
#: core 实测 AR50-T1 = PECVD1/LDW2/ICP3/ASH4/DRIE5，而别的 batch 里 RIE 也是 1。
#: 所以**权威来源是已入库的 core/runs.csv**（见 stage_seq_map），不是这张表。
STAGE_ORDER = ["PECVD", "LDW", "EBL", "UV", "MA6", "ICP", "ASH", "DRIE", "RIE",
               "EVAP", "SPUT", "LIFT", "DICE", "SEM", "ELLIP", "PROFILE", "STRESS"]

#: 从 core/runs.csv 读到的 (batch, stage) → stage_seq（进程内缓存）
_STAGE_SEQ_CACHE: dict[tuple[str, str], int] | None = None


def core_runs_path():
    """core/runs.csv 的位置（可用 OPENNANO_CORE_DIR 覆盖）。"""
    import os
    from pathlib import Path
    env = os.environ.get("OPENNANO_CORE_DIR")
    if env:
        return Path(env) / "runs.csv"
    try:
        from .menu_reader import _workspace
        return _workspace() / "个人空间/18_工艺数据资产/03_实验数据/core/runs.csv"
    except Exception:                     # noqa: BLE001
        return None


def stage_seq_map() -> dict[tuple[str, str], int]:
    """已入库的 (batch, stage) → stage_seq（**续做的 stage_seq 必须沿用这个**）。"""
    global _STAGE_SEQ_CACHE
    if _STAGE_SEQ_CACHE is not None:
        return _STAGE_SEQ_CACHE
    out: dict[tuple[str, str], int] = {}
    p = core_runs_path()
    try:
        import csv
        if p and p.exists():
            with p.open(newline="", encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    b, s = (r.get("batch_id") or "").strip(), (r.get("stage") or "").strip()
                    seq = (r.get("stage_seq") or "").strip()
                    if b and s and seq.isdigit():
                        out.setdefault((b, s), int(seq))
    except Exception:                     # noqa: BLE001
        pass
    _STAGE_SEQ_CACHE = out
    return out


def parse_run_id(run_id: str) -> dict | None:
    """`AR50-T1-DRIE-0002` → {batch:AR50-T1, stage:DRIE, seq:2}（stage 可能含 `-`，从右切）。"""
    if not run_id:
        return None
    parts = run_id.rsplit("-", 2)
    if len(parts) != 3 or not parts[2].isdigit():
        return None
    return {"batch": parts[0], "stage": parts[1], "seq": int(parts[2])}


def runs_of_batch(modules: list[dict], batch: str) -> list[dict]:
    """从画布模块里取出某 batch 的 run 行（按 stage_seq, stage, seq 排序）。

    ⚠️ 老包（2026-09-12 之前导出的 flow.json）只把 `core_run_id` 写进模块，
    `parent_run_id` 只存在 runs.csv ⇒ 这里做一次**回退绑定**（按 core_run_id 读包内 runs.csv），
    否则批次视图会丢掉整条 parent 链（实测 AR50-T1 丢 11 条边）。
    """
    parent_map = _parent_map_from_packs()
    rows = []
    for m in modules or []:
        rid = m.get("core_run_id") or ""
        p = parse_run_id(rid)
        if not p or p["batch"] != batch:
            continue
        rows.append({
            "run_id": rid,
            "stage": p["stage"],
            "seq": p["seq"],
            "stage_seq": _stage_seq(modules, p["batch"], p["stage"]),
            "parent_run_id": (m.get("core_parent_run_id") or m.get("parent_run_id")
                              or parent_map.get(rid) or ""),
            "status": m.get("run_state") or "planned",
            "tool_id": m.get("machine_name") or "",
            "date": (m.get("core_date") or ""),
            "title": m.get("name") or "",
            "note": m.get("comment") or "",
            "recipe_id": m.get("core_recipe_id") or "",
            "module_id": m.get("id") or "",
        })
    rows.sort(key=lambda r: (r["stage_seq"], r["stage"], r["seq"]))
    return rows


_PARENT_CACHE: dict[str, str] | None = None


def _parent_map_from_packs() -> dict[str, str]:
    """从实验包的 runs.csv 里读 run_id → parent_run_id（进程内缓存；读只读资产，不写）。"""
    global _PARENT_CACHE
    if _PARENT_CACHE is not None:
        return _PARENT_CACHE
    import csv
    from pathlib import Path
    out: dict[str, str] = {}
    try:
        from .menu_reader import _workspace
        base = _workspace() / "个人空间/18_工艺数据资产"
        for p in list(base.rglob("runs.csv"))[:200]:
            try:
                with p.open(newline="", encoding="utf-8-sig") as f:
                    for r in csv.DictReader(f):
                        rid, par = (r.get("run_id") or "").strip(), (r.get("parent_run_id") or "").strip()
                        if rid:
                            out.setdefault(rid, par)
            except Exception:                     # noqa: BLE001
                continue
    except Exception:                             # noqa: BLE001
        pass
    _PARENT_CACHE = out
    return out


def _stage_seq(modules: list[dict], batch: str, stage: str) -> int:
    """本 batch 内该 stage 的工序序号。

    优先级：① core/runs.csv 已入库值（**权威**，续做必须沿用）② 画布内已有的同 batch 值
    ③ 习惯序表兜底 ④ 表外 stage 续编。
    """
    known = stage_seq_map().get((batch, stage))
    if known:
        return known
    for m in modules or []:
        p = parse_run_id(m.get("core_run_id") or "")
        if p and p["batch"] == batch and p["stage"] == stage:
            v = m.get("core_stage_seq")
            if isinstance(v, int) and v > 0:
                return v
    if stage in STAGE_ORDER:
        return STAGE_ORDER.index(stage) + 1
    extra: list[str] = []
    for m in modules or []:
        p = parse_run_id(m.get("core_run_id") or "")
        if p and p["batch"] == batch and p["stage"] not in STAGE_ORDER \
                and p["stage"] not in extra:
            extra.append(p["stage"])
    return len(STAGE_ORDER) + (extra.index(stage) + 1 if stage in extra else len(extra) + 1)


def batches_of(modules: list[dict]) -> list[dict]:
    """画布上出现过的 batch → 概览（含 run 数与 stage 链）。"""
    seen: dict[str, dict] = {}
    for m in modules or []:
        p = parse_run_id(m.get("core_run_id") or "")
        if not p:
            continue
        b = seen.setdefault(p["batch"], {"batch_id": p["batch"], "runs": 0, "stages": []})
        b["runs"] += 1
        if p["stage"] not in b["stages"]:
            b["stages"].append(p["stage"])
    for b in seen.values():
        b["chain"] = " → ".join(b["stages"])
    return sorted(seen.values(), key=lambda x: x["batch_id"])


def next_run(modules: list[dict], batch: str, stage: str, parent_run_id: str | None = None,
             stage_hint: int | None = None) -> dict:
    """算下一步 run 的标识（**序号由工具算，禁手输**）。

    - 序号：该 batch 该 stage 已有 run 的**最大序号 + 1**（不按数量，避免删过 run 后撞号）
    - parent：显式给就用；否则取该 batch 该 stage 的**最后一个 run**（续做语义）
    - stage_seq：同 stage 保持同号（复用现有值）
    """
    same = [r for r in runs_of_batch(modules, batch) if r["stage"] == stage]
    seq = (max((r["seq"] for r in same), default=0) + 1)
    parent = parent_run_id if parent_run_id is not None else (same[-1]["run_id"] if same else "")
    stage_seq = same[0]["stage_seq"] if same else (stage_hint or _stage_seq(modules, batch, stage))
    return {
        "batch_id": batch,
        "stage": stage,
        "seq": seq,
        "run_id": f"{batch}-{stage}-{seq:04d}",
        "parent_run_id": parent,
        "stage_seq": stage_seq,
        "is_continuation": bool(same),
    }


def chain_of(modules: list[dict], batch: str) -> dict:
    """画布上该 batch 的链是否自洽（给 UI 画链 + 验收用）。

    ⚠️ 画布工程只画**本次要跑的那一段**（AR50-T1 历史上是多次上机拼接），
    所以这里只校验"画布内可见的链"，不要求 12 个节点齐全。
    """
    rows = runs_of_batch(modules, batch)
    ids = {r["run_id"] for r in rows}
    broken = [r["run_id"] for r in rows if r["parent_run_id"] and r["parent_run_id"] not in ids]
    return {
        "batch_id": batch,
        "runs": rows,
        "count": len(rows),
        "nodes": len(rows),
        "edges": sum(1 for r in rows if r["parent_run_id"]),
        "roots": [r["run_id"] for r in rows if not r["parent_run_id"]],
        "dangling_parents": broken,     # parent 指向画布外的 run —— 正常(跨包续做)，不算错
    }

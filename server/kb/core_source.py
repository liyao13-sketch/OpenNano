"""数据域 core 只读适配器（协议 §11：core = 原始事实 · KB = 结论与规则 · 单向推导）。

core 位置：`个人空间/18_工艺数据资产/03_实验数据/core/`（CSV 权威 + process.db 索引）
本模块**只读 CSV**（绝不写 db、绝不改 CSV），为 OpenNano 工具提供：
  · 运行/测量/现象/证据 的查询（Agent 工具 query_core）
  · 建模数据集（opt 引擎：step 参数 → measurement 量）
  · 量名词/机台/阶段 词表（taxonomy 对齐：stage 枚举 + tool_id）
"""
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

from opennano_config import CORE_DIR  # 路径可在 .env 覆盖(发布用)

STAGES = {"PECVD", "LDW", "EBL", "MA6", "ICP", "RIE", "DRIE", "ASH", "EVAP",
          "SPUT", "LIFTOFF", "DICE", "SEM", "ELLIP", "STRESS", "PROFILE"}

# 工具侧旧 process_type → core stage（taxonomy 对齐）
PROCESS_TYPE_TO_STAGE = {
    "RIE_Cl": "RIE", "RIE_F": "RIE", "DRIE_Bosch": "DRIE",
    "ICP": "ICP", "PECVD": "PECVD", "EBL": "EBL", "LDW": "LDW", "MA6": "MA6",
}
# 工具侧结果字段 → core 量名词（同名者略）
FIELD_TO_QUANTITY = {
    "er_nm_min": "etch_rate_nm_min",     # core 暂无该量,待数据线新增
    "sidewall_angle_deg": "swa_deg",
    "roughness_nm": "roughness_nm",      # core 暂无
    "scallop": "scallop_nm",             # core 暂无
    "depth_center_nm": "depth_center_nm",
    "final_cd_nm": "final_cd_nm",
    "selectivity": "selectivity",
    "mask_loss_nm": "mask_loss_nm",
    "mask_remain_nm": "mask_remain_nm",
}


def available() -> bool:
    return (CORE_DIR / "runs.csv").exists()


def _rows(name: str) -> list[dict]:
    p = CORE_DIR / f"{name}.csv"
    if not p.exists():
        return []
    with open(p, newline="", encoding="utf-8-sig") as f:
        return [r for r in csv.DictReader(f)]


def _num(v):
    try:
        f = float(v)
        return f
    except (TypeError, ValueError):
        return None


# ---------- 查询 ----------

def stats() -> dict:
    tables = ["batches", "samples", "runs", "recipes", "steps",
              "measurements", "observations", "artifacts", "eq_state"]
    return {"core_dir": str(CORE_DIR), "available": available(),
            "counts": {t: len(_rows(t)) for t in tables}}


def runs(stage: str | None = None, tool_id: str | None = None,
         batch_id: str | None = None, limit: int = 200) -> list[dict]:
    out = []
    for r in _rows("runs"):
        if stage and r.get("stage") != stage:
            continue
        if tool_id and r.get("tool_id") != tool_id:
            continue
        if batch_id and r.get("batch_id") != batch_id:
            continue
        out.append(r)
    return out[-limit:]


def measurements(quantity: str | None = None, run_id: str | None = None,
                 limit: int = 500) -> list[dict]:
    out = []
    for m in _rows("measurements"):
        if quantity and m.get("quantity") != quantity:
            continue
        if run_id and m.get("run_id") != run_id:
            continue
        out.append(m)
    return out[-limit:]


def observations(run_id: str | None = None, limit: int = 200) -> list[dict]:
    return [o for o in _rows("observations")
            if not run_id or o.get("run_id") == run_id][-limit:]


def quantities() -> list[dict]:
    c = Counter((m.get("quantity"), m.get("unit")) for m in _rows("measurements"))
    return [{"quantity": q, "unit": u, "n": n} for (q, u), n in c.most_common()]


def tools() -> list[dict]:
    c = Counter((r.get("tool"), r.get("tool_id")) for r in _rows("runs"))
    return [{"tool": t, "tool_id": tid, "n": n} for (t, tid), n in c.most_common()]


def step_params(stage: str | None = None, tool_id: str | None = None) -> dict:
    """按 stage/tool 汇总 steps 的参数分布 {param: [值...]}(供先验中心点)。"""
    keep = {r["run_id"] for r in runs(stage=stage, tool_id=tool_id, limit=10000)}
    out: dict[str, list] = {}
    for st in _rows("steps"):
        if st.get("run_id") not in keep:
            continue
        try:
            pj = json.loads(st.get("param_json") or "{}")
        except Exception:  # noqa: BLE001
            pj = {}
        pre = str(st.get("step_name") or "step").strip().lower().replace(" ", "_")
        for k, v in pj.items():
            nv = _num(v)
            if nv is not None:
                out.setdefault(f"{pre}_{k}", []).append(nv)
        for k in ("duration_s", "pressure"):
            nv = _num(st.get(k))
            if nv is not None:
                out.setdefault(f"{pre}_{k}", []).append(nv)
    return out


def run_detail(run_id: str) -> dict:
    run = next((r for r in _rows("runs") if r.get("run_id") == run_id), None)
    if not run:
        return {"error": f"run not found: {run_id}"}
    steps = [s for s in _rows("steps") if s.get("run_id") == run_id]
    for s in steps:
        try:
            s["params"] = json.loads(s.get("param_json") or "{}")
        except Exception:  # noqa: BLE001
            s["params"] = {}
    return {"run": run, "steps": steps,
            "measurements": measurements(run_id=run_id, limit=999),
            "observations": observations(run_id=run_id, limit=99),
            "artifacts": [a for a in _rows("artifacts") if a.get("run_id") == run_id]}


def runs_wide(quantity: str | None = None, stage: str | None = None,
              tool_id: str | None = None, limit: int = 300) -> dict:
    """一次 run 一行的宽视图（含指定量的值 + 该 run 的全部量）。"""
    ms = _rows("measurements")
    by_run: dict[str, dict] = {}
    for m in ms:
        q = m.get("quantity")
        if not q:
            continue
        by_run.setdefault(m.get("run_id"), {})[q] = m.get("value")
    rows = []
    for r in runs(stage=stage, tool_id=tool_id, limit=10000):
        vals = by_run.get(r["run_id"], {})
        if quantity and quantity not in vals:
            continue
        rows.append({**{k: r.get(k) for k in
                        ("run_id", "batch_id", "stage", "date", "tool", "tool_id",
                         "recipe_id", "operator", "purpose")},
                     **vals})
    return {"quantity": quantity, "rows": rows[-limit:], "n": len(rows)}


# ---------- 建模数据集（opt 引擎用：step 参数 → measurement 量） ----------

def dataset(quantity: str, stage: str | None = None, tool_id: str | None = None,
            features: list[str] | None = None) -> dict:
    """core → {X, y, feature_names, run_ids}。

    特征 = 该 run 全部步骤的 param_json 展平（键加步骤前缀，如 me_rf_w）+ 时长/压强；
    目标 = 指定 quantity 的第一个 measurement 值（长表，需该 run 有该量）。
    """
    import numpy as np

    want = {m.get("run_id"): m for m in measurements(quantity=quantity, limit=100000)}
    keep = {r["run_id"] for r in runs(stage=stage, tool_id=tool_id, limit=10000)}
    feats: dict[str, dict] = {}
    for s in _rows("steps"):
        rid = s.get("run_id")
        if rid not in keep or rid not in want:
            continue
        try:
            params = json.loads(s.get("param_json") or "{}")
        except Exception:  # noqa: BLE001
            params = {}
        prefix = str(s.get("step_name") or "step").strip().lower().replace(" ", "_")
        d = feats.setdefault(rid, {})
        for k, v in params.items():
            nv = _num(v)
            if nv is not None:
                d[f"{prefix}_{k}"] = nv
        for k in ("duration_s", "pressure"):
            nv = _num(s.get(k))
            if nv is not None:
                d[f"{prefix}_{k}"] = nv

    run_ids = [rid for rid in feats if _num((want.get(rid) or {}).get("value")) is not None]
    if not run_ids:
        return {"X": None, "y": None, "feature_names": [], "run_ids": [],
                "skipped": len(want)}
    if features:
        names = [f for f in features if any(f in feats[r] for r in run_ids)]
    else:
        names = sorted({k for r in run_ids for k in feats[r]})
        arr = np.array([[feats[r].get(n, np.nan) for n in names] for r in run_ids])
        names = [names[i] for i in range(len(names)) if np.nanvar(arr[:, i]) > 1e-12]
    X = np.array([[feats[r].get(n, np.nan) for n in names] for r in run_ids])
    y = np.array([_num(want[r]["value"]) for r in run_ids], dtype=float)
    cm = np.nanmean(X, axis=0)
    pos = np.isnan(X)
    if pos.any():
        X[pos] = np.take(cm, np.where(pos)[1])
    return {"X": X, "y": y, "feature_names": names, "run_ids": run_ids,
            "skipped": len(want) - len(run_ids), "unit": (want[run_ids[0]].get("unit") or "")}

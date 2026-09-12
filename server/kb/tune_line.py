"""参数调试线（O1 单点优化视图）—— 只读数据线的 `v_tune_line` 视图。

出处（2026-09-12 · 数据线 续187 / 工单 20260912-工具线-to-数据线-01）：
    数据线已在 `core/process.db` 建好视图 **`v_tune_line`**（25 列）：
    参数轴（t_set_s / t_dwell_s / source_w / bias_w / 各气体）+ 响应列
    （cd_delta_nm / depth_nm / er_nm_min / selectivity / film_thickness_nm / stress_mpa / refractive_index），
    **缺的就是 NULL、不报错不补值**。工具侧画纵向调试线**直接查它**，不自己拼表（07 §G.26）。

纪律：
    · **只读**（`file:...?mode=ro` 打开 sqlite）；process.db 是派生物，读它不改它。
    · **缺就明说**：db/视图不存在 ⇒ `available=False` + 怎么修，绝不静默返回空表
      （与 `form_contract.ContractUnavailable` 同一套语义）。
    · **可比性只报不猜**：同一 `tune_id` 内哪个响应列有 ≥2 个非空值才算"可比的量纲"，
      不够就如实标注（TUNE1 现状：cd_delta 有 2 点、depth 只有 1 点、step3 全空）。
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

#: 视图列分两组（与数据线 v_tune_line 的列序一致；画图/制表都按这个分组）
PARAM_COLS = ["t_set_s", "t_dwell_s", "source_w", "bias_w", "bias_w_actual",
              "chf3_sccm", "ar_sccm", "o2_sccm", "cf4_sccm", "sf6_sccm"]
RESPONSE_COLS = ["cd_delta_nm", "depth_nm", "er_nm_min", "selectivity",
                 "film_thickness_nm", "stress_mpa", "refractive_index"]
META_COLS = ["tune_id", "tune_step", "run_id", "date", "stage", "tool", "sample_id"]


class TuneLineUnavailable(RuntimeError):
    """`core/process.db` 或 `v_tune_line` 视图拿不到（数据资产未装载 / 还没重建）。"""


def _db_path() -> Path:
    from .batch_runs import core_runs_path
    p = core_runs_path()
    if not p:
        raise TuneLineUnavailable("找不到 core 目录（OPENNANO_CORE_DIR 未解析）")
    return Path(p).parent / "process.db"


def _connect() -> sqlite3.Connection:
    db = _db_path()
    if not db.exists():
        raise TuneLineUnavailable(
            f"没有 {db}（core 派生库；请数据侧跑一次 ingest/build_core.py 重建）")
    try:
        return sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    except sqlite3.Error as e:
        raise TuneLineUnavailable(f"打不开 {db}（只读模式）：{e}") from e


def tune_lines(batch: str = "") -> dict:
    """读 `v_tune_line` → 按 `tune_id` 分组的调试线。

    返回 {"available": True, "series": [...], "note": …}；
    视图不存在 ⇒ {"available": False, "reason": …}（不抛给前端 500）。
    """
    try:
        con = _connect()
    except TuneLineUnavailable as e:
        return {"available": False, "reason": str(e), "series": []}
    try:
        has = con.execute(
            "select count(*) from sqlite_master where type='view' and name='v_tune_line'"
        ).fetchone()[0]
        if not has:
            return {"available": False,
                    "reason": "process.db 里没有 v_tune_line 视图"
                              "（数据侧版本偏旧；请其重建 core）",
                    "series": []}
        sql = ("select * from v_tune_line" + (" where tune_id like ?" if batch else "")
               + " order by tune_id, cast(tune_step as integer)")
        cur = con.execute(sql, (f"{batch}%",) if batch else ())
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        con.close()

    series: dict[str, dict] = {}
    for r in rows:
        tid = r.get("tune_id") or ""
        if not tid:
            continue
        s = series.setdefault(tid, {"tune_id": tid, "steps": []})
        s["steps"].append(r)

    out = []
    for tid, s in sorted(series.items()):
        steps = s["steps"]
        # 可比性：每个响应列的非空点数；≥2 才算"有第二把尺可拟合"
        resp_avail = {c: sum(1 for r in steps if r.get(c) not in (None, ""))
                      for c in RESPONSE_COLS if any(r.get(c) not in (None, "") for r in steps)}
        param_avail = {c: sum(1 for r in steps if r.get(c) not in (None, ""))
                       for c in PARAM_COLS if any(r.get(c) not in (None, "") for r in steps)}
        comparable = {c: n for c, n in resp_avail.items() if n >= 2}
        empty_steps = [str(r.get("tune_step") or "?") for r in steps
                       if all(r.get(c) in (None, "") for c in RESPONSE_COLS)]
        out.append({
            "tune_id": tid,
            "stage": steps[0].get("stage") or "",
            "sample_id": steps[0].get("sample_id") or "",
            "n_steps": len(steps),
            "steps": steps,
            "param_cols": [c for c in PARAM_COLS if c in param_avail],
            "response_cols": [c for c in RESPONSE_COLS if c in resp_avail],
            "param_avail": param_avail,
            "response_avail": resp_avail,
            "comparable_responses": comparable,
            "comparability_note": (
                f"响应可比 {len(comparable)}/{len(resp_avail)}"
                + (f"（可拟合：{'、'.join(comparable)}）" if comparable
                   else "（**没有任何响应列有 ≥2 个点**）")
                + (f"；全空轮次：step {'、'.join(empty_steps)}" if empty_steps else "")),
        })
    return {
        "available": True,
        "series": out,
        "source": "core/process.db · v_tune_line（数据线视图，只读）",
        "note": ("缺的格就是 NULL（不补值）；**可比性只按响应列非空点数报**，"
                 "量纲定义以 schema 为准（`cd_delta_nm` 定义工单已请数据线补写）。"),
    }

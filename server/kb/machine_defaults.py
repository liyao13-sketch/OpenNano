"""设备/机台的**实测默认参数**（只读 core 推出来，不是手写常量）。

为什么（2026-09-13 owner："根据目前我们设备使用的参数，更新一下各个设备的默认参数。
很多设备的配置和参数都明确了。DRIE 甚至和 ICP 是一样的"）：

    设备模板（`engine/library.py` 里的 `eq_*`）当初是按"通用教科书写法"填的占位值，
    与**我们实际在用的键名和数值**是两套东西：
      模板：`rf_power / pressure / gas_SF6 / gas_O2 …`
      core：`source_w / bias_w / chf3_sccm / ar_sccm / chuck_temp_c / t_set_s …`
    ⇒ 画布上新建/回灌出来的节点，默认参数自然就是"像 ICP 的 DRIE"这种错配。

本模块的做法：**从 core 的真实历史里推**每个（机台 × 工序）的默认值 ——
    · 默认值 = **最近一次**用过的该键的值（"我们现在的用法"）；
    · 同时给出该键的历史 **n 次 / 最小~最大**（"我们用过的范围"）；
    · 每个值都带**来源 run 与日期**（可追溯，不是凭空来的）。
绝不写死常量、绝不改 core；core 拿不到就如实返回 available=false。

用法：
    python3 -m kb.machine_defaults                 # 人读全部机台
    python3 -m kb.machine_defaults --stage DRIE    # 只看某工序
    python3 -m kb.machine_defaults --json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _rows() -> tuple[list[dict], dict[str, list[dict]]]:
    from .batch_runs import core_runs_path
    import csv
    p = core_runs_path()
    if not p or not Path(p).exists():
        return [], {}
    with Path(p).open(newline="", encoding="utf-8-sig") as f:
        runs = list(csv.DictReader(f))
    sp = Path(p).parent / "steps.csv"
    by_run: dict[str, list[dict]] = {}
    if sp.exists():
        with sp.open(newline="", encoding="utf-8-sig") as f:
            for s in csv.DictReader(f):
                by_run.setdefault(s.get("run_id") or "", []).append(s)
    return runs, by_run


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _machine_index() -> list[dict]:
    """library 里的机台（用于把 core 的 tool_id 对上机台档案）。

    机台表在 `engine.library.LibraryStore`（**实例**，不是模块级函数）；
    拿不到就返回空表 —— 对齐不上时**如实列进 unmatched_tool_ids**，不硬猜。
    """
    try:
        from engine.library import LibraryStore             # type: ignore
        return list(LibraryStore().data.get("machines") or [])
    except Exception:                                       # noqa: BLE001
        return []


def _norm(s: str) -> str:
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


def _match_machine(tool_id: str, machines: list[dict]) -> dict | None:
    """把 core 的 `tool_id`（如 `ICP-PishowA`）对上机台档案。

    顺序（**宁可不匹配，也不硬猜**）：
      ① 名字完全相同 ② 型号完全相同
      ③ 归一化后互相包含 —— **但必须唯一命中**；命中多台（如 `RIE` 同时像 RIE200NL/RIE10NR）
         一律判为**未匹配**并如实报出，让人去修 core 里的 tool_id。
    """
    t = (tool_id or "").strip()
    if not t:
        return None
    for m in machines:
        if (m.get("name") or "").strip() == t:
            return m
    for m in machines:
        if (m.get("model") or "").strip() == t:
            return m
    nt = _norm(t)
    if not nt:
        return None
    hits = [m for m in machines
            if any(nc and (nc in nt or nt in nc)
                   for nc in (_norm(m.get("name") or ""), _norm(m.get("model") or "")))]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        return None                                     # 多义 ⇒ 不猜
    # ④ 特征词命中（≥5 字符的片段）—— `ICP-PishowA` ↔ 型号 `Hassrode PishowA` 靠这个对上
    import re as _re
    toks = {w for w in _re.split(r"[^0-9a-zA-Z]+", t) if len(w) >= 5}
    for tok in toks:
        ntok = _norm(tok)
        th = [m for m in machines
              if any(ntok in nc for nc in (_norm(m.get("name") or ""), _norm(m.get("model") or "")))]
        if len(th) == 1:
            return th[0]
    return None


def _phase_of(s: dict, pj: dict) -> str:
    """该步属于哪一段（chuck / etch / dechuck / …）。

    ⚠️ 必须分段：DRIE 是 **19 步多段工艺**，若按"最后一次的值"扁平取，
    `bias_w` 会取到 dechuck 段末尾的 0（2026-09-13 实测踩到）——那完全不是刻蚀参数。
    """
    for k in ("phase", "阶段"):
        v = (pj.get(k) or "").strip()
        if v:
            return v
    role = (s.get("role") or "").strip()
    if role:
        return role
    name = (s.get("step_name") or "").strip()
    return name.split("-")[0] if name else "etch"


def machine_defaults(stage: str = "") -> dict:
    """每个（机台 × 工序）的实测默认参数，**按段（chuck/etch/dechuck）分开给**。只读 core。"""
    runs, by_run = _rows()
    if not runs:
        return {"available": False, "reason": "拿不到 core/runs.csv（数据资产未装载？）",
                "groups": []}
    machines = _machine_index()

    groups: dict[tuple[str, str], list[dict]] = {}
    for r in runs:
        st = (r.get("stage") or "").strip()
        tool = (r.get("tool_id") or "").strip()
        if stage and st.upper() != stage.upper():
            continue
        if not tool or not by_run.get(r.get("run_id") or ""):
            continue                                    # 没记机台 / 没参数步 ⇒ 不参与（不猜）
        groups.setdefault((tool, st), []).append(r)

    out, unmatched = [], set()
    for (tool, st), rs in sorted(groups.items()):
        rs.sort(key=lambda r: ((r.get("date") or ""), r.get("run_id") or ""))
        latest = rs[-1]
        # 按段收集：phase → key → [(值, 日期, run, 步序)]
        phases: dict[str, dict[str, list[tuple]]] = {}
        run_of_phase: dict[str, str] = {}
        for r in rs:
            for s in by_run.get(r.get("run_id") or "", []):
                try:
                    pj = json.loads(s.get("param_json") or "{}")
                except json.JSONDecodeError:
                    continue
                ph = _phase_of(s, pj)
                run_of_phase.setdefault(ph, r.get("run_id") or "")
                bucket = phases.setdefault(ph, {})
                for k, v in pj.items():
                    if v in (None, ""):
                        continue
                    bucket.setdefault(k, []).append(
                        (v, r.get("date") or "", r.get("run_id") or "", s.get("step_order") or 0))

        def _pick(vals: list[tuple]) -> dict:
            """默认值 = **该段内出现最多**的值；并列时取**最近**那条（可复现，不随机）。"""
            from collections import Counter
            cnt = Counter(str(v[0]) for v in vals)
            top = max(cnt.values())
            cands = [v for v in vals if cnt[str(v[0])] == top]
            best = max(cands, key=lambda v: (v[1], v[2], str(v[3])))
            nums = [_num(v[0]) for v in vals]
            nums = [n for n in nums if n is not None]
            return {"value": best[0], "from_run": best[2], "date": best[1],
                    "n": len(vals), "n_distinct": len(cnt),
                    "min": min(nums) if nums else None, "max": max(nums) if nums else None,
                    "samples": [v[0] for v in vals[:5]]}

        by_phase = {}
        for ph, keys in phases.items():
            per_key = {k: _pick(v) for k, v in keys.items()}
            by_phase[ph] = {
                "params": {k: v["value"] for k, v in per_key.items()},
                "per_key": per_key,
                "n_keys": len(per_key),
            }

        mach = _match_machine(tool, machines)
        if mach is None:
            unmatched.add(tool)
        only = list(by_phase.values())
        out.append({
            "tool_id": tool,
            "machine_id": (mach or {}).get("id", ""),
            "machine_name": (mach or {}).get("name", ""),
            "model": (mach or {}).get("model", ""),
            "stage": st,
            "n_runs": len(rs),
            "as_of": latest.get("date") or "",
            "from_run": latest.get("run_id") or "",
            "phases": sorted(by_phase),
            "by_phase": by_phase,
            "params": (only[0]["params"] if len(only) == 1 else {}),
            "per_key": (only[0]["per_key"] if len(only) == 1 else {}),
            "sample_runs": [r.get("run_id") for r in rs[-3:]],
        })
    return {
        "available": True,
        "groups": out,
        "count": len(out),
        "unmatched_tool_ids": sorted(unmatched),
        "source": "core/runs.csv + core/steps.csv（只读）",
        "rule": ("**按段**（phase/role）分别取默认值：该段内**出现最多**的值（并列取最近那条）；"
                 "`per_key` 另给 n 次 / 不同值数 / min~max；每个值带 from_run+date 可追溯。"
                 "没记机台或没有参数步的 run **不参与**（不猜）。"),
        "note": ("⚠️ 设备模板（engine）里的参数键名与 core/契约的键名目前是**两套**"
                 "（模板 `rf_power/gas_SF6` vs 实际 `source_w/bias_w/chf3_sccm`）——"
                 "键名统一属【跨线】决定；本模块只给**实测数值**，不改模板键名。"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="机台实测默认参数（只读 core）")
    ap.add_argument("--stage", default="", help="只看某工序，如 DRIE / ICP / RIE")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    res = machine_defaults(a.stage)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res.get("available") else 1
    if not res.get("available"):
        print("不可用：", res.get("reason"))
        return 1
    print(f"机台实测默认参数：{res['count']} 组（{res['source']}）")
    for g in res["groups"]:
        tag = f"{g['machine_name'] or g['tool_id']}"
        print(f"\n  [{g['stage']}] {g['tool_id']}（机台档案 {tag}"
              f"{' · ' + g['model'] if g['model'] else ''}）"
              f" · {g['n_runs']} 次 · 最近 {g['as_of']} {g['from_run']}"
              f" · 段={g['phases']}")
        for ph in g["phases"]:
            blk = g["by_phase"][ph]
            print(f"    ── {ph}（{blk['n_keys']} 个键）")
            for k, v in blk["params"].items():
                meta = blk["per_key"][k]
                rng = (f"{meta['min']:g}~{meta['max']:g}"
                       if meta["min"] is not None and meta["max"] is not None else "")
                extra = (f" · 范围 {rng}" if rng and meta["min"] != meta["max"] else "")
                print(f"      {k:22} = {str(v):>10}   （n={meta['n']}"
                      f"{' · ' + str(meta['n_distinct']) + ' 种值' if meta['n_distinct'] > 1 else ''}{extra}）")
    if res["unmatched_tool_ids"]:
        print("\n  ⚠️ 对不上机台档案的 tool_id（需补/纠机台记录）：", res["unmatched_tool_ids"])
    print("\n" + res["note"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

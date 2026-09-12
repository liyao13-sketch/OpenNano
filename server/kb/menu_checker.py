"""设备菜单导出**批量解析 + 体检报告**（跨线交接单 §7.5：拿到新机台 dump 先体检）。

为什么需要它：
    工艺侧每次导出菜单后，得有人回答"这份导出能不能用"——现在只能靠人工比。
    本模块一次扫多台/多次导出，自动回答五个问题：
      ① 配对可靠吗（.grp/.rcp 是否同刻导出）
      ② 列认全了吗（哪些原始列还没映射成规范键）
      ③ 配方是空壳吗（有名/无名、空槽数、无有效步）
      ④ 越界了吗（G50+ 他人菜单，必须剔）
      ⑤ 与上一次 dump 比**名字↔槽位漂移**了吗

边界（零号铁律）：本模块**只读** dump、**只出报告**，不写任何数据资产；
未映射列只是"提出来给人看"，不自动改契约、不推断列的含义。
"""
from __future__ import annotations

import re
from pathlib import Path

from . import form_contract as fc
from .menu_reader import (SCOPE_MAX, _stamp_minutes, _stamp_text, group_steps,
                          load_menu, parser, step_columns)

DATE_RE = re.compile(r"(20\d{6})")

#: **按规则故意不映射**成 param_json 键的列（不算"漏映射"）：
#: §13.4 时间列 ⚠️ → 合成秒值写 `steps.duration_s`，不散成三个键。
RULE_HANDLED_COLS = {
    "Process time Hr.", "Process time Hr. Ramp",
    "Process time min.", "Process time min. Ramp",
    "Process time sec.", "Process time sec. Ramp",
}
RULE_HANDLED_NOTE = "§13.4：时间列合成秒值进 steps.duration_s（不散成键）"


def discover_dumps(root: str | Path) -> list[dict]:
    """扫出 root 下所有"含 .grp/.rcp 的目录"（= 一次导出），按日期排序。"""
    root = Path(root).expanduser()
    out: list[dict] = []
    if root.is_file():
        root = root.parent
    if not root.is_dir():
        return out
    seen: set[Path] = set()
    for pat in ("*.grp", "*.rcp"):
        for p in root.rglob(pat):
            d = p.parent
            if d in seen:
                continue
            seen.add(d)
            files = sorted(list(d.glob("*.grp")) + list(d.glob("*.rcp")))
            dates = [m.group(1) for f in files if (m := DATE_RE.search(f.name))]
            out.append({
                "dir": str(d),
                "name": d.name,
                "date": max(dates) if dates else "",
                "files": [{"name": f.name, "stamp": _stamp_text(f),
                           "minutes": _stamp_minutes(f)} for f in files],
            })
    out.sort(key=lambda x: (x["date"], x["name"]))
    return out


def _pairing(dump: dict) -> dict:
    mins = [f["minutes"] for f in dump["files"] if f.get("minutes")]
    if len(mins) < 2:
        return {"ok": None, "delta_min": None, "warning": "只有一份文件（.grp/.rcp 不全）⇒ 无法配对"}
    delta = abs(max(mins) - min(mins))
    from .menu_reader import PAIR_TOL_MIN
    if delta > PAIR_TOL_MIN:
        return {"ok": False, "delta_min": round(delta, 1),
                "warning": f"相差 {delta:.0f} 分钟 > {PAIR_TOL_MIN} ⇒ 名字↔槽位配对**不可靠**，请同刻重导"}
    return {"ok": True, "delta_min": round(delta, 1), "warning": None}


def _step_slots(rec: dict) -> set[int]:
    return {int(s.get("machine_step", s.get("i") or 0)) for s in (rec.get("steps") or [])}


def _fingerprint_sig(dm, grp_files) -> dict:
    """名字 ↔ 槽位 指纹（用于跨 dump 漂移检测）。"""
    sig = {}
    for p in grp_files:
        for r in dm.parse_grp(p):
            slot = int(r["recipe_id"][-3:])
            if slot > SCOPE_MAX:
                continue
            sig[slot] = {"name": (r.get("name") or "").strip(),
                         "steps": len(__import__("json").loads(r["params_json"] or "{}").get("steps", []))}
    return sig


def check_dump(export_dir: str | Path) -> dict:
    """单次导出的体检结果。"""
    dm = parser()
    m = load_menu(export_dir)
    root = Path(export_dir)
    grps = sorted(root.rglob("*.grp"))
    dump = {"dir": str(root), "name": root.name,
            "date": (m["grp_files"][0]["stamp"][:10].replace("-", "") if m["grp_files"] else ""),
            "files": [{"name": f["name"], "stamp": f["stamp"],
                       "minutes": _stamp_minutes(root / f["name"])} for f in m["grp_files"] + m["rcp_files"]]}

    # ① 配对
    pairing = _pairing(dump)

    # ③ 配方质量
    recipes, empty_named, no_steps, empty_slot_total = [], [], [], 0
    base_keys = set(fc.param_keys())
    used_raw_cols: set[str] = set()
    for r in m["recipes"]:
        try:
            rec = dm.recipe_by_slot(r["slot"], root=root)
        except (SystemExit, Exception):                       # noqa: BLE001
            continue
        steps_raw = rec.get("steps") or []
        if steps_raw:
            used_raw_cols |= {k for s in steps_raw for k in (s.get("params") or {})}
        ex = dm.executed_steps(rec)
        empty_slot_total += len(rec.get("empty_slots") or [])
        item = {"slot": r["slot"], "name": r["name"], "defined": len(steps_raw),
                "executed": len(ex), "empty_slots": rec.get("empty_slots") or [],
                "loop": rec.get("loop") or {}, "zone": r["zones"]}
        recipes.append(item)
        if not r["name"]:
            empty_named.append(r["slot"])
        if steps_raw and not ex:
            no_steps.append(r["slot"])

    # ② 未映射列：原始列 → 规范键 的缺口
    cols: list[str] = []
    for p in grps:
        cols = step_columns(p) or cols
        if cols:
            break
    mapped_cols = set()
    for k, v in fc.param_keys().items():
        if v.get("from") and v["from"] != "(结构键)":
            mapped_cols.add(v["from"].replace(" Ramp", "").strip())
    unmapped = [c for c in cols
                if c and c not in ("Step type", "Step No", "Step no", "No")
                and c not in RULE_HANDLED_COLS                      # §13.4 故意不映射
                and c.replace(" Ramp", "").strip() not in mapped_cols]

    # ⑤ 与上一次 dump 漂移（同名 recipe 槽位是否换了名字）
    drift = _compare_with_previous(export_dir, {r["slot"]: r["name"] for r in recipes})

    # 验收/建议
    warns: list[str] = []
    if pairing["warning"]:
        warns.append(pairing["warning"])
    if unmapped:
        warns.append(f"{len(unmapped)} 个原始列未映射成规范键：{unmapped[:6]}"
                     f"{'…' if len(unmapped) > 6 else ''} ⇒ 需补 §13.2 契约（LLM 可提候选，人裁决）")
    if empty_named:
        warns.append(f"{len(empty_named)} 个槽无配方名（空壳）：{empty_named[:8]}")
    if no_steps:
        warns.append(f"{len(no_steps)} 个槽有定义但**无实际执行步**：{no_steps[:8]}")
    if m["skipped_out_of_scope"]:
        warns.append(f"{m['skipped_out_of_scope']} 个槽属 G50+ 他人菜单，已剔除（不入库/不外发）")
    if drift["changed"]:
        warns.append(f"与上一次导出相比 {len(drift['changed'])} 个槽的**名字变了** ⇒ 疑似配对漂移")

    return {
        "ok": not warns,
        "dump": dump,
        "pairing": pairing,
        "counts": {"recipes": len(recipes), "groups": len(m["groups"]),
                   "out_of_scope": m["skipped_out_of_scope"],
                   "empty_named": len(empty_named), "no_executed_steps": len(no_steps),
                   "empty_step_slots": empty_slot_total},
        "step_columns": {"total": len(cols), "unmapped": unmapped,
                         "rule_handled": sorted(set(cols) & RULE_HANDLED_COLS),
                         "rule_handled_note": RULE_HANDLED_NOTE},
        "recipes": recipes,
        "groups": [g["slot"] for g in m["groups"]],
        "drift": drift,
        "warnings": warns,
        "compare_to": drift.get("prev"),
    }


def _compare_with_previous(export_dir: str | Path, cur: dict[int, str]) -> dict:
    """同机台目录下，找上一次 dump 比名字。"""
    dm = parser()
    root = Path(export_dir).expanduser()
    dumps = discover_dumps(root.parent)
    mine = str(root)
    prev = None
    for d in dumps:
        if d["dir"] == mine:
            break
        prev = d
    if not prev:
        return {"prev": None, "changed": [], "added": [], "removed": []}
    prev_sig = _fingerprint_sig(dm, sorted(Path(prev["dir"]).glob("*.grp")))
    changed = [{"slot": s, "was": prev_sig[s]["name"], "now": cur.get(s, "")}
               for s in sorted(cur) if s in prev_sig and prev_sig[s]["name"] != cur.get(s, "")]
    return {"prev": prev["name"], "changed": changed,
            "added": sorted(set(cur) - set(prev_sig)),
            "removed": sorted(set(prev_sig) - set(cur))}


def check_tree(root: str | Path) -> dict:
    """批量体检：扫一棵树下所有导出，逐个出报告 + 汇总。"""
    root = Path(root).expanduser()
    dumps = discover_dumps(root)
    reports = []
    for d in dumps:
        try:
            reports.append(check_dump(d["dir"]))
        except Exception as e:                                # noqa: BLE001
            reports.append({"ok": False, "dump": d, "error": f"{type(e).__name__}: {e}",
                            "warnings": [f"解析失败：{e}"], "counts": {}, "step_columns": {}})
    summary = {
        "root": str(root),
        "dumps": len(reports),
        "ok": sum(1 for r in reports if r.get("ok")),
        "with_warnings": sum(1 for r in reports if not r.get("ok")),
        "total_recipes": sum(r.get("counts", {}).get("recipes", 0) for r in reports),
        "unmapped_cols": sorted({c for r in reports for c in (r.get("step_columns", {}).get("unmapped") or [])}),
        "pairing_bad": [r["dump"]["name"] for r in reports if (r.get("pairing") or {}).get("ok") is False],
        "drifted": [r["dump"]["name"] for r in reports if (r.get("drift") or {}).get("changed")],
    }
    summary["verdict"] = ("全部可用" if summary["with_warnings"] == 0
                          else f"{summary['with_warnings']}/{summary['dumps']} 份有问题，见各条 warnings")
    return {"summary": summary, "reports": reports}


def report_text(res: dict) -> str:
    """人读报告（给面板和回执用）。"""
    L: list[str] = []
    s = res["summary"]
    L.append(f"批量体检：{s['dumps']} 份导出 · 可用 {s['ok']} · 有问题 {s['with_warnings']}")
    L.append(f"结论：{s['verdict']}")
    if s["unmapped_cols"]:
        L.append(f"未映射列（需补契约）：{', '.join(s['unmapped_cols'][:10])}")
    if s["pairing_bad"]:
        L.append(f"配对不可靠：{', '.join(s['pairing_bad'])}")
    if s["drifted"]:
        L.append(f"疑似名字↔槽位漂移：{', '.join(s['drifted'])}")
    L.append("")
    for r in res["reports"]:
        d = r.get("dump", {})
        c = r.get("counts", {})
        mark = "✅" if r.get("ok") else "⚠️"
        L.append(f"{mark} {d.get('name')}（{d.get('date') or '无日期'}）"
                 f" recipe {c.get('recipes', 0)} · group {c.get('groups', 0)}"
                 f" · 空壳 {c.get('empty_named', 0)} · 无执行步 {c.get('no_executed_steps', 0)}")
        for w in r.get("warnings", []):
            L.append(f"    - {w}")
    return "\n".join(L)

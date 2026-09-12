"""按工艺序**重排工程画布**（坐标 + 连线），只读 core，默认干跑。

为什么单独一个模块（而不是塞进 `repair_edges`）：
    · `repair_edges` 管的是"**删掉被编造的 parent 边**"（数据归属问题）；
    · 本模块管的是"**按工艺顺序把画布摆一次**"（显示问题）。
    两件事的判据不同，混在一起会互相掩盖 —— 2026-09-13 owner要"按准确流程显示一次"。

重排规则（全部来自 core，工具不发明）：
    · **x** = 工序列：`stage_seq` 决定第几列 ⇒ 左到右就是 PECVD → LDW → ICP → ASH → DRIE；
    · **y** = 主行放核心链、并存试验往下缩（见 `expack._layout_modules`）；
    · **连线** = `expack._edges_from_runs`：
        实线 `recorded`（core 的 parent 明确写的）／ 虚线 `inferred`（按工艺序补的显示边）。
    · **不在 core 里的节点**（还没入库的计划 run，如 DRIE-0002）⇒ **保留它们原有的边与父**，
      否则"计划中的下一步"会在重排时被抹掉。
    · 测量 / 现象 / 备注 / 参数 / 机台 / 环境，**一律不动**。

用法：
    python3 -m kb.relayout AR50-T1 [--file 路径] [--write]
"""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path

PROJECTS_DIR = Path.home() / ".opennano" / "projects"

_LINK_RECORDED = "recorded"


def _core_runs(batch: str) -> list[dict]:
    import csv
    from .batch_runs import core_runs_path
    p = core_runs_path()
    if not p or not Path(p).exists():
        return []
    rows = [r for r in csv.DictReader(Path(p).open(newline="", encoding="utf-8-sig"))
            if (r.get("batch_id") or "").strip() == batch]
    rows.sort(key=lambda r: ((r.get("date") or ""), int(r.get("stage_seq") or 0),
                             r.get("run_id") or ""))
    return rows


def relayout_project(path: Path, batch: str = "", write: bool = False) -> dict:
    """就地重排一个工程文件的坐标与连线，返回摘要（不写盘，除非 `write=True`）。"""
    from .expack import _edges_from_runs, _layout_modules
    path = Path(path).expanduser()
    if not path.exists():
        return {"ok": False, "error": f"工程文件不存在：{path}"}
    proj = json.loads(path.read_text(encoding="utf-8"))
    mods = proj.get("modules") or []
    if not batch:
        rids = [m.get("core_run_id") or "" for m in mods]
        cands = sorted({r.rsplit("-", 2)[0] for r in rids if r.count("-") >= 2})
        batch = cands[0] if len(cands) == 1 else ""
    if not batch:
        return {"ok": False, "error": f"无法确定批次：{path.name}"}

    rows = _core_runs(batch)
    by_run = {m.get("core_run_id"): m for m in mods if m.get("core_run_id")}
    id_of = {rid: m.get("id") for rid, m in by_run.items() if rid}
    present = [r for r in rows if (r.get("run_id") or "") in by_run]

    # ① 坐标：只对 core 里有的 run 重排（未入库的计划 run 保留原坐标与相对位置）
    core_mods = [by_run[r["run_id"]] for r in present]
    _layout_modules(present, core_mods)
    # 计划 run：挂在父的右侧一列（保持"下一步"的视觉位置）
    for rid, m in by_run.items():
        if rid in {r["run_id"] for r in present}:
            continue
        par = id_of.get((m.get("core_parent_run_id") or "").strip())
        pm = next((x for x in mods if x["id"] == par), None)
        if pm:
            m["x"], m["y"] = float(pm.get("x") or 0) + 300, float(pm.get("y") or 0)

    # ② 连线：记录边 + 推断边，再补上"计划 run"的原有父边（core 里没有它，不能丢）
    edges = _edges_from_runs(present, id_of, [m["id"] for m in core_mods])
    keys = {(e["src"], e["dst"]) for e in edges}
    core_parent = {(r.get("run_id") or "").strip(): (r.get("parent_run_id") or "").strip()
                   for r in rows}
    for rid, m in by_run.items():
        if rid in {r["run_id"] for r in present}:
            continue
        # 计划 run 的父可能只记在 core（若它已入库过）或只记在模块上 ⇒ 两处都看，取到就用
        par = id_of.get((m.get("core_parent_run_id") or "").strip()) \
            or id_of.get(core_parent.get(rid, ""))
        if par and par != m["id"] and (par, m["id"]) not in keys:
            edges.append({"src": par, "dst": m["id"], "_link": _LINK_RECORDED})
            keys.add((par, m["id"]))
        if par:
            m["core_parent_run_id"] = next(
                (k for k, v in id_of.items() if v == par), m.get("core_parent_run_id"))
    # 工程里那些"父/子都不在 core、也不在本批"的旧连线：原样保留（工具不擅自删）
    for e in proj.get("edges") or []:
        k = (e.get("src"), e.get("dst"))
        if k not in keys and all(x.get("id") != e.get("src") for x in mods):
            edges.append(e)
            keys.add(k)

    n_rec = sum(1 for e in edges if e.get("_link") != "inferred")
    res = {
        "ok": True, "file": str(path), "batch": batch,
        "modules": len(mods), "from_core": len(present),
        "planned_only": len(by_run) - len(present),
        "edges": len(edges), "recorded": n_rec, "inferred": len(edges) - n_rec,
        "edges_before": len(proj.get("edges") or []),
    }
    if write:
        bak = path.with_suffix(f".json.bak-relayout-{datetime.now():%Y%m%d%H%M%S}")
        shutil.copy2(path, bak)
        proj["edges"] = edges
        path.write_text(json.dumps(proj, ensure_ascii=False, indent=2), encoding="utf-8")
        res["backup"] = str(bak)
        res["written"] = True
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description="按工艺序重排工程画布（默认干跑）")
    ap.add_argument("batch", nargs="?", default="", help="批次号（也用于定位同名工程文件）")
    ap.add_argument("--file", default="", help="工程 JSON 路径")
    ap.add_argument("--all", action="store_true", help="扫 ~/.opennano/projects/*.json")
    ap.add_argument("--write", action="store_true", help="落盘（自动备份）")
    a = ap.parse_args()
    targets: list[tuple[Path, str]] = []
    if a.file:
        targets.append((Path(a.file), a.batch))
    elif a.all:
        targets += [(p, "") for p in sorted(PROJECTS_DIR.glob("*.json"))]
    elif a.batch:
        targets.append((PROJECTS_DIR / f"{a.batch}.json", a.batch))
    else:
        ap.error("给一个批次号，或 --file/--all")

    bad = 0
    for path, batch in targets:
        r = relayout_project(path, batch, write=a.write)
        if not r.get("ok"):
            print(f"⚠️ {path.name}：{r['error']}")
            bad += 1
            continue
        print(f"{'✍️' if r.get('written') else '🔍'} {path.name}（{r['batch']}）："
              f"节点 {r['modules']}（core {r['from_core']} · 计划 {r['planned_only']}）· "
              f"边 {r['edges_before']} → {r['edges']}"
              f"（实线 {r['recorded']} / 虚线 {r['inferred']}）")
        if r.get("backup"):
            print(f"    备份：{r['backup']}")
    if not a.write:
        print("\n（干跑结束；确认无误后加 --write 落盘）")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())

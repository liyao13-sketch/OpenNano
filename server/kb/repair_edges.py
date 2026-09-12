"""修掉工程文件里**被编造出来的连线**（只读 core 判定 · 默认干跑）。

背景（2026-09-13 owner实测）：画布上 "DWL 后面跟着 8 个连续的 ICP 刻蚀"。
根因是 `expack.py` 在"包内无 flow.json ⇒ 由 runs 合成画布"时，把**真实的空 parent**
（并存试验，如 AR50-T1 的 6 条 ICP）按"上一条"补了边，形成假直线；而这些假边随后
被**持久化进工程文件**（`~/.opennano/projects/*.json`），刷新后照旧显示。

本模块：按 core 的 `parent_run_id` **重算**工程里的连线并给出差异；`--write` 才落盘。
零号铁律：core 只读；**不推断任何新父**（core 说空就是空）；每条被去掉的边都逐条报出来。

用法：
    python3 -m kb.repair_edges AR50-T1              # 干跑，打印差异
    python3 -m kb.repair_edges AR50-T1 --write      # 落盘（先自动备份 .bak）
    python3 -m kb.repair_edges --all                # 扫 ~/.opennano/projects/*.json
"""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path

PROJECTS_DIR = Path.home() / ".opennano" / "projects"


def _core_parent_map(batch: str) -> dict[str, str]:
    """只读 core → {run_id: parent_run_id}（该 batch）。**不猜、不补**。"""
    import csv
    from .batch_runs import core_runs_path
    p = core_runs_path()
    out: dict[str, str] = {}
    if not p or not Path(p).exists():
        return out
    with Path(p).open(newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rid = (r.get("run_id") or "").strip()
            if rid and (r.get("batch_id") or "").strip() == batch:
                out[rid] = (r.get("parent_run_id") or "").strip()
    return out


def repair_project(path: Path, batch: str = "", write: bool = False) -> dict:
    """重算一个工程文件的连线。返回差异摘要（不写文件，除非 `write=True`）。"""
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
        return {"ok": False, "error": f"无法确定批次（工程里有多个或没有核心 run）：{path.name}"}

    parents = _core_parent_map(batch)
    by_run = {m.get("core_run_id"): m for m in mods if m.get("core_run_id")}
    id_of = {rid: m.get("id") for rid, m in by_run.items()}

    # 期望的边：严格按 core 的 parent（parent 不在本工程里 ⇒ 不画，跨包续做本来就不该连）
    want: list[dict] = []
    for rid, m in by_run.items():
        par = parents.get(rid, m.get("core_parent_run_id") or "")
        if par and par in id_of and id_of[par] and id_of[par] != m.get("id"):
            e = {"src": id_of[par], "dst": m.get("id"), "_link": "recorded"}
            if e not in want:
                want.append(e)

    have = proj.get("edges") or []
    key = lambda e: (e.get("src"), e.get("dst"))                      # noqa: E731
    have_k, want_k = {key(e) for e in have}, {key(e) for e in want}
    added = [e for e in want if key(e) not in have_k]
    # ⚠️ 两条判据**必须分开**（曾写反：拿"推断边"去比对"已记录边"集合 ⇒ 推断边反被当假边删）：
    #    · 假边 = 标着 recorded、但 core 里 parent 为空/指向别处 ⇒ 删
    #    · 推断边（`_link=inferred`）= 显示层工艺序提示 ⇒ **留**
    fake, inferred_kept = [], []
    for e in have:
        if key(e) in want_k:
            continue
        (inferred_kept if (e.get("_link") or "") == "inferred" else fake).append(e)
    dropped = fake

    def _label(edge: dict, side: str) -> str:
        mid = edge.get(side)
        rid = next((m.get("core_run_id") for m in mods if m.get("id") == mid), mid)
        return rid or str(mid)

    res = {
        "ok": True, "file": str(path), "batch": batch,
        "modules": len(mods), "edges_before": len(have),
        "edges_after": len(want) + len(inferred_kept),
        "dropped": [{"src": _label(e, "src"), "dst": _label(e, "dst")} for e in dropped],
        "added": [{"src": _label(e, "src"), "dst": _label(e, "dst")} for e in added],
        "kept_inferred": [{"src": _label(e, "src"), "dst": _label(e, "dst")} for e in inferred_kept],
        "ambiguous_parents": [rid for rid in by_run
                              if (parents.get(rid) or "") and parents.get(rid) not in id_of],
        "changed": bool(dropped or added),
    }
    if write and res["changed"]:
        bak = path.with_suffix(f".json.bak-{datetime.now():%Y%m%d%H%M%S}")
        shutil.copy2(path, bak)
        proj["edges"] = want + inferred_kept
        # 同步模块上的 core_parent_run_id（画布与 core 保持一致；空就是空）
        # ⚠️ **只同步 core 里真实存在的 run**：尚未入库的"计划 run"（如 DRIE-0002）
        #    在 core 里查不到 ⇒ 一律覆盖会把它的父抹掉（2026-09-13 实际踩到：
        #    重排后 DRIE-0001→DRIE-0002 这条计划边消失）。core 没有它，就该保留原值。
        for rid, m in by_run.items():
            if rid in parents:
                m["core_parent_run_id"] = parents[rid]
        res["parent_synced"] = [rid for rid in by_run if rid in parents]
        path.write_text(json.dumps(proj, ensure_ascii=False, indent=2), encoding="utf-8")
        res["backup"] = str(bak)
        res["written"] = True
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description="修掉工程文件里被编造的连线（默认干跑）")
    ap.add_argument("batch", nargs="?", default="", help="批次号（也用于定位同名工程文件）")
    ap.add_argument("--file", default="", help="直接指定工程 JSON 路径")
    ap.add_argument("--all", action="store_true", help="扫 ~/.opennano/projects/*.json")
    ap.add_argument("--write", action="store_true", help="落盘（自动备份 .bak-时间戳）")
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
        r = repair_project(path, batch, write=a.write)
        if not r.get("ok"):
            print(f"⚠️ {path.name}：{r['error']}")
            bad += 1
            continue
        mark = "有假边" if r["changed"] else "已是真实链"
        print(f"{'✍️' if r.get('written') else '🔍'} {path.name}（{r['batch']}）：{mark}"
              f" · 边 {r['edges_before']} → {r['edges_after']}")
        for e in r["dropped"]:
            print(f"    − 去掉（core 里 parent 为空，是并存/独立）: {e['src']} → {e['dst']}")
        for e in r["added"]:
            print(f"    + 补上（core 里有 parent）: {e['src']} → {e['dst']}")
        if r["ambiguous_parents"]:
            print(f"    ⓘ 父在本工程之外（跨包续做，不连线）：{r['ambiguous_parents']}")
        if r.get("backup"):
            print(f"    备份：{r['backup']}")
    if not a.write and any(True for _ in targets):
        print("\n（干跑结束；确认无误后加 --write 落盘）")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())

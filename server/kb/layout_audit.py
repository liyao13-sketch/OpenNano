"""画布布局**体检**（只读，可复跑）—— 回答"这张画布画得对不对"。

为什么要有它（2026-09-13 owner："run2 和 run3 两个方块会叠在一起，能否对画布绘制做一次大排查"）：
    画布是**数据驱动的绘制**：位置来自工程文件 / 合成算法 / 用户拖拽，三者都可能产生
    重叠、悬空边、串列错位、向上回折等问题。肉眼看不全，**必须机器查**。

检查项（每条都能客观判定，不猜）：
    ① 节点重叠/过近（按节点框：宽 190 × 高（含备注行数））
    ② id / core_run_id 重复
    ③ 悬空边（端点不在节点里）、自环、重复边
    ④ **向上回折**（dst.y < src.y）—— 旧版"连线混乱"的根因
    ⑤ 跨工序边（x 不前进）
    ⑥ 列内错位（同工序列里混进了别的工序）
    ⑦ 孤立节点（没有任何边，且不是 season）
    ⑧ 隐藏 season 占位（默认不画，但仍占行号 ⇒ 留白浪费）
    ⑨ 边标签/推断边数量（扇出标签太挤的观感来源）
    ⑩ 两列间距是否够（≥ 节点宽 + 最小留白）

用法：
    python3 -m kb.layout_audit ~/.opennano/projects/AR50-T1-明天.json
    python3 -m kb.layout_audit --batch AR50-T1          # 从 core 合成一份来查
    python3 -m kb.layout_audit --file x.json --json
"""
from __future__ import annotations

import argparse
import itertools
import json
from pathlib import Path

#: 几何**唯一来源**在 `kb/canvas_geom.py`（前端尺寸 / 后端布局 / 本体检器三处对齐）
from .canvas_geom import (CHIP_H, CLAMP_COMMENT_LINES, COMMENT_LINE_H, GAP,  # noqa: E402
                          NODE_BASE_H, NODE_W, STAGGER, gaps as geom_gaps, node_height)

MIN_GAP_X = 24            # 两列之间最少留白（小于它报"列太近"）
MIN_GAP_Y = 20            # 同列相邻节点最少留白（小于它报"重叠/过近"）


def _node_height(m: dict, comment_lines: int = 0) -> int:
    """节点高度估算 —— 直接问几何模块（按前端真实规则：备注限高 3 行）。"""
    return node_height(m, comments_shown=bool(comment_lines))


def audit(project: dict, comment_lines: int = 0) -> dict:
    """体检一个画布工程（`{modules, edges}`），返回结论 + 问题清单。"""
    mods = project.get("modules") or []
    edges = project.get("edges") or []
    by_id = {m.get("id"): m for m in mods}
    issues: list[dict] = []

    #: 只报不拦的项（结构没错，属于"可优化"/"观感"）
    WARN_KINDS = {"orphan", "fanout", "hidden_gap", "gap_uneven"}

    def add(kind: str, msg: str, **extra) -> None:
        issues.append({"kind": kind, "msg": msg,
                       "severity": "warn" if kind in WARN_KINDS else "error", **extra})

    # ① 重叠 / 过近
    boxes = [(m, float(m.get("x") or 0), float(m.get("y") or 0), _node_height(m, comment_lines))
             for m in mods]
    for (a, ax, ay, ah), (b, bx, by, bh) in itertools.combinations(boxes, 2):
        if abs(ax - bx) < NODE_W and abs(ay - by) < max(ah, bh) + MIN_GAP_Y:
            add("overlap", f"{a.get('core_run_id') or a.get('name')} 与 "
                           f"{b.get('core_run_id') or b.get('name')} 重叠/过近"
                           f"（Δx={abs(ax-bx):.0f} Δy={abs(ay-by):.0f}）",
                a=a.get("id"), b=b.get("id"))

    # ② 重复 id / run
    ids = [m.get("id") for m in mods]
    dup_ids = sorted({k for k in ids if ids.count(k) > 1})
    if dup_ids:
        add("dup_id", f"节点 id 重复：{dup_ids}")
    rids = [m.get("core_run_id") for m in mods if m.get("core_run_id")]
    dup_runs = sorted({k for k in rids if rids.count(k) > 1})
    if dup_runs:
        add("dup_run", f"core_run_id 重复：{dup_runs}")

    # ③④⑤ 边
    seen_pairs: dict[tuple, int] = {}
    label_edges = 0
    inferred = 0
    for e in edges:
        src, dst = e.get("src"), e.get("dst")
        if e.get("_link") == "inferred":
            inferred += 1
        if e.get("label"):
            label_edges += 1
        if src not in by_id or dst not in by_id:
            add("dangling_edge", f"悬空边：{src} → {dst}（端点不在节点里）")
            continue
        if src == dst:
            add("self_loop", f"自环：{src}")
        key = (src, dst)
        seen_pairs[key] = seen_pairs.get(key, 0) + 1
        a, b = by_id[src], by_id[dst]
        if float(b.get("y") or 0) < float(a.get("y") or 0):
            add("upward_edge", f"边向上回折：{a.get('core_run_id')} → {b.get('core_run_id')}"
                               f"（{a.get('y'):.0f} → {b.get('y'):.0f}）")
        if float(b.get("x") or 0) < float(a.get("x") or 0):
            add("backward_edge", f"边往左回退：{a.get('core_run_id')} → {b.get('core_run_id')}")
    for (s, d), n in seen_pairs.items():
        if n > 1:
            add("dup_edge", f"重复边 {n} 次：{by_id[s].get('core_run_id')} → {by_id[d].get('core_run_id')}")

    # ⑥ 列内错位 + ⑦ 孤立 +
    #    ⚠️ 2026-09-13 改判据（owner：「为什么 Plasma Strip 距上一个 ICP 的横向距离比别处大？」）：
    #       并列分支的**溢出子列**允许"借"下一列的 x（它们在更下面的行里，不会撞方块）
    #       ⇒ 一列里出现多个工序**本身不是错**。真正要拦的是：某个节点的工序比该列**脊柱**
    #       （最上面那条）还靠后 —— 那才会读成"更晚的工序挤在同一列"。
    by_x: dict[float, list] = {}
    for m in mods:
        by_x.setdefault(float(m.get("x") or 0), []).append(m)
    for x, items in by_x.items():
        top = min(items, key=lambda mm: float(mm.get("y") or 0))
        top_stage = int(top.get("core_stage_seq") or 0)
        for m in items:
            st = int(m.get("core_stage_seq") or 0)
            if st and top_stage and st > top_stage:
                add("column_mixed",
                    f"x={x:.0f}：{m.get('core_run_id')} 的工序 {st} 比同列脊柱 "
                    f"{top.get('core_run_id')} 的 {top_stage} 还靠后 ⇒ 会读成更晚的工序挤在同一列")
    touched = {e.get("src") for e in edges} | {e.get("dst") for e in edges}
    for m in mods:
        if m.get("id") not in touched and m.get("run_nature") != "season":
            add("orphan", f"孤立节点（无任何边，也不是 season）：{m.get('core_run_id') or m.get('name')}")

    # ⑧ 隐藏 season 占位（留白浪费）
    hidden = [m for m in mods if m.get("run_nature") == "season"]
    if hidden:
        ys = sorted(float(m.get("y") or 0) for m in mods if m.get("run_nature") != "season")
        if ys and min(float(m.get("y") or 0) for m in hidden) - max(ys) > 340:
            add("hidden_gap", f"{len(hidden)} 个 season 节点（默认不画）在主流程下方留了"
                              f"{min(float(m.get('y') or 0) for m in hidden) - max(ys):.0f}px 空白")

    # ⑨ 列间距
    xs = sorted({float(m.get("x") or 0) for m in mods})
    for a, b in zip(xs, xs[1:]):
        if b - a < NODE_W + MIN_GAP_X:
            add("col_tight", f"两列太近：x={a:.0f} 与 x={b:.0f}（间距 {b-a:.0f} < {NODE_W + MIN_GAP_X}）")

    # ⑪ **间距是否规矩**（owner 2026-09-13：横纵等宽）
    #    ⚠️ 2026-09-13 改判据：并列分支收成 2 列子格后，**右列整体下错 STAGGER** ⇒
    #       纵向间距会出现 `GAP` 与 `GAP+STAGGER` 两种值，这是**故意的错位**、不是毛病。
    #       所以：横向一律 == GAP；纵向只要求"不小于 GAP（不许挤）且不大于 GAP+STAGGER（不许空太多）"。
    g = geom_gaps({"modules": mods}, comments_shown=bool(comment_lines))
    if g["h_gaps"] and g["v_gaps"]:
        hs, vs = g["h_gaps"], g["v_gaps"]
        bad_h = [x for x in hs if abs(x - GAP) > 2]
        if bad_h:
            add("gap_uneven", f"横向间距不等于 {GAP}：{hs}")
        squeeze = [x for x in vs if x < GAP - 1]
        waste = [x for x in vs if x > GAP + STAGGER + 1]
        if squeeze:
            add("gap_uneven", f"纵向间距被挤到小于 {GAP}：{squeeze}")
        if waste:
            add("gap_uneven", f"纵向间距超过 {GAP}+错位{STAGGER}（留白偏多）：{waste}")

    # ⑫ **检测节点说不出"我在测谁"**（2026-09-13 · metrology B+）
    #    B+ 的口径：检测节点＝对**某个上游 run 的一次测量** ⇒ 它的语义全靠**入边**表达
    #    （导出时入边写成 core 的 `parent_run_id`）。没有入边的检测节点既导不出归属、
    #    也没法在画布上读出被测对象 —— 这正是owner说的"游离于体系之外"。
    #    只报 warn：用户可能正画到一半（刚拖进来还没连线）。
    from .expack import is_metrology_stage, resolve_stage, stage_from_run_id
    has_in = {e.get("dst") for e in edges}
    for m in mods:
        stage = (stage_from_run_id(m.get("core_run_id") or "")
                 or m.get("core_stage") or resolve_stage(m))
        if is_metrology_stage(stage) and m.get("id") not in has_in:
            add("metrology_no_input",
                f"检测节点没有入边 ⇒ 说不出它在测哪条 run："
                f"{m.get('core_run_id') or m.get('name') or m.get('id')}")

    # ⑩ 扇出标签（观感噪声来源）
    fanout: dict[str, int] = {}
    for e in edges:
        fanout[e.get("src")] = fanout.get(e.get("src"), 0) + 1
    worst = max(fanout.values(), default=0)
    if worst >= 4:
        who = [by_id[k].get("core_run_id") for k, v in fanout.items() if v == worst]
        add("fanout", f"单点扇出 {worst} 条（{who}）⇒ 若每条都带标签会很挤")

    kinds: dict[str, int] = {}
    for i in issues:
        kinds[i["kind"]] = kinds.get(i["kind"], 0) + 1
    errs = [i for i in issues if i["severity"] == "error"]
    warns = [i for i in issues if i["severity"] == "warn"]
    return {
        "ok": not errs,
        "errors": len(errs), "warnings": len(warns),
        "modules": len(mods), "edges": len(edges),
        "inferred_edges": inferred, "edges_with_label": label_edges,
        "hidden_season": len(hidden),
        "issue_kinds": kinds, "issues": issues,
        "geometry": {"node_w": NODE_W, "base_h": NODE_BASE_H, "chip_h": CHIP_H,
                     "comment_lines_assumed": comment_lines,
                     "comment_lines_used": min(max(comment_lines, 0), CLAMP_COMMENT_LINES),
                     "comment_clamped": comment_lines > CLAMP_COMMENT_LINES,
                     "gap": GAP},
    }


def project_from_batch(batch: str) -> dict:
    from . import append_pack as ap
    return ap.core_to_project(batch)


def main() -> int:
    ap_ = argparse.ArgumentParser(description="画布布局体检（只读）")
    ap_.add_argument("path", nargs="?", default="", help="工程 JSON 路径")
    ap_.add_argument("--file", default="", help="同 path")
    ap_.add_argument("--batch", default="", help="从 core 合成该 batch 的画布来查")
    ap_.add_argument("--comment-lines", type=int, default=0,
                     help="模拟'显示备注'时每节点的备注行数（默认 0=不显示备注）")
    ap_.add_argument("--json", action="store_true")
    a = ap_.parse_args()

    if a.batch:
        proj = project_from_batch(a.batch)
    else:
        p = Path(a.file or a.path).expanduser()
        if not p.exists():
            print(f"文件不存在：{p}")
            return 2
        proj = json.loads(p.read_text(encoding="utf-8"))

    res = audit(proj, comment_lines=a.comment_lines)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0 if res["ok"] else 1
    print(f"画布体检：{res['modules']} 节点 / {res['edges']} 边"
          f"（推断 {res['inferred_edges']} · 带标签 {res['edges_with_label']} · "
          f"隐藏 season {res['hidden_season']}）")
    if res["ok"] and not res["warnings"]:
        print("  ✅ 未发现问题")
    for i in res["issues"]:
        mark = "✗" if i["severity"] == "error" else "⚠"
        print(f"  {mark} [{i['kind']}] {i['msg']}")
    if res["ok"]:
        print(f"  ✅ 无结构性问题（{res['warnings']} 条提示可忽略/可优化）")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

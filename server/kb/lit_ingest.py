#!/usr/bin/env python3
"""文献 KnowledgeItem → 知识条目 导入器(v0.2 契约实现)。

契约: `个人空间/19_工艺资料/契约/知识条目Schema与录入规范_v0.2_20260912.md` §四之二~之五

用法:
    python3 kb/lit_ingest.py <抽取文件.md> --batch D29-20260912 [--dry-run] [--db <path>]
    python3 kb/lit_ingest.py <抽取文件.md> --batch ... --anchors 升锚表.json   # 二次抽取/升锚用

设计要点(逐条对应契约):
- 幂等键 = (source, locator);locator 取 loc,故同一 source 下多条不会互相覆盖。
- source_tier 标注:机理/结论 = literature;整表原始值 = public_data。
- 可靠度:literature 档缺省 2(store 守卫兜底并留痕);表类 = 2;升锚后由 --anchors 显式给 4。
- process_type:按 knowledge_type 走 THEORY_*/LIT_*;判不了用 --overrides 人工归位,**不写 GENERAL**。
- 条目 id 原样沿用抽取文件 id(D29-K07);kb_batch / schema_version / extracted_by_model 入 extra_metadata。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from kb.store import (KBStore, detect_drift, locator_of, note_drift,  # noqa: E402
                      resolve_reliability)

SCHEMA_VERSION = "v0.2"
TITLE_MAX = 200

# knowledge_type / 正文特征 → process_type 的路由表(契约 §四之三)
# 先看正文特征(更贴近条目实体),再看 knowledge_type 兜底。
CONTENT_ROUTES = [
    (("各向异性", "各向同性"), "THEORY_ANISO"),
    (("facet",), "THEORY_GEOM"),
    (("trade-off", "tradeoff", "权衡", "折衷"), "THEORY_TRADEOFF"),
    (("饱和", "模型", "经验式", "公式"), "THEORY_MODEL"),
    (("工艺窗口", "配方窗口", "参数窗口", "窗口"), "LIT_WINDOW"),
]
TYPE_ROUTES = [
    (("失败警示", "冲突"), "THEORY_GEOM"),
    (("各向异性", "通量"), "THEORY_ANISO"),
    (("模型", "公式", "定量"), "THEORY_MODEL"),
    (("权衡", "trade", "折衷"), "THEORY_TRADEOFF"),
    (("机理", "机制", "理论", "原理"), "THEORY_KINETICS"),
    (("术语", "定义"), "THEORY_KINETICS"),
    (("事实", "陈述"), "THEORY_KINETICS"),
    (("经验数值",), "THEORY_KINETICS"),
    (("配方窗口", "工艺窗口"), "LIT_WINDOW"),
    (("参数表", "整表", "表"), "LIT_TABLE"),
]
TABLE_MARKERS = ("参数表", "整表", "Table", "配方表")

# ── 手册层 `MANUAL_*`（契约 §三 第四层；2026-09-14 启用，应工艺线跨线工单）──────────
# **判据是"来源"，不是"内容形态"**：厂商说明书/出厂值里的每一条（无论写成"配方窗口"还是
# 干脆是"机理"口吻）都出自那一份 TDS ⇒ 全部落 `MANUAL_*`，形态信息留在 `knowledge_type`/tags。
# 为什么不能只看逐条 `knowledge_type`：实测 M01/M02/M04 共 36 条的取值只有
# 「配方窗口/经验数值/事实陈述/参数表(整表)/失败警示/trade-off」——**没有一条含"手册/说明书"**，
# 只看逐条必然漏判（旧行为就是漏判后按文献规则塞进 THEORY_*，实测 21 条）。
# 故三级判据，从强到弱：①`--manual` 显式开关 ②抽取件**文件头**声明（"厂商说明书"/"说明书元信息"…）
# ③逐条 `knowledge_type`/`citation` 里出现说明书词。②已实测零误伤：
# 8 份文献抽取件（D29/D131…）文件头**一个标记都没有**，只有 M 系列有。
MANUAL_ITEM_MARKERS = ("说明书", "手册", "TDS", "datasheet", "出厂")
MANUAL_FILE_MARKERS = ("厂商说明书", "设备手册", "说明书元信息", "说明书索引",
                       "出厂参考值", "出厂值", "TDS", "datasheet")
#: 亚类 = **工艺域**（与前端族/设备域对齐：预设的消费方就是某台设备）。
#: 顺序有意：LITHO 在前 —— 光刻胶说明书里会顺带提到"耐刻蚀/去胶"，先按主域归架；
#: 归错也无副作用（只是书架标签），人工可用 `--overrides` 逐条改。
MANUAL_ROUTES = [
    (("光刻", "曝光", "显影", "前烘", "涂胶", "旋涂", "ebeam", "e-beam", "电子束",
      "resist", "光刻胶", "套刻", "litho"), "MANUAL_LITHO"),
    (("去胶", "灰化", "剥膜", "刻蚀", "etch", "icp", "rie", "drie", "bosch"), "MANUAL_ETCH"),
    (("显影液", "刻蚀液", "清洗", "湿法", "溶剂", "剥离液", "lift-off", "liftoff", "rca"), "MANUAL_WET"),
    (("沉积", "pecvd", "cvd", "溅射", "sputter", "蒸发", "evap", "镀膜", "ald"), "MANUAL_DEPO"),
    (("退火", "氧化", "anneal", "烧结", "烘箱", "thermal"), "MANUAL_THERMAL"),
    (("椭偏", "台阶仪", "电镜", "afm", "xrd", "量测", "metrology"), "MANUAL_METRO"),
]
#: 兜底亚类：**显式取值**（不是 GENERAL 那种"蒙过去"）—— 干跑会打印分布，人工可 overrides 改
MANUAL_FALLBACK = "MANUAL_OTHER"


def detect_manual_source(text: str) -> str:
    """抽取件**文件头**（JSON 之前那段元信息）是否声明自己是厂商说明书。返回命中的标记（空=不是）。

    只扫 JSON 之前的正文：正文里顺口提到"手册"不该把整份文献判成说明书。
    """
    head = text.split("```json", 1)[0]
    for m in MANUAL_FILE_MARKERS:
        if m in head:
            return m
    return ""


def pick_manual_subtype(item: dict) -> str:
    """手册条目 → 工艺域亚类（`MANUAL_LITHO` …，兜底 `MANUAL_OTHER`）。"""
    hay = " ".join(str(x) for x in (
        item.get("content") or "", item.get("citation") or "", item.get("source") or "",
        json.dumps(item.get("context") or {}, ensure_ascii=False)))
    low = hay.lower()
    for keys, code in MANUAL_ROUTES:
        if any(k.lower() in low for k in keys):
            return code
    return MANUAL_FALLBACK


def is_manual_item(item: dict, manual_source: bool = False) -> bool:
    """该条目是不是手册来源（文件级声明 / 显式开关 / 逐条标注，任一命中）。

    ⚠️ 逐条标注**只看 `knowledge_type` 与 `source`，不看 `citation`** —— 这一条是被自己的
    用例抓出来的：`citation` 是**书目引用**，一篇正经文献完全可以引用某份厂商手册
    （路径里带"手册"两字），拿它当判据会把那篇文献整条误判成手册层。
    `knowledge_type`/`source` 才是**抽取器对该条自身的定性**，用它才不越界。
    """
    if manual_source:
        return True
    blob = " ".join(str(x) for x in (item.get("knowledge_type") or "", item.get("source") or ""))
    return any(m in blob for m in MANUAL_ITEM_MARKERS)


def load_items(path: Path) -> list[dict]:
    """从抽取文件里取出第一个非空 JSON 数组。"""
    text = path.read_text(encoding="utf-8")
    for raw in re.findall(r"```json\s*\n(.*?)\n```", text, re.S):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, list) and data:
            return data
    raise SystemExit(f"[错误] {path} 里没找到非空的 KnowledgeItem JSON 数组")


def pick_process_type(item: dict, manual_source: bool = False) -> str:
    """条目 → process_type。**手册来源优先**（契约 §三 第四层）。

    为什么手册判据要放在最前：出厂值一旦被当成 `THEORY_*`，检索侧按设备过滤时会**带上它**
    （契约 §三 配套规则 1：THEORY_* 是普适的、默认包含）⇒ 平台会把"某厂商的前烘参数"
    当普适机理推给所有设备，正是配套规则 3 明令禁止的"把局部结论伪装成普适结论"的镜像错误。
    """
    if is_manual_item(item, manual_source):
        return pick_manual_subtype(item)
    kt = str(item.get("knowledge_type") or "")
    content = str(item.get("content") or "")
    if any(m in content for m in TABLE_MARKERS):     # 整表最优先(不可误判为窗口)
        return "LIT_TABLE"
    for keys, code in CONTENT_ROUTES:                # 正文特征优先
        if any(k in content for k in keys):
            return code
    for keys, code in TYPE_ROUTES:                   # 再看 knowledge_type
        if any(k in kt for k in keys):
            return code
    return ""


def build_entry(item: dict, batch: str, model: str,
                manual_source: bool = False) -> tuple[dict, list[str]]:
    """KnowledgeItem → KnowledgeEntry(dict)。返回 (entry, 问题清单)。

    `manual_source=True` ⇒ 该抽取件是厂商说明书（文件头声明或 `--manual`），条目走 `MANUAL_*`。
    """
    issues: list[str] = []
    iid = str(item.get("id") or "").strip()
    content = str(item.get("content") or "").strip()
    ktype = str(item.get("knowledge_type") or "").strip()
    if not iid:
        issues.append("缺 id")
    if not content:
        issues.append("缺 content")

    ptype = pick_process_type(item, manual_source)
    is_table = ptype == "LIT_TABLE"
    is_manual = ptype.startswith("MANUAL_")
    if not ptype:
        issues.append("process_type 判不了(需人工归位,禁止写 GENERAL)")

    title = content if len(content) <= TITLE_MAX else content[:TITLE_MAX].rstrip() + "…"
    if ktype:
        title = f"[{ktype}] {title}"
    title = title[:255]

    ctx = item.get("context") or {}
    equipment: dict = {}
    material: dict = {}
    for k, v in ctx.items():
        if isinstance(v, dict):
            continue
        if any(t in k for t in ("设备", "机台", "工艺")):
            equipment[k] = v
        elif any(t in k for t in ("基材", "材料", "掩膜")):
            material[k] = v
        else:
            equipment[k] = v
    if ctx:
        equipment.setdefault("context", {k: v for k, v in ctx.items() if not isinstance(v, dict)})

    parameters: dict = {}
    if item.get("normalized"):
        parameters["formula"] = item["normalized"]
    if is_table and ctx:
        parameters["table_context"] = ctx

    extra: dict = {
        "source_kind": "literature_extraction",
        "knowledge_type": ktype or None,
        "citation": item.get("citation"),
        "loc": item.get("loc"),
        # 幂等键:注入器必须保证**唯一** —— 同一 source 下 loc 会重复(如多条都是 "§II.A"),
        # 故用 'loc#id' 形式(契约 §四之五:同一 locator 出多条时加后缀)。
        "locator": f"{item.get('loc') or 'noloc'}#{iid}",
        "gap": item.get("gap"),
        "conflicts": item.get("conflicts"),
        "freshness": item.get("freshness"),
        "normalized": item.get("normalized"),
        "confidence": item.get("confidence"),
        "verification": item.get("verification"),
        # 分档口径（契约 §2.1 表）：**厂商说明书/出厂值 = `manual` 档（允许 1–2）** ——
        # 契约那行本来就写着"设备手册/出厂参考值"，只是此前 `MANUAL_*` 未启用、代码把它
        # 按"文献整表 ⇒ public_data(2–3)"处理。改成 `manual` 后**分数不变（缺省 2）**，
        # 但上限收紧到 2：出厂标称值不能悄悄升到 3 而看起来像"更权威的公开数据集"。
        # ⚠️ 别把 `source_tier=manual`（手册档）与"本室人工录入"混为一谈 —— 后者不是档位概念。
        "source_tier": ("manual" if is_manual else
                        "public_data" if is_table else "literature"),
        "kb_batch": batch,
        "schema_version": SCHEMA_VERSION,
        "extracted_by_model": model,
        "extracted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if is_table:
        extra["source_kind"] = "external_table"
    if is_manual:
        # 让"这是说明书来源"在 extra_metadata 里**可查**（原来只有 literature_extraction）
        extra["source_kind"] = "manual_extraction"
        extra["manual_doc"] = str(item.get("citation") or "") or None

    tags = sorted({t for t in (ktype.split("(")[0] if ktype else "", ptype.split("_")[0],
                               "文献") if t})   # 排序:集合顺序不稳定会造成假漂移

    entry = {
        "id": iid,
        "process_type": ptype,
        "title": title,
        "equipment": equipment,
        "material": material,
        "parameters": parameters,
        "results": {},
        "source": str(item.get("source") or ""),
        "constraints": ([{"type": "scope", "value": ctx}] if ctx else []),
        "tags": tags,
        "extra_metadata": {k: v for k, v in extra.items() if v is not None},
    }
    return entry, issues


def apply_anchors(entry: dict, anchors: dict, stored: dict | None = None) -> None:
    """二次抽取/升锚:按 id 覆盖分数与 citation(显式,不默认 4)。

    依据定权(规范 §四之六):锚表显式给 `reliability_basis` ⇒ provenance=anchor;
    锚表只改分、没给依据 ⇒ 从库内继承(manual),避免被守卫默认文案洗掉。
    """
    a = anchors.get(entry["id"])
    if not a:
        return
    md = entry.setdefault("extra_metadata", {})
    if "reliability_score" in a:
        entry["reliability_score"] = int(a["reliability_score"])
    for k in ("citation", "verification", "superseded_by"):
        if a.get(k):
            md[k] = a[k]
    if a.get("reliability_basis"):
        md["reliability_basis"] = a["reliability_basis"]
        md["reliability_basis_provenance"] = "anchor"
    elif stored:
        prev = (stored.get("extra_metadata") or {})
        if prev.get("reliability_basis") and not prev.get("reliability_basis_provenance"):
            md["reliability_basis"] = prev["reliability_basis"]
            md["reliability_basis_provenance"] = "manual"
    if a.get("process_type"):
        entry["process_type"] = a["process_type"]
        entry["extra_metadata"]["routed_by"] = "anchors"


def _brief(e: dict | None) -> tuple[str, str]:
    if not e:
        return ("(新建)", "")
    return (str(e.get("process_type") or ""), str(e.get("title") or "")[:40])


def reconcile(entries: list[dict], kb: KBStore, allow_reroute: bool,
              anchors: dict | None = None) -> list[dict]:
    """重跑保护(规范 §四之六):忠实优先 + 差异留痕。就地改写 entries。

    - 已有行且 process_type 非空 ⇒ **默认沿用库内值**(仅在显式 --allow-reroute 时才改路由,
      并写 reroute_from / reroute_at / rerouted_by)。
    - 其它字段有差异 ⇒ 沿用新值,但差异写进 extra_metadata.field_drift(可见,不静默)。
    - `reliability_basis`(分档依据)变化也进差异清单 —— 否则"升锚理由被守卫默认文案洗掉"
      会完全无声(2026-09-12 工艺线实测)。
    """
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    report = []
    for e in entries:
        loc = locator_of(e)
        stored = kb.fetch(e["source"], loc, e["id"])
        apply_anchors(e, anchors or {}, stored)             # 依据继承需先看到库内值
        raw_new = dict(e)                                   # 守卫前的"解析意图"
        raw_pt = e.get("process_type")
        stored_basis = (stored or {}).get("extra_metadata", {}).get("reliability_basis")
        reroute_from = None
        if stored and stored.get("process_type") and raw_pt != stored["process_type"]:
            if allow_reroute:
                reroute_from = stored["process_type"]
                md = dict(e.get("extra_metadata") or {})
                md.update({"reroute_from": reroute_from, "reroute_at": now,
                           "rerouted_by": os.environ.get("USER", "unknown")})
                e["extra_metadata"] = md
            else:
                e["process_type"] = stored["process_type"]   # 沿用库内值
                md = dict(e.get("extra_metadata") or {})
                md["reroute_blocked"] = (
                    f"解析得到 {raw_pt},沿用库内 {stored['process_type']}"
                    f"(需 --allow-reroute 才改路由)")
                e["extra_metadata"] = md
        # 其余字段漂移:以**未含本次元数据的**解析结果对比,避免自比自
        cmp_new = {k: v for k, v in e.items() if k != "extra_metadata"}
        drift = detect_drift(stored, cmp_new)
        eff_new = resolve_reliability(dict(e), stored)       # 守卫生效后的最终分
        stored_score = stored.get("reliability_score") if stored else None
        downgraded = (raw_new.get("reliability_score") is not None
                      and raw_new["reliability_score"] != eff_new)
        if stored and drift:
            note_drift(e, drift, ["process_type"] if reroute_from else [])
        final_basis = (e.get("extra_metadata") or {}).get("reliability_basis")
        basis_changed = bool(stored and stored_basis and final_basis
                             and stored_basis != final_basis)
        report.append({
            "id": e["id"], "new": stored is None,
            "old_pt": (stored or {}).get("process_type") or "",
            "new_pt": e["process_type"],
            "parsed_pt": raw_pt,
            "old_title": ((stored or {}).get("title") or "")[:40],
            "new_title": (e.get("title") or "")[:40],
            "old_score": stored_score, "new_score": eff_new,
            "drift": sorted(drift.keys()), "reroute": reroute_from,
            "blocked": bool(e.get("extra_metadata", {}).get("reroute_blocked")),
            "downgrade": (f"{raw_new['reliability_score']}→{eff_new}" if downgraded else ""),
            "basis_changed": basis_changed,
            "old_basis": str(stored_basis or "")[:60],
            "new_basis": str(final_basis or "")[:60],
        })
    return report


def print_report(report: list[dict], problems: list, dry: bool) -> None:
    changed = [r for r in report if (not r["new"]) and (
        r["old_pt"] != r["new_pt"] or r["old_title"] != r["new_title"]
        or r["old_score"] != r["new_score"] or r["drift"] or r["downgrade"]
        or r["basis_changed"])]
    reroutes = [r for r in report if r["reroute"]]
    blocked = [r for r in report if r["blocked"]]
    downgrades = [r for r in report if r["downgrade"]]
    basis_moves = [r for r in report if r["basis_changed"]]
    news = [r for r in report if r["new"]]
    print(f"\n{'[dry-run] ' if dry else ''}差异清单:"
          f" 新建 {len(news)} · 有字段改动 {len(changed)} · 改路由 {len(reroutes)}"
          f" · 被保护拦下 {len(blocked)} · 分档降级 {len(downgrades)}"
          f" · 依据变化 {len(basis_moves)}")
    for r in changed[:20]:
        mark = "🔀" if r["reroute"] else ("🛡" if r["blocked"] else "·")
        print(f"  {mark} {r['id']:<10} {r['old_pt'] or '(新建)':<17}→ {r['new_pt']:<17}"
              f" 分 {r['old_score']}→{r['new_score']}"
              + (f" 降级{r['downgrade']}" if r["downgrade"] else "")
              + (f" 漂移[{','.join(r['drift'])}]" if r["drift"] else ""))
        if r["old_title"] != r["new_title"]:
            print(f"      标题: {r['old_title']} → {r['new_title']}")
        if r["basis_changed"]:
            print(f"      ⚠️ 依据变化: 「{r['old_basis']}」 → 「{r['new_basis']}」")
    for r in blocked[:5]:
        print(f"  🛡 {r['id']}: 解析 {r['parsed_pt']} ≠ 库内 {r['old_pt']},已按忠实优先沿用库内值")
    if problems:
        print(f"\n⚠️ 需人工处理 {len(problems)} 条:")
        for iid, issues in problems[:20]:
            print(f"  {iid}: {'; '.join(issues)}")


def main() -> int:
    ap = argparse.ArgumentParser(description="文献 KnowledgeItem → 知识条目 导入器")
    ap.add_argument("src", help="抽取文件(如 07_文献库/D29_知识抽取.md)")
    ap.add_argument("--batch", default="", help="批次号(默认 <文献号>-<日期>)")
    ap.add_argument("--db", default="", help="目标库(默认 ~/.opennano/opennano.db)")
    ap.add_argument("--model", default="opennano-extractor", help="抽取执行者,写进 extra_metadata")
    ap.add_argument("--anchors", default="", help="JSON 文件:{条目id: {reliability_score, citation, …}}")
    ap.add_argument("--overrides", default="", help="JSON 文件:{条目id: {process_type}} 人工归位")
    ap.add_argument("--allow-reroute", action="store_true",
                    help="允许改写库内已有的 process_type(默认忠实优先、沿用库内值)")
    ap.add_argument("--dry-run", action="store_true", help="只预检并打印差异清单,不写库")
    ap.add_argument("--manual", action="store_true",
                    help="本抽取件是厂商说明书/出厂值 ⇒ 全部条目落 MANUAL_*(契约 §三 第四层)。"
                         "不传时也会**自动识别文件头**(厂商说明书/说明书元信息/出厂参考值/TDS)")
    args = ap.parse_args()

    src = Path(args.src).expanduser()
    items = load_items(src)
    # 手册来源判定：**显式开关 > 文件头声明**（文件头只扫 JSON 之前那段）
    raw_text = src.read_text(encoding="utf-8")
    hint = detect_manual_source(raw_text)
    manual_source = bool(args.manual or hint)
    batch = args.batch or f"{src.stem.split('_')[0]}-{datetime.now():%Y%m%d}"
    anchors = json.loads(Path(args.anchors).read_text(encoding="utf-8")) if args.anchors else {}
    overrides = json.loads(Path(args.overrides).read_text(encoding="utf-8")) if args.overrides else {}

    entries, problems = [], []
    for it in items:
        e, issues = build_entry(it, batch, args.model, manual_source)
        if e["id"] in overrides:
            e["process_type"] = overrides[e["id"]]["process_type"]
            e["extra_metadata"]["routed_by"] = "overrides"
            issues = [i for i in issues if "process_type" not in i]
        entries.append(e)          # anchors 在 reconcile 内应用(需先读库内值以继承依据)
        if issues:
            problems.append((e["id"], issues))

    by_type: dict[str, int] = {}
    for e in entries:
        by_type[e["process_type"]] = by_type.get(e["process_type"], 0) + 1

    print(f"源文件 : {src}")
    print(f"批次   : {batch}   条目数: {len(entries)}")
    # 判据要**说得出来**：哪条信号把这份件判成了说明书（否则用户不知道为何整份改了层）
    if manual_source:
        why = "--manual 显式开关" if args.manual else f"文件头声明命中「{hint}」"
        print(f"来源层 : MANUAL_*（手册/出厂值）—— {why}")
    else:
        print("来源层 : 文献（未命中说明书标记）")
    print("process_type 分布(解析侧):")
    for k, v in sorted(by_type.items()):
        print(f"  {k or '(未定)':<18} {v}")

    kb = KBStore(args.db) if args.db else KBStore()
    report = reconcile(entries, kb, args.allow_reroute, anchors)
    print_report(report, problems, args.dry_run)
    if args.dry_run:
        print("\n[dry-run] 未写库。样例(含守卫与留痕后):")
        print(json.dumps(entries[0], ensure_ascii=False, indent=2)[:1200])
        return 0 if not problems else 2

    created = updated = 0
    for e in entries:
        obj, is_new = kb.upsert(e)
        created += is_new
        updated += (not is_new)
    st = kb.stats()
    print(f"\n写库完成: 新增 {created} / 更新 {updated} / 总条目 {st['total']}")
    print("按 process_type×分:")
    for pt, dist in sorted(st["by_process_type"].items()):
        print(f"  {pt:<18} {dist}")
    return 0 if not problems else 2


if __name__ == "__main__":
    raise SystemExit(main())

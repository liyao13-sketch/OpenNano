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
_STAGE_SEQ_CACHE_SIG: tuple | None = None


def _src_sig(paths) -> tuple:
    """源文件指纹：(path, mtime_ns, size)；文件不存在记 (path, None, None)。

    存在的理由（2026-09-16，A4 实测坐实）：本文件这批"进程内缓存"原来是
    **读一次、永不失效** —— 服务做成 launchd 常驻后，数据线 `build_core` 落了新数据，
    这里还在喂旧值（实测：盘上改了 runs.csv，第二次读出来的还是旧的）。
    现在每次调用先对源文件做一次 `stat`（便宜），指纹变了就重建；
    真正贵的发现（rglob 整棵树）才走 TTL（见 `_pack_runs_files`）。
    """
    out = []
    for p in paths:
        try:
            st = p.stat()
            out.append((str(p), st.st_mtime_ns, st.st_size))
        except OSError:
            out.append((str(p), None, None))
    return tuple(out)


def _core_sig() -> tuple:
    """core/runs.csv 的指纹（路径含在里面 ⇒ 测试切 OPENNANO_CORE_DIR 也自动失效）。"""
    p = core_runs_path()
    return _src_sig([p] if p else [])


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
    global _STAGE_SEQ_CACHE, _STAGE_SEQ_CACHE_SIG
    sig = _core_sig()
    if _STAGE_SEQ_CACHE is not None and _STAGE_SEQ_CACHE_SIG == sig:
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
    _STAGE_SEQ_CACHE_SIG = sig
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

    ⚠️ 教训（连踩三次：`parent_run_id` / `run_nature` / `sample_id`）：
    **语义标注常只在 core 侧** —— 模块上没有的字段一律从 core/runs.csv 兜底回读。
    """
    parent_map = _parent_map_from_packs()
    facts = _core_run_facts()
    rows = []
    for m in modules or []:
        rid = m.get("core_run_id") or ""
        p = parse_run_id(rid)
        if not p or p["batch"] != batch:
            continue
        f = facts.get(rid) or {}
        rows.append({
            "run_id": rid,
            "stage": p["stage"],
            "seq": p["seq"],
            "stage_seq": _stage_seq(modules, p["batch"], p["stage"]),
            "parent_run_id": (m.get("core_parent_run_id") or m.get("parent_run_id")
                              or parent_map.get(rid) or ""),
            "sample_id": (m.get("core_sample_id") or m.get("sample_id")
                          or f.get("sample_id") or ""),
            # 语义标注常只在 core 侧 ⇒ 模块没有时从 core/runs.csv 读（同 sample_id 的处理）
            "run_nature": (m.get("core_run_nature") or m.get("run_nature")
                           or f.get("run_nature") or ""),
            "status": m.get("run_state") or f.get("status") or "planned",
            # 机台口径：core 原值优先（画布 `machine_name` 是应用库显示名，不是 core 机台号）
            "tool_id": (m.get("core_tool_id") or m.get("machine_name")
                        or f.get("tool_id") or ""),
            "date": (m.get("core_date") or f.get("date") or ""),
            "title": m.get("name") or "",
            "note": m.get("comment") or "",
            "recipe_id": m.get("core_recipe_id") or f.get("recipe_id") or "",
            "module_id": m.get("id") or "",
        })
    rows.sort(key=lambda r: (r["stage_seq"], r["stage"], r["seq"]))
    return rows


_RUN_FACTS_CACHE: dict[str, dict] | None = None
_RUN_FACTS_CACHE_SIG: tuple | None = None


def _core_run_facts() -> dict[str, dict]:
    """core/runs.csv 的 run_id → 若干事实列（进程内缓存，**只读**）。

    存在的理由：`sample_id` / `date` / `status` / `tool_id` / `recipe_id` 在模块上**常为空**，
    而 core 里有权威值。**每发现一次"某个字段模块上是空的"，就加进这张表** ——
    不要再为每个字段各写一套 `_xxx_map()`（那正是漏掉 `sample_id` 的原因）。
    """
    global _RUN_FACTS_CACHE, _RUN_FACTS_CACHE_SIG
    sig = _core_sig()
    if _RUN_FACTS_CACHE is not None and _RUN_FACTS_CACHE_SIG == sig:
        return _RUN_FACTS_CACHE
    cols = ("sample_id", "run_nature", "date", "status", "tool_id", "recipe_id", "stage_seq")
    out: dict[str, dict] = {}
    for r in _core_runs_rows():
        rid = (r.get("run_id") or "").strip()
        if rid:
            out[rid] = {c: (r.get(c) or "").strip() for c in cols}
    _RUN_FACTS_CACHE = out
    _RUN_FACTS_CACHE_SIG = sig
    return out


def _nature_map() -> dict[str, str]:
    """core/runs.csv 的 run_id → run_nature（只读）。**只是 `_core_run_facts()` 的一个视图**。"""
    return {rid: f["run_nature"] for rid, f in _core_run_facts().items() if f.get("run_nature")}


def _core_runs_rows() -> list[dict]:
    """只读 core/runs.csv（失败返回 []，不抛）。"""
    try:
        import csv
        p = core_runs_path()
        if p and p.exists():
            with p.open(newline="", encoding="utf-8-sig") as f:
                return list(csv.DictReader(f))
    except Exception:                             # noqa: BLE001
        pass
    return []


_PARENT_CACHE: dict[str, str] | None = None
_PARENT_CACHE_SIG: tuple | None = None

#: 实验包 runs.csv 的**清单**缓存：贵的是 `rglob` 发现（整棵树），不是读文件。
#: 清单每 300 秒重扫一次；清单里每个文件的**内容指纹**每次调用都 stat（便宜）。
#: 300 秒够用的原因：新包进 core 必过 `build_core`（runs.csv 变 ⇒ core 部分指纹立即失效），
#: 包扫描只补"core 尚未入库的老包"的父边 —— 那条路晚几分钟刷新无损。
_PARENT_FILES: list = []
_PARENT_FILES_AT: float = 0.0
_PARENT_FILES_BASE: str = ""
_PACK_LIST_TTL_S = 300.0


def _pack_runs_files(base) -> list:
    """实验包 runs.csv 清单（按 base 区分；TTL 内复用，见上）。"""
    global _PARENT_FILES, _PARENT_FILES_AT, _PARENT_FILES_BASE
    import time
    key = str(base)
    if key != _PARENT_FILES_BASE or (time.monotonic() - _PARENT_FILES_AT) > _PACK_LIST_TTL_S:
        _PARENT_FILES = list(base.rglob("runs.csv"))[:200] if base else []
        _PARENT_FILES_AT = time.monotonic()
        _PARENT_FILES_BASE = key
    return _PARENT_FILES


def _parent_map_from_packs() -> dict[str, str]:
    """run_id → parent_run_id（进程内缓存；**只读**，不写任何资产）。

    两个来源，**core 优先**：
      ① `core/runs.csv`（权威）—— 2026-09-13 回归网查出：以前这里只扫实验包，
         于是"core 里已入库、但画布模块没带 parent"的 run 会**丢父边**
         （与 `sample_id` / `run_nature` 是同一个坑：语义标注常在 core 侧）。
      ② 各实验包内的 `runs.csv`（老包能补 core 尚未入库的续做边）。
    """
    global _PARENT_CACHE, _PARENT_CACHE_SIG
    # ⚠️ 可隔离：`OPENNANO_PACKS_ROOT` 指定"只扫这一棵"（空串 = 不扫）。
    #    ① 测试必须隔离（否则夹具会被真实验包里的父污染 ⇒ 假绿/假红都出现过）
    #    ② 大工作区上 `rglob` 整棵树会拖慢每次点开批次面板 —— 这是它真正的代价
    import os
    env = os.environ.get("OPENNANO_PACKS_ROOT")
    try:
        if env is None:
            from .menu_reader import _workspace
            base = _workspace() / "个人空间/18_工艺数据资产"
        elif env == "":
            base = None
        else:
            from pathlib import Path
            base = Path(env).expanduser()
    except Exception:                             # noqa: BLE001 —— 工作区推断失败退化为只用 core
        base = None
    pack_files = _pack_runs_files(base)
    sig = (_core_sig(), str(base), _src_sig(pack_files))
    if _PARENT_CACHE is not None and _PARENT_CACHE_SIG == sig:
        return _PARENT_CACHE
    out: dict[str, str] = {}
    for r in _core_runs_rows():                    # ① core 权威
        rid = (r.get("run_id") or "").strip()
        if rid:
            out[rid] = (r.get("parent_run_id") or "").strip()
    import csv                                     # ② 包内 runs.csv 补 core 没有的
    for p in pack_files:
        try:
            with p.open(newline="", encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    rid, par = (r.get("run_id") or "").strip(), (r.get("parent_run_id") or "").strip()
                    if rid:
                        out.setdefault(rid, par)
        except Exception:                          # noqa: BLE001
            continue
    _PARENT_CACHE = out
    _PARENT_CACHE_SIG = sig
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
             stage_hint: int | None = None, sample_id: str | None = None) -> dict:
    """算下一步 run 的标识（**序号由工具算，禁手输**）。

    - 序号：该 batch 该 stage 已有 run 的**最大序号 + 1**（不按数量，避免删过 run 后撞号）
    - parent（**分支安全**）：
        * 给了 `sample_id` ⇒ **优先取同 sample 的上一条 run**，且**显式 parent 也会被校验**：
          若显式 parent 不属于该 sample，说明"选中的 run"与"要续做的样品"不是一回事
          ⇒ 以 sample 为准（否则并发分支会挂错父）。
          （2026-09-13 回归网查出：UI 两个字段都发，且选中的 run 常是列表首条 ⇒ 必然挂错。）
        * 只给显式 parent ⇒ 用它；
        * 都没给 ⇒ 取该 stage 最后一条 run（线性续做语义）。
      取不到任何上游时返回**空 parent** —— 无父 run 本身就是合法语义
      （并发分支的兄弟共享同一个上游 LDW，不是首尾相链）。
    - stage_seq：同 stage 保持同号。
    """
    same = [r for r in runs_of_batch(modules, batch) if r["stage"] == stage]
    seq = (max((r["seq"] for r in same), default=0) + 1)
    mine = [r for r in same if sample_id and r.get("sample_id") == sample_id]
    if sample_id:
        if mine:
            parent = mine[-1]["run_id"]
        elif parent_run_id and any(r["run_id"] == parent_run_id and
                                   r.get("sample_id") == sample_id for r in same):
            parent = parent_run_id              # 显式父确实属于该 sample ⇒ 认它
        else:
            # 指定了 sample ⇒ **只看这个 sample**；它没做过就保持空
            # （空 parent 是合法语义：并发分支的兄弟不首尾相链。绝不退回别人的 run）
            parent = ""
    else:
        parent = parent_run_id if parent_run_id is not None else (
            same[-1]["run_id"] if same else "")
    stage_seq = same[0]["stage_seq"] if same else (stage_hint or _stage_seq(modules, batch, stage))
    return {
        "batch_id": batch,
        "stage": stage,
        "seq": seq,
        "run_id": f"{batch}-{stage}-{seq:04d}",
        "parent_run_id": parent,
        "stage_seq": stage_seq,
        "sample_id": sample_id or (same[-1].get("sample_id") if same else ""),
        "is_continuation": bool(same),
        "same_sample_runs": [r["run_id"] for r in same if sample_id and r.get("sample_id") == sample_id],
    }


def parallels(modules: list[dict], batch: str) -> list[dict]:
    """识别**并行分支**：同一 (parent, stage) 下有多条 run ⇒ 并发实验，不是首尾相链。

    AR50-T1 的实例：LDW 后裂片成 8 个 die、各做 1 次 ICP（同一工序的 8 个样品并发）。
    若这些 run 没有区分 sample/die，画布只能画成直线 ⇒ 会被误读为"同一片刻了 8 次"。
    """
    rows = runs_of_batch(modules, batch)
    bucket: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        bucket.setdefault((r["parent_run_id"] or "", r["stage"]), []).append(r)
    out = []
    for (parent, stage), group in bucket.items():
        if len(group) < 2:
            continue
        samples = sorted({g["sample_id"] for g in group if g["sample_id"]})
        same_parent = bool(parent)
        if len(samples) >= 2:
            hint = ""                                  # 各 run 有各自的样品 ⇒ 已能区分
        elif not samples:
            # ⚠️ 全都没标 sample ≠ "sample 相同"（曾经这里误报成"同一个样品组"）
            hint = ("none of these runs has a sample ⇒ cannot tell whether one wafer ran several times "
                    "or several wafers ran once each; please fill sample_id (or tag core_run_nature)")
        else:
            hint = (f"these runs share sample \"{samples[0]}\" (possibly a sample group) ⇒ "
                    "per-die attribution was not recorded; if each die ran once, tag core_run_nature=trial")
        out.append({
            "parent_run_id": parent,
            "stage": stage,
            "runs": [g["run_id"] for g in group],
            "count": len(group),
            "samples": samples,
            "distinct_samples": len(samples),
            # 有父且同 stage ⇒ 同一上游下的并发；无父且同 stage ⇒ 大概率是分片后的同工序并发
            "kind": ("concurrent under one upstream (split / several wafers doing the same step)" if same_parent
                     else "same-stage concurrency with no common upstream (split likely, die not recorded)"),
            "hint": hint,
        })
    out.sort(key=lambda x: (-x["count"], x["stage"]))
    return out


#: run 性质（数据线 2026-09-12 建议）—— 防"同 stage 同 stage_seq ⇒ 串行"的误读。
#: ⚠️ 2026-09-13 owner定「界面全英文」⇒ 这些**界面标签**改英文；`nature` 的**键**（chain/trial/…）
#:    一个字母都没动（它们是契约里的值，翻了对不上库）。
from .expack import METROLOGY_STAGES as _METROLOGY_STAGES   # 表征 stage 的唯一真相

NATURE_LABEL = {
    "chain": "chained",            # 有父 run ⇒ 真实上游链
    "trial": "standalone trial",   # 无父 + 有独立 sample ⇒ 与其他 run 并列的试验片
    "batch_level": "batch level (multi-die)",  # 无父 + 与兄弟同 stage/sample
    "unclassified": "unclassified",
    # 2026-09-14（数据线协议 §15.3）：检测 run 的仪器 session —— 由数据线写入 core，
    # 工具侧只负责**显示**。⚠️ 不许留空：留空会按 parent_run_id 推成 chain（把它当链环），
    # 而检测不是工序、不在链上。
    "metrology": "metrology session",
}


#: "整片级"工序：这些步骤通常做整片（还没裂片或不分到具体 die），
#: 与"某颗 die 上的一次试验"性质不同 ⇒ 分类时优先判 batch_level。
WHOLE_WAFER_STAGES = ("PECVD", "LDW", "EBL", "UV", "MA6", "ASH", "EVAP", "SPUT", "LITHO")


def classify(modules: list[dict], batch: str) -> list[dict]:
    """给每条 run 标**性质**：`chain`（链内续接）/ `trial`（独立试验）/ `batch_level`（多片同做）。

    判定顺序（先证据、后启发、再人工覆盖）：
      1. 模块显式带 `core_run_nature`（或 `run_nature`）⇒ **用它**（域知识优先，如 season 判定）
      2. 有 `parent_run_id` ⇒ `chain`（真实上游链，最硬）
      3. 无父，但同 stage 内**只有它自己**用这个 sample（或它是该 stage 唯一一条）⇒ `trial`
      4. 无父，且同 stage 内有**别的 run 与它共享 sample** ⇒ `batch_level`（多片一起做）
      5. 无父、无 sample、且同 stage 有多条 ⇒ `trial`（**保守**）+ 计入 `needs_human`
         —— season 预热 vs 独立试验靠域知识，工具不猜（数据线 2026-09-12 要求）
    """
    rows = runs_of_batch(modules, batch)
    by_stage: dict[str, list[dict]] = {}
    for r in rows:
        by_stage.setdefault(r["stage"], []).append(r)
    by_run = {m.get("core_run_id"): m for m in (modules or [])}
    out, needs_human = [], []
    for r in rows:
        m = by_run.get(r["run_id"]) or {}
        # 标注优先级：画布模块 → core/runs.csv 的 run_nature 列（与 sample_id 同理：标注常只在 core 侧）
        override = (m.get("core_run_nature") or m.get("run_nature")
                    or r.get("run_nature") or "").strip()
        same_stage = by_stage[r["stage"]]
        if override in NATURE_LABEL:
            nature, why = override, "人工标注（域知识优先）"
        elif (r.get("stage") or "").strip().upper() in _METROLOGY_STAGES:
            # 检测 run（仪器 session）：**按 stage 认**，不看 parent —— 若走下面那条 parent 分支
            # 会被推成 `chain`（＝当成链环），而那正是数据线协议 §15.3 警告的错标。
            # core 的 run_nature 该列现在还是空的（数据线将同批补），所以这层兜底必须有。
            nature, why = "metrology", "检测 run（仪器 session，不在工序链上）"
        elif r["parent_run_id"]:
            nature, why = "chain", f"上游 = {r['parent_run_id']}"
        elif (r["stage"] in WHOLE_WAFER_STAGES and "-DIE" not in (r["sample_id"] or "").upper()
              and any("-DIE" in (x["sample_id"] or "").upper() for x in rows)):
            # 该批已有 die 归属的 run，而这一条仍挂在**整片**上 ⇒ 未分到具体 die
            # （例：AR50-T1 的 PECVD/LDW 做整片；ASH 在多片同炉后仍写整片）
            nature = "batch_level"
            why = ("无上游、挂在**整片**上；本批已有 die 归属的 run ⇒ "
                   "此 run 未分到具体 die（整片 / 多片同炉）")
        elif len(same_stage) == 1:
            nature, why = "trial", "该 stage 只有这一条"
        else:
            peers = [x for x in same_stage if x["run_id"] != r["run_id"]]
            same_sample = [x for x in peers if r["sample_id"] and x["sample_id"] == r["sample_id"]]
            if not r["sample_id"]:
                nature = "batch_level"
                why = "no upstream and no sample ⇒ probably season or batch level (needs human confirmation)"
            elif same_sample:
                # ⚠️ 关键：sample 也可能是**样品组**（如 DIE4 = 4 颗一组）。
                # 同组多条 run **不等于**同一样品做多次，也不等于独立试验 —— 工具不猜。
                nature = "batch_level"
                why = (f"no upstream; shares sample \"{r['sample_id']}\" with {len(same_sample)} "
                       f"run(s) in the same stage. If that sample is a **sample group** (one run per die), "
                       f"tag it core_run_nature=trial to tell them apart")
            else:
                nature, why = "trial", "no upstream; its sample is unique within the stage ⇒ standalone trial"
        item = {"run_id": r["run_id"], "stage": r["stage"], "sample_id": r["sample_id"],
                "parent_run_id": r["parent_run_id"], "nature": nature,
                "nature_label": NATURE_LABEL[nature], "why": why,
                "overridden": bool(override)}
        out.append(item)
        # 需人工判定：无上游 + 无 sample + 未被人工标注（season? 独立试验? —— 工具不猜）
        if nature == "batch_level" and not r["parent_run_id"] and not r["sample_id"] and not override:
            needs_human.append(r["run_id"])
    return out


def needs_human_nature(modules: list[dict], batch: str) -> list[str]:
    """无法自动判定性质、需域知识（season? 独立试验?）的 run —— 工具不猜，列出来给人标。

    ⚠️ **口径只有一份**：直接复用 `classify()` 的判定结果（`nature=batch_level`
    且"无上游 + 无 sample"）⇒ 两条出口永不漂移。
    曾经这里自己重写了一遍条件、且只看画布不看 core ⇒ 已被人标注的 run 仍被列进"待标"
    （2026-09-13 由回归网查出）。
    """
    return [c["run_id"] for c in classify(modules, batch)
            if c["nature"] == "batch_level" and not c["parent_run_id"] and not c["sample_id"]
            and not c["overridden"]]


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
        "parallels": parallels(modules, batch),   # 并行分支（防"直线误读"）
        "natures": classify(modules, batch),      # 每条 run 的性质（链内续接/独立试验/批次级）
        "nature_needs_human": needs_human_nature(modules, batch),   # 需域知识判定（season?）的 run
    }

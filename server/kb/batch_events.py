"""批次事件（裂片 / 取样分配）—— **只读台账 + 产出提案**，写账走数据线的 `propose_apply.py`。

契约（数据线 2026-09-12/13 定 · 他们已实现）：
    · 源台账 `18_工艺数据资产/03_实验数据/ingest/batch_events.csv`（append-only · 同 event_id 后写生效=纠错路径）
    · `kind` 三态要分清（**这是数据线纠正过的关键概念**）：
        `split`    物理裂片（1 片 → N 颗）—— AR50-T1 **只有 1 条**：×49
        `allocate` 取样分配（从现有样品取 N 颗，**不改样品总数**）—— AR50-T1 有 3 条：×4 / ×15 / ×1
        `dice` / `merge` 预留
      ⇒ 把 allocate 当成 split 会让"裂片次数"记成 2 次、计划/实际比数失真。
    · 落账入口：`python3 ingest/propose_apply.py --proposal p.json [--apply]`（默认干跑）
      · 幂等按**语义键**（kind+at+from+to+count）⇒ 换种写法也不写重
      · 不推断：脏父样品 / 未知 kind / 缺 at / 负数 ⇒ 拦下不写
    · **工具不写 core、也不直接写台账**：只产出提案 JSON，交给数据线 `--apply`。

本模块只做两件事：**只读**读台账（给 UI 用）+ 生成并本地预检提案 JSON。
"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

from .append_pack import read_core_table
from .menu_reader import _workspace

KINDS = ("split", "allocate", "dice", "merge")
#: 台账字段（照数据线的 batch_events.csv）
EVENT_FIELDS = ["event_id", "batch_id", "kind", "at", "from_sample_id", "to_sample_id",
                "count", "after_stage", "id_pattern", "status", "note"]


def events_path() -> Path:
    env = os.environ.get("OPENNANO_BATCH_EVENTS")
    return Path(env) if env else _workspace() / "个人空间/18_工艺数据资产/03_实验数据/ingest/batch_events.csv"


def proposer_path() -> Path:
    env = os.environ.get("OPENNANO_PROPOSE_APPLY")
    return Path(env) if env else _workspace() / "个人空间/18_工艺数据资产/03_实验数据/ingest/propose_apply.py"


def read_events(batch: str = "") -> list[dict]:
    """**只读**批次事件台账（按 event_id 后者覆盖，模拟"同号后写生效"）。"""
    p = events_path()
    if not p.exists():
        return []
    import csv as _csv
    by_id: dict[str, dict] = {}
    with p.open(newline="", encoding="utf-8-sig") as f:
        for r in _csv.DictReader(f):
            eid = (r.get("event_id") or "").strip()
            if not eid:
                continue
            if batch and (r.get("batch_id") or "").strip() != batch:
                continue
            by_id[eid] = {k: (r.get(k) or "").strip() for k in EVENT_FIELDS}
    return list(by_id.values())


def plan_vs_actual(batch: str) -> dict:
    """**计划 N 颗 / 实际 M 颗**比数（这个议题对owner最直观的价值）。

    - 计划：`batches.sample_spec_json.grid.count`（契约 v0.1.5 未定则先看 `id_pattern`）；
    - 实际：台账里该批 `allocate` 的 count 之和（**不含 split**：split 是物理裂片，不是"用了"）。
    """
    batches = [b for b in read_core_table("batches")
               if (b.get("batch_id") or "").strip() == batch]
    spec = {}
    if batches:
        raw = batches[0].get("sample_spec_json") or ""
        if raw:
            try:
                spec = json.loads(raw)
            except json.JSONDecodeError:
                spec = {}
    evs = read_events(batch)
    splits = [e for e in evs if e["kind"] == "split"]
    allocs = [e for e in evs if e["kind"] == "allocate"]
    planned = ((spec.get("grid") or {}).get("count")
               or (spec.get("count") if isinstance(spec.get("count"), int) else None))
    # ⚠️ 分层口径（数据线 2026-09-13 强调）：
    #   顶层累加会把"从 15 颗组里再取 1 颗"算成新增用量 ⇒ 必须区分
    #   · 顶层用量 = 从**整片**取（from 是整片）—— AR50-T1 = 4+15 = 19 颗
    #   · 组内取用 = from 是某个**组**（不含 `-DIE` 之外仍属组）—— AR50-T1 = 1 颗（从 DIE15 组取第 1 颗）
    root_id = (spec.get("from_sample_id") or f"{batch}-01").strip()
    top = sum(int(e["count"]) for e in allocs
              if str(e["count"]).isdigit() and e["from_sample_id"] == root_id)
    inner = sum(int(e["count"]) for e in allocs
                if str(e["count"]).isdigit() and e["from_sample_id"] != root_id)
    actual = top + inner                                  # 兼容旧字段：仍给总和
    unallocated = (planned - top) if isinstance(planned, int) else None

    def _ids(pattern: str, n: int) -> list[str]:
        """按 id_pattern 生成计划位号（如 AR50-T1-01-D{n:02d} → D01…D49）。"""
        out = []
        if not pattern or not n:
            return out
        for i in range(1, n + 1):
            try:
                out.append(pattern.replace("{n:02d}", f"{i:02d}").replace("{n}", str(i)))
            except Exception:                     # noqa: BLE001
                break
        return out

    return {
        "batch_id": batch,
        "planned": planned,
        "planned_ids": _ids(spec.get("id_pattern", ""), planned or 0)[:8] + (["…"] if (planned or 0) > 8 else []),
        "id_pattern": spec.get("id_pattern", ""),
        "actual_allocated": actual,          # 兼容旧字段（= top + inner）
        "used_from_wafer": top,              # ★ 顶层用量（从整片取）—— 数据线口径的"19"
        "used_from_group": inner,            # ★ 组内取用（从某个组里再取）
        "root_sample_id": root_id,
        "splits": [{"at": e["at"], "count": e["count"], "status": e["status"],
                    "after_stage": e["after_stage"], "note": e["note"][:40]} for e in splits],
        "allocations": [{"at": e["at"], "from": e["from_sample_id"], "to": e["to_sample_id"],
                         "count": e["count"], "status": e["status"]} for e in allocs],
        "spec": spec,
        "source": "core(只读) + batch_events.csv(只读)",
        "note": ("`split`=物理裂片（只 1 条×49）；`allocate`=取样分配（3 条 4/15/1）。"
                 "二者不可混为一谈；实际用量只算 allocate。"),
        "unallocated": unallocated,          # 未用 = 计划 − 顶层用量（不含组内取用）
    }


def build_proposal(batch: str, events: list[dict], operator: str = "",
                   note_prefix: str = "") -> dict:
    """生成提案 JSON（**不落盘、不写台账**；本地先预检）。"""
    out = []
    for e in events:
        kind = (e.get("kind") or "").strip()
        to = (e.get("to_sample_id") or "").strip()
        at = (e.get("at") or datetime.now().strftime("%Y-%m-%d")).strip()
        eid = (e.get("event_id") or
               f"EV-{batch.replace('-', '')}-{kind}-{at.replace('-', '')}-{(to or str(e.get('count') or ''))}")
        out.append({
            "event_id": eid, "batch_id": batch, "kind": kind, "at": at,
            "from_sample_id": (e.get("from_sample_id") or "").strip(),
            "to_sample_id": to, "count": e.get("count", ""),
            "after_stage": (e.get("after_stage") or "").strip(),
            "id_pattern": (e.get("id_pattern") or "").strip(),
            "status": (e.get("status") or "done").strip(),
            "note": ((note_prefix + "；") if note_prefix else "") + (e.get("note") or "").strip(),
        })
    return {"batch_id": batch, "operator": operator or "", "events": out,
            "_generated_at": datetime.now().isoformat(timespec="seconds"),
            "_generated_by": "OpenNano 工具线（提案，未落账）"}


def precheck(proposal: dict) -> dict:
    """本地预检（对齐数据线的校验口径，**提前**发现会被拦的提案）。

    只做能确定的检查：kind 合法 / 有 `at` / allocate 必须有 `to` / count 非负 /
    `from_sample_id` 必须在 core/samples 里（不推断）。
    """
    errs, warns = [], []
    batch = (proposal.get("batch_id") or "").strip()
    known = {s.get("sample_id", "").strip() for s in read_core_table("samples")}
    ledger = read_events()
    sigs = {(e["kind"], e["at"], e["from_sample_id"], e["to_sample_id"], e["count"]) for e in ledger}
    for i, e in enumerate(proposal.get("events") or [], start=1):
        tag = f"[{i}]"
        kind = (e.get("kind") or "").strip()
        at = (e.get("at") or "").strip()
        frm = (e.get("from_sample_id") or "").strip()
        to = (e.get("to_sample_id") or "").strip()
        cnt = str(e.get("count") or "")
        if kind not in KINDS:
            errs.append(f"{tag} kind 不认识：{kind!r}（允许 {list(KINDS)}）")
        if not at:
            errs.append(f"{tag} 缺 at（事件日期 YYYY-MM-DD）")
        if frm and known and frm not in known:
            errs.append(f"{tag} from_sample_id「{frm}」不在 core/samples ⇒ 数据线会拦（先建父样品）")
        if kind == "allocate" and not to:
            errs.append(f"{tag} allocate 必须给 to_sample_id")
        if cnt and not cnt.isdigit():
            errs.append(f"{tag} count 非整数：{cnt!r}")
        if (kind, at, frm, to, cnt) in sigs:
            warns.append(f"{tag} 与台账已有事件**语义等价** ⇒ 数据线会跳过（幂等，不算错）")
    return {"ok": not errs, "errors": errs, "warnings": warns, "batch_id": batch,
            "checked": len(proposal.get("events") or [])}


def run_proposer(proposal: dict, apply: bool = False, timeout: int = 120) -> dict:
    """把提案交给数据线的 `propose_apply.py`（默认**干跑**；`apply=True` 才 `--apply`）。

    ⚠️ 这是**外部命令**调用：只在用户显式点"落账"时才用 `apply=True`。
    """
    exe = proposer_path()
    if not exe.exists():
        return {"ok": False, "error": f"找不到数据线脚本：{exe}（可用 OPENNANO_PROPOSE_APPLY 指定）"}
    tmp = Path(os.environ.get("TMPDIR", "/tmp")) / f"opennano_proposal_{os.getpid()}.json"
    tmp.write_text(json.dumps(proposal, ensure_ascii=False, indent=2), encoding="utf-8")
    cmd = ["python3", str(exe), "--proposal", str(tmp)] + (["--apply"] if apply else [])
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        return {"ok": r.returncode == 0, "applied": apply, "exit": r.returncode,
                "stdout": (r.stdout or "")[-4000:], "stderr": (r.stderr or "")[-2000:],
                "proposal_file": str(tmp)}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"超时（{timeout}s）", "proposal_file": str(tmp)}

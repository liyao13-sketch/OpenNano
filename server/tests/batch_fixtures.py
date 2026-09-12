"""run 编号 / 续做分支安全 / 样品继承树 / 用量分层 / 事件预检 的数据与断言。

⚠️ 这里钉的不是"实现细节"，是**已被owner与数据线确认过的口径**：
    · 序号 = 该 batch 该 stage 的最大序号 + 1（不按条数，删过 run 也不撞号）
    · stage_seq 沿用 core 已入库值（权威），同 stage 不递增
    · 续做取父：给了 sample ⇒ **只看这个 sample**（并发分支绝不误挂）
    · 用量分层：`used_top`（从整片取）与 `used_within`（从已分配组再取）**不得相加**
"""
from __future__ import annotations

BATCH = "TEST-T1"
ROOT = f"{BATCH}-01"


# ------------------------------------------------------------------ 合成 core
def sample_rows():
    """整片 → die 组（DIE4 = 4 颗）→ 组内（DIE15-01）→ 悬空 parent 一个。"""
    return [
        {"sample_id": ROOT, "batch_id": BATCH, "position": "整片", "role": "wafer",
         "parent_sample_id": ""},
        {"sample_id": f"{BATCH}-01-DIE4", "batch_id": BATCH, "position": "1-4",
         "role": "die_group", "parent_sample_id": ROOT},
        {"sample_id": f"{BATCH}-01-DIE15", "batch_id": BATCH, "position": "15",
         "role": "die_group", "parent_sample_id": ROOT},
        {"sample_id": f"{BATCH}-01-DIE15-01", "batch_id": BATCH, "position": "15-1",
         "role": "die", "parent_sample_id": f"{BATCH}-01-DIE15"},
        {"sample_id": f"{BATCH}-01-DIE99", "batch_id": BATCH, "position": "99",
         "role": "die", "parent_sample_id": f"{BATCH}-01-NOPE"},     # 悬空 parent
    ]


def run_rows():
    """12 条 run 的骨架：线性链（PECVD→LDW）+ 一条真分支（LDW→4×ICP）+ 2 条 DRIE 续做。"""
    return [
        {"run_id": f"{BATCH}-PECVD-0001", "batch_id": BATCH, "sample_id": ROOT,
         "stage": "PECVD", "stage_seq": "1", "date": "2026-09-01",
         "run_nature": "batch_level"},
        {"run_id": f"{BATCH}-LDW-0001", "batch_id": BATCH, "sample_id": ROOT,
         "stage": "LDW", "stage_seq": "2", "date": "2026-09-02",
         "parent_run_id": f"{BATCH}-PECVD-0001", "run_nature": "chain"},
        {"run_id": f"{BATCH}-ICP-0001", "batch_id": BATCH,
         "sample_id": f"{BATCH}-01-DIE4", "stage": "ICP", "stage_seq": "3",
         "date": "2026-09-03", "parent_run_id": f"{BATCH}-LDW-0001", "run_nature": "chain"},
        {"run_id": f"{BATCH}-ICP-0002", "batch_id": BATCH,
         "sample_id": f"{BATCH}-01-DIE15", "stage": "ICP", "stage_seq": "3",
         "date": "2026-09-03", "parent_run_id": f"{BATCH}-LDW-0001", "run_nature": "chain"},
        # ICP-0003 故意**不写 parent**：并发分支的兄弟不首尾相链，空父是合法语义
        {"run_id": f"{BATCH}-ICP-0003", "batch_id": BATCH,
         "sample_id": f"{BATCH}-01-DIE15-01", "stage": "ICP", "stage_seq": "3",
         "date": "2026-09-03", "parent_run_id": "", "run_nature": "trial"},
        # ICP-0004 无 sample 无 parent ⇒ 需人工判定性质
        {"run_id": f"{BATCH}-ICP-0004", "batch_id": BATCH, "sample_id": "",
         "stage": "ICP", "stage_seq": "3", "date": "2026-09-03", "parent_run_id": ""},
        {"run_id": f"{BATCH}-DRIE-0001", "batch_id": BATCH,
         "sample_id": ROOT, "stage": "DRIE", "stage_seq": "5", "date": "2026-09-12",
         "parent_run_id": f"{BATCH}-ICP-0001"},
        {"run_id": f"{BATCH}-DRIE-0002", "batch_id": BATCH,
         "sample_id": f"{BATCH}-01-DIE15-01", "stage": "DRIE", "stage_seq": "5",
         "date": "2026-09-13", "parent_run_id": f"{BATCH}-DRIE-0001"},
    ]


def batch_rows(planned=49, with_spec=True):
    import json
    spec = json.dumps({
        "grid": {"count": planned}, "id_pattern": f"{BATCH}-01-D{{n:02d}}",
        "from_sample_id": ROOT,
        "planned_use": {"usage_rule": "top_level_only"},
    }, ensure_ascii=False) if with_spec else ""
    return [{"batch_id": BATCH, "title": "回归用合成批次", "status": "running",
             "sample_spec_json": spec}]


def modules(batch=BATCH):
    """画布模块：**故意不带** parent/sample/nature —— 考验"从 core 回读"。"""
    mods = []
    for r in run_rows():
        if r["batch_id"] != batch:
            continue
        mods.append({"id": f"m-{r['run_id']}", "core_run_id": r["run_id"],
                     "name": r["stage"], "run_state": "planned"})
    return mods


def events_ledger(count=4, alloc_count=15):
    """台账样本：1 条 split（×49）+ 2 条 allocate（顶层 4 / 15）。"""
    return [
        {"event_id": "EV-1", "batch_id": BATCH, "kind": "split", "at": "2026-09-03",
         "from_sample_id": ROOT, "to_sample_id": f"{BATCH}-01-DIE4", "count": str(count),
         "after_stage": "LDW", "status": "done", "note": "物理裂片"},
        {"event_id": "EV-2", "batch_id": BATCH, "kind": "allocate", "at": "2026-09-03",
         "from_sample_id": ROOT, "to_sample_id": f"{BATCH}-01-DIE4", "count": "4",
         "after_stage": "LDW", "status": "done", "note": "顶层取样"},
        {"event_id": "EV-3", "batch_id": BATCH, "kind": "allocate", "at": "2026-09-03",
         "from_sample_id": ROOT, "to_sample_id": f"{BATCH}-01-DIE15", "count": str(alloc_count),
         "after_stage": "LDW", "status": "done", "note": "顶层取样"},
    ]

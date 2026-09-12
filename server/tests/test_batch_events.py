"""批次事件 / 计划-实际 / 提案预检（`kb/batch_events.py`）—— 回归网。

**只读台账 + 产出提案**：工具不写 core、不直接写台账；落账走数据线 `propose_apply.py`。
这里的每一条断言都对着数据线纠正过的概念：
  · `split`（物理裂片，AR50-T1 只有 1 条 ×49）≠ `allocate`（取样分配，不改样品总数）
  · `used_top`（从整片取）与 `used_within`（从已分配组再取）**不得相加**
  · 一致性权威在 `ingest/core_schema.py` 的 `qa()`（QA 关 [11]），工具只预览
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from conftest import seed_core
from batch_fixtures import BATCH, ROOT, batch_rows, run_rows, sample_rows

DIE4 = f"{BATCH}-01-DIE4"
DIE15 = f"{BATCH}-01-DIE15"
DIE15_01 = f"{BATCH}-01-DIE15-01"


def ledger(rows):
    """把事件行写成真台账（append-only ⇒ 直接落文件即可）。"""
    fields = ["event_id", "batch_id", "kind", "at", "from_sample_id", "to_sample_id",
              "count", "after_stage", "id_pattern", "status", "note"]
    return fields, rows


DEFAULT_EVENTS = [
    {"event_id": "EV-1", "batch_id": BATCH, "kind": "split", "at": "2026-09-03",
     "from_sample_id": ROOT, "to_sample_id": DIE4, "count": "49",
     "after_stage": "LDW", "status": "done", "note": "物理裂片：1 片 → 49 颗"},
    {"event_id": "EV-2", "batch_id": BATCH, "kind": "allocate", "at": "2026-09-03",
     "from_sample_id": ROOT, "to_sample_id": DIE4, "count": "4",
     "after_stage": "LDW", "status": "done", "note": "顶层取样 4 颗"},
    {"event_id": "EV-3", "batch_id": BATCH, "kind": "allocate", "at": "2026-09-03",
     "from_sample_id": ROOT, "to_sample_id": DIE15, "count": "15",
     "after_stage": "LDW", "status": "done", "note": "顶层取样 15 颗"},
    {"event_id": "EV-4", "batch_id": BATCH, "kind": "allocate", "at": "2026-09-03",
     "from_sample_id": DIE15, "to_sample_id": DIE15_01, "count": "1",
     "after_stage": "LDW", "status": "done", "note": "组内再取 1 颗"},
]


@pytest.fixture
def core(tmp_path, monkeypatch):
    from kb import append_pack as ap
    d = seed_core(tmp_path / "core", runs=run_rows(), batches=batch_rows(),
                  samples=sample_rows())
    monkeypatch.setattr(ap, "CORE_DIR", d)
    monkeypatch.setenv("OPENNANO_CORE_DIR", str(d))
    return d


@pytest.fixture
def events_file(tmp_path, monkeypatch):
    def _write(rows):
        fields = ["event_id", "batch_id", "kind", "at", "from_sample_id", "to_sample_id",
                  "count", "after_stage", "id_pattern", "status", "note"]
        p = tmp_path / "batch_events.csv"
        with p.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in fields})
        monkeypatch.setenv("OPENNANO_BATCH_EVENTS", str(p))
        return p
    return _write


# ------------------------------------------------------------------ 读台账
def test_read_events_同号后写生效_且按批过滤(events_file):
    from kb.batch_events import read_events
    events_file(DEFAULT_EVENTS + [
        {"event_id": "EV-2", "batch_id": BATCH, "kind": "allocate", "at": "2026-09-03",
         "from_sample_id": ROOT, "to_sample_id": DIE4, "count": "6", "status": "done",
         "note": "纠错：4 → 6"},
        {"event_id": "EV-9", "batch_id": "OTHER", "kind": "split", "at": "2026-09-04",
         "from_sample_id": "X", "count": "2", "status": "done"},
    ])
    evs = read_events(BATCH)
    assert [e["event_id"] for e in evs] == ["EV-1", "EV-2", "EV-3", "EV-4"]   # 别的批不进
    by_id = {e["event_id"]: e for e in evs}
    assert by_id["EV-2"]["count"] == "6"                                     # 后写生效
    assert by_id["EV-2"]["note"].startswith("纠错")


def test_read_events_没有台账就空列表(monkeypatch, tmp_path):
    from kb.batch_events import read_events
    monkeypatch.setenv("OPENNANO_BATCH_EVENTS", str(tmp_path / "nope.csv"))
    assert read_events(BATCH) == []


# ------------------------------------------------------------------ 计划 / 实际（用量分层）
def test_plan_vs_actual_用量分层_三口径不许混(core, events_file):
    from kb.batch_events import plan_vs_actual
    events_file(DEFAULT_EVENTS)
    r = plan_vs_actual(BATCH)
    assert r["planned"] == 49                                     # 计划来自 sample_spec_json.grid.count
    assert r["used_top"] == 19                                    # 4 + 15（都从整片取）
    assert r["used_within"] == 1                                  # 从 DIE15 组里再取 1 颗
    assert r["sum_all"] == 20                                     # 仅兼容旧字段：**不得当实际用量展示**
    assert r["unallocated"] == 30                                 # 49 − 19（不含组内取用）
    assert r["used_from_wafer"] == r["used_top"]
    assert r["used_from_group"] == r["used_within"]
    assert r["root_sample_id"] == ROOT


def test_plan_vs_actual_split_不算用量(core, events_file):
    """`split` 是物理裂片、不是"用了" ⇒ 计数里不能含它（否则 49 会被算成用量）。"""
    from kb.batch_events import plan_vs_actual
    events_file([e for e in DEFAULT_EVENTS if e["kind"] == "split"])
    r = plan_vs_actual(BATCH)
    assert r["used_top"] == 0 and r["used_within"] == 0 and r["sum_all"] == 0
    assert len(r["splits"]) == 1 and r["splits"][0]["count"] == "49"
    assert r["allocations"] == []
    assert "不可混为一谈" in r["note"]


def test_plan_vs_actual_位号按_id_pattern_生成(core, events_file):
    from kb.batch_events import plan_vs_actual
    events_file(DEFAULT_EVENTS)
    r = plan_vs_actual(BATCH)
    assert r["id_pattern"] == f"{BATCH}-01-D{{n:02d}}"
    assert r["planned_ids"][0] == f"{BATCH}-01-D01"
    assert r["planned_ids"][-1] == "…"                            # 超过 8 个就省略


def test_plan_vs_actual_没规格时_planned_为_None(core, events_file, monkeypatch, tmp_path):
    """没写 `sample_spec_json` ⇒ `planned=None`（**不猜 49**）、`unallocated=None`。"""
    from kb import append_pack as ap
    from kb.batch_events import plan_vs_actual
    d = seed_core(tmp_path / "core2", runs=run_rows(), batches=batch_rows(with_spec=False),
                  samples=sample_rows())
    monkeypatch.setattr(ap, "CORE_DIR", d)
    monkeypatch.setenv("OPENNANO_CORE_DIR", str(d))
    events_file(DEFAULT_EVENTS)
    r = plan_vs_actual(BATCH)
    assert r["planned"] is None and r["unallocated"] is None
    assert r["used_top"] == 19                                    # 实际用量照算


# ------------------------------------------------------------------ 提案
def test_build_proposal_自动编号且不落盘(events_file, tmp_path):
    from kb.batch_events import build_proposal
    p = build_proposal(BATCH, [{"kind": "allocate", "to_sample_id": DIE4, "count": 2,
                                "from_sample_id": ROOT, "at": "2026-09-14"}],
                       operator="owner", note_prefix="回归")
    assert p["batch_id"] == BATCH and p["operator"] == "owner"
    e = p["events"][0]
    assert e["event_id"].startswith(f"EV-{BATCH.replace('-', '')}-allocate-20260914")
    assert e["note"].startswith("回归；")
    assert "未落账" in p["_generated_by"]
    assert not list(tmp_path.rglob("*.json"))                     # **不落盘**


def test_precheck_拦下会被数据线拒的提案(core, events_file):
    """注意：`build_proposal` 会给缺 `at` 的行填今天 ⇒ 测"缺 at"要直接喂 precheck。"""
    from kb.batch_events import build_proposal, precheck
    events_file(DEFAULT_EVENTS)
    p = build_proposal(BATCH, [
        {"kind": "frobnicate", "at": "2026-09-14"},                        # 未知 kind
        {"kind": "allocate", "at": "2026-09-14", "from_sample_id": "NO-SUCH",
         "to_sample_id": DIE4, "count": "2"},                              # 脏父样品
        {"kind": "allocate", "at": "2026-09-14", "from_sample_id": ROOT,
         "to_sample_id": DIE4, "count": "-3"},                             # 负数
        {"kind": "allocate", "at": "2026-09-14", "from_sample_id": ROOT, "count": "2"},  # 缺 to
    ])
    p["events"].append({"event_id": "EV-X", "batch_id": BATCH, "kind": "split",
                        "at": "", "from_sample_id": ROOT, "count": "1"})   # 缺 at
    r = precheck(p)
    assert r["ok"] is False and r["checked"] == 5
    joined = "\n".join(r["errors"])
    assert "kind 不认识" in joined and "缺 at" in joined
    assert "不在 core/samples" in joined and "count 非整数" in joined
    assert "allocate 必须给 to_sample_id" in joined


def test_precheck_幂等重复只算警告(core, events_file):
    """与台账语义等价 ⇒ 数据线会跳过（**不算错**）—— 但要提醒。"""
    from kb.batch_events import build_proposal, precheck
    events_file(DEFAULT_EVENTS)
    p = build_proposal(BATCH, [{"kind": "allocate", "at": "2026-09-03",
                                "from_sample_id": ROOT, "to_sample_id": DIE15,
                                "count": "15"}])
    r = precheck(p)
    assert r["ok"] is True and r["errors"] == []
    assert "语义等价" in r["warnings"][0]


def test_precheck_干净提案通过(core, events_file):
    from kb.batch_events import build_proposal, precheck
    events_file(DEFAULT_EVENTS)
    p = build_proposal(BATCH, [{"kind": "allocate", "at": "2026-09-14",
                                "from_sample_id": ROOT, "to_sample_id": DIE4, "count": "3"}])
    assert precheck(p)["ok"] is True


# ------------------------------------------------------------------ 一致性预览
def test_consistency_preview_三类违规(core, events_file):
    from kb.batch_events import consistency_preview
    events_file(DEFAULT_EVENTS + [
        {"event_id": "EV-5", "batch_id": BATCH, "kind": "allocate", "at": "2026-09-04",
         "from_sample_id": "GHOST-FROM", "to_sample_id": "GHOST-TO", "count": "1",
         "status": "done"},
    ])
    r = consistency_preview(BATCH)
    assert r["checked"] == 5
    assert [x["event_id"] for x in r["dangling_to"]] == ["EV-5"]
    assert [x["event_id"] for x in r["dangling_from"]] == ["EV-5"]
    # 三项：to 悬空 + from 悬空 + done 的 allocate 却没样品行（GHOST-TO 不在 samples）
    assert r["violations"] == 3
    assert [x["event_id"] for x in r["allocate_done_without_sample"]] == ["EV-5"]


def test_consistency_preview_指向数据线权威(core, events_file):
    """⚠️ 工具只预览；**权威**是 `ingest/core_schema.py` 的 `qa()`（QA 关 [11]）。"""
    from kb.batch_events import consistency_preview
    events_file(DEFAULT_EVENTS)
    r = consistency_preview(BATCH)
    assert r["violations"] == 0
    assert "ingest/core_schema.py" in r["authority"] and "qa()" in r["authority"]
    assert "split" in r["note"] and "不建样品行" in r["note"]


# ------------------------------------------------------------------ 落账入口
def test_run_proposer_找不到脚本要报错不静默(monkeypatch, tmp_path):
    from kb.batch_events import build_proposal, run_proposer
    monkeypatch.setenv("OPENNANO_PROPOSE_APPLY", str(tmp_path / "nope.py"))
    r = run_proposer(build_proposal(BATCH, []))
    assert r["ok"] is False and "找不到数据线脚本" in r["error"]

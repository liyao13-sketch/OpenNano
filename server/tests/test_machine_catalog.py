"""机台清单外置（T1 数据扩展点）—— `kb/machine_catalog.py` 的回归锁。

口径（本文件钉住）：
  · **没有外部清单 ⇒ 行为与以前完全一样**（内建表播种，本轮不改变权威归属）；
  · 有清单 ⇒ 用它播种（新增机台＝改数据，不改仓库代码）；
  · 清单**存在但坏** ⇒ 抛 `MachineCatalogError` 出声，**绝不静默回退**内建表；
  · `status()` 永不抛（给 /api/health 用）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.library import LibraryStore
from kb import machine_catalog as mc


def test_没有清单时行为不变(tmp_path, monkeypatch):
    """回归锁：不提供外部清单时，播种结果与改动前一致（机台数与字段都齐）。"""
    monkeypatch.delenv("OPENNANO_MACHINES", raising=False)
    monkeypatch.setenv("OPENNANO_MACHINES", str(tmp_path / "nope.json"))
    lib = LibraryStore(tmp_path / "library.json")
    assert len(lib.data["machines"]) >= 10, "没有清单时内建播种失效了"
    assert all(m.get("name") for m in lib.data["machines"])


def test_有清单时按清单播种(tmp_path, monkeypatch):
    cat = tmp_path / "machines.json"
    cat.write_text(json.dumps({"version": 1, "machines": [
        {"name": "机台甲", "equipment_id": "etch_x", "tool_id": "TOOLX",
         "vendor": "厂A", "model": "M1"},
        {"name": "机台乙", "status": "active"},
    ]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("OPENNANO_MACHINES", str(cat))
    lib = LibraryStore(tmp_path / "library.json")
    names = [m["name"] for m in lib.data["machines"]]
    # 外部清单管辖"电学/工艺机台"这一段（后面 `_seed_metrology_machines` 仍会追加共用表征设备）
    assert names[:2] == ["机台甲", "机台乙"], names
    assert lib.data["machines"][0]["vendor"] == "厂A"
    assert lib.data["machines"][0]["tool_id"] == "TOOLX"


def test_清单坏掉要出声_不静默回退(tmp_path, monkeypatch):
    cat = tmp_path / "machines.json"
    cat.write_text("{这不是合法 json", encoding="utf-8")
    monkeypatch.setenv("OPENNANO_MACHINES", str(cat))
    with pytest.raises(mc.MachineCatalogError) as e:
        mc.load()
    assert "不会" in str(e.value) or "不回退" in str(e.value).replace("**", "")


def test_清单校验_缺名与重名都要报(tmp_path):
    errs = mc.validate([{"vendor": "x"}, {"name": "A"}, {"name": "A"}])
    assert any("缺 `name`" in e for e in errs)
    assert any("重复" in e for e in errs)
    assert mc.validate([{"name": "A"}]) == []


def test_status_永不抛(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENNANO_MACHINES", str(tmp_path / "x.json"))
    st = mc.status()
    assert st["present"] is False and st["ok"] is True      # 没清单 = 正常（用内建表）
    bad = tmp_path / "bad.json"
    bad.write_text("[[[", encoding="utf-8")
    monkeypatch.setenv("OPENNANO_MACHINES", str(bad))
    st2 = mc.status()
    assert st2["present"] is True and st2["ok"] is False and st2["error"]
